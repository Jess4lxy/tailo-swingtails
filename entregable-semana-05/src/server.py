"""Servicio HTTP de Tailo Agent - entregable semana 05.

Hereda TODO lo de la semana 04 (memoria persistente multiusuario, RAG +
Function Calling, aislamiento de token por peticion con ContextVar) y agrega la
capa que el frontend necesita para integrarse:

  * `POST /chat/stream` (Server-Sent Events): respuesta en STREAMING token por
    token + eventos de FASE del agente (pensando / buscando / ejecutando accion)
    para que el cliente pinte indicadores de carga dinamicos.
  * Capa de seguridad (Guardrails): cada entrada pasa por
    `guardrails.check_prompt_injection` ANTES de tocar el LLM. Si es un intento
    de inyeccion conocido, se devuelve un mensaje generico y NO se gasta
    inferencia local.
  * Observabilidad: cada interaccion se persiste en SQLite (observability.py)
    con TTFT, latencia total, tokens/segundo, estado del guardrail y el JSON de
    las herramientas ejecutadas.
  * `POST /transcribe`: Speech-to-Text local con Whisper (faster-whisper, CPU
    int8) para la entrada de voz.

Arranque:
    uvicorn server:app --reload --port 8000      # desde la carpeta src/
    # o:  python src/server.py

Endpoints:
    GET    /health
    POST   /chat                       respuesta completa (sin streaming)
    POST   /chat/stream                respuesta en streaming SSE + fases
    POST   /transcribe                 audio (multipart) -> texto (Whisper local)
    GET    /conversations              lista las conversaciones del usuario
    GET    /conversations/{id}         devuelve el historial completo
    DELETE /conversations/{id}         borra una conversacion
"""
from __future__ import annotations

import asyncio
import datetime
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
from chat import _tool_status, build_user_message
from config import (
    CORS_ORIGINS,
    LLM_MODEL,
    OLLAMA_HOST,
    TOP_K,
    WHISPER_COMPUTE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)
from guardrails import check_prompt_injection
from retrieve import Retriever
from tools import TOOL_SCHEMAS, execute_tool

app = FastAPI(title="Tailo Agent", version="0.5")

# El frontend de la semana 05 corre en otro origen (Vite/Live Server), asi que
# habilitamos CORS. Por defecto "*" para desarrollo local (ver config).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Recursos compartidos entre peticiones (solo lectura): se crean una vez.
_RETRIEVER: Retriever | None = None
_OLLAMA: ollama.Client | None = None
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
    sources: list[str]
    turns: int
    compacted: bool
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
# Etiquetas de accion legibles para los indicadores de carga del frontend.
# Mapean cada tool a una frase de "Ejecutando accion" (rubrica fase A, punto 3).
# ---------------------------------------------------------------------------
_TOOL_LABELS: dict[str, str] = {
    "list_my_pets": "Consultando tus mascotas…",
    "get_pet": "Buscando la ficha de tu mascota…",
    "register_pet": "Registrando tu mascota…",
    "update_pet": "Actualizando los datos de tu mascota…",
    "delete_pet": "Eliminando la mascota…",
    "list_clinics": "Buscando clínicas veterinarias…",
    "list_appointments": "Consultando tus citas…",
    "book_appointment": "Agendando tu cita…",
    "reschedule_appointment": "Reagendando tu cita…",
    "cancel_appointment": "Cancelando la cita…",
    "list_products": "Buscando en el catálogo…",
    "get_product": "Consultando el producto…",
    "list_clinic_reviews": "Leyendo reseñas de la clínica…",
    "get_clinic_rating": "Calculando la calificación de la clínica…",
    "review_clinic": "Publicando tu reseña…",
}


def _humanize_tool(name: str) -> str:
    return _TOOL_LABELS.get(name, f"Ejecutando {name}…")


