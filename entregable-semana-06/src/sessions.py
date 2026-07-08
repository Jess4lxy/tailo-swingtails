"""Persistencia de memoria conversacional para Tailo (entregable semana 04).

Resuelve los puntos A-D de la rubrica:

  A. Identificacion de sesion (conversation_id): cada conversacion es una fila
     en `conversations` con un UUID. El cliente lo envia en cada peticion; si no
     trae ninguno (o pide una nueva) el backend genera uno y lo retorna.

  B. Almacenamiento del historial (Chat History Store): SQLite en disco
     -> persistencia NO volatil. Sobrevive reinicios del servidor (a diferencia
     de un dict en RAM, que se pierde si el proceso se cae). Tabla `messages`
     con la secuencia ordenada de turnos.

  C. Logica de prompting: `build_context()` reconstruye el buffer ordenado
     [resumen? + turnos recientes] que se le antepone al mensaje nuevo.

  D. Ventana de contexto: `compact()` aplica ventana deslizante + resumen
     (summarization). Cuando el historial activo supera el presupuesto de
     tokens, los turnos mas antiguos se condensan en un resumen acumulado y se
     marcan como `summarized=1` (siguen en la BD como bitacora auditable, pero
     ya no se envian crudos al modelo). Asi el buffer nunca desborda `num_ctx`.

Resiliencia de memoria (punto E): este store SOLO persiste mensajes
`user`/`assistant`. Los resultados de tools (role="tool") y los mensajes
assistant que solo llevan `tool_calls` NUNCA se escriben aqui. Asi un error de
herramienta no se "pega" como verdad en la memoria de largo plazo ni reaparece
en turnos futuros (evita el state poisoning). El manejo del ciclo de tools vive
en chat.py; este modulo es deliberadamente agnostico a tools.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

from config import (
    COMPACT_TARGET_TOKENS,
    COMPACT_THRESHOLD_TOKENS,
    HISTORY_TOKEN_BUDGET,
    KEEP_RECENT_MESSAGES,
    SESSIONS_DB,
)

# Roles que SI forman parte de la memoria persistente de largo plazo.
PERSISTED_ROLES = {"user", "assistant"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,          -- UUID (conversation_id)
    user_id     INTEGER,                   -- dueño (del JWT); NULL en modo CLI sin login
    title       TEXT    NOT NULL DEFAULT 'Nueva conversación',
    summary     TEXT    NOT NULL DEFAULT '',  -- resumen acumulado de turnos viejos
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL,       -- 'user' | 'assistant'
    content         TEXT    NOT NULL,
    summarized      INTEGER NOT NULL DEFAULT 0,  -- 1 = ya plegado en summary
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id, updated_at DESC);
"""


