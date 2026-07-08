# Cómo correr la IA (Tailo) — Semana 06 (Dockerizado, arquitectura híbrida)

**Arquitectura híbrida:** el **backend** (API del agente) corre dockerizado; el
**frontend** está publicado en **Render** (nube). Ollama corre **nativo en el
host** (para usar la GPU) y el backend lo alcanza por `host.docker.internal`. El
túnel ngrok apunta al **backend (`:8000`)**, así el frontend de Render le pega a
la API local por una URL pública fija.

```
[ Render (frontend) ]  --https-->  [ ngrok ]  -->  [ backend :8000 (Docker) ]
                                                          |
                                     host.docker.internal:11434
                                                          v
                                              [ Ollama nativo + GPU ]
```

---

## A) Requisitos (instalar UNA sola vez)

```powershell
winget install Docker.DockerDesktop     # motor de contenedores
winget install Ollama.Ollama            # motor del LLM (NATIVO, no se dockeriza)
winget install Cloudflare.cloudflared   # (opcional) túnel de URL efímera
```

El túnel ngrok ahora corre **dentro de Docker** (no necesitas instalar ngrok en
el host). Solo pon tu token y tu dominio estático en el `.env`:

```
NGROK_AUTHTOKEN=<tu-token-de-dashboard.ngrok.com>
NGROK_DOMAIN=nomenclatorial-gilly-contessa.ngrok-free.dev
```

---

## B) Preparar (UNA sola vez)

```powershell
cd C:\workCodes\utmCodes\programasCuatri9\desarrolloWeb\entregable-semana-06

# 1. Modelos de Ollama en el HOST (no van en Docker):
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama create tailo-agent -f Modelfile.tailo-agent
ollama list                             # verifica que aparezca 'tailo-agent'

# 2. Credenciales de SwingTails (si no existe .env):
copy .env.example .env                  # edita SWINGTAILS_EMAIL / SWINGTAILS_PASSWORD

# 3. Que Ollama escuche en TODAS las interfaces (para que el contenedor lo
#    alcance). Por defecto solo escucha en 127.0.0.1 y el contenedor NO llega.
#    Fija la variable de entorno de USUARIO (una vez) y reinicia Ollama:
setx OLLAMA_HOST "0.0.0.0:11434"
#    Cierra sesión de Ollama en la bandeja y vuelve a abrirlo (o reinicia sesión).
```

> No hace falta crear venv ni `pip install`: las dependencias se instalan dentro
> de la imagen del backend al hacer `docker compose build`.

**Frontend en Render** (una vez): en el dashboard del Static Site pon
- **Root Directory:** `entregable-semana-06/frontend`
- **Build Command:** `npm install; npm run build`
- **Publish Directory:** `dist`
- **Environment variable:** `VITE_BACKEND_URL = https://nomenclatorial-gilly-contessa.ngrok-free.dev`

Esa variable hace que el front de Render le pegue a tu backend local vía ngrok.

---

## C) Levantar el backend dockerizado + túnel (un solo comando)

Con Ollama ya corriendo en el host (paso B3):

```powershell
cd C:\workCodes\utmCodes\programasCuatri9\desarrolloWeb\entregable-semana-06

docker compose up --build               # 1a vez tarda (instala deps del backend)
# En segundo plano:  docker compose up -d --build
```

Esto levanta **backend + ngrok** (y el contenedor `frontend`, que en el modo
híbrido es OPCIONAL: el front público es el de Render). El túnel publica el
**backend** en tu dominio fijo: **https://nomenclatorial-gilly-contessa.ngrok-free.dev**

Comprobaciones:

```powershell
curl http://localhost:8000/health                                  # backend local
curl.exe -H "ngrok-skip-browser-warning: true" `
  https://nomenclatorial-gilly-contessa.ngrok-free.dev/health      # backend público
docker compose logs -f ngrok                                        # log del túnel
docker compose logs -f backend                                      # logs del agente
```

Para la demo: abre tu **frontend de Render** en el **móvil con datos 4G/5G**
(WiFi apagado). El front llamará a la API por la URL de ngrok. Detener:
`Ctrl+C` (o `docker compose down`). Los datos persisten en volúmenes.

> Si NO quieres levantar el contenedor `frontend` (no se usa en híbrido):
> `docker compose up backend ngrok`

> ⚠️ NO corras además `ngrok` en el host (ni `serve-docker-ngrok.ps1`) apuntando
> al mismo dominio: ngrok free solo permite UN endpoint por dominio y daría el
> error `ERR_NGROK_334`. Si te pasa, cierra el otro agente y reinicia el túnel:
> `docker compose restart ngrok`.

---

## D) Alternativa: túnel de URL efímera (Cloudflare) o script manual

Si prefieres una URL efímera de Cloudflare, o levantar el túnel aparte:

```powershell
.\serve-docker-public.ps1                 # Cloudflare (URL nueva cada vez) → :8080
```

> El servicio `ngrok` del compose ya cubre el caso de URL fija; usa esto solo si
> quieres Cloudflare. (`serve-docker-ngrok.ps1` sigue disponible pero recuerda no
> tenerlo activo a la vez que el contenedor `ngrok`.)

---

## E) Inspeccionar la observabilidad (para las evidencias del informe)

```powershell
docker compose exec backend python inspect_observability.py
# o vía API:  curl http://localhost:8000/observability/stats
```

---

## F) Alternativa SIN Docker (modo semana 05, nativo)

Sigue disponible por si necesitas correr sin contenedores:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-voice.txt
.\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev   # backend :8000 + túnel
# o solo local:  cd src ; ..\.venv\Scripts\python.exe -m uvicorn server:app --port 8000
```

---

## Notas para el front / consumo de la API

- Dockerizado, el front usa **mismo origen** (la URL del túnel) → sin CORS.
- Endpoints: `POST /chat/stream` (SSE, principal), `POST /chat`, `POST /transcribe`
  (voz), `GET/DELETE /conversations…`, `GET /health`, `GET /observability/…`.
- Con Ngrok (plan gratis) el front ya manda el header `ngrok-skip-browser-warning`.
- Colección de pruebas: `Tailo-semana-05.postman_collection.json`.
