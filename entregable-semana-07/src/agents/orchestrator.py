"""Orquestador multi-agente (entregable semana 07 - Fase A).

Es la unica puerta de entrada al sistema de agentes. Coordina un turno completo:

    guardrail  ->  ruteador  ->  subagente especialista  ->  respuesta

y emite un flujo de eventos {type, ...} que consumen tanto el servidor HTTP
(server.py, para el streaming SSE al frontend) como el evaluador local
(evaluar_agente.py, para medir ruteo / fidelidad / bloqueo).

Diseño de CONTEXTO ENTRE AGENTES (rubrica Fase A punto 2): el orquestador recibe
el historial conversacional ya reconstruido (`context`, la lista de mensajes
user/assistant que el server saca de sessions.build_context) y lo pasa TAL CUAL
al especialista elegido. Asi el subagente hereda el hilo completo de la
conversacion sin perdida ni bucles: es el mismo buffer, solo cambia el system
prompt (rol) y el set de herramientas.

El orquestador NO persiste memoria ni observabilidad: eso lo hace el caller
(server.py), para que el evaluador pueda correr el turno en proceso sin tocar
las bases de datos de produccion.

Eventos que emite:
    phase   {phase: routing|searching|thinking|executing|generating, detail, tool?}
    route   {route, reason, method}
    token   {text}
    tool    {name, status}
    blocked {message, reason}
    done    {route, reply, sources, tools_executed, context, ttft_ms,
             total_latency_ms, tokens_per_second, retrieval?, blocked}
"""
from __future__ import annotations

import datetime
import time

import ollama

import web_reader
from config import LLM_MODEL, OLLAMA_HOST, TOP_K
from guardrails import check_prompt_injection
from retrieve import Retriever
from agents.prompts import LENGUAJE
from agents.rag_agent import RagAgent
from agents.router import RouterAgent
from agents.transactional_agent import TransactionalAgent


class Route:
    RAG = "rag"
    TRANSACTIONAL = "transactional"
    SMALLTALK = "smalltalk"
    BLOCKED = "blocked"


# Prompt minusculo para el turno de charla/capacidades (sin tools, sin RAG).
_SMALLTALK_SYSTEM = """Eres Tailo, el asistente virtual de SwingTails (app de mascotas). Responde saludos, agradecimientos y preguntas sobre quien eres o que puedes hacer de forma calida y BREVE, en español. Cuando te pregunten en que ayudas, menciona tus areas: consultar, registrar y actualizar mascotas; agendar y gestionar citas veterinarias; ver clinicas y el catalogo de productos; y dar consejos de cuidado, salud y alimentacion. NO llames herramientas ni listes datos de la cuenta del usuario (todavia no te lo han pedido). Termina invitando a decir en que te gustaria ayudar.""" + LENGUAJE


def _date_prefix() -> str:
    today = datetime.date.today().isoformat()
    return f"[Fecha de hoy: {today}. Si el usuario da una fecha sin año, usa el año actual.]\n\n"


def _history_hint(context: list[dict], max_chars: int = 400) -> str:
    """Resumen minimo del historial reciente para ayudar al ruteador."""
    tail = context[-4:]
    parts = [f"{m.get('role')}: {m.get('content', '')}" for m in tail if m.get("content")]
    hint = "\n".join(parts)
    return hint[-max_chars:]


def _tokens_per_second(eval_count, eval_duration_ns, fb_tokens=None, fb_seconds=None):
    if eval_count and eval_duration_ns:
        return eval_count / (eval_duration_ns / 1e9)
    if fb_tokens and fb_seconds and fb_seconds > 0:
        return fb_tokens / fb_seconds
    return None


