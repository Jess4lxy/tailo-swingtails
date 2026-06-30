# Cómo correr la IA (Tailo) — Semana 05

Backend local (Ollama + FastAPI) publicado a internet con ngrok (URL fija).

---

## A) Requisitos (instalar UNA sola vez)

```powershell
# Ollama (motor del LLM) y Python 3.12 (la VOZ/Whisper no instala en 3.14)
winget install Ollama.Ollama
winget install Python.Python.3.12
winget install Ngrok.Ngrok            # túnel con URL fija para el front
```

```powershell
# ngrok: pega tu authtoken (dashboard.ngrok.com -> Your Authtoken). Una sola vez.
ngrok config add-authtoken TU_TOKEN
ngrok update                          # el de winget viene viejo; deja la última versión
```

---

## B) Preparar el proyecto (UNA sola vez)

```powershell
cd C:\workCodes\utmCodes\programasCuatri9\desarrolloWeb\entregable-semana-05

# 1. Modelos de Ollama
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama create tailo-agent -f Modelfile.tailo-agent

# 2. Entorno de Python 3.12 + dependencias (core + voz)
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt

# 3. Credenciales de SwingTails (si no existe .env)
copy .env.example .env                # luego edita SWINGTAILS_EMAIL / SWINGTAILS_PASSWORD
```

> Atajo: `.\setup.ps1` hace los pasos 1–2 de golpe (usa Python 3.12 si está) y regenera `chroma_db/` solo si falta.

---

## C) Correr y PUBLICAR (cada vez)

```powershell
cd C:\workCodes\utmCodes\programasCuatri9\desarrolloWeb\entregable-semana-05

# Levanta el backend (:8000) + el túnel ngrok con URL fija, en un solo comando:
.\serve-ngrok.ps1 -Domain nomenclatorial-gilly-contessa.ngrok-free.dev
```

URL pública para el front: **https://nomenclatorial-gilly-contessa.ngrok-free.dev**
Detener todo: `Ctrl+C`.

> Ollama normalmente ya corre solo en Windows. Si no, ábrelo (o `ollama serve` en otra terminal) antes del paso anterior.

---

## D) Solo local (sin publicar, para probar tú)

```powershell
cd C:\workCodes\utmCodes\programasCuatri9\desarrolloWeb\entregable-semana-05\src
..\.venv\Scripts\python.exe -m uvicorn server:app --port 8000
# o el REPL por consola:  ..\.venv\Scripts\python.exe chat.py
```

---

## Para el compañero del front (consumir la IA)

- Base URL: `https://nomenclatorial-gilly-contessa.ngrok-free.dev`
- En CADA petición, headers:
  - `Authorization: Bearer <jwt-del-login-de-SwingTails>`
  - `ngrok-skip-browser-warning: true`  ← obligatorio en plan gratis de ngrok
- Endpoints: `POST /chat/stream` (SSE, el principal), `POST /chat`, `POST /transcribe` (voz), `GET/DELETE /conversations…`, `GET /health`.
- Detalle de eventos SSE y ejemplos: ver `README.md`. Colección de pruebas: `Tailo-semana-05.postman_collection.json`.
