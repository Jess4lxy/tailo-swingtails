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

    # --- Fuga de codigo fuente / archivos internos ------------------------
    # Cuidado con falsos positivos del dominio (p.ej. "codigo de descuento",
    # "codigo postal"): por eso se exige un calificador claramente interno.
    ("code_leak", r"\b(codigo fuente|source code|codigo del (sistema|agente|backend|programa|bot)|tu codigo|codigo python|source-code)\b"),
    ("code_leak", r"\b(server|prompts?|tools|config|api_client|orchestrator|guardrails|sessions|retrieve|web_reader|stress_db|geo)\.py\b"),
    ("code_leak", r"\b(modelfile|dockerfile|docker-compose|requirements\.txt|package\.json)\b"),

    # --- Fuga de secretos / configuracion / estructura interna ------------
    ("secret_leak", r"(?<!\w)\.env\b|\barchivo env\b|\bvariables? de entorno\b|\benvironment variables?\b|\benv vars?\b"),
    ("secret_leak", r"\b(api[\s_-]?key|clave de api|access[\s_-]?token|token de acceso|refresh[\s_-]?token|jwt secret|secret key|clave secreta|db password|contrasena de la base(\s+de datos)?|credenciales del (sistema|servidor)|llave privada|private key)\b"),
    ("secret_leak", r"\b(estructura|esquema|schema|tablas?|modelo de datos|diagrama)\b.{0,18}\b(base de datos|bd|sqlite|db|backend)\b"),
    ("secret_leak", r"\b(que|cuales)\b.{0,18}\b(herramientas|funciones|tools|endpoints?|apis?)\b.{0,12}\b(internas?|tienes|dispones|usas|disponibles)\b"),

    # --- Escalada de privilegios / suplantacion de autoridad --------------
    # NO se bloquea un simple "soy administrador de la clinica" (rol de negocio
    # legitimo): se exige que la frase apunte al SISTEMA/acceso/datos internos.
    ("authority", r"\b(dame|otorgame|activa|habilita|entra en|ponte en|necesito|quiero|concedeme|dame acceso a)\b.{0,22}\b(modo|acceso|permisos?|privilegios?|rol)\s+(de\s+)?(administrador|admin|root|superusuario|super usuario|dios|elevados?|sudo)\b"),
    ("authority", r"\b(modo|acceso|permisos?|privilegios?)\s+(administrador|admin|root|superusuario|dios|sudo)\b"),
    ("authority", r"\bsoy (el |la |un |una |su )?(administrador|admin|superusuario|super usuario|root|desarrollador|developer|ingeniero|programador|hacker)\b.{0,35}\b(sistema|tailo|swingtails|servidor|base de datos|backend|codigo|prompt|instruccion(es)?|acceso|permiso|configuracion|interno)\b"),
    ("authority", r"\bsoy (el |la |tu )?(creador|desarrollador|dueno|programador|ingeniero)\b.{0,18}\b(de tailo|del sistema|de swingtails|del agente|del backend|de esta ia)\b"),
    ("authority", r"\bcomo (administrador|admin|superusuario|root|desarrollador)\b.{0,25}\b(dame|muestra|revela|accede|entra|desbloquea|ignora|elimina|borra)\b"),

    # --- Multilingue: variantes en INGLES de los ataques (reporte #16) -----
    ("jailbreak", r"\bignore\b.{0,25}\b(all|any|the)?\s*(previous|prior|above|earlier|system)\s*(instructions?|prompts?|rules?|messages?)\b"),
    ("jailbreak", r"\b(forget|disregard|override|bypass)\b.{0,20}\b(your|all|the|previous)?\s*(instructions?|rules?|restrictions?|guardrails?|system prompt)\b"),
    ("jailbreak", r"\b(you are|you're|act as|pretend to be|roleplay as|behave as|from now on you are)\b.{0,25}\b(dan|a different|an unrestricted|no restrictions|jailbroken|developer mode|do anything now)\b"),
    ("jailbreak", r"\b(enable|activate|enter|switch to|turn on)\b.{0,15}\b(developer|dev|god|admin|root|jailbreak|unrestricted|sudo)\s*mode\b"),
    ("instruction_leak", r"\b(show|reveal|print|repeat|reprint|display|expose|leak|give me|what (is|are))\b.{0,30}\b(your )?(system prompt|initial prompt|instructions?|system message|hidden (prompt|instructions?)|rules?|guidelines?|configuration)\b"),
    ("code_leak", r"\b(show|give|send|print|reveal|leak|dump|paste)\b.{0,25}\b(your )?(source code|code|env( file)?|\.env|api keys?|secret keys?|credentials|tokens?|database (schema|structure)|system files?)\b"),
    ("authority", r"\bi('m| am)\b.{0,15}\b(the |an |your )?(admin(istrator)?|developer|superuser|root|owner|creator|engineer|sysadmin)\b.{0,35}\b(system|server|database|backend|code|prompt|access|grant|give me|reveal|show)\b"),
    ("authority", r"\bgrant\b.{0,15}\b(me )?\b(admin|root|superuser|elevated|full)\s*(access|privileges?|permissions?|rights?)\b"),

    # --- Payloads de inyeccion tecnica (reporte M-03): SQLi / comandos / XSS --
    # Un usuario de mascotas jamas escribe esto; casi no hay falsos positivos.
    ("sqli", r"\bunion\s+select\b|\bselect\b.{0,40}\bfrom\b.{0,40}\bwhere\b"),
    ("sqli", r"\b(drop|truncate)\s+table\b|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\b.{0,30}\bset\b.{0,30}="),
    ("sqli", r"'\s*or\s*'?1'?\s*=\s*'?1|\bor\s+1\s*=\s*1\b|'\s*or\s+1=1|\"\s*or\s+1=1"),
    ("sqli", r"';?\s*--|\bxp_cmdshell\b|\bsleep\s*\(|\bpg_sleep\s*\(|\bwaitfor\s+delay\b"),
    ("cmdi", r"[;&|]\s*(rm|cat|ls|wget|curl|nc|bash|sh|powershell|cmd|whoami|id|cat)\s|\$\([^)]+\)|`[^`]+`|\|\|\s*(rm|curl|wget)"),
    ("xss_payload", r"<\s*script\b|javascript\s*:|on(error|load|click|mouseover)\s*=|<\s*img[^>]*onerror|<\s*iframe\b|document\.cookie|<\s*svg[^>]*onload"),
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
