"""Tailo Agent: RAG + Function Calling end-to-end.

Flujo por turno (rubrica fase 2):

  1. Usuario pregunta.
  2. Recuperamos top-k del RAG y construimos el mensaje user con un bloque
     [Informacion interna de SwingTails ...] inyectado (mismo mecanismo de la
     semana 02).
  3. Llamamos a Ollama con `messages` + parametro `tools` (esquemas de
     tools.TOOL_SCHEMAS). Como una llamada con tools no se puede streamear,
     usamos stream=False en este paso.
  4. **Interceptamos**: si la respuesta trae `tool_calls`, NO lo mostramos al
     usuario. Ejecutamos cada tool localmente (tools.execute_tool) y
     anadimos cada resultado al historial con role="tool".
  5. Reinvocamos al LLM con el historial actualizado para que genere la
     respuesta conversacional final, ahora si en streaming.
  6. Si falla una tool, su resultado entra al historial como {"error": ...};
     el LLM lo lee y se lo explica al usuario. El error NO se guarda como
     "verdad" en memoria: el siguiente turno parte limpio (rubrica: evitar
     loops por estado fallido).

Uso interactivo:
    python src/chat.py

One-shot (sin memoria conversacional):
    python src/chat.py "agenda una cita para mi perro"
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time

import ollama

from api_client import get_client
from config import LLM_MODEL, OLLAMA_HOST, TOP_K
from retrieve import Retriever
from tools import TOOL_SCHEMAS, execute_tool


# Bloque RAG (mismo del entregable de la semana 02 para mantener coherencia).
INFO_BLOCK_TEMPLATE = """[Informacion interna de SwingTails relevante a esta consulta - NO menciones este bloque al usuario]
{contenido}
[Fin de la informacion interna]

