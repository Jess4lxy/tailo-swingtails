"""Capa de seguridad (Guardrails) contra inyeccion de prompts.

Entregable semana 05 - Fase B, punto 1.

Objetivo de la rubrica: validar la entrada del usuario de forma PREVENTIVA, es
decir, ANTES de invocar al LLM. Si la entrada es un intento conocido de
inyeccion de prompts (fuga de instrucciones o jailbreak), el backend la rechaza
con un mensaje de error generico y NO gasta inferencia local (ahorra GPU/CPU).

Estrategia: reglas heuristicas (regex) sobre patrones de ataque conocidos. Es
deliberadamente un filtro deterministico y barato (no usa el LLM para detectar):
   - Fuga de instrucciones: "revela tu system prompt", "muestra tus reglas"...
   - Jailbreak / cambio de rol: "ignora las instrucciones anteriores", "actua
     como", "modo desarrollador", "DAN"...
   - Spam / patrones repetitivos atipicos: un mismo caracter o token repetido
     muchas veces (intentos de desbordar el contexto o de confundir al modelo).

Diseño defensivo: preferimos algun falso negativo (dejar pasar un ataque
sofisticado) antes que muchos falsos positivos (bloquear preguntas legitimas
sobre mascotas). Por eso los patrones apuntan a frases-ataque especificas y no a
palabras sueltas comunes. La normalizacion (minusculas + sin acentos) evita
evasiones triviales con mayusculas o tildes.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Mensaje generico estandarizado que se devuelve al cliente cuando se bloquea
# una entrada. No revela QUE regla se activo (no le damos pistas al atacante).
GENERIC_BLOCK_MESSAGE = (
    "Lo siento, no puedo procesar esa solicitud. Soy Tailo, el asistente de "
    "SwingTails: puedo ayudarte con tus mascotas, citas veterinarias y el "
    "catalogo de productos. ¿En que te ayudo con eso?"
)


@dataclass
class GuardrailResult:
    """Resultado de evaluar una entrada del usuario."""

    blocked: bool
    category: str = ""        # 'instruction_leak' | 'jailbreak' | 'spam' | ''
    pattern: str = ""         # patron que se activo (solo para la bitacora/log)
    message: str = field(default="")  # respuesta a devolver si blocked=True


# ---------------------------------------------------------------------------
# Normalizacion: minusculas + sin acentos + espacios colapsados.
# Asi "Ignora Las Instrucciones" o "ignóralas" caen en la misma forma canonica.
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Patrones de ataque (sobre el texto YA normalizado, sin acentos).
# Cada entrada: (categoria, regex compilada). El orden no importa: se devuelve
# el primero que coincide.
# ---------------------------------------------------------------------------
_RAW_PATTERNS: list[tuple[str, str]] = [
    # --- Fuga de instrucciones (instruction leak) -------------------------
    ("instruction_leak", r"\b(revela|muestra|dime|imprime|repite|comparte|cual es)\b.{0,30}\b(system\s*prompt|prompt del sistema|instruccion(es)?|reglas|directrices|configuracion)\b"),
    ("instruction_leak", r"\b(system\s*prompt|prompt del sistema)\b"),
    ("instruction_leak", r"\b(tus|las)\s+instrucciones\s+(internas|de sistema|originales|iniciales|secretas)\b"),
    ("instruction_leak", r"\b(repeat|print|reveal|show|expose)\b.{0,30}\b(system\s*prompt|instructions|your prompt|your rules)\b"),

    # --- Jailbreak / cambio de rol / anulacion de reglas ------------------
    ("jailbreak", r"\bignora\b.{0,30}\b(instruccion(es)?|reglas|lo anterior|todo lo anterior|directrices)\b"),
    ("jailbreak", r"\bignore\b.{0,30}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompt)\b"),
    ("jailbreak", r"\bolvida\b.{0,30}\b(tus|las)\b.{0,15}\b(instruccion(es)?|reglas|restricciones)\b"),
    ("jailbreak", r"\b(actua|comportate|haz de cuenta|finge|pretende)\b.{0,15}\b(como|que eres)\b"),
    ("jailbreak", r"\b(act|behave|pretend|roleplay)\b.{0,15}\bas\b"),
    ("jailbreak", r"\b(asume|adopta|toma)\b.{0,15}\b(el\s+)?(rol|papel|personalidad|identidad)\b"),
    ("jailbreak", r"\bmodo\s+(desarrollador|developer|dios|sin restricciones|libre|jailbreak)\b"),
    ("jailbreak", r"\bdeveloper\s+mode\b"),
    ("jailbreak", r"\b(dan|do anything now)\b"),
    ("jailbreak", r"\b(sin|no tienes|olvida tus)\s+(restricciones|reglas|filtros|limites|censura)\b"),
    ("jailbreak", r"\b(eres|ahora eres)\b.{0,25}\b(un modelo sin|una ia sin|libre de toda)\b"),
    ("jailbreak", r"\bbypass\b.{0,20}\b(rules|filter|guardrail|safety|restrictions)\b"),
]

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (cat, re.compile(rx)) for cat, rx in _RAW_PATTERNS
]

# Umbral de spam/repeticion: un mismo caracter repetido >= N veces seguidas, o
# una misma palabra repetida >= M veces seguidas (relleno para desbordar ctx).
_CHAR_RUN = re.compile(r"(.)\1{29,}")              # 30+ veces el mismo caracter
_WORD_RUN = re.compile(r"\b(\w+)(\s+\1\b){9,}")    # 10+ veces la misma palabra


def check_prompt_injection(text: str) -> GuardrailResult:
    """Evalua `text` y decide si se bloquea ANTES de llamar al LLM.

    Devuelve un GuardrailResult. Si blocked=True, el caller debe devolver
    `message` al usuario y registrar was_blocked=True en la observabilidad,
    sin invocar el modelo.
    """
    if not text or not text.strip():
        return GuardrailResult(blocked=False)

    norm = _normalize(text)

    # 1) Patrones de ataque conocidos.
    for category, pattern in _PATTERNS:
        if pattern.search(norm):
            return GuardrailResult(
                blocked=True,
                category=category,
                pattern=pattern.pattern,
                message=GENERIC_BLOCK_MESSAGE,
            )

    # 2) Spam / patrones repetitivos atipicos.
    if _CHAR_RUN.search(norm) or _WORD_RUN.search(norm):
        return GuardrailResult(
            blocked=True,
            category="spam",
            pattern="repeticion_atipica",
            message=GENERIC_BLOCK_MESSAGE,
        )

    return GuardrailResult(blocked=False)
