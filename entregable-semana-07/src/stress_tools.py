"""Tools locales (Function Calling) sobre la BD de estres (semana 07 - Fase A).

Estas herramientas dan al agente especialista transaccional acceso a la base de
datos relacional local sembrada a escala de produccion (>=50,000 citas, con
indices B-Tree). Complementan a las tools remotas de la API de SwingTails
(tools.py): las remotas son para el demo de Function Calling real; estas
locales son las que la bateria evaluadora ejercita a escala para medir latencia
e indexacion.

Convencion identica a tools.py:
  - codigo Python normal con type hints + docstring (Ollama arma el schema);
  - el user_id NUNCA lo provee el modelo: se toma de la sesion autenticada;
  - devuelven siempre algo serializable a JSON; los errores como {"error": ...}.
"""
from __future__ import annotations

from typing import Any, Callable

import stress_db
from api_client import get_client

# Fecha de una cita: la funcion toma el user de la sesion, como en tools.py.
_NO_SESSION_ERROR = {
    "error": "No hay una sesion iniciada. El usuario debe autenticarse antes "
    "de consultar o agendar citas."
}


def _current_user_id() -> int | None:
    return get_client().current_user_id


def _normalize_status(status: str | None) -> str | None:
    """Mapea lo que diga el usuario a uno de los estados validos (o None)."""
    if not status:
        return None
    s = status.strip().lower()
    for est in stress_db.ESTADOS:
        if est.lower() == s or est.lower().startswith(s):
            return est
    return None


def consultar_citas(
    fecha: str = "",
    estado: str = "",
    folio: str = "",
    limit: int = 10,
) -> dict:
    """Busca citas del usuario autenticado en la agenda local a escala.

    Consulta la base de datos de citas (indexada por fecha, estado y folio).
    Uselo cuando el usuario pida ver/buscar SUS citas por fecha ("mis citas del
    2026-08-15"), por estado ("mis citas pendientes") o por folio exacto.

    Args:
        fecha: Fecha exacta YYYY-MM-DD (opcional).
        estado: 'Pendiente', 'Confirmada', 'Completada' o 'Cancelada' (opcional).
        folio: Folio exacto de la cita, p.ej. 'SW-0001234' (opcional).
        limit: Maximo de resultados (default 10).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    rows = stress_db.search_citas(
        fecha=fecha or None,
        status=_normalize_status(estado),
        user_id=uid,
        folio=folio or None,
        limit=max(1, min(int(limit or 10), 50)),
    )
    if not rows:
        return {
            "vacio": True,
            "mensaje": "El usuario NO tiene citas que coincidan con esos filtros. "
            "Diselo asi; NUNCA inventes citas, fechas ni folios.",
        }
    return {"total": len(rows), "citas": rows}


def contar_citas(fecha: str = "", estado: str = "") -> dict:
    """Cuenta cuantas citas del usuario cumplen un filtro (consulta agregada).

    Uselo para "cuantas citas tengo", "cuantas citas pendientes tengo el
    2026-08-15", etc. Usa un COUNT(*) indexado sobre la base a escala.

    Args:
        fecha: Fecha exacta YYYY-MM-DD (opcional).
        estado: 'Pendiente', 'Confirmada', 'Completada' o 'Cancelada' (opcional).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    n = stress_db.count_citas(
        fecha=fecha or None, status=_normalize_status(estado), user_id=uid
    )
    return {"total": n, "fecha": fecha or None, "estado": _normalize_status(estado)}


