"""Base de datos de Observabilidad de LLM (entregable semana 05 - Fase B, punto 2).

Persiste una fila por interaccion del agente para auditoria de rendimiento en
hardware local. La tabla cumple el esquema MINIMO exigido por la rubrica:

    id                 INTEGER PK autoincremental
    session_id         TEXT     id de la conversacion (conversation_id)
    timestamp          TEXT     fecha/hora exacta de la solicitud (UTC ISO-8601)
    user_prompt        TEXT     texto ingresado por el usuario
    system_response    TEXT     texto generado por el agente
    ttft_ms            REAL     ms hasta el primer token emitido
    total_latency_ms   REAL     ms del ciclo completo pregunta->respuesta
    tokens_per_second  REAL     tokens generados / tiempo de generacion activa
    was_blocked        INTEGER  1 si el guardrail bloqueo la entrada, 0 si no
    tools_executed     TEXT     JSON: [{name, parameters, status}], SUCCESS|ERROR

A diferencia de la memoria conversacional (sessions.py), esta bitacora es
APPEND-ONLY: no se actualiza ni se borra por usuario. Es la fuente de verdad
para medir TTFT, throughput y trazabilidad de herramientas durante las pruebas.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from config import OBSERVABILITY_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observability_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT,
    timestamp         TEXT    NOT NULL,
    user_prompt       TEXT    NOT NULL,
    system_response   TEXT    NOT NULL DEFAULT '',
    ttft_ms           REAL,
    total_latency_ms  REAL,
    tokens_per_second REAL,
    was_blocked       INTEGER NOT NULL DEFAULT 0,
    tools_executed    TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_obs_session   ON observability_logs(session_id, id);
CREATE INDEX IF NOT EXISTS idx_obs_timestamp ON observability_logs(timestamp);
"""


@dataclass
class ToolTrace:
    """Una invocacion de herramienta para el campo tools_executed."""

    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "SUCCESS"  # 'SUCCESS' | 'ERROR'

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "parameters": self.parameters, "status": self.status}


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    OBSERVABILITY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OBSERVABILITY_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Crea la tabla si no existe (idempotente)."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _now_iso() -> str:
    """Timestamp UTC en ISO-8601 (con sufijo Z), granularidad de segundos."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def log_interaction(
    *,
    session_id: Optional[str],
    user_prompt: str,
    system_response: str = "",
    ttft_ms: Optional[float] = None,
    total_latency_ms: Optional[float] = None,
    tokens_per_second: Optional[float] = None,
    was_blocked: bool = False,
    tools_executed: Optional[list] = None,
    timestamp: Optional[str] = None,
) -> int:
    """Inserta una fila de auditoria y devuelve su id.

    `tools_executed` acepta una lista de ToolTrace o de dicts ya formados; se
    serializa a JSON. Nunca levanta hacia el caller: si el log falla, devuelve
    -1 (la observabilidad jamas debe tumbar la respuesta al usuario).
    """
    traces = tools_executed or []
    normalized = [t.to_dict() if isinstance(t, ToolTrace) else t for t in traces]
    payload = json.dumps(normalized, ensure_ascii=False)

    def _round(v: Optional[float]) -> Optional[float]:
        return round(v, 2) if isinstance(v, (int, float)) else None

    row = (
        session_id,
        timestamp or _now_iso(),
        user_prompt,
        system_response,
        _round(ttft_ms),
        _round(total_latency_ms),
        _round(tokens_per_second),
        1 if was_blocked else 0,
        payload,
    )
    insert = """
        INSERT INTO observability_logs (
            session_id, timestamp, user_prompt, system_response,
            ttft_ms, total_latency_ms, tokens_per_second,
            was_blocked, tools_executed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    try:
        with _connect() as conn:
            try:
                cur = conn.execute(insert, row)
            except sqlite3.OperationalError:
                # Tabla aun no creada (p.ej. no corrio el startup): auto-reparar
                # y reintentar una vez, asi la bitacora nunca pierde un registro.
                conn.executescript(_SCHEMA)
                cur = conn.execute(insert, row)
            return int(cur.lastrowid)
    except sqlite3.Error:
        return -1


def recent_logs(limit: int = 20, session_id: Optional[str] = None) -> list[dict]:
    """Devuelve las ultimas interacciones (para el inspector / demo)."""
    with _connect() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM observability_logs WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM observability_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    """Agregados rapidos para la bitacora del informe (promedios, % bloqueos)."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                   AS total,
                SUM(was_blocked)                           AS blocked,
                AVG(ttft_ms)                               AS avg_ttft_ms,
                AVG(total_latency_ms)                      AS avg_latency_ms,
                AVG(tokens_per_second)                     AS avg_tps
            FROM observability_logs
            """
        ).fetchone()
    return dict(row) if row else {}