class Orchestrator:
    """Coordina ruteo + especialistas. Recursos compartidos por proceso."""

    def __init__(
        self,
        client: ollama.Client | None = None,
        retriever: Retriever | None = None,
        model: str = LLM_MODEL,
    ) -> None:
        self._client = client or ollama.Client(host=OLLAMA_HOST)
        self._retriever = retriever  # carga perezosa (embeddings + BM25) al primer uso RAG
        self.model = model
        self._router = RouterAgent(client=self._client)

    def _get_retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(top_k=TOP_K)
        return self._retriever

    # ------------------------------------------------------------------
    def run_turn(self, context: list[dict], user_message: str):
        """Generador del turno completo. Yields eventos; el ultimo es 'done'."""
        t_start = time.perf_counter()
        date_prefix = _date_prefix()

        # --- 1) Guardrail PREVENTIVO (bloqueo de inyecciones) ----------------
        guard = check_prompt_injection(user_message)
        if guard.blocked:
            yield {"type": "route", "route": Route.BLOCKED, "reason": guard.category,
                   "method": "guardrail"}
            yield {"type": "blocked", "message": guard.message, "reason": guard.category}
            yield {
                "type": "done", "route": Route.BLOCKED, "reply": guard.message,
                "sources": [], "tools_executed": [], "context": [],
                "ttft_ms": None,
                "total_latency_ms": round((time.perf_counter() - t_start) * 1000, 2),
                "tokens_per_second": None, "blocked": True,
            }
            return

        # --- 2) Lectura de enlaces compartidos por el usuario ----------------
        # Si el mensaje trae URLs, el backend las descarga y extrae su texto para
        # inyectarlo al contexto. Asi el agente relaciona el contenido del enlace
        # con la sesion, en vez de alucinar (antes hasta ofrecia "leerlo" con un
        # script de Python). El bloque se agrega como un turno de usuario extra,
        # ANTES del mensaje real, para no contaminar la recuperacion del RAG.
        urls = web_reader.extract_urls(user_message)
        web_context: list[dict] = list(context)
        if urls:
            yield {"type": "phase", "phase": "searching",
                   "detail": f"Leyendo {'el enlace' if len(urls) == 1 else 'los enlaces'}…"}
            web_block, _res = web_reader.read_urls(urls)
            if web_block:
                web_context = list(context) + [{"role": "user", "content": web_block}]

        # --- 3) Ruteo --------------------------------------------------------
        yield {"type": "phase", "phase": "routing", "detail": "Analizando tu solicitud…"}
        decision = self._router.route(user_message, history_hint=_history_hint(context))
        route = decision["route"]
        # Un mensaje con un enlace para revisar es informativo: si el ruteador lo
        # mando a charla, lo tratamos como RAG para que use el contenido leido.
        if urls and route == Route.SMALLTALK:
            route = Route.RAG
        yield {"type": "route", "route": route, "reason": decision.get("reason", ""),
               "method": decision.get("method", "")}

        # --- 4) Delegacion al especialista -----------------------------------
        if route == Route.TRANSACTIONAL:
            agent = TransactionalAgent(self._client, model=self.model)
            result = yield from agent.run(web_context, user_message, date_prefix)
        elif route == Route.SMALLTALK:
            result = yield from self._run_smalltalk(web_context, user_message, t_start)
        else:  # RAG (ruta por defecto)
            agent = RagAgent(self._client, self._get_retriever(), model=self.model)
            result = yield from agent.run(web_context, user_message, date_prefix)

        # --- 5) Cierre: metricas + done --------------------------------------
        total_latency = (time.perf_counter() - t_start) * 1000
        tps = _tokens_per_second(
            result.get("eval_count"), result.get("eval_duration"),
            fb_tokens=len(result.get("reply", "")), fb_seconds=(time.perf_counter() - t_start),
        )
        done = {
            "type": "done",
            "route": result.get("route", route),
            "reply": result.get("reply", ""),
            "sources": result.get("sources", []),
            "tools_executed": result.get("tools_executed", []),
            "context": result.get("context", []),
            "ttft_ms": round(result["ttft_ms"], 2) if result.get("ttft_ms") else None,
            "total_latency_ms": round(total_latency, 2),
            "tokens_per_second": round(tps, 2) if tps else None,
            "needs_location": bool(result.get("needs_location")),
            "blocked": False,
        }
        if result.get("retrieval"):
            done["retrieval"] = result["retrieval"]
        yield done

    # ------------------------------------------------------------------
    def _run_smalltalk(self, context: list[dict], user_message: str, t_start: float):
        """Turno de charla/capacidades: una generacion breve, sin tools ni RAG."""
        yield {"type": "phase", "phase": "generating", "detail": "Respondiendo…"}
        messages = (
            [{"role": "system", "content": _SMALLTALK_SYSTEM}]
            + list(context)
            + [{"role": "user", "content": user_message}]
        )
        ttft_ms = None
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
        return {
            "reply": "".join(pieces).strip(),
            "route": Route.SMALLTALK,
            "context": [], "sources": [], "tools_executed": [],
            "ttft_ms": ttft_ms, "eval_count": eval_count, "eval_duration": eval_duration,
        }
