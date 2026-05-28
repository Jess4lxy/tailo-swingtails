"""Servicio HTTP de Tailo Agent (RAG + Function Calling) - modo multiusuario.

La app de SwingTails ya autentica al usuario (su pantalla de login). Cuando
quiere conversar con Tailo, reenvia el JWT del usuario en la cabecera
`Authorization: Bearer <jwt>` de cada peticion. Tailo NO pide credenciales:
toma la identidad de quien conversa desde ese token (y de ahi su user_id, ya
embebido en el JWT) para todas las operaciones de escritura.

Aislamiento: cada peticion fija su propio cliente HTTP en un ContextVar
(ver api_client.use_request_client), asi dos usuarios concurrentes nunca se
pisan el token.

Arranque:
    uvicorn server:app --reload --port 8000      # desde la carpeta src/
    # o:  python src/server.py

Endpoints:
    GET  /health
    POST /chat   body: {"message": "...", "history": [{"role","content"}, ...]}
                 header: Authorization: Bearer <jwt-del-usuario>
"""
from __future__ import annotations

import ollama
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import api_client
from chat import _run_tool_cycle, build_user_message, strip_info_block
from config import LLM_MODEL, OLLAMA_HOST, TOP_K
from retrieve import Retriever

app = FastAPI(title="Tailo Agent", version="0.3")

# Recursos compartidos entre peticiones (solo lectura): se crean una vez.
_RETRIEVER: Retriever | None = None
_OLLAMA: ollama.Client | None = None


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
    history: list[dict] = Field(
        default_factory=list,
        description="Turnos previos [{role,content}]. La app mantiene la memoria.",
    )


class ChatResponse(BaseModel):
    reply: str
    user_id: int | None
    sources: list[str]


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <jwt>")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vacio")
    return token


def _sanitize_history(history: list[dict]) -> list[dict]:
    """Limpia el historial recibido: quita bloques RAG viejos y mensajes de
    tool/asistente-con-tool_calls (igual que el REPL)."""
    clean: list[dict] = []
    for m in history:
        role = m.get("role")
        if role == "tool":
            continue
        if role == "assistant" and m.get("tool_calls"):
            continue
        content = m.get("content", "") or ""
        if role == "user":
            content = strip_info_block(content)
        if role in {"user", "assistant"}:
            clean.append({"role": role, "content": content})
    return clean[-12:]  # cap defensivo (6 turnos)


def _generate_final(messages: list[dict]) -> str:
    """Respuesta conversacional final (sin tools, sin streaming)."""
    resp = _ollama().chat(model=LLM_MODEL, messages=messages, stream=False)
    return (resp.get("message", {}) or {}).get("content", "").strip()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": LLM_MODEL}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    token = _bearer_token(authorization)

    # Cliente ligado al usuario de ESTA peticion (aislado por ContextVar).
    client = api_client.client_from_token(token)
    if client.current_user_id is None:
        raise HTTPException(status_code=401, detail="Token invalido o sin id de usuario")

    ctx_token = api_client.use_request_client(client)
    try:
        user_msg, chunks, _lat = build_user_message(req.message, _retriever(), top_k=TOP_K)
        messages = _sanitize_history(req.history) + [{"role": "user", "content": user_msg}]

        # Fase 1: ciclo de tools (verbose=False; no hay consola del lado servidor).
        messages = _run_tool_cycle(_ollama(), messages, verbose=False)
        # Fase 2: respuesta final.
        reply = _generate_final(messages)

        sources = sorted({c.metadata.get("source", "?") for c in chunks})
        return ChatResponse(reply=reply, user_id=client.current_user_id, sources=sources)
    finally:
        api_client.reset_request_client(ctx_token)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
