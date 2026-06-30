"""Prueba de humo de sessions.py (sin Ollama). Usa un summarizer-stub.

Valida: CRUD, persistencia, aislamiento por user_id, resiliencia (rechazo de
roles tool), build_context, y ventana deslizante + resumen (compact).
"""
import os
import tempfile

# Apunta la BD a un archivo temporal ANTES de importar sessions/config.
_tmp = os.path.join(tempfile.gettempdir(), "tailo_smoke_sessions.db")
if os.path.exists(_tmp):
    os.remove(_tmp)
os.environ["TAILO_SESSIONS_DB"] = _tmp

import sessions  # noqa: E402

sessions.init_db()

# --- A. conversation_id + B. persistencia ---------------------------------
c1 = sessions.create_conversation(user_id=29, title="Mascotas de Ana")
c2 = sessions.create_conversation(user_id=42, title="Otro usuario")
assert sessions.conversation_exists(c1, 29)
assert not sessions.conversation_exists(c1, 42), "fuga entre usuarios!"
assert not sessions.conversation_exists("no-existe", 29)
print("OK  A/B  creacion + aislamiento por user_id")

sessions.append_turn(c1, "Tengo un perro llamado Toby", "Anotado: Toby es tu perro.")
sessions.append_turn(c1, "Como esta el clima?", "No tengo datos del clima.")
msgs = sessions.get_active_messages(c1)
assert len(msgs) == 4 and msgs[0]["content"].startswith("Tengo un perro")
print("OK  B    persistencia y orden de mensajes")

# --- E. resiliencia: no se persisten roles tool ---------------------------
try:
    sessions.append_message(c1, "tool", '{"error": "API 500"}')
    raise SystemExit("FALLO: acepto un mensaje role=tool")
except ValueError:
    print("OK  E    rechaza persistir role=tool (anti state-poisoning)")

# --- listado / aislamiento en list ----------------------------------------
assert len(sessions.list_conversations(user_id=29)) == 1
assert len(sessions.list_conversations(user_id=42)) == 1
assert len(sessions.list_conversations(user_id=None)) == 2
print("OK       list_conversations filtra por usuario")

# --- D. ventana de contexto: ventana deslizante + resumen -----------------
# Bajamos los umbrales en caliente para forzar compactacion con poco texto.
sessions.COMPACT_THRESHOLD_TOKENS = 80
sessions.COMPACT_TARGET_TOKENS = 40
sessions.KEEP_RECENT_MESSAGES = 2

c3 = sessions.create_conversation(user_id=29, title="Larga")
for i in range(8):
    sessions.append_turn(c3, f"pregunta numero {i} con algo de relleno", f"respuesta {i} con relleno")

folded = []

def stub_summarizer(old_msgs, prev):
    folded.append(len(old_msgs))
    return (prev + " | " if prev else "") + f"[resumen de {len(old_msgs)} msj]"

before = sessions.get_active_messages(c3)
info = sessions.compact(c3, summarizer=stub_summarizer)
after = sessions.get_active_messages(c3)

assert info["compacted"], "no compacto pese a exceder umbral"
assert len(after) < len(before), "no redujo el historial activo"
assert len(after) >= sessions.KEEP_RECENT_MESSAGES, "borro mensajes recientes"
assert sessions.get_summary(c3), "no genero resumen"
# La bitacora completa sigue integra (no se borra nada):
assert len(sessions.get_all_messages(c3)) == 16
print(f"OK  D    compact: {len(before)}->{len(after)} msj activos, "
      f"resumen='{sessions.get_summary(c3)}', bitacora intacta (16)")

# --- C. build_context: resumen como system + activos ----------------------
ctx = sessions.build_context(c3)
assert ctx[0]["role"] == "system" and "[Resumen" in ctx[0]["content"]
assert all(m["role"] in {"system", "user", "assistant"} for m in ctx)
print(f"OK  C    build_context -> {len(ctx)} mensajes (system+activos)")

# --- persistencia NO volatil: reabrir simulando reinicio ------------------
import importlib
importlib.reload(sessions)
sessions.init_db()
assert sessions.conversation_exists(c1, 29), "no persistio tras 'reinicio'"
assert len(sessions.get_active_messages(c1)) == 4
print("OK       persistencia no volatil (sobrevive recarga del modulo)")

# --- borrado --------------------------------------------------------------
sessions.delete_conversation(c2)
assert not sessions.conversation_exists(c2, 42)
print("OK       delete_conversation")

print("\nTODAS LAS PRUEBAS PASARON")
