"""Servicio HTTP de Tailo Agent con memoria persistente - modo multiusuario.

La app de SwingTails ya autentica al usuario (su pantalla de login). Cuando
quiere conversar con Tailo, reenvia el JWT del usuario en la cabecera
`Authorization: Bearer <jwt>` de cada peticion. Tailo NO pide credenciales:
toma la identidad de quien conversa desde ese token (y de ahi su user_id, ya
embebido en el JWT) para todas las operaciones de escritura.

Memoria de sesion (entregable semana 04):
    HTTP es sin estado. Para simular una charla continua, el backend mantiene
    el historial en SQLite (persistencia NO volatil) y lo asocia a un
    `conversation_id` (UUID). El cliente envia ese id en cada peticion:
      - si NO lo envia (o manda new_session=true) -> se crea una conversacion
        nueva, se inicializa su historial y se DEVUELVE el id;
      - si envia uno existente (y es suyo) -> se recupera el historial y se
        continua el hilo.
    Las conversaciones se aislan por user_id (del JWT): un usuario no puede leer
    ni continuar la conversacion de otro.

Aislamiento de tokens: cada peticion fija su propio cliente HTTP en un
ContextVar (ver api_client.use_request_client), asi dos usuarios concurrentes
nunca se pisan el token.

Arranque:
    uvicorn server:app --reload --port 8000      # desde la carpeta src/
    # o:  python src/server.py

Endpoints:
    GET    /health
    POST   /chat                       body: {message, conversation_id?, new_session?}
    GET    /conversations              lista las conversaciones del usuario
    GET    /conversations/{id}         devuelve el historial completo
    DELETE /conversations/{id}         borra una conversacion
"""
from __future__ import annotations

import datetime

import ollama
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import api_client
import sessions
from chat import _run_tool_cycle, build_user_message
from config import LLM_MODEL, OLLAMA_HOST, TOP_K
from retrieve import Retriever

app = FastAPI(title="Tailo Agent", version="0.4")

# Recursos compartidos entre peticiones (solo lectura): se crean una vez.
_RETRIEVER: Retriever | None = None
_OLLAMA: ollama.Client | None = None


@app.on_event("startup")
def _startup() -> None:
    sessions.init_db()  # crea las tablas si no existen (idempotente).


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


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    n_messages: int
    updated_at: str


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <jwt>")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vacio")
    return token


def _authed_user_id(authorization: str | None) -> tuple[int, object]:
    """Valida el JWT y devuelve (user_id, ctx_token del ContextVar)."""
    token = _bearer_token(authorization)
    client = api_client.client_from_token(token)
    if client.current_user_id is None:
        raise HTTPException(status_code=401, detail="Token invalido o sin id de usuario")
    ctx_token = api_client.use_request_client(client)
    return client.current_user_id, ctx_token


def _generate_final(messages: list[dict]) -> str:
    """Respuesta conversacional final (sin tools, sin streaming)."""
    resp = _ollama().chat(model=LLM_MODEL, messages=messages, stream=False)
    return (resp.get("message", {}) or {}).get("content", "").strip()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": LLM_MODEL}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    user_id, ctx_token = _authed_user_id(authorization)
    try:
        # --- A. Resolucion del conversation_id ---------------------------
        conv_id = req.conversation_id
        if req.new_session or not conv_id or not sessions.conversation_exists(conv_id, user_id):
            # Sin id, pidio uno nuevo, o el id no existe / no es suyo -> crea.
            conv_id = sessions.create_conversation(user_id=user_id)

        # --- C. Logica de prompting: historial persistido + mensaje nuevo -
        user_msg, chunks, _lat = build_user_message(req.message, _retriever(), top_k=TOP_K)
        today = datetime.date.today().isoformat()
        user_msg = f"[Fecha de hoy: {today}. Si el usuario da una fecha sin año, usa el año actual.]\n\n{user_msg}"
        context = sessions.build_context(conv_id)
        messages = context + [{"role": "user", "content": user_msg}]

        # Fase 1: ciclo de tools (los role=tool viven solo en este buffer
        # efimero; nunca se persisten -> la memoria no se envenena con errores).
        messages = _run_tool_cycle(_ollama(), messages, verbose=True)
        # Fase 2: respuesta final.
        reply = _generate_final(messages)

        # --- B. Persistencia: par user/assistant LIMPIO (sin RAG ni tools) -
        sessions.append_turn(conv_id, req.message, reply)

        # --- D. Ventana de contexto: compacta si excede el presupuesto -----
        comp = sessions.compact(conv_id, client=_ollama(), model=LLM_MODEL)

        sources = sorted({c.metadata.get("source", "?") for c in chunks})
        turns = len(sessions.get_all_messages(conv_id)) // 2
        return ChatResponse(
            reply=reply,
            conversation_id=conv_id,
            user_id=user_id,
            sources=sources,
            turns=turns,
            compacted=comp["compacted"],
        )
    finally:
        api_client.reset_request_client(ctx_token)


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
