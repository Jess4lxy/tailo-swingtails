"""Agente especialista transaccional (entregable semana 07 - Fase A).

Domina las operaciones sobre la cuenta y la agenda del usuario mediante Function
Calling. Tiene acceso EXCLUSIVO a las herramientas (no RAG) y su system prompt
es reducido (agents/prompts.TRANSACTIONAL_SYSTEM).

Combina DOS conjuntos de tools:
  - remotas (tools.py): API real de SwingTails -> demo de function calling real.
  - locales (stress_tools.py): BD de estres SQLite a escala (>=50k citas
    indexadas) -> lo que la bateria evaluadora ejercita para medir latencia.

`run()` es un generador: emite fases + eventos de tool + tokens y DEVUELVE un
dict con la respuesta final y la traza de herramientas (para la observabilidad y
la metrica de Precision de Parametros del evaluador).
"""
from __future__ import annotations

import json
import time

import ollama

from config import LLM_MODEL
from agents.prompts import TRANSACTIONAL_SYSTEM
from chat import _tool_status
from tools import TOOL_REGISTRY, TOOL_SCHEMAS, execute_tool
from stress_tools import LOCAL_TOOL_REGISTRY, LOCAL_TOOL_SCHEMAS

# Registro y schemas COMBINADOS (remoto + local). El agente transaccional ve
# ambos; el ruteador ya garantizo que este es un turno de operaciones.
_REGISTRY = {**TOOL_REGISTRY, **LOCAL_TOOL_REGISTRY}
_SCHEMAS = TOOL_SCHEMAS + LOCAL_TOOL_SCHEMAS


# Etiquetas legibles para los indicadores de carga del frontend.
_TOOL_LABELS: dict[str, str] = {
    "list_my_pets": "Consultando tus mascotas…",
    "get_pet": "Buscando la ficha de tu mascota…",
    "register_pet": "Registrando tu mascota…",
    "update_pet": "Actualizando los datos de tu mascota…",
    "delete_pet": "Eliminando la mascota…",
    "list_clinics": "Buscando clínicas veterinarias…",
    "find_nearest_clinics": "Buscando veterinarias cerca de ti…",
    "list_appointments": "Consultando tus citas…",
    "book_appointment": "Agendando tu cita…",
    "reschedule_appointment": "Reagendando tu cita…",
    "cancel_appointment": "Cancelando la cita…",
    "list_products": "Buscando en el catálogo…",
    "get_product": "Consultando el producto…",
    "list_clinic_reviews": "Leyendo reseñas de la clínica…",
    "get_clinic_rating": "Calculando la calificación de la clínica…",
    "review_clinic": "Publicando tu reseña…",
    "consultar_citas": "Buscando en tu agenda…",
    "contar_citas": "Contando tus citas…",
    "agendar_cita_local": "Agendando tu cita…",
    "actualizar_estado_cita": "Actualizando el estado de la cita…",
}


def _humanize(name: str) -> str:
    return _TOOL_LABELS.get(name, f"Ejecutando {name}…")


class TransactionalAgent:
    """Especialista de operaciones. Ciclo de tools + respuesta final en streaming."""

    def __init__(self, client: ollama.Client, model: str = LLM_MODEL, max_iters: int = 4) -> None:
        self._client = client
        self.model = model
        self.max_iters = max_iters

    def run(self, context: list[dict], user_message: str, date_prefix: str = ""):
        """Ejecuta el turno transaccional. Yields eventos; devuelve dict final."""
        t_start = time.perf_counter()
        trace: list[dict] = []

        messages = (
            [{"role": "system", "content": TRANSACTIONAL_SYSTEM}]
            + list(context)
            + [{"role": "user", "content": date_prefix + user_message}]
        )

        # --- Fase 1: ciclo de herramientas -----------------------------------
        exhausted = True
        for _ in range(self.max_iters):
            yield {"type": "phase", "phase": "thinking", "detail": "Procesando tu solicitud…"}
            resp = self._client.chat(
                model=self.model,
                messages=messages,
                tools=_SCHEMAS,
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
                yield {"type": "phase", "phase": "executing", "tool": name,
                       "detail": _humanize(name)}
                result = execute_tool(name, args, registry=_REGISTRY)
                status = _tool_status(result)
                trace.append({
                    "name": name,
                    "parameters": args if isinstance(args, dict) else {"_raw": args},
                    "status": status,
                })
                yield {"type": "tool", "name": name, "status": status}
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

        # --- Fase 2: respuesta conversacional final en streaming -------------
        yield {"type": "phase", "phase": "generating", "detail": "Generando respuesta…"}
        ttft_ms: float | None = None
        pieces: list[str] = []
        eval_count = eval_duration = None
        for part in self._client.chat(model=self.model, messages=messages, stream=True):
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
        return {
            "reply": reply,
            "route": "transactional",
            "context": [json.dumps(t, ensure_ascii=False) for t in trace],
            "sources": [],
            "tools_executed": trace,
            "ttft_ms": ttft_ms,
            "eval_count": eval_count,
            "eval_duration": eval_duration,
        }