def _date_prefixed(user_msg: str) -> str:
    today = datetime.date.today().isoformat()
    return (
        f"[Fecha de hoy: {today}. Si el usuario da una fecha sin año, usa el "
        f"año actual.]\n\n{user_msg}"
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": LLM_MODEL}


# ---------------------------------------------------------------------------
# /chat  (respuesta completa, sin streaming)  -- compat semana 04 + guardrails
# ---------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    user_id, ctx_token = _authed_user_id(authorization)
    t_start = time.perf_counter()
    try:
        # --- Guardrail PREVENTIVO: filtra antes de gastar inferencia ---------
        guard = check_prompt_injection(req.message)
        if guard.blocked:
            observability.log_interaction(
                session_id=req.conversation_id,
                user_prompt=req.message,
                system_response=guard.message,
                total_latency_ms=(time.perf_counter() - t_start) * 1000,
                was_blocked=True,
                tools_executed=[],
            )
            return ChatResponse(
                reply=guard.message,
                conversation_id=req.conversation_id or "",
                user_id=user_id,
                sources=[],
                turns=0,
                compacted=False,
                blocked=True,
            )

        # --- A. Resolucion del conversation_id ------------------------------
        conv_id = req.conversation_id
        if req.new_session or not conv_id or not sessions.conversation_exists(conv_id, user_id):
            conv_id = sessions.create_conversation(user_id=user_id)

        # --- C. Prompting: historial persistido + mensaje nuevo (RAG) -------
        user_msg, chunks, _lat = build_user_message(req.message, _retriever(), top_k=TOP_K)
        context = sessions.build_context(conv_id)
        messages = context + [{"role": "user", "content": _date_prefixed(user_msg)}]

        # Fase 1: ciclo de tools (con traza para la observabilidad).
        trace: list[dict] = []
        messages = _run_tool_cycle_traced(messages, trace)

        # Fase 2: respuesta final (sin streaming) + stats para tokens/segundo.
        reply, eval_count, eval_duration = _generate_final(messages)
        tps = _tokens_per_second(eval_count, eval_duration)

        # --- B. Persistencia del par user/assistant LIMPIO ------------------
        sessions.append_turn(conv_id, req.message, reply)
        comp = sessions.compact(conv_id, client=_ollama(), model=LLM_MODEL)

        total_latency = (time.perf_counter() - t_start) * 1000
        sources = sorted({c.metadata.get("source", "?") for c in chunks})
        turns = len(sessions.get_all_messages(conv_id)) // 2

        observability.log_interaction(
            session_id=conv_id,
            user_prompt=req.message,
            system_response=reply,
            ttft_ms=None,  # respuesta no-streaming: no hay "primer token" medible
            total_latency_ms=total_latency,
            tokens_per_second=tps,
            was_blocked=False,
            tools_executed=trace,
        )
        return ChatResponse(
            reply=reply,
            conversation_id=conv_id,
            user_id=user_id,
            sources=sources,
            turns=turns,
            compacted=comp["compacted"],
            total_latency_ms=round(total_latency, 2),
            tokens_per_second=round(tps, 2) if tps else None,
        )
    finally:
        api_client.reset_request_client(ctx_token)


# ---------------------------------------------------------------------------
# /chat/stream  (Server-Sent Events: tokens + fases del agente)
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    """Formatea un evento SSE (text/event-stream)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest, authorization: str | None = Header(default=None)
) -> StreamingResponse:
    """Respuesta en streaming. Emite eventos SSE:

        event: phase   -> {phase: searching|thinking|executing|generating, detail, tool?}
        event: token   -> {text}            (un fragmento del LLM)
        event: blocked -> {message, reason} (guardrail: NO se llamo al LLM)
        event: done    -> {conversation_id, sources, turns, ttft_ms,
                           total_latency_ms, tokens_per_second, compacted}
        event: error   -> {message}
    """
    client, user_id = _validate_token(authorization)

    # Guardrail PREVENTIVO: si bloquea, ni siquiera levantamos el worker/LLM.
    guard = check_prompt_injection(req.message)

    async def event_gen():
        if guard.blocked:
            observability.log_interaction(
                session_id=req.conversation_id,
                user_prompt=req.message,
                system_response=guard.message,
                was_blocked=True,
                tools_executed=[],
            )
            yield _sse("blocked", {"message": guard.message, "reason": guard.category})
            yield _sse("done", {
                "blocked": True,
                "conversation_id": req.conversation_id or "",
            })
            return

        # El pipeline es sincrono (Ollama + tools + SQLite) y usa el ContextVar
        # del api_client, que es por-hilo. Lo corremos COMPLETO en un unico hilo
        # del executor (asi el ContextVar vive durante todo el pipeline) y
        # empujamos los eventos a una cola que este generador async consume.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        sentinel = object()

        def worker() -> None:
            ctx_token = api_client.use_request_client(client)
            try:
                for ev in _run_chat_pipeline(req, user_id):
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


def _run_chat_pipeline(req: ChatRequest, user_id: int):
    """Generador SINCRONO del turno de chat. Yields dicts {type, ...}.

    Reproduce el flujo de /chat pero emitiendo eventos de fase entre cada paso
    (para los indicadores de carga del frontend) y los tokens del LLM uno a uno.
    Registra la observabilidad al final. Debe correr dentro de un contexto con
    el cliente HTTP del usuario activado (ver chat_stream.worker)."""
    t_start = time.perf_counter()
    trace: list[dict] = []

    # --- A. Resolucion del conversation_id ----------------------------------
    conv_id = req.conversation_id
    if req.new_session or not conv_id or not sessions.conversation_exists(conv_id, user_id):
        conv_id = sessions.create_conversation(user_id=user_id)

    # --- Fase: Buscando informacion (RAG) -----------------------------------
    yield {"type": "phase", "phase": "searching", "detail": "Buscando información relevante…"}
    user_msg, chunks, _lat = build_user_message(req.message, _retriever(), top_k=TOP_K)
    context = sessions.build_context(conv_id)
    messages = context + [{"role": "user", "content": _date_prefixed(user_msg)}]

    # --- Fase 1: ciclo de tools (pensando / ejecutando accion) --------------
    max_iters = 4
    exhausted = True
    for _ in range(max_iters):
        yield {"type": "phase", "phase": "thinking", "detail": "Procesando tu solicitud…"}
        resp = _ollama().chat(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            stream=False,
            options={"temperature": 0},
        )
        msg = resp.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            exhausted = False
            break

        messages.append({
            "role": "assistant",
            "content": msg.get("content", "") or "",
            "tool_calls": tool_calls,
        })
        for call in tool_calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            yield {
                "type": "phase",
                "phase": "executing",
                "tool": name,
                "detail": _humanize_tool(name),
            }
            result = execute_tool(name, args)
            trace.append({
                "name": name,
                "parameters": args if isinstance(args, dict) else {"_raw": args},
                "status": _tool_status(result),
            })
            messages.append({"role": "tool", "name": name, "content": result})
    if exhausted:
        messages.append({
            "role": "tool",
            "name": "system",
            "content": json.dumps(
                {"error": "Se alcanzo el limite de llamadas a herramientas. "
                          "Resume al usuario lo obtenido y pidele instrucciones."},
                ensure_ascii=False,
            ),
        })

    # --- Fase 2: respuesta final en STREAMING -------------------------------
    yield {"type": "phase", "phase": "generating", "detail": "Generando respuesta…"}
    ttft_ms: float | None = None
    pieces: list[str] = []
    eval_count = eval_duration = None
    for part in _ollama().chat(model=LLM_MODEL, messages=messages, stream=True):
        piece = (part.get("message", {}) or {}).get("content", "") or ""
        if piece:
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - t_start) * 1000
            pieces.append(piece)
            yield {"type": "token", "text": piece}
        if part.get("done"):
            eval_count = part.get("eval_count")
            eval_duration = part.get("eval_duration")
    reply = "".join(pieces).strip()
    tps = _tokens_per_second(
        eval_count, eval_duration,
        fallback_tokens=len(pieces),
        fallback_seconds=(time.perf_counter() - t_start),
    )

    # --- B. Persistencia + D. compactacion ----------------------------------
    sessions.append_turn(conv_id, req.message, reply)
    comp = sessions.compact(conv_id, client=_ollama(), model=LLM_MODEL)

    total_latency = (time.perf_counter() - t_start) * 1000
    sources = sorted({c.metadata.get("source", "?") for c in chunks})
    turns = len(sessions.get_all_messages(conv_id)) // 2

    observability.log_interaction(
        session_id=conv_id,
        user_prompt=req.message,
        system_response=reply,
        ttft_ms=ttft_ms,
        total_latency_ms=total_latency,
        tokens_per_second=tps,
        was_blocked=False,
        tools_executed=trace,
    )

    yield {
        "type": "done",
        "conversation_id": conv_id,
        "user_id": user_id,
        "sources": sources,
        "turns": turns,
        "ttft_ms": round(ttft_ms, 2) if ttft_ms else None,
        "total_latency_ms": round(total_latency, 2),
        "tokens_per_second": round(tps, 2) if tps else None,
        "compacted": comp["compacted"],
        "blocked": False,
    }


# ---------------------------------------------------------------------------
# Helpers de generacion / metricas
# ---------------------------------------------------------------------------
def _run_tool_cycle_traced(messages: list[dict], trace: list[dict]) -> list[dict]:
    """Ciclo de tools no-streaming con traza (para /chat). Equivalente al de
    chat.py pero alimentando `trace` para la observabilidad."""
    from chat import _run_tool_cycle  # import local: evita ciclos en import-time
    return _run_tool_cycle(_ollama(), messages, verbose=False, trace=trace)


def _generate_final(messages: list[dict]) -> tuple[str, int | None, int | None]:
    """Respuesta conversacional final sin streaming. Devuelve (texto,
    eval_count, eval_duration_ns) para calcular tokens/segundo."""
    resp = _ollama().chat(model=LLM_MODEL, messages=messages, stream=False)
    text = (resp.get("message", {}) or {}).get("content", "").strip()
    return text, resp.get("eval_count"), resp.get("eval_duration")


def _tokens_per_second(
    eval_count: int | None,
    eval_duration_ns: int | None,
    fallback_tokens: int | None = None,
    fallback_seconds: float | None = None,
) -> float | None:
    """tokens/segundo = tokens generados / tiempo de generacion activa.

    Usa las stats nativas de Ollama (eval_count / eval_duration en nanosegundos),
    que miden SOLO el tiempo de generacion. Si no estan, cae a una estimacion
    por wall-clock (menos precisa, pero nunca deja el campo vacio)."""
    if eval_count and eval_duration_ns:
        return eval_count / (eval_duration_ns / 1e9)
    if fallback_tokens and fallback_seconds and fallback_seconds > 0:
        return fallback_tokens / fallback_seconds
    return None


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
    """Transcribe un audio (multipart 'audio') a texto con Whisper local.

    Pensado para la entrada de voz del frontend: el cliente graba del microfono
    y envia el blob; el backend devuelve el texto que luego se manda a /chat o
    /chat/stream. Corre en CPU (int8) para no competir con la VRAM de Llama."""
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

    # La transcripcion es CPU-bound y bloqueante: fuera del event loop.
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
