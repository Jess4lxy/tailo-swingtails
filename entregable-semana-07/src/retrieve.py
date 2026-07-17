"""Recuperacion AVANZADA para el agente especialista en RAG (semana 07 - Fase B).

Sustituye al RAG basico de la semana 02 (solo similitud vectorial) por un
pipeline HIBRIDO de dos niveles:

  Nivel 1 - Recuperacion hibrida (dense + sparse, fusionadas con RRF)
     - Densa:  similitud coseno sobre embeddings nomic-embed-text (ChromaDB /
       HNSW). Buena para coincidencia SEMANTICA aunque cambien las palabras.
     - Dispersa: BM25 (rank_bm25) sobre el texto de los chunks. Buena para
       coincidencia LEXICA exacta (nombres, terminos raros, numeros) que los
       embeddings a veces diluyen.
     - Fusion: Reciprocal Rank Fusion (RRF): score(d) = sum 1/(k + rank_i(d)).
       No necesita normalizar escalas (coseno vs BM25 no son comparables); solo
       usa las POSICIONES en cada ranking. Devuelve un Top-N candidato.

  Nivel 2 - Re-ranking (Cross-Encoder local bge-reranker-v2-m3)
     Reevalua el Top-N frente a la pregunta y deja el Top-K de calidad extrema
     (config: HYBRID_TOP_N -> RERANK_TOP_K). Si el reranker no esta instalado,
     se conserva el orden de RRF (degradacion con gracia).

La coleccion Chroma se reutiliza tal cual (no re-ingestamos). Como el corpus
estatico es pequeño (guias + politicas), cargamos TODOS los documentos en
memoria una vez para construir el indice BM25; el costo es despreciable.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

import chromadb
import ollama
from chromadb.config import Settings

from config import (
    CHROMA_DIR,
    COLLECTION_NAME,
    DENSE_CANDIDATES,
    EMBED_MODEL,
    HYBRID_TOP_N,
    OLLAMA_HOST,
    RERANK_TOP_K,
    RRF_K,
    SPARSE_CANDIDATES,
    TOP_K,
)
from reranker import get_reranker


@dataclass
class Retrieved:
    text: str
    metadata: dict
    distance: float                 # distancia vectorial (coseno); None si no vino por la via densa
    rrf_score: float = 0.0          # score de fusion hibrida (mayor = mejor)
    rerank_score: float | None = None  # score del cross-encoder (None si no se aplico)


# ---------------------------------------------------------------------------
# Tokenizacion ligera para BM25 (minusculas + sin acentos + palabras).
# ---------------------------------------------------------------------------
_WORD_RX = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _WORD_RX.findall(t)


@dataclass
class Retriever:
    top_k: int = TOP_K
    _collection: object = field(init=False, repr=False)
    _ollama: ollama.Client = field(init=False, repr=False)
    _ids: list[str] = field(init=False, repr=False, default_factory=list)
    _docs: list[str] = field(init=False, repr=False, default_factory=list)
    _metas: list[dict] = field(init=False, repr=False, default_factory=list)
    _bm25: object | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = client.get_collection(COLLECTION_NAME)
        self._ollama = ollama.Client(host=OLLAMA_HOST)
        self._build_sparse_index()

    # ------------------------------------------------------------------
    # Indice disperso (BM25) sobre TODO el corpus, en memoria.
    # ------------------------------------------------------------------
    def _build_sparse_index(self) -> None:
        """Carga todos los documentos de la coleccion y construye el BM25.

        Si rank_bm25 no esta instalado, deja `_bm25=None` y el pipeline cae a
        recuperacion solo-densa (vectorial). Se registra pero no rompe."""
        got = self._collection.get(include=["documents", "metadatas"])
        self._ids = got.get("ids") or []
        self._docs = got.get("documents") or []
        self._metas = got.get("metadatas") or [{} for _ in self._ids]

        if not self._docs:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi([_tokenize(d) for d in self._docs])

    def embed(self, text: str) -> list[float]:
        return self._ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]

    # ------------------------------------------------------------------
    # Recuperacion densa (vectorial / HNSW en Chroma).
    # ------------------------------------------------------------------
    def _dense_search(self, question: str, n: int) -> tuple[list[str], dict[str, float]]:
        """Devuelve (ids ordenados por similitud, {id: distancia})."""
        qv = self.embed(question)
        res = self._collection.query(
            query_embeddings=[qv],
            n_results=min(n, max(len(self._ids), 1)),
            include=["distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return ids, {i: float(d) for i, d in zip(ids, dists)}

    # ------------------------------------------------------------------
    # Recuperacion dispersa (BM25 lexica).
    # ------------------------------------------------------------------
    def _sparse_search(self, question: str, n: int) -> list[str]:
        """Devuelve los ids de los n documentos con mayor score BM25."""
        if self._bm25 is None or not self._docs:
            return []
        scores = self._bm25.get_scores(_tokenize(question))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        # Descarta los de score 0 (ninguna palabra en comun): solo ruido.
        top = [i for i in order[:n] if scores[i] > 0]
        return [self._ids[i] for i in top]

    # ------------------------------------------------------------------
    # Fusion RRF de los dos rankings.
    # ------------------------------------------------------------------
    @staticmethod
    def _rrf_fuse(
        dense_ids: list[str], sparse_ids: list[str], k: int
    ) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion. score(d) = sum_i 1/(k + rank_i(d)).

        `rank` es 1-based. Devuelve [(id, score)] ordenado desc. Un id que
        aparece BIEN posicionado en ambas listas sube; uno que solo esta en una,
        tambien suma pero menos."""
        scores: dict[str, float] = {}
        for ranking in (dense_ids, sparse_ids):
            for rank, doc_id in enumerate(ranking, start=1):
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    # ------------------------------------------------------------------
    # API publica: recuperacion avanzada completa.
    # ------------------------------------------------------------------
    def query(self, question: str, top_k: int | None = None) -> tuple[list[Retrieved], dict]:
        """Pipeline completo: hibrida (dense+BM25 -> RRF) + reranking -> Top-K.

        Devuelve (lista de Retrieved (mejor primero, recortada a top_k), stats).
        `stats` incluye la latencia por etapa y que metodo se uso, util para la
        observabilidad y el reporte de la semana 07.
        """
        final_k = top_k or self.top_k

        # --- Nivel 1: recuperacion hibrida ----------------------------------
        t0 = time.perf_counter()
        dense_ids, dist_by_id = self._dense_search(question, DENSE_CANDIDATES)
        t_dense = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        sparse_ids = self._sparse_search(question, SPARSE_CANDIDATES)
        t_sparse = (time.perf_counter() - t1) * 1000

        fused = self._rrf_fuse(dense_ids, sparse_ids, RRF_K)[:HYBRID_TOP_N]
        candidate_ids = [doc_id for doc_id, _ in fused]
        rrf_by_id = dict(fused)

        # Materializa los candidatos (texto + metadata) preservando orden RRF.
        id_to_pos = {i: p for p, i in enumerate(self._ids)}
        candidates: list[Retrieved] = []
        for doc_id in candidate_ids:
            pos = id_to_pos.get(doc_id)
            if pos is None:
                continue
            candidates.append(
                Retrieved(
                    text=self._docs[pos],
                    metadata=self._metas[pos] or {},
                    distance=dist_by_id.get(doc_id, float("nan")),
                    rrf_score=rrf_by_id.get(doc_id, 0.0),
                )
            )

        # --- Nivel 2: reranking (Cross-Encoder) -----------------------------
        method = "hybrid+rerank"
        t_rerank = 0.0
        reranker = get_reranker()
        rr = reranker.rerank(question, [c.text for c in candidates], top_k=final_k)
        if rr is not None:
            ranked, t_rerank = rr
            results: list[Retrieved] = []
            for item in ranked:
                c = candidates[item.index]
                c.rerank_score = item.score
                results.append(c)
        else:
            # Degradacion con gracia: sin reranker, usamos el orden de RRF.
            method = "hybrid+rrf" if sparse_ids else "dense-only"
            results = candidates[:final_k]

        stats = {
            "method": method,
            "n_dense": len(dense_ids),
            "n_sparse": len(sparse_ids),
            "n_candidates": len(candidates),
            "n_final": len(results),
            "ms_dense": round(t_dense, 2),
            "ms_sparse": round(t_sparse, 2),
            "ms_rerank": round(t_rerank, 2),
            "ms_total": round(t_dense + t_sparse + t_rerank, 2),
        }
        return results, stats