{pregunta}"""

INFO_MARKER_END = "[Fin de la informacion interna]"


# ---------------------------------------------------------------------------
# RAG helpers (copiados de semana 02)
# ---------------------------------------------------------------------------
def build_user_message(question: str, retriever: Retriever, top_k: int) -> tuple[str, list, dict]:
    chunks, lat = retriever.query(question, top_k=top_k)
    contenido = "\n\n".join(
        f"- Fuente: {c.metadata.get('source')}"
        f"{(' | id ' + c.metadata['record_id']) if c.metadata.get('record_id') else ''}\n"
        f"  {c.text}"
        for c in chunks
    )
    return INFO_BLOCK_TEMPLATE.format(contenido=contenido, pregunta=question), chunks, lat


def strip_info_block(user_content: str) -> str:
    if INFO_MARKER_END not in user_content:
        return user_content
    return user_content.split(INFO_MARKER_END, 1)[-1].strip()


# ---------------------------------------------------------------------------
# Sesion de usuario
# ---------------------------------------------------------------------------
# El agente NO tiene un usuario propio: adopta la identidad de quien conversa.
# Quien usa el chat inicia sesion con sus credenciales de SwingTails y todas
# las operaciones (registrar mascota, agendar cita, carrito) se hacen con su
# user_id, que se extrae del JWT (ver api_client).
def _print_session_status() -> None:
    api = get_client()
    if api.has_token and api.current_user_id is not None:
        print(f"[sesion activa: usuario id={api.current_user_id}]\n")
    elif api.has_token:
        print("[sesion activa (no se pudo leer el id del token)]\n")
    else:
        print("[sin sesion: escribe 'login' para autenticarte y poder operar]\n")


def _interactive_login() -> bool:
    """Pide credenciales al usuario que conversa y abre sesion."""
    api = get_client()
    print("Inicia sesion en SwingTails (el agente actuara en tu nombre).")
    try:
        email = input("  email: ").strip()
        password = getpass.getpass("  contrasena: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not email or not password:
        print("  [login cancelado: faltan datos]\n")
        return False

    resp = api.login(email, password)
    if isinstance(resp, dict) and resp.get("error"):
        print(f"  [login fallido: {resp['error']}]\n")
        return False
    if not api.has_token:
        msg = resp.get("message") if isinstance(resp, dict) else None
        print(f"  [login fallido: {msg or 'credenciales invalidas'}]\n")
        return False
    print(f"  [sesion iniciada como usuario id={api.current_user_id}]\n")
    return True


# ---------------------------------------------------------------------------
# Function Calling core
# ---------------------------------------------------------------------------
def _run_tool_cycle(
    client: ollama.Client,
    messages: list[dict],
    verbose: bool,
    max_iters: int = 4,
) -> list[dict]:
    """Bucle tools: pide al modelo, ejecuta tool_calls si los hay, repite.

    Termina cuando el modelo deja de pedir tools o se alcanza max_iters
    (proteccion contra bucles patologicos). Devuelve el historial extendido
    con los mensajes role=assistant (con tool_calls) y role=tool (con los
    resultados). El caller es responsable de la respuesta final.
    """
    for _ in range(max_iters):
        resp = client.chat(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            stream=False,
        )
        msg = resp.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Sin tools: terminamos el ciclo. La respuesta final se hace fuera
            # con streaming, asi que NO agregamos este mensaje al historial.
            return messages

        # El modelo pidio una o mas tools. Agregamos su mensaje (con los
        # tool_calls) al historial para mantener el protocolo de Ollama.
        messages.append({
            "role": "assistant",
            "content": msg.get("content", "") or "",
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if verbose:
                print(
                    f"\n  [tool_call] {name}({json.dumps(args, ensure_ascii=False)})",
                    flush=True,
                )
            result = execute_tool(name, args)
            if verbose:
                preview = result if len(result) < 300 else result[:300] + "..."
                print(f"  [tool_result] {preview}\n", flush=True)
            messages.append({"role": "tool", "name": name, "content": result})

    # Salimos por max_iters: agregamos un aviso para que el LLM no insista.
    messages.append({
        "role": "tool",
        "name": "system",
        "content": json.dumps(
            {"error": "Se alcanzo el limite de llamadas a herramientas. Resume al usuario lo obtenido y pidele instrucciones."},
            ensure_ascii=False,
        ),
    })
    return messages


def _stream_final(client: ollama.Client, messages: list[dict]) -> tuple[str, float | None]:
    """Genera la respuesta final en streaming y devuelve (texto, TTFT ms)."""
    t0 = time.perf_counter()
    ttft_ms: float | None = None
    pieces: list[str] = []
    # No pasamos `tools` aqui: ya cerramos el ciclo de herramientas; queremos
    # texto plano y poder streamear (Ollama no soporta stream con tools).
    for part in client.chat(model=LLM_MODEL, messages=messages, stream=True):
        if ttft_ms is None:
            ttft_ms = (time.perf_counter() - t0) * 1000
        piece = part.get("message", {}).get("content", "") or ""
        pieces.append(piece)
        sys.stdout.write(piece)
        sys.stdout.flush()
    sys.stdout.write("\n")
    return "".join(pieces).strip(), ttft_ms


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------
def answer(question: str, top_k: int = TOP_K, verbose: bool = True) -> dict:
    """Respuesta one-shot: RAG + tools + respuesta final. Sin memoria."""
    retriever = Retriever(top_k=top_k)
    user_msg, chunks, ret_lat = build_user_message(question, retriever, top_k=top_k)

    client = ollama.Client(host=OLLAMA_HOST)
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    messages = _run_tool_cycle(client, messages, verbose=verbose)
    final, ttft = _stream_final(client, messages)

    return {
        "respuesta": final,
        "fuentes": [c.metadata for c in chunks],
        "latencia_recuperacion": ret_lat,
        "ttft_ms": round(ttft or 0, 2),
    }


def repl(top_k: int = TOP_K, max_history_turns: int = 6, verbose: bool = True) -> None:
    """REPL con memoria conversacional. Limpia bloques RAG viejos del historial."""
    print("Tailo Agent (RAG + Function Calling) - escribe 'salir' para terminar.")
    print("Tip: 'reset' limpia el historial. 'verbose on/off' alterna el log de tools.")
    print("     'login' inicia/renueva sesion de usuario.\n")

    retriever = Retriever(top_k=top_k)
    client = ollama.Client(host=OLLAMA_HOST)
    history: list[dict] = []

    # Sesion: intenta usar credenciales del .env; si no hay, pide login.
    api = get_client()  # dispara ensure_authenticated() (login via .env si existe)
    if not api.has_token:
        _interactive_login()
    _print_session_status()

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
        if low == "verbose on":
            verbose = True
            print("[verbose ON]\n")
            continue
        if low == "verbose off":
            verbose = False
            print("[verbose OFF]\n")
            continue
        if low == "login":
            _interactive_login()
            continue
        if low in {"whoami", "sesion", "session"}:
            _print_session_status()
            continue

        user_msg, chunks, ret_lat = build_user_message(q, retriever, top_k=top_k)

        # Sanitiza historial viejo: quita bloques RAG y descarta los
        # mensajes role=tool de turnos previos (ya no son contexto util y
        # confunden al modelo si se acumulan).
        sanitized: list[dict] = []
        for m in history:
            if m.get("role") == "tool":
                continue
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # mensajes "vacios" que solo llevaban tool_calls: descartar.
                continue
            if m.get("role") == "user":
                sanitized.append({"role": "user", "content": strip_info_block(m["content"])})
            else:
                sanitized.append({"role": m["role"], "content": m.get("content", "")})

        if len(sanitized) > max_history_turns * 2:
            sanitized = sanitized[-max_history_turns * 2 :]

        messages = sanitized + [{"role": "user", "content": user_msg}]

        # Fase 1: ciclo de tools (silencioso para el usuario salvo verbose).
        messages = _run_tool_cycle(client, messages, verbose=verbose)

        # Fase 2: respuesta conversacional final en streaming.
        print("\nTailo: ", end="", flush=True)
        final, ttft = _stream_final(client, messages)

        # Memoria: solo guardamos el par user/assistant final, sin tool calls,
        # para que un error de tool no se "pegue" como verdad en turnos futuros.
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": final})

        srcs = sorted({c.metadata.get("source", "?") for c in chunks})
        print(
            f"[fuentes: {', '.join(srcs)} | TTFT {round(ttft or 0, 2)}ms "
            f"| recuperacion {ret_lat['ms_total']}ms "
            f"| historial {len(history)//2} turno(s)]\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--k", type=int, default=TOP_K)
    parser.add_argument("--quiet", action="store_true", help="Oculta el log de tool_calls.")
    args = parser.parse_args()

    if args.query:
        answer(args.query, top_k=args.k, verbose=not args.quiet)
    else:
        repl(top_k=args.k, verbose=not args.quiet)
