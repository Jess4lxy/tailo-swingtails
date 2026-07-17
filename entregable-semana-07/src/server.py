"""Servicio HTTP de Tailo Agent - entregable semana 07 (arquitectura multi-agente).

Hereda TODO lo de la semana 05/06 (memoria persistente multiusuario, guardrails,
observabilidad SQLite, streaming SSE, voz Whisper, aislamiento de token por
peticion con ContextVar) y sustituye el pipeline MONOLITICO por el ORQUESTADOR
multi-agente de la semana 07:

    guardrail -> ruteador -> subagente especialista (RAG | transaccional | charla)

El orquestador (agents/orchestrator.py) decide la ruta y delega en el
especialista adecuado, que corre con un prompt reducido y sus herramientas
exclusivas. El especialista RAG usa el pipeline de Advanced RAG (busqueda
hibrida + reranking). El servidor conserva la responsabilidad de persistir la
memoria conversacional y la observabilidad; el orquestador solo produce el turno.

Endpoints (sin cambios de contrato salvo campos nuevos en la respuesta):
    GET    /health
    POST   /chat                 respuesta completa (ahora incluye route/sources/tools)
    POST   /chat/stream          streaming SSE: fases + route + tokens
    POST   /transcribe           audio -> texto (Whisper local)
    GET    /conversations        lista de conversaciones del usuario
    GET    /conversations/{id}   historial completo
    DELETE /conversations/{id}   borra una conversacion
    GET    /observability/logs   bitacora de auditoria (?session_id, ?limit)
    GET    /observability/stats  agregados (TTFT/latencia/tps, % bloqueos)

Arranque:
    uvicorn server:app --reload --port 8000      # desde la carpeta src/
    # o:  python src/server.py
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import ollama
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import api_client
import observability
import sessions
from agents.orchestrator import Orchestrator
from config import (
    CORS_ORIGINS,
    LLM_MODEL,
    OLLAMA_HOST,
    TOP_K,
    WEB_DIST,
    WHISPER_COMPUTE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)
from retrieve import Retriever

app = FastAPI(title="Tailo Agent", version="0.7")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Recursos compartidos entre peticiones (solo lectura / concurrencia segura):
# se crean una vez. El orquestador reutiliza el mismo Retriever (embeddings +
# indice BM25 + reranker) y cliente Ollama para todas las peticiones.
_RETRIEVER: Retriever | None = None
_OLLAMA: ollama.Client | None = None
_ORCH: Orchestrator | None = None
_WHISPER = None  # modelo faster-whisper (carga perezosa: es pesado)


@app.on_event("startup")
def _startup() -> None:
    sessions.init_db()        # memoria conversacional (semana 04)
    observability.init_db()   # bitacora de auditoria (semana 05)


def _retriever() -> Retriever:
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever(top_k=TOP_K)
    return _RETRIEVER


def _ollama() -> ollama.Client:
    global _OLLAMA
    if _OLLAMA is None:
        _OLLAMA = ollama.Client(host=OLLAMA_HOST)
    return _OLLAMA


def _orchestrator() -> Orchestrator:
    global _ORCH
    if _ORCH is None:
        _ORCH = Orchestrator(client=_ollama(), retriever=_retriever(), model=LLM_MODEL)
    return _ORCH


# ---------------------------------------------------------------------------
# Modelos de request/response
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., description="Mensaje del usuario.")
    conversation_id: str | None = Field(
        default=None,
        description="UUID del hilo. Si falta o no existe, se crea uno nuevo.",
    )
    new_session: bool = Field(
        default=False,
        description="Fuerza una conversacion nueva aunque se envie un id.",
    )


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    user_id: int | None
    route: str | None = None                 # ruta elegida por el ruteador (semana 07)
    sources: list[str] = []
    tools_executed: list[dict] = []          # traza de herramientas [{name, parameters, status}]
    context: list[str] = []                  # contexto recuperado / resultados de tools (para auditoria)
    turns: int = 0
    compacted: bool = False
    blocked: bool = False
    ttft_ms: float | None = None
    total_latency_ms: float | None = None
    tokens_per_second: float | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    n_messages: int
    updated_at: str


class TranscriptionResponse(BaseModel):
    text: str
    language: str | None = None
    duration_ms: float | None = None


# ---------------------------------------------------------------------------
# Autenticacion (JWT que reenvia la app de SwingTails)
# ---------------------------------------------------------------------------
def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <jwt>")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vacio")
    return token


def _validate_token(authorization: str | None) -> tuple[api_client.SwingTailsClient, int]:
    """Valida el JWT y devuelve (cliente ligado al token, user_id). NO toca el
    ContextVar: el caller decide cuando/donde activarlo (en el hilo correcto)."""
    token = _bearer_token(authorization)
    if api_client.token_is_expired(token):
        raise HTTPException(status_code=401, detail="Token expirado; inicia sesion de nuevo")
    client = api_client.client_from_token(token)
    if client.current_user_id is None:
        raise HTTPException(status_code=401, detail="Token invalido o sin id de usuario")
    return client, client.current_user_id


def _authed_user_id(authorization: str | None) -> tuple[int, object]:
    """Valida el JWT y ACTIVA el cliente en el ContextVar del contexto actual.

    Usar solo en endpoints sincronos (corren en un unico hilo del threadpool de
    Starlette, asi que el ContextVar set/reset vive y muere en el mismo hilo)."""
    client, user_id = _validate_token(authorization)
    ctx_token = api_client.use_request_client(client)
    return user_id, ctx_token


# ---------------------------------------------------------------------------
# Pipeline del turno: orquestador multi-agente + persistencia + observabilidad
# ---------------------------------------------------------------------------
def _run_turn_pipeline(req: "ChatRequest", user_id: int):
    """Generador SINCRONO del turno. Yields los eventos del orquestador (phase,
    route, token, tool, blocked) y, al final, un evento 'done' ENRIQUECIDO con
    conversation_id/turns/compacted despues de persistir memoria + observabilidad.

    Debe correr dentro de un contexto con el cliente HTTP del usuario activado
    (ContextVar) para que las tools transaccionales operen en su nombre."""
    # --- A. Resolucion del conversation_id ----------------------------------
    conv_id = req.conversation_id
    if req.new_session or not conv_id or not sessions.conversation_exists(conv_id, user_id):
        conv_id = sessions.create_conversation(user_id=user_id)

    # --- C. Prompting: historial persistido (resumen + turnos recientes) -----
    # El orquestador lo pasa TAL CUAL al especialista (contexto entre agentes).
    context = sessions.build_context(conv_id)

    done_ev: dict | None = None
    for ev in _orchestrator().run_turn(context, req.message):
        if ev.get("type") == "done":
            done_ev = ev
            break
        yield ev

    if done_ev is None:  # defensivo: no deberia pasar
        done_ev = {"type": "done", "reply": "", "route": None, "blocked": False,
                   "sources": [], "tools_executed": [], "context": []}

    reply = done_ev.get("reply", "")
    blocked = bool(done_ev.get("blocked"))

    # --- B. Persistencia + D. compactacion (solo si no fue bloqueado) --------
    compacted = False
    turns = 0
    if not blocked:
        sessions.append_turn(conv_id, req.message, reply)
        comp = sessions.compact(conv_id, client=_ollama(), model=LLM_MODEL)
        compacted = comp["compacted"]
        turns = len(sessions.get_all_messages(conv_id)) // 2

    # --- Observabilidad (semana 05): una fila por interaccion ----------------
    observability.log_interaction(
        session_id=conv_id,
        user_prompt=req.message,
        system_response=reply,
        ttft_ms=done_ev.get("ttft_ms"),
        total_latency_ms=done_ev.get("total_latency_ms"),
        tokens_per_second=done_ev.get("tokens_per_second"),
        was_blocked=blocked,
        tools_executed=done_ev.get("tools_executed", []),
    )

    done_ev.update(conversation_id=conv_id, user_id=user_id, turns=turns, compacted=compacted)
    yield done_ev


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": LLM_MODEL, "arquitectura": "multi-agente (semana 07)"}


# ---------------------------------------------------------------------------
# /chat  (respuesta completa, sin streaming)
# ---------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    user_id, ctx_token = _authed_user_id(authorization)
    try:
        done = None
        for ev in _run_turn_pipeline(req, user_id):
            if ev.get("type") == "done":
                done = ev
        done = done or {}
        return ChatResponse(
            reply=done.get("reply", ""),
            conversation_id=done.get("conversation_id", ""),
            user_id=user_id,
            route=done.get("route"),
            sources=done.get("sources", []),
            tools_executed=done.get("tools_executed", []),
            context=done.get("context", []),
            turns=done.get("turns", 0),
            compacted=done.get("compacted", False),
            blocked=done.get("blocked", False),
            ttft_ms=done.get("ttft_ms"),
            total_latency_ms=done.get("total_latency_ms"),
            tokens_per_second=done.get("tokens_per_second"),
        )
    finally:
        api_client.reset_request_client(ctx_token)


# ---------------------------------------------------------------------------
# /chat/stream  (Server-Sent Events: fases + ruta + tokens)
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    """Formatea un evento SSE (text/event-stream)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest, authorization: str | None = Header(default=None)
) -> StreamingResponse:
    """Respuesta en streaming. Emite eventos SSE:

        event: phase   -> {phase: routing|searching|thinking|executing|generating, detail, tool?}
        event: route   -> {route, reason, method}      (decision del ruteador)
        event: token   -> {text}                        (un fragmento del LLM)
        event: tool    -> {name, status}                (herramienta ejecutada)
        event: blocked -> {message, reason}             (guardrail: NO se llamo al LLM)
        event: done    -> {conversation_id, route, sources, tools_executed, turns,
                           ttft_ms, total_latency_ms, tokens_per_second, compacted}
        event: error   -> {message}
    """
    client, user_id = _validate_token(authorization)

    async def event_gen():
        # El pipeline es sincrono (Ollama + tools + SQLite) y usa el ContextVar
        # del api_client (por-hilo). Lo corremos COMPLETO en un unico hilo del
        # executor y empujamos los eventos a una cola que este generador consume.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        sentinel = object()

        def worker() -> None:
            ctx_token = api_client.use_request_client(client)
            try:
                for ev in _run_turn_pipeline(req, user_id):
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": f"{exc.__class__.__name__}: {exc}"},
                )
            finally:
                api_client.reset_request_client(ctx_token)
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        loop.run_in_executor(None, worker)

        while True:
            ev = await queue.get()
            if ev is sentinel:
                break
            etype = ev.pop("type")
            yield _sse(etype, ev)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # desactiva el buffering de nginx/proxies
        },
    )


