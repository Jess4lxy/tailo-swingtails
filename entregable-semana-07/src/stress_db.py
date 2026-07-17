"""Base de datos relacional de ESTRES a escala de produccion (semana 07 - Fase A).

SQLite local (config.STRESS_DB) que el seeder puebla con >=50,000 citas
ficticias pero coherentes. Es la "base de datos de produccion" contra la que el
agente especialista transaccional ejecuta Function Calling y sobre la que corre
la bateria evaluadora, para medir el impacto real de la latencia y la
indexacion (B-Tree) a escala.

Este modulo centraliza:
  - el esquema (tabla `citas` + indices B-Tree),
  - la conexion,
  - las consultas indexadas que usan las tools locales (buscar/contar/agendar/
    actualizar estado).

seed_stress.py reutiliza `connect()` y `SCHEMA` para el sembrado masivo con
transacciones / bulk inserts.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import STRESS_DB

# Estados validos de una cita (enum de negocio).
ESTADOS = ("Pendiente", "Confirmada", "Completada", "Cancelada")

# Esquema. La tabla se crea SIN indices para acelerar el bulk insert; los
# indices B-Tree se crean DESPUES de sembrar (ver seed_stress.py y create_indexes).
SCHEMA = """
CREATE TABLE IF NOT EXISTS citas (
    id                INTEGER PRIMARY KEY,
    folio             TEXT    NOT NULL,       -- p.ej. 'SW-0000042' (identificador de negocio)
    user_id           INTEGER NOT NULL,       -- dueño de la cita
    pet_name          TEXT    NOT NULL,
    specie            TEXT    NOT NULL,
    clinic_name       TEXT    NOT NULL,
    service_name      TEXT    NOT NULL,
    appointment_date  TEXT    NOT NULL,       -- YYYY-MM-DD
    hour              TEXT    NOT NULL,        -- HH:MM:SS
    status            TEXT    NOT NULL,
    notes             TEXT,
    created_at        TEXT    NOT NULL
);
"""

# Indices B-Tree que aceleran las consultas de la bateria (folio exacto, por
# fecha, por estado, por usuario). Se crean tras el bulk insert: con la tabla
# ya poblada, construir el arbol de una vez es mas rapido que mantenerlo fila a
# fila durante millones de INSERT.
INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_citas_folio  ON citas(folio)",
    "CREATE INDEX IF NOT EXISTS idx_citas_fecha  ON citas(appointment_date)",
    "CREATE INDEX IF NOT EXISTS idx_citas_status ON citas(status)",
    "CREATE INDEX IF NOT EXISTS idx_citas_user   ON citas(user_id, appointment_date)",
)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Conexion a la BD de estres (crea el directorio data/ si falta)."""
    STRESS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STRESS_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def create_indexes(conn: sqlite3.Connection) -> None:
    for stmt in INDEXES:
        conn.execute(stmt)


def is_seeded() -> bool:
    """True si la BD de estres existe y tiene al menos una cita."""
    if not STRESS_DB.exists():
        return False
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='citas'"
            ).fetchone()
            if not row:
                return False
            n = conn.execute("SELECT COUNT(*) AS n FROM citas").fetchone()["n"]
        return n > 0
    except sqlite3.Error:
        return False


def count_citas(
    fecha: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
) -> int:
    """COUNT(*) con filtros opcionales (usa los indices B-Tree)."""
    where, params = _build_where(fecha, status, user_id, None)
    with connect() as conn:
        sql = "SELECT COUNT(*) AS n FROM citas"
        if where:
            sql += " WHERE " + where
        return conn.execute(sql, params).fetchone()["n"]


def search_citas(
    fecha: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    folio: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Busca citas por folio exacto y/o fecha/estado/usuario (indexado)."""
    where, params = _build_where(fecha, status, user_id, folio)
    with connect() as conn:
        sql = "SELECT * FROM citas"
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY appointment_date, hour LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _build_where(
    fecha: str | None, status: str | None, user_id: int | None, folio: str | None
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if folio:
        clauses.append("folio = ?")
        params.append(folio)
    if fecha:
        clauses.append("appointment_date = ?")
        params.append(fecha)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(int(user_id))
    return " AND ".join(clauses), params


def next_folio(conn: sqlite3.Connection) -> str:
    """Genera el siguiente folio 'SW-XXXXXXX' a partir del max id actual."""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM citas").fetchone()
    return f"SW-{row['m'] + 1:07d}"


def explain_plan(sql: str, params: tuple = ()) -> list[str]:
    """Devuelve el EXPLAIN QUERY PLAN de una consulta (evidencia de indices)."""
    with connect() as conn:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return [row["detail"] if "detail" in row.keys() else str(tuple(row)) for row in rows]
