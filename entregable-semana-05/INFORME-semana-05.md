# Informe Semana 04 — Persistencia de Memoria en el Agente Tailo

**Materia:** Desarrollo Web Integral — UTM, Cuatrimestre 9
**Proyecto:** SwingTails / Tailo (asistente de mascotas, IA local con Ollama + Llama 3.1)
**Fase:** Transición de interacciones *stateless* (un turno) a conversaciones
*stateful* multi-turno con **memoria persistente y multi-sesión**.

Este documento describe la arquitectura de **Gestión de Estado de Sesión**
implementada en el backend local, el modelo de datos, el flujo de control para
diferenciar hilos de chat, y la **Bitácora de Decisiones** con sus referencias
(punto F de la rúbrica).

---

## 0. Punto de partida (qué había en la semana 03)

En la semana 03 el agente ya hacía RAG + Function Calling, pero la memoria era
**responsabilidad del cliente**: el endpoint `POST /chat` recibía un campo
`history` con los turnos previos y el REPL guardaba el historial en una lista
en RAM que se perdía al cerrar el proceso. Es decir: **persistencia volátil** y
sin diferenciación real de sesiones en el servidor.

La semana 04 mueve la memoria **al backend** y la hace **no volátil y
multi-sesión**: cada conversación es un hilo identificable, recuperable y
aislado por usuario, almacenado en SQLite.

---

## 1. Arquitectura de persistencia

```
┌──────────────┐   POST /chat {message, conversation_id?}    ┌────────────────────────┐
│  Cliente     │ ──────────────────────────────────────────▶ │  server.py (FastAPI)   │
│ (REPL / app  │ ◀────────────────────────────────────────── │  - resuelve conv_id    │
│  SwingTails) │     {reply, conversation_id, turns, ...}      │  - build_context()     │
└──────────────┘                                              │  - tool cycle (efímero)│
                                                              │  - append_turn()       │
                                                              │  - compact()           │
                                                              └───────────┬────────────┘
                                                                          │
                                                       sessions.py (capa de memoria)
                                                                          │
                                                              ┌───────────▼────────────┐
                                                              │  SQLite  data/sessions.db│
                                                              │  conversations / messages│
                                                              │  (persistencia NO volátil)│
                                                              └─────────────────────────┘
```

La capa de memoria (`src/sessions.py`) es **agnóstica al LLM y a las tools**:
solo conoce conversaciones y mensajes. El orquestador (`chat.py` para el REPL,
`server.py` para HTTP) la usa para recuperar el contexto, persistir el turno y
compactar la ventana.

**Componentes nuevos de esta fase:**

| Archivo | Rol |
|---|---|
| `src/sessions.py` | Store SQLite + gestión de ventana de contexto (núcleo del entregable). |
| `src/config.py` | Añade ruta de la BD y los parámetros de la ventana de contexto. |
| `src/server.py` | `/chat` con `conversation_id`; endpoints de listar/ver/borrar conversaciones. |
| `src/chat.py` | REPL multi-sesión con comandos `nueva`, `sesiones`, `abrir`, `borrar`, `historial`. |
| `src/inspect_sessions.py` | Inspector de la BD para la demo. |
| `src/_smoke_sessions.py` | Prueba de humo de la lógica de memoria (sin Ollama). |

---

## 2. Modelo de datos (estructura de la memoria)

Dos tablas en SQLite (`data/sessions.db`):

```sql
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,          -- UUID = conversation_id
    user_id     INTEGER,                   -- dueño (extraído del JWT)
    title       TEXT NOT NULL DEFAULT 'Nueva conversación',
    summary     TEXT NOT NULL DEFAULT '',  -- resumen acumulado (ventana de contexto)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    summarized      INTEGER NOT NULL DEFAULT 0,  -- 1 = ya plegado en summary
    created_at      TEXT NOT NULL
);
```

Decisiones de diseño del esquema:

- **`id` UUID en `conversations`** → es el `conversation_id` que viaja en cada
  petición. Identificador opaco, no adivinable, independiente del usuario.
- **`user_id`** → permite **aislar** conversaciones por dueño. El backend nunca
  devuelve un hilo de otro usuario (ver §3.A).
- **`summary`** → guarda el resumen acumulado de los turnos antiguos
  (estrategia de *summarization*, ver §3.D).
