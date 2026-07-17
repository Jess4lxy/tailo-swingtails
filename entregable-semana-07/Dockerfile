# syntax=docker/dockerfile:1
# =============================================================================
# Dockerfile del BACKEND de Tailo (FastAPI/uvicorn) - entregable semana 06.
#
# Compilacion MULTI-ETAPA (multi-stage build) para reducir el tamano final:
#   * Etapa "builder": instala TODAS las dependencias de Python (incluida la voz
#     Whisper) dentro de un virtualenv aislado, con las herramientas de compilar.
#   * Etapa "runtime": imagen ligera que SOLO recibe el virtualenv ya construido
#     + el codigo + ffmpeg. Las herramientas de build NO viajan a la final.
#
# El motor de inferencia (Ollama) NO se dockeriza: corre nativo en el host para
# usar la GPU. El backend lo alcanza por el puente de red de Docker
# (host.docker.internal), configurado por variable de entorno en el compose.
# =============================================================================

# -----------------------------------------------------------------------------
# ETAPA 1 - builder
# -----------------------------------------------------------------------------
# Python 3.11: faster-whisper arrastra 'av' (PyAV), que publica wheel para 3.11
# pero NO para 3.14 (en 3.14 pip intentaria compilar desde fuente y fallaria).
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Virtualenv dedicado: en la etapa runtime copiamos SOLO esta carpeta.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Instalamos primero los requirements (capa cacheable): si cambia el codigo pero
# no las dependencias, Docker reutiliza esta capa y NO reinstala todo.
COPY requirements.txt requirements-voice.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip install -r requirements-voice.txt

# -----------------------------------------------------------------------------
# ETAPA 2 - runtime (imagen final ligera)
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Cache de los modelos de Whisper: se monta como volumen en el compose para
    # NO re-descargar el modelo 'small' cada vez que se recrea el contenedor.
    HF_HOME=/models

# Dependencias del sistema para el runtime:
#   - ffmpeg: red de seguridad para decodificar el audio webm/opus del
#     MediaRecorder del navegador (PyAV trae el suyo embebido, pero por si acaso).
#   - libgomp1: OpenMP, requerido por ctranslate2 (motor de faster-whisper); sin
#     el, importar Whisper falla en tiempo de ejecucion.
#   - curl: lo usa el HEALTHCHECK.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

# Trae el virtualenv con TODAS las dependencias ya instaladas (etapa builder).
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Codigo + recursos del agente. chroma_db/ (RAG ya ingestado) se COPIA como
# SEMILLA; en el compose se monta como volumen nombrado para persistir.
COPY src/ ./src/
COPY corpus/ ./corpus/
COPY chroma_db/ ./chroma_db/
COPY Modelfile.tailo-agent ./Modelfile.tailo-agent

# data/ NO se copia (esta en .dockerignore y en .gitignore -> en un clon limpio
# no existe y el COPY romperia el build). Solo creamos el punto de montaje: el
# volumen 'tailo-data' lo persiste y su contenido se genera EN el contenedor:
#   - sessions.db / observability.db -> los crea init_db() al arrancar,
#   - stress.db (semana 07)          -> se siembra con:
#       docker compose exec backend python seed_stress.py --target 50000 --reset
RUN mkdir -p /app/data

# NOTA: el .env NO se copia a la imagen (evita hornear credenciales en las
# capas). Las variables se inyectan en runtime via 'env_file' en el compose.

# El backend NO sirve el frontend (de eso se encarga el contenedor nginx), asi
# que apuntamos WEB_DIST a una ruta inexistente -> arranca en modo "solo API".
ENV TAILO_WEB_DIST=/app/_no_web

EXPOSE 8000

# Healthcheck: compose y el script del tunel esperan a que /health responda.
# start-period alto porque el primer arranque importa chromadb y carga modelos.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=10 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# config.py calcula ROOT = padre de src/ = /app, asi que corpus/chroma_db/data
# y .env quedan en las rutas correctas al ejecutar desde /app/src.
WORKDIR /app/src
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
