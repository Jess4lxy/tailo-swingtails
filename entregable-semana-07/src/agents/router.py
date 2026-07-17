"""Agente Ruteador / Orquestador de tareas (entregable semana 07 - Fase A).

Recibe el mensaje del usuario (y un resumen del historial reciente) y decide a
que subagente especialista delegar: "rag", "transactional" o "smalltalk".

Estrategia HIBRIDA (rapida y robusta):
  1. Atajos DETERMINISTICOS (sin LLM) para los casos obvios y frecuentes:
     saludos / capacidades -> smalltalk. Esto evita gastar una inferencia de
     clasificacion en cada "hola" y da latencia ~0.
  2. Clasificacion con el LLM (ROUTER_MODEL, temperature 0, salida JSON) para
     el resto. Un prompt minusculo: solo emite una etiqueta.
  3. Fallback por palabras clave si el JSON del modelo no se puede parsear, para
     no quedarnos sin ruta nunca.

El costo del ruteo es una unica llamada corta (num_predict bajo); a cambio, cada
especialista corre con un prompt reducido y (en el caso RAG) sin los 15 schemas
de tools, lo que acelera y precisa su respuesta.
"""
from __future__ import annotations

import json
import re
import unicodedata

import ollama

from config import OLLAMA_HOST, ROUTER_MODEL
from agents.prompts import ROUTER_SYSTEM

VALID_ROUTES = {"rag", "transactional", "smalltalk"}


# ---------------------------------------------------------------------------
# Normalizacion + atajos deterministicos (reutiliza la heuristica de la sem. 05)
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


_CAPABILITY_RX = re.compile(
    r"en que (me |te )?(puedes |podrias |podras )?ayud"
    r"|con que (me |te )?(puedes |podrias )?ayud"
    r"|de que (me )?(puedes )?ayud"
    r"|que (puedes|sabes|podrias) hacer"
    r"|para que (sirves|eres)"
    r"|quien eres|que eres\b|como te llamas|como funcionas"
    r"|que es swingtails|que es tailo|que haces"
)
_GREETING_RX = re.compile(
    r"^(hola|hey|holi|buenas|buenos dias|buenas tardes|buenas noches"
    r"|que tal|que onda|saludos|hello|hi|adios|hasta luego)\b"
)
_THANKS_RX = re.compile(r"^(gracias|muchas gracias|ok gracias|vale gracias|perfecto gracias)\b")

# Palabras clave para el fallback (si el LLM no devuelve JSON valido).
_TRANSACTIONAL_KW = re.compile(
    r"\b(mis|mi)\b.{0,20}\b(mascota|mascotas|perr|gat|cita|citas|agenda|expediente)"
    r"|\b(agenda|agendar|agendame|reagenda|cancela|cancelar|registra|registrar|"
    r"actualiza|actualizar|elimina|eliminar|confirma|resen|reseña|resena)\b"
    r"|\bcuantas?\s+citas\b|\bfolio\b|\bmis citas\b|\bmis mascotas\b"
)
_RAG_KW = re.compile(
    r"\b(como|que|cual|cuando|cuanto|por que|porque|recomienda|consejo|cuidad|"
    r"aliment|vacuna|sintoma|salud|raza|comportamiento|higiene|politica|puedo darle|"
    r"le doy)\b"
)


def _shortcut(norm: str) -> str | None:
    """Atajos sin LLM. Devuelve la ruta o None si hay que preguntarle al LLM."""
    if _CAPABILITY_RX.search(norm):
        return "smalltalk"
    if len(norm.split()) <= 6 and (_GREETING_RX.search(norm) or _THANKS_RX.search(norm)):
        return "smalltalk"
    return None


def _keyword_fallback(norm: str) -> str:
    """Clasificacion por palabras clave (ultima red de seguridad)."""
    if _TRANSACTIONAL_KW.search(norm):
        return "transactional"
    if _RAG_KW.search(norm):
        return "rag"
    return "rag"  # por defecto, tratar como pregunta informativa (mas seguro)


class RouterAgent:
    """Clasificador de intencion. Devuelve dict {route, reason, method}."""

    def __init__(self, client: ollama.Client | None = None, model: str = ROUTER_MODEL) -> None:
        self._client = client or ollama.Client(host=OLLAMA_HOST)
        self.model = model

    def route(self, message: str, history_hint: str = "") -> dict:
        norm = _norm(message)

        # 1) Atajos deterministicos.
        shortcut = _shortcut(norm)
        if shortcut:
            return {"route": shortcut, "reason": "atajo deterministico", "method": "shortcut"}

        # 2) Clasificacion con el LLM (salida JSON).
        user_block = message
        if history_hint:
            user_block = f"[Contexto reciente]\n{history_hint}\n\n[Mensaje a clasificar]\n{message}"
        try:
            resp = self._client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM},
                    {"role": "user", "content": user_block},
                ],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 80},
            )
            raw = (resp.get("message", {}) or {}).get("content", "") or ""
            data = json.loads(raw)
            route = str(data.get("route", "")).strip().lower()
            if route in VALID_ROUTES:
                return {
                    "route": route,
                    "reason": str(data.get("reason", ""))[:200],
                    "method": "llm",
                }
        except (json.JSONDecodeError, KeyError, TypeError, ollama.ResponseError):
            pass
        except Exception:  # noqa: BLE001 - cualquier fallo del LLM cae al fallback
            pass

        # 3) Fallback por palabras clave.
        return {"route": _keyword_fallback(norm), "reason": "fallback por palabras clave", "method": "keyword"}
