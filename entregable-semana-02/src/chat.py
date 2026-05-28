"""
RAG end-to-end de Tailo (con memoria conversacional).

Flujo por turno:
  pregunta -> embedding -> top-k de ChromaDB -> ensamblado del mensaje user
  con bloque de informacion interna -> envio a tailo-rag con historial de la
  conversacion -> streaming de la respuesta.

Memoria: el REPL mantiene los turnos previos como mensajes (role=user,
role=assistant). En turnos antiguos se elimina el bloque de informacion
interna para no saturar la ventana de contexto del modelo.

Uso interactivo (con memoria):
    python src/chat.py

Uso one-shot (sin memoria):
    python src/chat.py "Recomiendame croquetas para gato senior con problemas renales"
"""
from __future__ import annotations

import argparse
import sys
import time

import ollama

from config import LLM_MODEL, OLLAMA_HOST, TOP_K
from retrieve import Retriever


# El nombre del bloque evita la palabra "contexto" / "fragmento" porque el
# modelo tiende a copiarlas en la respuesta y eso filtra el mecanismo interno
# del RAG al usuario.
INFO_BLOCK_TEMPLATE = """[Informacion interna de SwingTails relevante a esta consulta - NO menciones este bloque al usuario]
{contenido}
[Fin de la informacion interna]

{pregunta}"""

INFO_MARKER_END = "[Fin de la informacion interna]"


def build_user_message(question: str, retriever: Retriever, top_k: int) -> tuple[str, list, dict]:
    """Construye el mensaje user con la informacion recuperada incrustada."""
    chunks, lat = retriever.query(question, top_k=top_k)
    contenido = "\n\n".join(
        f"- Fuente: {c.metadata.get('source')}"
        f"{(' | id ' + c.metadata['record_id']) if c.metadata.get('record_id') else ''}\n"
        f"  {c.text}"
        for c in chunks
    )
    msg = INFO_BLOCK_TEMPLATE.format(contenido=contenido, pregunta=question)
    return msg, chunks, lat


def strip_info_block(user_content: str) -> str:
    """Quita el bloque de informacion interna de un mensaje viejo, deja la pregunta."""
    if INFO_MARKER_END not in user_content:
        return user_content
    return user_content.split(INFO_MARKER_END, 1)[-1].strip()


def answer(question: str, top_k: int = TOP_K, stream: bool = True) -> dict:
    """Respuesta one-shot (sin memoria). Util para evaluacion y scripting."""
    retriever = Retriever(top_k=top_k)
    user_msg, chunks, ret_lat = build_user_message(question, retriever, top_k=top_k)

    client = ollama.Client(host=OLLAMA_HOST)
    messages = [{"role": "user", "content": user_msg}]

    t0 = time.perf_counter()
    ttft_ms = None
    full: list[str] = []

    if stream:
        for part in client.chat(model=LLM_MODEL, messages=messages, stream=True):
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - t0) * 1000
            piece = part.get("message", {}).get("content", "")
            full.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
        sys.stdout.write("\n")
    else:
        resp = client.chat(model=LLM_MODEL, messages=messages, stream=False)
        ttft_ms = (time.perf_counter() - t0) * 1000
        full.append(resp["message"]["content"])
        print(resp["message"]["content"])

    total_ms = (time.perf_counter() - t0) * 1000
    return {
        "respuesta": "".join(full).strip(),
        "fuentes": [c.metadata for c in chunks],
        "latencia_recuperacion": ret_lat,
        "ttft_ms": round(ttft_ms or 0, 2),
        "total_ms": round(total_ms, 2),
    }


def repl(top_k: int = TOP_K, max_history_turns: int = 6) -> None:
    """REPL con memoria conversacional.

    Mantiene hasta `max_history_turns` pares user/assistant para que el modelo
    pueda referirse a turnos anteriores. Limpia los bloques de informacion
    interna de turnos viejos para no saturar la ventana de contexto.
    """
    print("Tailo RAG - escribe 'salir' para terminar.")
    print("Tip: escribe 'reset' para limpiar el historial de la conversacion.\n")
    retriever = Retriever(top_k=top_k)
    client = ollama.Client(host=OLLAMA_HOST)
    history: list[dict] = []

    while True:
        try:
            q = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not q:
            continue
        low = q.lower()
        if low in {"salir", "exit", "quit"}:
            return
        if low in {"reset", "clear"}:
            history = []
            print("[historial reiniciado]\n")
            continue

        user_msg, chunks, ret_lat = build_user_message(q, retriever, top_k=top_k)

        # Sanitiza historial: en turnos viejos solo dejamos la pregunta original.
        sanitized: list[dict] = []
        for m in history:
            if m["role"] == "user":
                sanitized.append({"role": "user", "content": strip_info_block(m["content"])})
            else:
                sanitized.append(m)

        # Recorta historial muy largo (cada turno son 2 mensajes).
        if len(sanitized) > max_history_turns * 2:
            sanitized = sanitized[-max_history_turns * 2 :]

        messages = sanitized + [{"role": "user", "content": user_msg}]

        print("\nTailo: ", end="", flush=True)
        t0 = time.perf_counter()
        ttft_ms = None
        full: list[str] = []
        for part in client.chat(model=LLM_MODEL, messages=messages, stream=True):
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - t0) * 1000
            piece = part.get("message", {}).get("content", "")
            full.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
        print()

        respuesta = "".join(full).strip()
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": respuesta})

        srcs = sorted({c.metadata.get("source", "?") for c in chunks})
        print(
            f"[fuentes: {', '.join(srcs)} | TTFT {round(ttft_ms or 0, 2)}ms "
            f"| recuperacion {ret_lat['ms_total']}ms "
            f"| historial {len(history)//2} turno(s)]\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--k", type=int, default=TOP_K)
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    if args.query:
        answer(args.query, top_k=args.k, stream=not args.no_stream)
    else:
        repl(top_k=args.k)