- **`messages.summarized`** → marca los mensajes ya condensados en `summary`.
  **No se borran**: la tabla `messages` es una **bitácora íntegra y auditable**
  del hilo; lo que cambia es solo qué se envía crudo al modelo.
- **`ON DELETE CASCADE` + índices** sobre `(conversation_id, id)` y
  `(user_id, updated_at)` → borrado consistente y listados rápidos.

---

## 3. Flujo de control (mapeo punto por punto con la rúbrica)

### A. Mecanismo de identificación de sesión (`conversation_id`)

HTTP es **sin estado**: cada petición es independiente. Para simular una charla
continua, el servidor asocia un `conversation_id` (UUID v4) a un hilo:

- El cliente envía `conversation_id` en el cuerpo de `POST /chat`.
- **Si falta**, si manda `new_session: true`, o **si el id no existe / no
  pertenece al usuario** → el backend **genera un UUID nuevo**, inicializa el
  hilo y **lo devuelve** en la respuesta.
- Si envía un id **existente y propio** → recupera el historial y continúa.

```python
conv_id = req.conversation_id
if req.new_session or not conv_id or not sessions.conversation_exists(conv_id, user_id):
    conv_id = sessions.create_conversation(user_id=user_id)
```

`conversation_exists(conv_id, user_id)` valida **pertenencia**: un usuario no
puede continuar ni leer la conversación de otro (aislamiento multi-usuario).

### B. Almacenamiento del historial (Chat History Store)

**SQLite en disco** → persistencia **no volátil**: el historial sobrevive a
reinicios o caídas del proceso del backend (a diferencia de un `dict` en RAM,
que es volátil y se pierde). Se persiste **solo el par `user`/`assistant`
limpio** de cada turno (sin el bloque RAG inyectado, sin `tool_calls`, sin
`role:"tool"`). Ver justificación de resiliencia en §3.E.

La prueba de humo (`_smoke_sessions.py`) verifica explícitamente que el estado
sobrevive a una recarga del módulo (simulación de reinicio).

### C. Lógica de flujo de *prompting* (inferencia con contexto acumulado)

Un LLM no tiene memoria interna; hay que **recrear** el contexto en cada turno
enviando la lista ordenada de mensajes con sus roles (`system`, `user`,
`assistant`, `tool`). Por turno:

1. Se recupera el buffer histórico con `sessions.build_context(conv_id)`
   = `[resumen como system?] + [turnos recientes user/assistant]`.
2. Se le **concatena el mensaje nuevo** del usuario — el único que lleva el
   bloque RAG y la fecha del día (lo demás no debe re-inyectarse cada turno).
3. Se envía el buffer completo a Ollama (`/api/chat`).

```python
context  = sessions.build_context(conv_id)               # resumen + recientes
messages = context + [{"role": "user", "content": rag_msg}]
messages = _run_tool_cycle(client, messages)             # fase 1: tools
reply    = _generate_final(messages)                     # fase 2: respuesta
sessions.append_turn(conv_id, pregunta_limpia, reply)    # persiste el turno
```

### D. Gestión de la ventana de contexto (Context Window Management)

**Estrategia activa: ventana deslizante + resumen (*summarization*).** No se
envía el buffer completo sin control; se mantiene acotado de forma determinista.

**Presupuesto de tokens (justificación cuantitativa):**

El Modelfile fija `PARAMETER num_ctx 16384` para `tailo-agent`. De esos 16 384
tokens hay que descontar lo que **no** es historial conversacional y viaja en
cada turno:

| Componente del prompt | Tokens (aprox.) |
|---|---:|
| System prompt del Modelfile | ~1 500 |
| Esquemas de las 15 tools que Ollama serializa al modelo | ~3 000 |
| Bloque RAG inyectado en el mensaje del usuario | ~1 000 |
| Respuesta a generar (`num_predict 512`) | 512 |
| Margen de seguridad | resto |
| **Reservado (`CONTEXT_RESERVED_TOKENS`)** | **6 000** |
| **Presupuesto para historial (`HISTORY_TOKEN_BUDGET`)** | **≈ 10 240** |

