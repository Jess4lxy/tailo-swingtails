# Tailo Agent - Semana 05 (Streaming + Voz + Seguridad + Observabilidad)

Continuacion del entregable de la semana 04 (memoria persistente multi-sesion).
Esta semana el backend queda **listo para integrarse con el frontend**: expone
respuestas en **streaming** (SSE) con **fases del agente** para indicadores de
carga, una **capa de seguridad** (guardrails anti prompt-injection), **voz**
(Speech-to-Text local con Whisper) y una **bitacora de observabilidad** que mide
TTFT, latencia, tokens/segundo y traza de herramientas.

> El **frontend lo desarrolla otra persona**; este repo es el backend/IA que
> consume. La seccion [Contrato de eventos SSE](#contrato-de-eventos-sse-para-el-frontend)
> documenta exactamente lo que el cliente recibe.

- **Doc API:** `https://swingtails-api-yz02.onrender.com/api-docs/`
- **Base API:** `https://swingtails-api-yz02.onrender.com`

El conocimiento estatico (RAG con ChromaDB) y las tools se heredan de las
semanas 02-03; el entregable es **autonomo**: corpus, base vectorial y BD de
sesiones/observabilidad viven dentro de esta carpeta.

> **Lo nuevo de la semana 05** (detalle completo en `INFORME-semana-05.md`):
> - `POST /chat/stream` — **streaming SSE** token por token + eventos de **fase**
>   (`searching` / `thinking` / `executing` / `generating`) para que el front
>   pinte indicadores de carga dinamicos segun el estado interno del agente.
> - `src/guardrails.py` — **capa de seguridad** preventiva: detecta inyeccion de
>   prompts (fuga de instrucciones, jailbreak, spam) con reglas heuristicas
>   **antes** de invocar el LLM (no gasta inferencia en ataques).
> - `src/observability.py` — **bitacora SQLite de auditoria**: una fila por
>   interaccion con `ttft_ms`, `total_latency_ms`, `tokens_per_second`,
>   `was_blocked` y `tools_executed` (JSON con nombre/parametros/estado).
> - `POST /transcribe` — **voz**: Whisper local (faster-whisper, CPU int8) para
>   no competir por la VRAM que ocupa Llama 3.1.
> - **CORS** habilitado (el front corre en otro origen).
>
> **Lo heredado de la semana 04:** memoria conversacional persistente y
> multi-sesion en SQLite (`src/sessions.py`), `conversation_id` (UUID), ventana
> deslizante + resumen para no desbordar `num_ctx`, y anti *state poisoning*
> (la memoria nunca guarda errores de tools). **Todo sigue activo.**

## Requisitos previos

1. Ollama corriendo localmente con los modelos `llama3.1:8b` y
   `nomic-embed-text` descargados.
2. La base vectorial `chroma_db/` (ya incluida). Si quieres regenerarla:
   `python src/ingest.py` (lee `corpus/` y necesita Ollama + `nomic-embed-text`).
3. Una cuenta valida en la API de SwingTails (registrarla via
   `POST /api/auth/register` o usar una existente). La contrasena debe tener
   **mayuscula + minuscula + numero + simbolo** (p.ej. `Password123!`).
   El agente no tiene usuario propio: **actua en nombre del usuario que inicia
   sesion**, y toma su `user_id` del JWT para todas las operaciones.

## Setup

```powershell
cd entregable-semana-04

# 1. Crear el modelo Ollama personalizado
ollama create tailo-agent -f Modelfile.tailo-agent

# 2. Variables de entorno
copy .env.example .env
# editar .env y poner SWINGTAILS_EMAIL + SWINGTAILS_PASSWORD
# (o SWINGTAILS_JWT si ya tienes un token)

# 3. Entorno Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # en PowerShell (NO "activate")
pip install -r requirements.txt
```

> Atajo: `.\setup.ps1` hace los 3 pasos (modelo + .env + venv + deps) y, si
> falta `chroma_db/`, la regenera con `ingest.py`.

## Uso

```powershell
# REPL interactivo con memoria PERSISTENTE y multi-sesion
python src\chat.py

# Una sola pregunta (one-shot, sin memoria)
python src\chat.py "Cuales son mis mascotas registradas?"

# Modo silencioso (oculta el log de tool_calls)
python src\chat.py --quiet

# Inspeccionar la BD de sesiones
python src\inspect_sessions.py            # lista conversaciones
python src\inspect_sessions.py <id-corto> # detalle + bitacora de un hilo
```

Al arrancar el REPL, si hay `SWINGTAILS_EMAIL`/`SWINGTAILS_PASSWORD` en el
`.env` se inicia sesion automaticamente; si no, se pide login interactivo.
El `user_id` para registrar mascotas, agendar citas o el carrito se toma de esa
sesion (no se le pregunta al usuario ni lo inventa el modelo). El REPL **retoma
la ultima conversacion** del usuario (la memoria sobrevive al reinicio).

Comandos dentro del REPL:
- `salir` / `exit` - cierra
- `nueva` - inicia OTRA conversacion (nuevo `conversation_id`)
- `sesiones` - lista las conversaciones guardadas del usuario
- `abrir <id>` - retoma una conversacion por su id corto
- `borrar <id>` - elimina una conversacion
- `titulo <texto>` - renombra la conversacion activa
- `historial` - muestra los turnos del hilo (incluye lo ya resumido)
- `verbose on` / `verbose off` - alterna el log de tools
- `login` - inicia/renueva sesion con otro usuario
- `whoami` - muestra el id del usuario de la sesion activa

## Modo servicio HTTP (multiusuario + memoria server-side)

Para integrarlo con la app de SwingTails, Tailo se expone como servicio. La app
**reenvia el JWT** del usuario en cada peticion (Tailo nunca pide credenciales;
el token se aisla por peticion con ContextVar). **La memoria la mantiene el
backend**: la app ya no envia `history`, solo el `conversation_id`.

```powershell
# desde la carpeta src/
uvicorn server:app --port 8000
#  o:  python src\server.py
```

```bash
# Turno 1: sin conversation_id -> el backend crea el hilo y lo devuelve
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <jwt-del-usuario>" -H "Content-Type: application/json" \
  -d '{"message": "mi perro Toby es labrador"}'
# -> {"reply":"...", "conversation_id":"7a11e532-...", "user_id":29, "turns":1, "compacted":false}

# Turno 2: reenvio el conversation_id -> recuerda a Toby
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <jwt-del-usuario>" -H "Content-Type: application/json" \
  -d '{"message":"que raza te dije?", "conversation_id":"7a11e532-..."}'
```

Otros endpoints (todos aislados por usuario via JWT):
`GET /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}`.

## Contrato de eventos SSE (para el frontend)

`POST /chat/stream` responde `text/event-stream`. Mismo body que `/chat`
(`{message, conversation_id?, new_session?}`) y mismo header
`Authorization: Bearer <jwt>`. El servidor emite estos eventos:

| `event:` | `data:` (JSON) | Para que sirve en la UI |
|----------|----------------|--------------------------|
| `phase`  | `{phase, detail, tool?}` | Indicador de carga dinamico. `phase` ∈ `searching` (RAG), `thinking` (inferencia/decision), `executing` (ejecutando una tool; trae `tool` y `detail` legible p.ej. "Agendando tu cita…"), `generating` (empezando la respuesta). |
| `token`  | `{text}` | Un fragmento de la respuesta. Concatenar para el efecto "maquina de escribir". |
| `blocked`| `{message, reason}` | El guardrail bloqueo la entrada: **no hubo LLM**. Mostrar `message` tal cual. |
| `done`   | `{conversation_id, user_id, sources, turns, ttft_ms, total_latency_ms, tokens_per_second, compacted, blocked}` | Fin del turno. Guardar `conversation_id` para el siguiente mensaje. |
| `error`  | `{message}` | Fallo inesperado durante la generacion. |

Ejemplo de consumo en el navegador (la respuesta es un POST con stream, asi que
se usa `fetch` + `ReadableStream`, no `EventSource`):

```js
const resp = await fetch("http://localhost:8000/chat/stream", {
  method: "POST",
  headers: { "Authorization": `Bearer ${jwt}`, "Content-Type": "application/json" },
  body: JSON.stringify({ message, conversation_id }),
});
const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
// parsear los bloques "event: <tipo>\ndata: <json>\n\n":
//   phase    -> actualizar el indicador de estado
//   token    -> append al globo del mensaje
//   blocked  -> pintar el mensaje de seguridad
//   done     -> guardar conversation_id y soltar el spinner
```

### Voz (Speech-to-Text)

`POST /transcribe` recibe `multipart/form-data` con el campo `audio` (el blob del
microfono) y header `Authorization`. Devuelve `{text, language, duration_ms}`.
El front manda el `text` a `/chat/stream`. Whisper corre en CPU (int8): si
`faster-whisper` no esta instalado, el endpoint responde `503` con instrucciones.

## Publicar el backend para el frontend (esquema "todo local + tunel")

La IA (Ollama) y el backend corren en el equipo local; un tunel expone el puerto
8000 a internet con **URL fija** para que el front se conecte desde cualquier lado:

```
front  ->  https://<dominio>.ngrok-free.dev  ->  (ngrok)  ->  localhost:8000  ->  Ollama
```

```powershell
# requiere: ngrok config add-authtoken <token>  (una vez) y un dominio estatico
.\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev
```

> **IMPORTANTE para el front (plan gratis de ngrok):** ademas del `Authorization`,
> cada peticion debe llevar el header **`ngrok-skip-browser-warning: true`** (con
> cualquier valor) para saltar la pagina de aviso de ngrok. Si no, ngrok devuelve
> un HTML de advertencia en vez del JSON del backend.

Alternativa sin cuenta/URL fija: `.\serve-public.ps1` (Cloudflare quick tunnel,
URL aleatoria que cambia en cada arranque). Util para una demo puntual.

## Estructura

```
entregable-semana-05/
├── Modelfile.tailo-agent      # llama3.1:8b + system prompt; num_ctx 16384
├── requirements.txt           # + faster-whisper, python-multipart
├── .env.example
├── README.md                  # este archivo
├── INFORME-semana-05.md       # documento auditable + Bitacora de Decisiones
├── corpus/                    # fuentes del RAG
├── chroma_db/                 # base vectorial persistente (ya generada)
├── data/                      # sessions.db + observability.db (runtime; gitignore)
└── src/
    ├── config.py              # rutas, JWT, Ollama, contexto, Whisper, CORS
    ├── sessions.py            # memoria: store SQLite + ventana de contexto
    ├── guardrails.py          # *** seguridad: deteccion de prompt-injection
    ├── observability.py       # *** bitacora SQLite de auditoria (TTFT/tps/tools)
    ├── inspect_sessions.py    # inspector de la BD de sesiones
    ├── inspect_observability.py # inspector de la bitacora de observabilidad
    ├── _smoke_sessions.py     # prueba de humo de la memoria (sin Ollama)
    ├── _smoke_security.py     # prueba de humo de guardrails + observabilidad
    ├── ingest.py              # (re)genera chroma_db desde corpus/
    ├── inspect_db.py          # inspector de la base vectorial
    ├── retrieve.py            # RAG (lee chroma_db local)
    ├── api_client.py          # cliente HTTP con JWT (por-peticion) y errores
    ├── tools.py               # 15 funciones + esquemas + dispatcher
    ├── chat.py                # REPL multi-sesion: RAG + tool cycle + memoria
    └── server.py              # *** servicio HTTP: /chat, /chat/stream, /transcribe
```

## Seguridad y observabilidad (semana 05)

```powershell
# Prueba de humo de guardrails + observabilidad (NO requiere Ollama)
python src\_smoke_security.py

# Ver la bitacora de auditoria (para las capturas del informe)
python src\inspect_observability.py            # ultimas 20 interacciones
python src\inspect_observability.py --stats    # promedios TTFT/latencia/tps, % bloqueo
```

La tabla `observability_logs` (en `data\observability.db`) registra por cada
interaccion: `id`, `session_id`, `timestamp`, `user_prompt`, `system_response`,
`ttft_ms`, `total_latency_ms`, `tokens_per_second`, `was_blocked` y
`tools_executed` (JSON `[{name, parameters, status: SUCCESS|ERROR}]`).

## Lista de funciones (15)

Lectura:
`list_my_pets`, `get_pet`, `list_clinics`, `list_appointments`,
`list_products`, `get_product`, `list_clinic_reviews`, `get_clinic_rating`.

Escritura:
`register_pet`, `update_pet`, `delete_pet`, `book_appointment`,
`reschedule_appointment`, `cancel_appointment`, `review_clinic`.

> **Esquemas verificados contra el codigo real del backend.** Ajustamos las
> tools al contrato que implementa el servidor (no al Swagger, que difiere):
> - Citas: `book_appointment` recibe NOMBRES (`pet_name`, `clinic_name`,
>   `service_name`, `appointment_date`, `hour`) y resuelve los ids reales en
>   codigo (el modelo no inventa ids); si un nombre no existe, devuelve las
>   opciones disponibles. `list_appointments` consulta `/api/appointments/user`.
> - Reseñas: son de **clinicas** en `/api/veterinary-reviews`
>   (`list_clinic_reviews`, `get_clinic_rating`, `review_clinic`).
> - Mascotas: `register_pet` exige `specie`; `update_pet` es reemplazo completo.
>
> Se retiraron `add_to_cart`, `view_cart` y `purchase_history`: el backend
> desplegado **no implementa** carrito ni ordenes (no existen esos
> controladores/rutas en el codigo). `reschedule_appointment`/`cancel_appointment`
> pueden devolver 500 intermitente por inestabilidad de la BD de Render.

Detalles de las tools y su mapeo a endpoints en las semanas 02-03. La
arquitectura de **memoria persistente** de esta semana, con su Bitacora de
Decisiones y referencias, esta en `INFORME-semana-04.md`.
