"""Parametros globales del pipeline RAG de Tailo."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus"
CHROMA_DIR = ROOT / "chroma_db"
EVAL_DIR = ROOT / "evaluacion"

COLLECTION_NAME = "tailo_swingtails"

# Ollama local (mismo motor de la semana 1, bindeado a localhost)
OLLAMA_HOST = "http://localhost:11434"

# Modelos
EMBED_MODEL = "nomic-embed-text"   # 768 dim, multilingue, corre en Ollama
LLM_MODEL = "tailo-rag"            # creado a partir de Modelfile.tailo-rag
JUDGE_MODEL = "llama3.1:8b"        # juez para RAGAS, base sin system prompt

# Chunking
CHUNK_SIZE = 500          # caracteres
CHUNK_OVERLAP = 80        # ~16% de solapamiento para preservar contexto

# Retrieval
TOP_K = 7                 # chunks a recuperar por consulta
                          # subido de 5 a 7 para garantizar que en consultas de
                          # urgencia entren tanto los primeros auxilios como
                          # al menos una clinica con servicio 24h
DISTANCE_METRIC = "cosine"  # compatible con nomic-embed-text