Estimación de tokens: heurística conservadora **~4 caracteres por token** para
texto mixto español/inglés con el tokenizador BPE de Llama 3, más ~4 tokens de
overhead por mensaje (marcadores de rol). Suficiente para **evitar el desborde**
sin necesitar el conteo exacto.

**Mecánica (`sessions.compact`):** cuando los tokens del historial **activo**
superan `COMPACT_THRESHOLD_TOKENS` (= presupuesto), los mensajes más antiguos se
**condensan en el `summary`** acumulado (vía el propio LLM local, temperatura 0,
`num_predict 256`) hasta bajar a `COMPACT_TARGET_TOKENS` (60 % del presupuesto),
**preservando siempre** los últimos `KEEP_RECENT_MESSAGES` (= 6, tres turnos).
Los mensajes plegados se marcan `summarized=1` (no se borran).

`build_context` antepone ese resumen como mensaje `system` y, como red de
seguridad **determinista**, aplica un recorte final por tokens si aún excediera
el presupuesto. Así el desborde de `num_ctx` queda **prevenido por construcción**.

> Por qué importa: superar `num_ctx` causa errores de la API o pérdida del
> system prompt; además, contextos largos disparan el *Time To First Token*
> (TTFT) y saturan la VRAM de la GPU local. La compactación mantiene el TTFT
> estable aunque la conversación crezca indefinidamente.

### E. Resiliencia de memoria ante fallos (anti *state poisoning*)

Si una tool falla, su error **no debe quedar grabado como "verdad"** en la
memoria de largo plazo. Dos capas de defensa:

1. **El error se captura, no crashea.** `api_client` devuelve `{"error": "..."}`
   en vez de lanzar excepción; `tools.execute_tool` lo serializa. El backend no
   se cae (no hay *crash del servidor*); el LLM lee el error y se lo explica al
   usuario.
2. **El error nunca se persiste.** Los mensajes `role:"tool"` y los `assistant`
   con `tool_calls` viven **solo en el buffer efímero de ese turno**; jamás
   tocan SQLite. `sessions.append_message` **rechaza explícitamente** cualquier
   rol distinto de `user`/`assistant` (lanza `ValueError`). Verificado en la
   prueba de humo.

Resultado: el turno siguiente parte de una memoria limpia → el modelo no relee
su propio error ni reintenta en bucle la misma llamada fallida (se rompe el
*bucle infinito de fallos* descrito en la rúbrica). El ciclo de tools del turno
en curso, además, tiene `max_iters=4` como cortafuegos adicional.

---

## 4. Interfaz (cómo se demuestra el flujo en vivo)

### Servicio HTTP (`server.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado y modelo. |
| `POST` | `/chat` | Conversa; crea/continúa hilo. Devuelve `conversation_id`, `turns`, `compacted`. |
| `GET` | `/conversations` | Lista las conversaciones **del usuario** (del JWT). |
| `GET` | `/conversations/{id}` | Historial completo + resumen de un hilo propio. |
| `DELETE` | `/conversations/{id}` | Borra un hilo propio. |

Demostración del **mantenimiento de contexto** (dos turnos, mismo hilo):

```bash
# Turno 1: no mando id -> el backend crea uno y lo devuelve
curl -s -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"message": "Mi perro se llama Toby y es un labrador"}'
# -> {"reply":"...", "conversation_id":"7a11e532-...", "turns":1, ...}

# Turno 2: reenvío el conversation_id -> recuerda a Toby
curl -s -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"message": "¿Qué raza te dije que era?", "conversation_id": "7a11e532-..."}'
# -> "...me dijiste que Toby es un labrador."
```

Diferenciación de hilos: una segunda llamada **sin** `conversation_id` (o con
`new_session:true`) arranca un hilo limpio que **no** conoce a Toby.

### REPL (`src/chat.py`)

Memoria persistente y multi-sesión desde consola. Comandos: `nueva`,
`sesiones`, `abrir <id>`, `borrar <id>`, `titulo <texto>`, `historial`,
`verbose on/off`, `login`, `whoami`, `salir`. Al arrancar **retoma** la última
conversación del usuario (demuestra la no volatilidad).

### Inspección

```powershell
python src\inspect_sessions.py            # lista conversaciones de la BD
python src\inspect_sessions.py 7a11e532   # detalle + bitácora íntegra de un hilo
```

---

