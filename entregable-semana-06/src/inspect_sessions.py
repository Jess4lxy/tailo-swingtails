"""Inspector de la base de datos de sesiones (data/sessions.db).

Util para la demo: muestra las conversaciones persistidas, sus metadatos y
opcionalmente el historial completo de una (incluyendo lo ya resumido).

Uso:
    python src/inspect_sessions.py                 # lista todas las conversaciones
    python src/inspect_sessions.py <id-corto>      # detalle + historial de una
"""
from __future__ import annotations

import sys

import sessions
from config import (
    COMPACT_THRESHOLD_TOKENS,
    HISTORY_TOKEN_BUDGET,
    SESSIONS_DB,
)


def _list_all() -> None:
    convs = sessions.list_conversations(user_id=None, limit=200)
    print(f"BD: {SESSIONS_DB}")
    print(f"Presupuesto de historial: {HISTORY_TOKEN_BUDGET} tok "
          f"| umbral de compactacion: {COMPACT_THRESHOLD_TOKENS} tok")
    print(f"{len(convs)} conversacion(es):\n")
    for c in convs:
        resumen = "si" if c["summary"] else "no"
        print(
            f"  {c['id'][:8]}  user={c['user_id']}  "
            f"msj={c['n_messages']:<3}  resumen={resumen}  "
            f"act={c['updated_at']}  | {c['title']}"
        )


def _detail(short: str) -> None:
    match = next(
        (c for c in sessions.list_conversations(user_id=None, limit=500)
         if c["id"].startswith(short)),
        None,
    )
    if not match:
        print(f"No encontre conversacion que empiece con '{short}'")
        return
    conv = sessions.get_conversation(match["id"])
    print(f"Conversacion {conv['id']}")
    print(f"  titulo : {conv['title']}")
    print(f"  user_id: {conv['user_id']}")
    print(f"  creada : {conv['created_at']}   actualizada: {conv['updated_at']}")
    if conv["summary"]:
        print(f"\n  [RESUMEN ACUMULADO]\n  {conv['summary']}\n")
    print("  Historial completo (bitacora integra):")
    for m in sessions.get_all_messages(conv["id"]):
        tag = " (resumido)" if m["summarized"] else ""
        print(f"    [{m['created_at']}] {m['role']}{tag}: {m['content'][:90]}")


if __name__ == "__main__":
    sessions.init_db()
    if len(sys.argv) > 1:
        _detail(sys.argv[1])
    else:
        _list_all()
