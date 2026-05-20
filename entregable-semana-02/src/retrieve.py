"""
Logica de recuperacion vectorial para Tailo.

Permite ejecutar consultas independientes (script CLI) y mide latencias
para evidenciar el objetivo de p95 < 100 ms exigido por la rubrica.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field

import chromadb
import ollama
from chromadb.config import Settings

from config import (
    CHROMA_DIR,
    COLLECTION_NAME,
    EMBED_MODEL,
    OLLAMA_HOST,
    TOP_K,
)


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

        t_embed_0 = time.perf_counter()
        qv = self.embed(question)
        t_embed = (time.perf_counter() - t_embed_0) * 1000

        t_search_0 = time.perf_counter()
        res = self._collection.query(query_embeddings=[qv], n_results=k)
        t_search = (time.perf_counter() - t_search_0) * 1000

        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        retrieved = [
            Retrieved(text=d, metadata=m, distance=float(dist))
            for d, m, dist in zip(docs, metas, dists)
        ]
        latency = {
            "ms_embed": round(t_embed, 2),
            "ms_search": round(t_search, 2),
            "ms_total": round(t_embed + t_search, 2),
        }
        return retrieved, latency


def benchmark(retriever: Retriever, questions: list[str]) -> dict:
    # Warmup: la primera llamada carga nomic-embed-text en VRAM y sesga el p95.
    # Hacemos dos calentamientos para amortizar tambien el JIT/cache de Chroma.
    for _ in range(2):
        retriever.query("calentamiento de modelo de embeddings")

    totals: list[float] = []
    searches: list[float] = []
    for q in questions:
        _, lat = retriever.query(q)
        totals.append(lat["ms_total"])
        searches.append(lat["ms_search"])
    return {
        "n": len(questions),
        "ms_total_p50": round(statistics.median(totals), 2),
        "ms_total_p95": round(_percentile(totals, 95), 2),
        "ms_search_p50": round(statistics.median(searches), 2),
        "ms_search_p95": round(_percentile(searches, 95), 2),
    }


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None, help="Consulta a probar")
    parser.add_argument("--k", type=int, default=TOP_K)
    parser.add_argument("--bench", action="store_true", help="Corre benchmark con preguntas de evaluacion")
    args = parser.parse_args()

    r = Retriever(top_k=args.k)

    if args.bench:
        from pathlib import Path
        eval_path = Path(__file__).resolve().parent.parent / "evaluacion" / "eval_dataset.json"
        ds = json.loads(eval_path.read_text(encoding="utf-8"))
        questions = [item["pregunta"] for item in ds]
        print(json.dumps(benchmark(r, questions), indent=2, ensure_ascii=False))
    else:
        q = args.query or "Que productos tienen para un cachorro labrador?"
        chunks, lat = r.query(q)
        print(f"\nConsulta: {q}\n")
        for i, c in enumerate(chunks, 1):
            print(f"[{i}] dist={c.distance:.4f}  src={c.metadata.get('source')}")
            print(f"    {c.text[:200]}{'...' if len(c.text) > 200 else ''}\n")
        print("Latencia:", lat)
