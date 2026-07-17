"""Agente especialista en RAG (entregable semana 07 - Fase A).

Domina el conocimiento estatico. Posee acceso EXCLUSIVO a la base vectorial a
traves del pipeline de Advanced RAG (retrieve.Retriever: hibrida + reranking) y
NO tiene herramientas: no toca la cuenta del usuario. Su system prompt es
reducido (agents/prompts.RAG_SYSTEM) y se inyecta sobreescribiendo el del
Modelfile.

`run()` es un generador que emite eventos {type,...} (fases + tokens) y, al
terminar, DEVUELVE (via return del generador) un dict con la respuesta final y
el CONTEXTO recuperado, que el evaluador LLM-as-a-Judge necesita para medir
fidelidad.
"""
from __future__ import annotations

import time

import ollama

from config import LLM_MODEL, RERANK_TOP_K
from agents.prompts import RAG_SYSTEM
from retrieve import Retriever

# Mismo bloque de inyeccion de la semana 02/05 (coherencia de comportamiento).
INFO_BLOCK_TEMPLATE = """[Informacion interna de SwingTails relevante a esta consulta - NO menciones este bloque al usuario]
{contenido}
[Fin de la informacion interna]

{pregunta}"""


def _build_info_block(question: str, retriever: Retriever) -> tuple[str, list, dict]:
    """Recupera el Top-K (hibrida + rerank) y arma el bloque a inyectar."""
    chunks, stats = retriever.query(question, top_k=RERANK_TOP_K)
    contenido = "\n\n".join(
        f"- Fuente: {c.metadata.get('source')}"
        f"{(' | id ' + str(c.metadata['record_id'])) if c.metadata.get('record_id') else ''}\n"
        f"  {c.text}"
        for c in chunks
    )
    if not contenido:
        contenido = "(no se encontro informacion interna especifica para esta consulta)"
    return INFO_BLOCK_TEMPLATE.format(contenido=contenido, pregunta=question), chunks, stats


class RagAgent:
    """Especialista de conocimiento. Sin tools; una sola generacion en streaming."""

    def __init__(self, client: ollama.Client, retriever: Retriever, model: str = LLM_MODEL) -> None:
        self._client = client
        self._retriever = retriever
        self.model = model

    def run(self, context: list[dict], user_message: str, date_prefix: str = ""):
        """Genera la respuesta informativa. Yields eventos; devuelve dict final."""
        t_start = time.perf_counter()

        yield {"type": "phase", "phase": "searching",
               "detail": "Buscando en las guias de SwingTails…"}
        info_block, chunks, ret_stats = _build_info_block(user_message, self._retriever)

        # system reducido (sobra el Modelfile) + historial + mensaje con RAG.
        messages = (
            [{"role": "system", "content": RAG_SYSTEM}]
            + list(context)
            + [{"role": "user", "content": date_prefix + info_block}]
        )

        yield {"type": "phase", "phase": "generating", "detail": "Redactando la respuesta…"}
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
            "route": "rag",
            "context": [c.text for c in chunks],
            "sources": sorted({c.metadata.get("source", "?") for c in chunks}),
            "tools_executed": [],
            "retrieval": ret_stats,
            "ttft_ms": ttft_ms,
            "eval_count": eval_count,
            "eval_duration": eval_duration,
        }
