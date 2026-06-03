# Tailo Agent - Semana 03 (RAG + Function Calling)

Continuacion del entregable de la semana 02. Agrega *Function Calling* sobre
la API publica de SwingTails:

- **Doc:** `https://swingtails-api-yz02.onrender.com/api-docs/`
- **Base:** `https://swingtails-api-yz02.onrender.com`

El conocimiento estatico (RAG con ChromaDB) se hereda de la semana 02 pero el
entregable es **autonomo**: el corpus (`corpus/`) y la base vectorial
(`chroma_db/`) viven dentro de esta carpeta. No depende de `entregable-semana-02`.

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
cd entregable-semana-03

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
# REPL interactivo (con memoria conversacional)
python src\chat.py

# Una sola pregunta (sin memoria)
python src\chat.py "Cuales son mis mascotas registradas?"

# Modo silencioso (oculta el log de tool_calls)
python src\chat.py --quiet
```

Al arrancar el REPL, si hay `SWINGTAILS_EMAIL`/`SWINGTAILS_PASSWORD` en el
`.env` se inicia sesion automaticamente; si no, se pide login interactivo.
El `user_id` para registrar mascotas, agendar citas o el carrito se toma de esa
sesion (no se le pregunta al usuario ni lo inventa el modelo).

Comandos dentro del REPL:
- `salir` / `exit` - cierra
- `reset` - limpia el historial
- `verbose on` / `verbose off` - alterna el log de tools
- `login` - inicia/renueva sesion con otro usuario
- `whoami` - muestra el id del usuario de la sesion activa

## Modo servicio HTTP (multiusuario)

Para integrarlo con la app de SwingTails, Tailo se expone como servicio. La app
ya tiene la sesion del usuario, asi que **reenvia su JWT** en cada peticion y
Tailo opera en su nombre (nunca pide credenciales). El token se aisla por
peticion (ContextVar), asi varios usuarios concurrentes no se pisan.

```powershell
# desde la carpeta src/
uvicorn server:app --port 8000
#  o:  python src\server.py
```

```bash
# la app llama asi (reenviando el Bearer del usuario logueado):
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <jwt-del-usuario>" \
  -H "Content-Type: application/json" \
  -d '{"message": "que mascotas tengo registradas?", "history": []}'
# -> {"reply": "...", "user_id": 29, "sources": [...]}
```

El `user_id` para registrar mascotas / agendar / carrito sale de ese JWT. La
app mantiene la memoria conversacional y la envia en `history`.

## Estructura

```
entregable-semana-03/
├── Modelfile.tailo-agent      # llama3.1:8b + system prompt para tools
├── requirements.txt
├── .env.example
├── README.md                  # este archivo
├── INFORME-semana-03.md       # documento auditable de la rubrica (Fase 2)
├── corpus/                    # fuentes del RAG (productos, vets, guias, politicas)
├── chroma_db/                 # base vectorial persistente (ya generada)
└── src/
    ├── config.py              # rutas, JWT, host de Ollama
    ├── ingest.py              # (re)genera chroma_db desde corpus/
    ├── inspect_db.py          # utilidad para inspeccionar la base vectorial
    ├── retrieve.py            # RAG (lee chroma_db local)
    ├── api_client.py          # cliente HTTP con JWT (por-peticion) y errores
    ├── tools.py               # 12 funciones + esquemas + dispatcher
    ├── chat.py                # orquestador CLI: RAG + tool cycle + respuesta
    └── server.py              # servicio HTTP multiusuario (FastAPI)
```

## Lista de funciones (15)

Lectura:
`list_my_pets`, `get_pet`, `list_clinics`, `list_appointments`,
`list_products`, `get_product`, `view_cart`, `purchase_history`.

Escritura:
`register_pet`, `update_pet`, `delete_pet`, `book_appointment`,
`reschedule_appointment`, `cancel_appointment`, `add_to_cart`.

> **Estado real en el despliegue actual.** La API publica diverge de su
> Swagger: varios endpoints documentados devuelven 404 o 400. Funcionan
> (verificado): `list_my_pets`, `get_pet`, `register_pet`, `update_pet`,
> `delete_pet`, `list_clinics`, `list_appointments`, `list_products`,
> `get_product`.
> Hoy NO responden en el servidor: carrito (`add_to_cart`, `view_cart` ->
> 404), `purchase_history` (404) y la creacion/edicion de citas
> (`book_appointment`, `reschedule_appointment` -> 400 por esquema distinto).
> Las dejamos expuestas porque la doc las contempla; si la API se actualiza
> volveran a operar sin tocar el agente.

Detalles, mapeo a endpoints y flujo paso a paso en `INFORME-semana-03.md`.
