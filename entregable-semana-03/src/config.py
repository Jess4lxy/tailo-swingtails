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
