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

# ---------------------------------------------------------------------------
# Advanced RAG: busqueda hibrida + reranking (entregable semana 07 - Fase B)
# ---------------------------------------------------------------------------
# Pipeline de dos niveles para reducir alucinaciones y latencia del LLM:
#   1. Recuperacion HIBRIDA: se combinan dos rankings independientes
#      - denso  (similitud vectorial / coseno sobre embeddings, via ChromaDB)
#      - disperso (BM25, coincidencia lexica de palabras clave, via rank_bm25)
#      fusionados con Reciprocal Rank Fusion (RRF) para traer un Top-N candidato.
#   2. RE-RANKING: un Cross-Encoder local (bge-reranker-v2-m3) reevalua ese
#      Top-N frente a la pregunta y deja solo el Top-K de calidad extrema, que
#      es lo unico que se inyecta al LLM. Menos tokens de ruido => menos
#      alucinaciones y menor TTFT.
HYBRID_TOP_N = int(os.getenv("TAILO_HYBRID_TOP_N", "10"))   # candidatos tras RRF
RERANK_TOP_K = int(os.getenv("TAILO_RERANK_TOP_K", "3"))    # se envian al LLM
RRF_K = int(os.getenv("TAILO_RRF_K", "60"))                 # constante de RRF (paper)

# Peso relativo de cada ranking al recuperar candidatos (cuantos pide cada via
# antes de fusionar). Un poco mas altos que HYBRID_TOP_N para no perder buenos
# fragmentos que solo aparecen en una de las dos listas.
DENSE_CANDIDATES = int(os.getenv("TAILO_DENSE_CANDIDATES", "20"))
SPARSE_CANDIDATES = int(os.getenv("TAILO_SPARSE_CANDIDATES", "20"))

