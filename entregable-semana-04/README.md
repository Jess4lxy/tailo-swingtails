# Tailo Agent - Semana 04 (Memoria Persistente + Multi-sesion)

Continuacion del entregable de la semana 03 (RAG + Function Calling). Agrega
**persistencia de memoria conversacional**: el backend deja de ser *stateless*
y mantiene conversaciones multi-turno **no volatiles** y **multi-sesion** en
SQLite, diferenciadas por un `conversation_id` (UUID).

- **Doc API:** `https://swingtails-api-yz02.onrender.com/api-docs/`
- **Base API:** `https://swingtails-api-yz02.onrender.com`

El conocimiento estatico (RAG con ChromaDB) y las tools se heredan de las
semanas 02-03; el entregable es **autonomo**: corpus, base vectorial y BD de
sesiones viven dentro de esta carpeta.

> **Lo nuevo de esta semana** (detalle completo en `INFORME-semana-04.md`):
> - `src/sessions.py` — store SQLite + gestion de ventana de contexto.
> - `conversation_id` (UUID) para identificar/diferenciar hilos.
> - Ventana deslizante + **resumen** para no desbordar `num_ctx` (16384).
> - La memoria **nunca** guarda errores de tools (anti *state poisoning*).

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

## Estructura

```
entregable-semana-04/
├── Modelfile.tailo-agent      # llama3.1:8b + system prompt; num_ctx 16384
├── requirements.txt
├── .env.example
├── README.md                  # este archivo
├── INFORME-semana-04.md       # documento auditable + Bitacora de Decisiones
├── corpus/                    # fuentes del RAG
├── chroma_db/                 # base vectorial persistente (ya generada)
├── data/                      # sessions.db (SQLite, runtime; en .gitignore)
└── src/
    ├── config.py              # rutas, JWT, Ollama, presupuesto de contexto
    ├── sessions.py            # *** memoria: store SQLite + ventana de contexto
    ├── inspect_sessions.py    # inspector de la BD de sesiones
    ├── _smoke_sessions.py     # prueba de humo de la memoria (sin Ollama)
    ├── ingest.py              # (re)genera chroma_db desde corpus/
    ├── inspect_db.py          # inspector de la base vectorial
    ├── retrieve.py            # RAG (lee chroma_db local)
    ├── api_client.py          # cliente HTTP con JWT (por-peticion) y errores
    ├── tools.py               # 15 funciones + esquemas + dispatcher
    ├── chat.py                # REPL multi-sesion: RAG + tool cycle + memoria
    └── server.py              # servicio HTTP con conversation_id (FastAPI)
```

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
