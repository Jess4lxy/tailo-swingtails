"""Parametros globales del agente Tailo con Function Calling.

Reutiliza la base RAG del entregable de la semana 02 (chroma_db ya generado)
y agrega configuracion de la API publica de SwingTails para Function Calling.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carga .env de la raiz de entregable-semana-03 (si existe).
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# Rutas (el entregable es autonomo: corpus y base vectorial viven aqui)
# ---------------------------------------------------------------------------
CORPUS_DIR = ROOT / "corpus"
CHROMA_DIR = ROOT / "chroma_db"

COLLECTION_NAME = "tailo_swingtails"
DISTANCE_METRIC = "cosine"
TOP_K = 5

# Chunking (usado por ingest.py si re-generas la base vectorial).
CHUNK_SIZE = 500          # caracteres
CHUNK_OVERLAP = 80        # ~16% de solapamiento para preservar contexto

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"

# Modelo afinado para herramientas (Llama 3.1 8B soporta tool_calls nativos
# segun la guia oficial de Ollama; ver rubrica fase 2).
LLM_MODEL = "tailo-agent"

# ---------------------------------------------------------------------------
# Persistencia de memoria de sesiones (entregable semana 04)
# ---------------------------------------------------------------------------
# Base de datos embebida SQLite (persistencia NO volatil: sobrevive reinicios
# del servidor). Vive bajo data/ para no mezclarse con el codigo y se ignora
# en git (es estado de runtime, no fuente).
DATA_DIR = ROOT / "data"
SESSIONS_DB = Path(os.getenv("TAILO_SESSIONS_DB", str(DATA_DIR / "sessions.db")))

# --- Gestion de la ventana de contexto (Context Window Management) ----------
# El Modelfile fija `num_ctx 16384` para tailo-agent. De ese presupuesto hay
# que descontar lo que NO es historial conversacional y se envia en cada turno:
#   - system prompt del Modelfile (~1.5k tokens),
#   - esquemas de las 15 tools que Ollama serializa al modelo (~3k tokens),
#   - el bloque RAG inyectado en el mensaje del usuario (~1k tokens),
#   - la respuesta a generar: `num_predict 512`.
# Reservamos un margen y dejamos el resto para el buffer de historial.
CONTEXT_WINDOW_TOKENS = 16384          # = num_ctx del Modelfile
CONTEXT_RESERVED_TOKENS = 6000         # system + tools + RAG + num_predict + margen
HISTORY_TOKEN_BUDGET = CONTEXT_WINDOW_TOKENS - CONTEXT_RESERVED_TOKENS  # ~10240

# Estrategia: ventana deslizante + resumen (summarization) de lo que se sale.
# Cuando el historial activo supera el umbral, condensamos los turnos mas
# antiguos en un resumen acumulado y los marcamos como "ya resumidos", de modo
# que el buffer enviado al modelo nunca crece sin control.
COMPACT_THRESHOLD_TOKENS = HISTORY_TOKEN_BUDGET          # disparo de compactacion
COMPACT_TARGET_TOKENS = int(HISTORY_TOKEN_BUDGET * 0.6)  # objetivo tras compactar
KEEP_RECENT_MESSAGES = 6               # ultimos N mensajes NUNCA se resumen (3 turnos)

# ---------------------------------------------------------------------------
# API publica de SwingTails
# ---------------------------------------------------------------------------
API_BASE = os.getenv(
    "SWINGTAILS_API_BASE",
    "https://swingtails-api-yz02.onrender.com",
).rstrip("/")
API_EMAIL = os.getenv("SWINGTAILS_EMAIL", "").strip() or None
API_PASSWORD = os.getenv("SWINGTAILS_PASSWORD", "").strip() or None
API_JWT = os.getenv("SWINGTAILS_JWT", "").strip() or None

# Timeout corto para evitar congelar el agente si Render esta dormido.
API_TIMEOUT = 30  # segundos