# Reranker Cross-Encoder local. Modelo ligero multilingue recomendado por la
# rubrica. Corre en CPU (int8/fp32) para NO competir por la VRAM de Llama.
# Es una dependencia OPCIONAL y pesada (sentence-transformers + torch): se
# instala aparte (requirements-rerank.txt). Si no esta disponible, el pipeline
# degrada con gracia a solo-RRF (ver reranker.py y retrieve.py).
RERANKER_MODEL = os.getenv("TAILO_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_DEVICE = os.getenv("TAILO_RERANKER_DEVICE", "cpu")
RERANKER_ENABLED = os.getenv("TAILO_RERANKER_ENABLED", "1") not in {"0", "false", "False"}

# Chunking (usado por ingest.py si re-generas la base vectorial).
CHUNK_SIZE = 500          # caracteres
CHUNK_OVERLAP = 80        # ~16% de solapamiento para preservar contexto

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
def _normalize_ollama_host(raw: str) -> str:
    """Normaliza OLLAMA_HOST para usarlo como destino de CLIENTE.

    La variable OLLAMA_HOST sirve para DOS cosas que NO son intercambiables:
      - al SERVIDOR de Ollama le dice en que interfaz ESCUCHAR (0.0.0.0 = todas),
      - a un CLIENTE le dice a que direccion CONECTARSE.

    En la semana 06 se fija `setx OLLAMA_HOST "0.0.0.0:11434"` para que el
    contenedor Docker alcance al Ollama del host. Pero 0.0.0.0 NO es una
    direccion de destino valida: conectarse ahi revienta en Windows con
    WinError 10049 ("The requested address is not valid in its context").
    Ademas la variable suele venir SIN esquema (`0.0.0.0:11434`), que httpx
    tampoco acepta.

    Por eso: agregamos http:// si falta y traducimos 0.0.0.0 -> 127.0.0.1
    (escuchar en todas las interfaces implica que el loopback tambien responde).
    """
    host = (raw or "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host
    return host.replace("://0.0.0.0", "://127.0.0.1")


OLLAMA_HOST = _normalize_ollama_host(os.getenv("OLLAMA_HOST", "http://localhost:11434"))
EMBED_MODEL = "nomic-embed-text"

# Modelo afinado para herramientas (Llama 3.1 8B soporta tool_calls nativos
# segun la guia oficial de Ollama; ver rubrica fase 2).
LLM_MODEL = "tailo-agent"

# ---------------------------------------------------------------------------
# Arquitectura multi-agente (entregable semana 07 - Fase A)
# ---------------------------------------------------------------------------
# El agente monolitico de la semana 5 (un solo prompt gigante con RAG + 15
# tools en CADA turno) se divide en un ruteador + subagentes especialistas con
# prompts reducidos y herramientas exclusivas por dominio. Todos comparten la
# misma base local (tailo-agent / llama3.1:8b) pero reciben un system prompt
# distinto por rol (se sobreescribe el del Modelfile via mensaje role=system).
#
# El ruteador puede usar un modelo mas pequeño/rapido para clasificar (basta
# con emitir una etiqueta); por defecto reutiliza el mismo para no exigir otra
# descarga. Se puede apuntar a un modelo ligero (p.ej. "llama3.2:3b") por env.
ROUTER_MODEL = os.getenv("TAILO_ROUTER_MODEL", LLM_MODEL)

# ---------------------------------------------------------------------------
# LLM-as-a-Judge: modelo evaluador local (entregable semana 07 - Fase B)
# ---------------------------------------------------------------------------
# Modelo que AUDITA las respuestas del agente de produccion (fidelidad /
# alucinaciones). La rubrica sugiere un modelo de mayor capacidad (14B-32B); si
# el hardware lo permite se apunta a "qwen2.5:14b" u otro por env. Por defecto
# reutiliza el 8B local (no exige otra descarga); el prompt de evaluacion es
# estructurado y deterministico (temperature 0) para mitigar el menor tamaño.
JUDGE_MODEL = os.getenv("TAILO_JUDGE_MODEL", LLM_MODEL)

# ---------------------------------------------------------------------------
# Persistencia de memoria de sesiones (entregable semana 04)
# ---------------------------------------------------------------------------
# Base de datos embebida SQLite (persistencia NO volatil: sobrevive reinicios
# del servidor). Vive bajo data/ para no mezclarse con el codigo y se ignora
# en git (es estado de runtime, no fuente).
DATA_DIR = ROOT / "data"
SESSIONS_DB = Path(os.getenv("TAILO_SESSIONS_DB", str(DATA_DIR / "sessions.db")))

# ---------------------------------------------------------------------------
# Observabilidad de LLM (entregable semana 05)
# ---------------------------------------------------------------------------
# Bitacora de auditoria persistente (SQLite): una fila por interaccion con
# TTFT, latencia total, tokens/segundo, estado del guardrail y el JSON de las
# herramientas ejecutadas. Se mantiene SEPARADA de sessions.db para que la
# auditoria no se mezcle con la memoria conversacional (distinto ciclo de vida:
# la memoria se puede borrar por usuario; la bitacora es append-only).
OBSERVABILITY_DB = Path(
    os.getenv("TAILO_OBSERVABILITY_DB", str(DATA_DIR / "observability.db"))
)

# ---------------------------------------------------------------------------
# Base de datos de ESTRES a escala de produccion (entregable semana 07 - Fase A)
# ---------------------------------------------------------------------------
# SQLite local que el seeder (seed_stress.py) puebla con >=50,000 citas
# ficticias pero coherentes usando bulk inserts / transacciones, con indices
# B-Tree. El agente especialista transaccional consulta ESTA base (indexada)
# para las pruebas de la bateria evaluadora, de modo que medimos el impacto
# real de la latencia y la indexacion a escala. Vive en data/ (estado de
# runtime, ignorado en git).
STRESS_DB = Path(os.getenv("TAILO_STRESS_DB", str(DATA_DIR / "stress.db")))

# Cantidad de registros objetivo del seeder. 10,000 = nivel Competente;
# 50,000+ = nivel Excelente (rubrica semana 07). Configurable por env.
STRESS_SEED_TARGET = int(os.getenv("TAILO_STRESS_SEED_TARGET", "50000"))

# Usuario "de prueba" al que el seeder asigna un bloque garantizado de citas,
# para que la bateria evaluadora (que abre una sesion local con este id) obtenga
# resultados no vacios al consultar la agenda a escala.
EVAL_USER_ID = int(os.getenv("TAILO_EVAL_USER_ID", "1"))

# ---------------------------------------------------------------------------
# Voz: Speech-to-Text con Whisper local (entregable semana 05)
# ---------------------------------------------------------------------------
# Se ejecuta con faster-whisper estrictamente en CPU (compute int8) para NO
# competir por la VRAM que ya ocupa Llama 3.1 (RTX 5060, 8 GB). Modelo pequeño
# por defecto: buen equilibrio latencia/precision en es-MX sin saturar memoria.
WHISPER_MODEL = os.getenv("TAILO_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("TAILO_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("TAILO_WHISPER_COMPUTE", "int8")
WHISPER_LANGUAGE = os.getenv("TAILO_WHISPER_LANGUAGE", "es")

# ---------------------------------------------------------------------------
# CORS (el frontend de la semana 05 corre en otro origen)
# ---------------------------------------------------------------------------
# Lista separada por comas; "*" permite cualquier origen (comodo en desarrollo
# local con Vite/Live Server). En produccion se restringe al dominio del front.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("TAILO_CORS_ORIGINS", "*").split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Frontend estatico servido por el MISMO backend (opcional)
# ---------------------------------------------------------------------------
# Si esta carpeta existe (el build de Vite del front), el backend la sirve en
# "/" -> asi un solo tunel de ngrok publica la pagina Y la API (mismo origen).
# Por defecto apunta al build de swingtails-web-1 (hermano del entregable).
WEB_DIST = os.getenv(
    "TAILO_WEB_DIST", str(ROOT.parent / "swingtails-web-1" / "dist")
)

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
# Geolocalizacion: "veterinarias mas cercanas" (entregable semana 07)
# ---------------------------------------------------------------------------
# La API/corpus NO traen coordenadas de las clinicas (datos ficticios), asi que
# a cada clinica se le asigna una coordenada sintetica DETERMINISTA dispersa
# alrededor de esta ciudad base. Merida, Yucatan (sede de la UTM y ciudad del
# corpus) por defecto; configurable por env. `GEO_SPREAD_DEG` es el radio de
# dispersion en grados (~0.12 deg ≈ 13 km). Ver geo.py.
GEO_BASE_LAT = float(os.getenv("TAILO_GEO_BASE_LAT", "20.9674"))
GEO_BASE_LON = float(os.getenv("TAILO_GEO_BASE_LON", "-89.5926"))
GEO_SPREAD_DEG = float(os.getenv("TAILO_GEO_SPREAD_DEG", "0.12"))

# ---------------------------------------------------------------------------
# Lectura de enlaces compartidos por el usuario (entregable semana 07)
# ---------------------------------------------------------------------------
# Si el usuario pega una URL en el chat, el backend la descarga, extrae su texto
# y lo inyecta al contexto (ver web_reader.py). Limites de seguridad/rendimiento:
WEB_READ_ENABLED = os.getenv("TAILO_WEB_READ_ENABLED", "1") not in {"0", "false", "False"}
WEB_READ_TIMEOUT = int(os.getenv("TAILO_WEB_READ_TIMEOUT", "8"))       # seg por enlace
WEB_READ_MAX_URLS = int(os.getenv("TAILO_WEB_READ_MAX_URLS", "2"))     # enlaces por mensaje
WEB_READ_MAX_BYTES = int(os.getenv("TAILO_WEB_READ_MAX_BYTES", str(2_000_000)))  # 2 MB
WEB_READ_MAX_CHARS = int(os.getenv("TAILO_WEB_READ_MAX_CHARS", "6000"))  # texto inyectado

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
