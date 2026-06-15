"""Recuperacion vectorial para Tailo Agent.

Es una copia delgada de entregable-semana-02/src/retrieve.py adaptada para
apuntar a la base ChromaDB ya generada en la semana 02 (config.CHROMA_DIR).
No re-ingestamos: reutilizamos la coleccion persistente.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import chromadb
import ollama
from chromadb.config import Settings

from config import CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL, OLLAMA_HOST, TOP_K


@dataclass
class Retrieved:
    text: str
    metadata: dict
    distance: float


@dataclass
class Retriever:
    top_k: int = TOP_K
    _collection: object = field(init=False, repr=False)
    _ollama: ollama.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = client.get_collection(COLLECTION_NAME)
        self._ollama = ollama.Client(host=OLLAMA_HOST)

    def embed(self, text: str) -> list[float]:
        return self._ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]

    def query(self, question: str, top_k: int | None = None) -> tuple[list[Retrieved], dict]:
        k = top_k or self.top_k

        t0 = time.perf_counter()
        qv = self.embed(question)
        t_embed = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        res = self._collection.query(query_embeddings=[qv], n_results=k)
        t_search = (time.perf_counter() - t1) * 1000

        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        retrieved = [
            Retrieved(text=d, metadata=m, distance=float(dist))
            for d, m, dist in zip(docs, metas, dists)
        ]
        return retrieved, {
            "ms_embed": round(t_embed, 2),
            "ms_search": round(t_search, 2),
            "ms_total": round(t_embed + t_search, 2),
        }