## 5. Bitácora de Decisiones (punto F)

| # | Decisión | Alternativas consideradas | Justificación (basada en evidencia) |
|---|---|---|---|
| 1 | **SQLite embebida** para el Chat History Store | Dict en RAM; PostgreSQL | El dict es **volátil** (se pierde al reiniciar). PostgreSQL exige un servidor aparte, innecesario para un agente **local**. SQLite es embebida (sin servidor), **ACID/durable** y la propia documentación la recomienda como reemplazo de archivos *ad-hoc* y para apps locales. [ref. 4, 5] |
| 2 | **`num_ctx = 16384`** como ventana del modelo | 8192 (default de Ollama); 128K (máximo de Llama 3.1) | Llama 3.1 soporta hasta **128K** [ref. 3], pero Ollama por defecto usa **2048/8192** y subir `num_ctx` **multiplica el uso de VRAM/KV-cache** [ref. 2]. 16384 es el punto medio que cabe en GPU de consumo y deja margen para system + 15 tools + RAG. |
| 3 | **Presupuesto de historial ≈ 10 240 tok** (16384 − 6000 reservados) | Enviar el buffer completo | Enviar todo desborda `num_ctx` y dispara el TTFT [ref. 2]. El cálculo reserva system (~1.5k) + tools (~3k) + RAG (~1k) + `num_predict` (512) + margen. |
| 4 | **Ventana deslizante + resumen** (no solo recorte duro) | Recorte simple (descartar viejos); solo resumen | El recorte pierde datos tempranos (nombre de la mascota dicho al inicio). El resumen los **conserva condensados**. Combinados: contexto acotado **sin** amnesia. Patrón estándar de *memory* en frameworks de agentes [ref. 6]. |
| 5 | **No persistir `role:"tool"` ni `tool_calls`** | Guardar el historial crudo completo | Persistir un error de tool **envenena la memoria** y provoca reintentos en bucle (rúbrica, punto E). Solo se guarda el par `user`/`assistant` limpio. |
| 6 | **Aislamiento por `user_id`** (del JWT) | Una sola memoria global | Multi-usuario real: cada quien ve solo sus hilos. El `user_id` sale del JWT (no lo provee el modelo), heredando el modelo de identidad de la semana 03 [ref. 1]. |
| 7 | **Heurística 4 chars/token** | Tokenizador exacto (tiktoken/HF) | Para *prevenir* desbordes basta una cota conservadora; un tokenizador exacto añade dependencia pesada sin beneficio para el recorte. |

---

## 6. Referencias de investigación

1. **Ollama — API (`/api/chat`, roles, `tools`).**
   https://github.com/ollama/ollama/blob/main/docs/api.md
2. **Ollama — Modelfile (`PARAMETER num_ctx`, defaults) y notas de contexto/VRAM.**
   https://github.com/ollama/ollama/blob/main/docs/modelfile.md ·
   https://github.com/ollama/ollama/blob/main/docs/faq.md
3. **Meta — Llama 3.1 (ventana de contexto 128K).**
   https://ai.meta.com/blog/meta-llama-3-1/ · https://ollama.com/library/llama3.1
4. **SQLite — *Appropriate Uses For SQLite* (cuándo usarla; apps locales).**
   https://www.sqlite.org/whentouse.html
5. **SQLite — *Atomic Commit* (durabilidad / ACID en disco).**
   https://www.sqlite.org/atomiccommit.html
6. **LangChain — *Memory / Message History* (patrón de buffer + summarization).**
   https://python.langchain.com/docs/concepts/chat_history/
7. **IETF RFC 9110 — *HTTP Semantics* (HTTP es un protocolo sin estado).**
   https://www.rfc-editor.org/rfc/rfc9110

---

## 7. Cómo correrlo

```powershell
cd entregable-semana-04
ollama create tailo-agent -f Modelfile.tailo-agent     # si no existe ya
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# REPL con memoria persistente y multi-sesión
python src\chat.py

# Servicio HTTP
uvicorn server:app --port 8000        # desde src/  (o: python src\server.py)

# Prueba de la lógica de memoria (sin Ollama)
python src\_smoke_sessions.py
```

La BD se crea sola en `data/sessions.db` al primer turno (está en `.gitignore`:
es estado de runtime, no fuente).