def agendar_cita_local(
    pet_name: str,
    clinic_name: str,
    service_name: str,
    appointment_date: str,
    hour: str,
    specie: str = "",
    notes: str | None = None,
) -> dict:
    """Registra una nueva cita en la agenda local (INSERT transaccional).

    Uselo para agendar una cita en la base a escala. Genera el folio y toma el
    user_id de la sesion. Devuelve el folio creado.

    Args:
        pet_name: Nombre de la mascota.
        clinic_name: Nombre de la clinica.
        service_name: Nombre del servicio (p.ej. 'Consulta General').
        appointment_date: Fecha YYYY-MM-DD.
        hour: Hora HH:MM:SS.
        specie: Especie de la mascota (opcional).
        notes: Nota opcional.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    faltan = [
        etiqueta
        for valor, etiqueta in (
            (pet_name, "el nombre de la mascota"),
            (clinic_name, "la clinica"),
            (service_name, "el servicio"),
            (appointment_date, "la fecha (YYYY-MM-DD)"),
            (hour, "la hora (HH:MM:SS)"),
        )
        if not (valor or "").strip()
    ]
    if faltan:
        return {"preguntar_al_usuario": f"Para agendar necesito: {', '.join(faltan)}."}

    with stress_db.connect() as conn:
        stress_db.init_schema(conn)
        folio = stress_db.next_folio(conn)
        conn.execute(
            "INSERT INTO citas (folio, user_id, pet_name, specie, clinic_name, "
            "service_name, appointment_date, hour, status, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))",
            (
                folio, uid, pet_name, specie or "N/D", clinic_name, service_name,
                appointment_date, hour, "Pendiente", notes,
            ),
        )
    return {
        "ok": True,
        "folio": folio,
        "mensaje": f"Cita agendada con folio {folio} para {pet_name} el "
        f"{appointment_date} a las {hour} en {clinic_name}.",
    }


def actualizar_estado_cita(folio: str, nuevo_estado: str) -> dict:
    """Modifica el estado (expediente) de una cita existente por su folio.

    Uselo para confirmar, completar o cancelar una cita ("cancela la cita
    SW-0001234", "marca como confirmada mi cita ...").

    Args:
        folio: Folio de la cita, p.ej. 'SW-0001234'.
        nuevo_estado: 'Pendiente', 'Confirmada', 'Completada' o 'Cancelada'.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    estado = _normalize_status(nuevo_estado)
    if estado is None:
        return {"preguntar_al_usuario":
                f"'{nuevo_estado}' no es un estado valido. Usa uno de: "
                f"{', '.join(stress_db.ESTADOS)}."}
    # (A-02) Cancelar una cita es DESTRUCTIVO -> compuerta de confirmacion de 2
    # pasos (misma que delete_pet/cancel_appointment). Confirmar/completar no.
    if estado.lower().startswith("cancel"):
        from tools import _confirm_destructive
        gate = _confirm_destructive("cancel_cita", folio, f"CANCELAR la cita {folio}")
        if gate is not None:
            return gate
    with stress_db.connect() as conn:
        cur = conn.execute(
            "UPDATE citas SET status = ? WHERE folio = ? AND user_id = ?",
            (estado, (folio or "").strip(), uid),
        )
        cambiadas = cur.rowcount
    if not cambiadas:
        return {"error": f"No encontre una cita con folio '{folio}' a tu nombre."}
    return {"ok": True, "folio": folio, "estado": estado,
            "mensaje": f"La cita {folio} quedo como {estado}."}


# ===========================================================================
# Registro y schemas (mismo formato OpenAI/Ollama que tools.py)
# ===========================================================================
LOCAL_TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "consultar_citas": consultar_citas,
    "contar_citas": contar_citas,
    "agendar_cita_local": agendar_cita_local,
    "actualizar_estado_cita": actualizar_estado_cita,
}

_ESTADO_ENUM = list(stress_db.ESTADOS)

LOCAL_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "consultar_citas",
            "description": "Busca las citas del usuario en la agenda por fecha, estado o folio exacto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {"type": "string", "description": "Fecha exacta YYYY-MM-DD."},
                    "estado": {"type": "string", "enum": _ESTADO_ENUM},
                    "folio": {"type": "string", "description": "Folio exacto, p.ej. SW-0001234."},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "contar_citas",
            "description": "Cuenta cuantas citas del usuario cumplen un filtro (COUNT indexado).",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {"type": "string", "description": "Fecha exacta YYYY-MM-DD."},
                    "estado": {"type": "string", "enum": _ESTADO_ENUM},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agendar_cita_local",
            "description": "Agenda una nueva cita en la agenda local. Genera el folio; el user_id se infiere de la sesion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_name": {"type": "string"},
                    "clinic_name": {"type": "string"},
                    "service_name": {"type": "string"},
                    "appointment_date": {"type": "string", "description": "Fecha YYYY-MM-DD."},
                    "hour": {"type": "string", "description": "Hora HH:MM:SS."},
                    "specie": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["pet_name", "clinic_name", "service_name", "appointment_date", "hour"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "actualizar_estado_cita",
            "description": "Cambia el estado (expediente) de una cita por su folio: confirmar, completar o cancelar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folio": {"type": "string", "description": "Folio de la cita."},
                    "nuevo_estado": {"type": "string", "enum": _ESTADO_ENUM},
                },
                "required": ["folio", "nuevo_estado"],
            },
        },
    },
]
