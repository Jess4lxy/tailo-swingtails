"""
RAG end-to-end de Tailo.

Flujo: pregunta -> embedding -> top-k de ChromaDB -> inyeccion al LLM tailo-rag.
Streaming activado para mostrar TTFT bajo.

Uso interactivo:
    python src/chat.py

Uso one-shot:
    python src/chat.py "Recomiendame croquetas para gato senior con problemas renales"
"""
from __future__ import annotations

import argparse
import sys
import time

import ollama

from config import LLM_MODEL, OLLAMA_HOST
from retrieve import Retriever


PROMPT_TEMPLATE = """Contexto recuperado (usa SOLO esta informacion para responder):
---
{contexto}
---

Pregunta del usuario: {pregunta}

Responde siguiendo las reglas del sistema (no inventes, redirige a veterinario humano cuando aplique)."""


def build_prompt(question: str, retriever: Retriever, top_k: int = 5) -> tuple[str, list]:
    chunks, lat = retriever.query(question, top_k=top_k)
    contexto = "\n\n".join(
        f"[Fragmento {i+1} | fuente={c.metadata.get('source')} | id={c.metadata.get('record_id','')}]\n{c.text}"
        for i, c in enumerate(chunks)
    )
    return PROMPT_TEMPLATE.format(contexto=contexto, pregunta=question), chunks, lat


def answer(question: str, top_k: int = 5, stream: bool = True) -> dict:
    retriever = Retriever(top_k=top_k)
    prompt, chunks, ret_lat = build_prompt(question, retriever, top_k=top_k)

    client = ollama.Client(host=OLLAMA_HOST)
    t0 = time.perf_counter()
    ttft_ms = None
    full = []

    if stream:
        for part in client.generate(model=LLM_MODEL, prompt=prompt, stream=True):
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - t0) * 1000
            chunk = part.get("response", "")
            full.append(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()
        sys.stdout.write("\n")
    else:
        resp = client.generate(model=LLM_MODEL, prompt=prompt, stream=False)
        ttft_ms = (time.perf_counter() - t0) * 1000
        full.append(resp["response"])
        print(resp["response"])

    total_ms = (time.perf_counter() - t0) * 1000
    return {
        "respuesta": "".join(full).strip(),
        "fuentes": [c.metadata for c in chunks],
        "latencia_recuperacion": ret_lat,
        "ttft_ms": round(ttft_ms or 0, 2),
        "total_ms": round(total_ms, 2),
    }


def repl(top_k: int = 5) -> None:
    print("Tailo RAG - escribe 'salir' para terminar.\n")
    while True:
        try:
            q = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not q or q.lower() in {"salir", "exit", "quit"}:
            return
        print("\nTailo: ", end="", flush=True)
        meta = answer(q, top_k=top_k, stream=True)
        srcs = sorted({m.get("source", "?") for m in meta["fuentes"]})
        print(f"\n[fuentes: {', '.join(srcs)} | TTFT {meta['ttft_ms']}ms | recuperacion {meta['latencia_recuperacion']['ms_total']}ms]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    if args.query:
        answer(args.query, top_k=args.k, stream=not args.no_stream)
    else:
        repl(top_k=args.k)
