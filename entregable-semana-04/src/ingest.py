"""
Pipeline de ingesta para el RAG de Tailo / SwingTails.

Pasos:
  1. Carga 4 corpora (productos.json, veterinarias.json, guias_cuidado.md, politicas_swingtails.md).
  2. Aplica chunking semantico (markdown) o estructurado (JSON por registro).
  3. Llama a Ollama para generar embeddings con nomic-embed-text.
  4. Persiste en ChromaDB (cosine, HNSW por defecto) bajo /chroma_db.

Uso:
    python src/ingest.py
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterable

import chromadb
import ollama
from chromadb.config import Settings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tqdm import tqdm

from config import (
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    CORPUS_DIR,
    DISTANCE_METRIC,
    EMBED_MODEL,
    OLLAMA_HOST,
)


def _ollama_client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_HOST)


def embed_texts(client: ollama.Client, texts: list[str]) -> list[list[float]]:
    """Genera embeddings llamando a Ollama /api/embeddings.
    Se llama de a un texto a la vez porque la API de embeddings de Ollama
    no soporta batch nativo; el costo extra es marginal en local.
    """
    out: list[list[float]] = []
    for t in texts:
        resp = client.embeddings(model=EMBED_MODEL, prompt=t)
        out.append(resp["embedding"])
    return out


def chunk_markdown(text: str, source: str) -> list[dict]:
    """Chunking semantico para Markdown: respeta encabezados, parrafos y listas."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
        keep_separator=True,
    )
    return [
        {"text": c, "metadata": {"source": source, "doc_type": "markdown"}}
        for c in splitter.split_text(text)
    ]


def chunk_json_records(records: list[dict], source: str, doc_type: str) -> list[dict]:
    """Cada registro de JSON se convierte en un unico chunk autocontenido
    con sus campos serializados en lenguaje natural. Esto preserva la
    integridad semantica de fichas de producto y clinicas (no se parten).
    """
    chunks: list[dict] = []
    for rec in records:
        text = _record_to_text(rec, doc_type)
        meta = {
            "source": source,
            "doc_type": doc_type,
            "record_id": rec.get("id", ""),
        }
        # Indexar tambien la categoria/especie/ciudad para futuros filtros
        for key in ("categoria", "especie", "ciudad", "urgencias_24h"):
            if key in rec:
                meta[key] = str(rec[key])
        chunks.append({"text": text, "metadata": meta})
    return chunks


def _record_to_text(rec: dict, doc_type: str) -> str:
    if doc_type == "producto":
        return (
            f"Producto {rec['id']}: {rec['nombre']}. "
            f"Categoria: {rec['categoria']}. Especie: {rec['especie']}. "
            f"Etapa: {rec['etapa']}. Tamano: {rec['tamano']}. "
            f"Presentacion: {rec['presentacion']}. Precio: {rec['precio_mxn']} MXN. "
            f"Descripcion: {rec['descripcion']}"
        )
    if doc_type == "veterinaria":
        especialidades = ", ".join(rec.get("especialidades", []))
        atiende = ", ".join(rec.get("atiende", []))
        urgencias = "Si atiende urgencias 24h." if rec.get("urgencias_24h") else "No atiende urgencias 24h."
        return (
            f"Clinica {rec['id']}: {rec['nombre']} en {rec['ciudad']}, colonia {rec['colonia']}. "
            f"Direccion: {rec['direccion']}. Telefono: {rec['telefono']}. "
            f"Horario: {rec['horario']}. Especialidades: {especialidades}. "
            f"Atiende: {atiende}. {urgencias} {rec['descripcion']}"
        )
    return json.dumps(rec, ensure_ascii=False)


def load_corpus() -> list[dict]:
    """Carga el corpus de conocimiento ESTATICO y devuelve chunks para embedding.

    En la semana 03 las clinicas y productos vienen de la API en vivo (tools
    list_clinics / list_products), asi que NO se indexan los catalogos
    ficticios (veterinarias.json, productos.json): solo confundian al modelo
    (p.ej. inventaba clinicas como "VetCare 24h" leidas del RAG en vez de usar
    las reales). El RAG queda para lo que SI es estatico: guias de cuidado y
    politicas de la app.
    """
    chunks: list[dict] = []

    guias = (CORPUS_DIR / "guias_cuidado.md").read_text(encoding="utf-8")
    chunks.extend(chunk_markdown(guias, "guias_cuidado.md"))

    politicas = (CORPUS_DIR / "politicas_swingtails.md").read_text(encoding="utf-8")
    chunks.extend(chunk_markdown(politicas, "politicas_swingtails.md"))

    return chunks


def get_collection(reset: bool = False):
    """Obtiene o crea la coleccion de Chroma persistente."""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": DISTANCE_METRIC},
    )


def ingest(reset: bool = True) -> dict:
    """Ejecuta el pipeline completo y devuelve metricas de la ingesta."""
    t0 = time.perf_counter()
    chunks = load_corpus()
    t_load = time.perf_counter() - t0

    print(f"[ingest] {len(chunks)} chunks generados desde el corpus")

    client = _ollama_client()
    collection = get_collection(reset=reset)

    # Embeddings + insercion en lotes de 32
    BATCH = 32
    t_embed = 0.0
    t_insert = 0.0
    for i in tqdm(range(0, len(chunks), BATCH), desc="embedding+insert"):
        batch = chunks[i : i + BATCH]
        texts = [c["text"] for c in batch]
        metas = [c["metadata"] for c in batch]
        ids = [f"chunk-{uuid.uuid4().hex[:12]}" for _ in batch]

        te = time.perf_counter()
        vectors = embed_texts(client, texts)
        t_embed += time.perf_counter() - te

        ti = time.perf_counter()
        collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
        t_insert += time.perf_counter() - ti

    stats = {
        "chunks": len(chunks),
        "tiempo_carga_s": round(t_load, 3),
        "tiempo_embedding_s": round(t_embed, 3),
        "tiempo_insert_s": round(t_insert, 3),
        "ms_por_chunk": round((t_embed + t_insert) * 1000 / max(len(chunks), 1), 2),
        "dimension": len(vectors[0]) if chunks else 0,
        "collection": COLLECTION_NAME,
        "chroma_dir": str(CHROMA_DIR),
    }
    print("[ingest] OK", json.dumps(stats, indent=2, ensure_ascii=False))
    return stats


if __name__ == "__main__":
    ingest(reset=True)