# ---------------------------------------------------------------------------
# /transcribe  (Speech-to-Text local con Whisper)
# ---------------------------------------------------------------------------
def _whisper():
    """Carga perezosa del modelo faster-whisper (CPU int8: no toca la VRAM)."""
    global _WHISPER
    if _WHISPER is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # dependencia opcional y pesada
            raise HTTPException(
                status_code=503,
                detail="Whisper no esta instalado. Ejecuta: pip install faster-whisper",
            ) from exc
        _WHISPER = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
        )
    return _WHISPER


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> TranscriptionResponse:
    """Transcribe un audio (multipart 'audio') a texto con Whisper local."""
    _validate_token(authorization)  # mismas credenciales que el chat
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Audio vacio")

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    t0 = time.perf_counter()

    def _do_transcription() -> tuple[str, str | None]:
        model = _whisper()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            segments, info = model.transcribe(
                tmp_path, language=WHISPER_LANGUAGE, vad_filter=True
            )
            text = "".join(seg.text for seg in segments).strip()
            lang = getattr(info, "language", None)
            return text, lang
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    text, lang = await asyncio.get_running_loop().run_in_executor(None, _do_transcription)
    return TranscriptionResponse(
        text=text,
        language=lang,
        duration_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


# ---------------------------------------------------------------------------
# Conversaciones (heredado de semana 04, sin cambios funcionales)
# ---------------------------------------------------------------------------
@app.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(authorization: str | None = Header(default=None)) -> list[ConversationSummary]:
    user_id, ctx_token = _authed_user_id(authorization)
    try:
        return [
            ConversationSummary(
                conversation_id=c["id"],
                title=c["title"],
                n_messages=c["n_messages"],
                updated_at=c["updated_at"],
            )
            for c in sessions.list_conversations(user_id=user_id)
        ]
    finally:
        api_client.reset_request_client(ctx_token)


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id, ctx_token = _authed_user_id(authorization)
    try:
        if not sessions.conversation_exists(conversation_id, user_id):
            raise HTTPException(status_code=404, detail="Conversacion no encontrada")
        conv = sessions.get_conversation(conversation_id)
        return {
            "conversation_id": conversation_id,
            "title": conv["title"],
            "summary": conv["summary"],
            "messages": sessions.get_all_messages(conversation_id),
        }
    finally:
        api_client.reset_request_client(ctx_token)


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id, ctx_token = _authed_user_id(authorization)
    try:
        if not sessions.conversation_exists(conversation_id, user_id):
            raise HTTPException(status_code=404, detail="Conversacion no encontrada")
        sessions.delete_conversation(conversation_id)
        return {"deleted": conversation_id}
    finally:
        api_client.reset_request_client(ctx_token)


# ---------------------------------------------------------------------------
# Observabilidad (Bitacora de Auditoria) - lectura para el frontend
# ---------------------------------------------------------------------------
def _obs_row(row: dict) -> dict:
    """Normaliza una fila para el front: was_blocked -> bool, tools -> lista."""
    try:
        tools = json.loads(row.get("tools_executed") or "[]")
    except (json.JSONDecodeError, TypeError):
        tools = []
    return {
        "id": row.get("id"),
        "session_id": row.get("session_id"),
        "timestamp": row.get("timestamp"),
        "user_prompt": row.get("user_prompt"),
        "system_response": row.get("system_response"),
        "ttft_ms": row.get("ttft_ms"),
        "total_latency_ms": row.get("total_latency_ms"),
        "tokens_per_second": row.get("tokens_per_second"),
        "was_blocked": bool(row.get("was_blocked")),
        "tools_executed": tools,
    }


@app.get("/observability/logs")
def observability_logs(
    session_id: str | None = None,
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    """Devuelve los registros de auditoria, mas reciente primero.

    - `?session_id=<conversation_id>` filtra por una conversacion.
    - `?limit=<n>` acota la cantidad (1..500). Requiere JWT valido."""
    _validate_token(authorization)
    limit = max(1, min(int(limit), 500))
    rows = observability.recent_logs(limit=limit, session_id=session_id)
    return [_obs_row(r) for r in rows]


@app.get("/observability/stats")
def observability_stats(authorization: str | None = Header(default=None)) -> dict:
    """Agregados para el informe: total, % bloqueados, TTFT/latencia/tps medios."""
    _validate_token(authorization)
    s = observability.stats()
    total = s.get("total") or 0
    blocked = s.get("blocked") or 0
    return {
        "total": total,
        "blocked": blocked,
        "blocked_pct": round(100 * blocked / total, 2) if total else 0,
        "avg_ttft_ms": round(s["avg_ttft_ms"], 2) if s.get("avg_ttft_ms") else None,
        "avg_latency_ms": round(s["avg_latency_ms"], 2) if s.get("avg_latency_ms") else None,
        "avg_tokens_per_second": round(s["avg_tps"], 2) if s.get("avg_tps") else None,
    }


# ---------------------------------------------------------------------------
# Frontend estatico (opcional): un SOLO tunel publica la pagina Y la API en el
# mismo origen. Se monta AL FINAL para que las rutas de la API tengan prioridad.
# ---------------------------------------------------------------------------
_web_dist = Path(WEB_DIST)
if _web_dist.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
    print(f"[web] Sirviendo frontend estatico desde: {_web_dist}")
else:
    print(f"[web] Sin frontend estatico (no existe {_web_dist}); solo API.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