# ---------------------------------------------------------------------------
# Estimacion de tokens
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Aproxima los tokens de un texto para Llama 3.1.

    Heuristica ~4 caracteres por token (validada para texto mixto es/en con el
    tokenizer BPE de Llama 3). Es una cota conservadora suficiente para decidir
    recortes; no necesitamos el conteo exacto para no desbordar `num_ctx`.
    """
    return (len(text) + 3) // 4


def _messages_tokens(messages: list[dict]) -> int:
    """Tokens del buffer + overhead por mensaje (marcadores de rol ~4 tokens)."""
    return sum(estimate_tokens(m.get("content", "") or "") + 4 for m in messages)


# ---------------------------------------------------------------------------
# Conexion
# ---------------------------------------------------------------------------
@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SESSIONS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Crea las tablas si no existen (idempotente)."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# CRUD de conversaciones
# ---------------------------------------------------------------------------
def create_conversation(user_id: Optional[int] = None, title: Optional[str] = None) -> str:
    """Crea una conversacion nueva y devuelve su conversation_id (UUID)."""
    conv_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
            (conv_id, user_id, title or "Nueva conversación"),
        )
    return conv_id


def conversation_exists(conv_id: str, user_id: Optional[int] = None) -> bool:
    """True si la conversacion existe (y pertenece a user_id, si se da)."""
    with _connect() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            ).fetchone()
    return row is not None


def get_conversation(conv_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
    return dict(row) if row else None


def list_conversations(user_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    """Lista conversaciones (de un usuario, si se da) mas recientes primero,
    con el conteo de mensajes para mostrarlas en un selector de sesiones."""
    with _connect() as conn:
        sql = (
            "SELECT c.*, "
            "  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n_messages "
            "FROM conversations c "
        )
        params: tuple = ()
        if user_id is not None:
            sql += "WHERE c.user_id = ? "
            params = (user_id,)
        sql += "ORDER BY c.updated_at DESC LIMIT ?"
        rows = conn.execute(sql, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def rename_conversation(conv_id: str, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title.strip()[:120] or "Nueva conversación", conv_id),
        )


def delete_conversation(conv_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))


# ---------------------------------------------------------------------------
# Mensajes
# ---------------------------------------------------------------------------
def append_message(conv_id: str, role: str, content: str) -> None:
    """Agrega un mensaje a la conversacion. Solo persiste user/assistant:
    rechaza tool/tool_calls para no envenenar la memoria de largo plazo."""
    if role not in PERSISTED_ROLES:
        raise ValueError(f"Rol no persistible en memoria de sesion: {role!r}")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conv_id, role, content),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
            (conv_id,),
        )


def append_turn(conv_id: str, user_content: str, assistant_content: str) -> None:
    """Atajo: persiste el par user+assistant de un turno completo."""
    append_message(conv_id, "user", user_content)
    append_message(conv_id, "assistant", assistant_content)


def get_active_messages(conv_id: str) -> list[dict]:
    """Mensajes NO resumidos (los que se envian crudos al modelo), en orden."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? AND summarized = 0 ORDER BY id",
            (conv_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_all_messages(conv_id: str) -> list[dict]:
    """Historial COMPLETO (incluye resumidos): bitacora auditable e integra."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, summarized, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_summary(conv_id: str) -> str:
    conv = get_conversation(conv_id)
    return conv["summary"] if conv else ""


# ---------------------------------------------------------------------------
# D. Gestion de la ventana de contexto: ventana deslizante + resumen
# ---------------------------------------------------------------------------
Summarizer = "Callable[[list[dict], str], str]"


def _default_summarizer(client, model: str):
    """Devuelve un summarizer que usa el propio LLM local para condensar.

    Se inyecta perezosamente para que sessions.py no dependa de ollama ni de
    config.LLM_MODEL en import-time (y para poder testear con un stub).
    """
    def summarize(old_msgs: list[dict], previous_summary: str) -> str:
        transcript = "\n".join(
            f"{m['role']}: {m['content']}" for m in old_msgs
        )
        prompt = (
            "Eres un compresor de memoria conversacional. Resume de forma "
            "concisa (en español, máximo ~120 palabras) los datos que hay que "
            "RECORDAR de este fragmento de conversación entre un usuario y el "
            "asistente Tailo: nombres de mascotas, citas, preferencias, "
            "decisiones y hechos. No inventes. Integra el resumen previo.\n\n"
            f"[Resumen previo]\n{previous_summary or '(vacío)'}\n\n"
            f"[Fragmento a integrar]\n{transcript}\n\n"
            "[Resumen actualizado]"
        )
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            options={"temperature": 0, "num_predict": 256},
        )
        return (resp.get("message", {}) or {}).get("content", "").strip()

    return summarize


def compact(
    conv_id: str,
    client=None,
    model: Optional[str] = None,
    summarizer=None,
) -> dict:
    """Aplica la estrategia de ventana de contexto si hace falta.

    Si los tokens del historial activo superan COMPACT_THRESHOLD_TOKENS, pliega
    los mensajes mas antiguos (preservando siempre los ultimos
    KEEP_RECENT_MESSAGES) en el resumen acumulado, hasta bajar de
    COMPACT_TARGET_TOKENS. Los mensajes plegados se marcan summarized=1 (no se
    borran: la BD sigue siendo bitacora integra).

    `client`+`model` se usan para el summarizer por defecto (LLM local). Si se
    pasa `summarizer` explicito, se ignoran (util para pruebas).

    Devuelve un dict con metricas de la operacion (para logs/demo).
    """
    active = get_active_messages(conv_id)
    tokens_before = _messages_tokens(active)
    info = {
        "compacted": False,
        "tokens_before": tokens_before,
        "tokens_after": tokens_before,
        "summarized_messages": 0,
    }
    if tokens_before <= COMPACT_THRESHOLD_TOKENS:
        return info  # dentro de presupuesto: nada que hacer.

    if summarizer is None:
        if client is None or model is None:
            # Sin forma de resumir: hacemos ventana deslizante "dura" (recorte)
            # como degradacion segura. Marcamos viejos como summarized sin texto.
            summarizer = lambda old, prev: prev  # noqa: E731
        else:
            summarizer = _default_summarizer(client, model)

    # Cuantos mensajes hay que plegar para bajar al objetivo, dejando intactos
    # los KEEP_RECENT_MESSAGES finales.
    to_fold: list[dict] = []
    remaining = list(active)
    while (
        _messages_tokens(remaining) > COMPACT_TARGET_TOKENS
        and len(remaining) > KEEP_RECENT_MESSAGES
    ):
        to_fold.append(remaining.pop(0))

    if not to_fold:
        return info

    previous_summary = get_summary(conv_id)
    new_summary = summarizer(to_fold, previous_summary)

    # Persistimos: actualizamos resumen y marcamos los plegados (los N mas
    # antiguos no resumidos) como summarized=1.
    with _connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND summarized = 0 "
                "ORDER BY id LIMIT ?",
                (conv_id, len(to_fold)),
            ).fetchall()
        ]
        if ids:
            conn.executemany(
                "UPDATE messages SET summarized = 1 WHERE id = ?",
                [(i,) for i in ids],
            )
        conn.execute(
            "UPDATE conversations SET summary = ?, updated_at = datetime('now') WHERE id = ?",
            (new_summary, conv_id),
        )

    after = _messages_tokens(get_active_messages(conv_id))
    info.update(
        compacted=True,
        tokens_after=after,
        summarized_messages=len(to_fold),
    )
    return info


def build_context(conv_id: str) -> list[dict]:
    """Reconstruye el buffer de contexto a anteponer al mensaje nuevo (punto C).

    [resumen acumulado como system] + [mensajes activos user/assistant].
    Asume que compact() ya dejo el historial dentro de presupuesto; aun asi
    aplica un recorte defensivo final por tokens (ventana deslizante) para
    garantizar de forma DETERMINISTA que no se desborda HISTORY_TOKEN_BUDGET.
    """
    context: list[dict] = []
    summary = get_summary(conv_id)
    if summary:
        context.append({
            "role": "system",
            "content": (
                "[Resumen de la parte anterior de esta conversación, para que "
                f"mantengas el contexto]\n{summary}"
            ),
        })

    active = get_active_messages(conv_id)

    # Recorte defensivo: si aun excede el presupuesto, descarta los mas viejos.
    budget = HISTORY_TOKEN_BUDGET - _messages_tokens(context)
    while active and _messages_tokens(active) > budget:
        active.pop(0)

    context.extend(active)
    return context
