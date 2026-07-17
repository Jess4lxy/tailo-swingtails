"""Re-ranking local con un Cross-Encoder (entregable semana 07 - Fase B).

Segundo nivel del pipeline de Advanced RAG. Mientras la recuperacion (densa +
BM25) es rapida pero ruidosa (bi-encoders / lexico, ~Top-10), un Cross-Encoder
LEE la pregunta y cada fragmento JUNTOS y emite un score de relevancia mucho
mas preciso. Lo usamos para quedarnos con el Top-K de calidad extrema antes de
inyectarlo al LLM: menos tokens de ruido => menos alucinaciones y menor TTFT.

Modelo: bge-reranker-v2-m3 (multilingue, recomendado por la rubrica), via
sentence-transformers, en CPU para NO competir por la VRAM que ocupa Llama.

Diseño DEFENSIVO (igual que Whisper en la semana 05): la dependencia
(sentence-transformers + torch) es pesada y OPCIONAL. Si no esta instalada o el
modelo no carga, `Reranker` queda `available=False` y el pipeline de retrieve
degrada con gracia al orden de RRF (solo-hibrida). El backend NUNCA se cae por
no tener el reranker; solo pierde el paso de afinado.

Instalacion (aparte):
    pip install -r requirements-rerank.txt
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from config import RERANKER_DEVICE, RERANKER_ENABLED, RERANKER_MODEL


@dataclass
class RerankResult:
    """Un fragmento con su score del cross-encoder y su indice original."""

    index: int          # posicion en la lista de candidatos que se paso
    score: float        # relevancia segun el cross-encoder (mayor = mejor)


class Reranker:
    """Envoltura perezosa y tolerante a fallos del Cross-Encoder bge.

    Carga el modelo la PRIMERA vez que se usa (es pesado). Si algo falla
    (dependencia ausente, sin red para bajar el modelo, poca RAM), marca
    `available=False` y `rerank()` devuelve None para que el caller use el
    orden de fusion (RRF) tal cual.
    """

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        device: str = RERANKER_DEVICE,
        enabled: bool = RERANKER_ENABLED,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.enabled = enabled
        self._model = None          # CrossEncoder (carga perezosa)
        self._load_attempted = False
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    def _ensure_model(self) -> bool:
        """Carga el CrossEncoder una sola vez. Devuelve True si esta listo."""
        if self._model is not None:
            return True
        if self._load_attempted:      # ya intentamos y fallo: no reintentar
            return False
        self._load_attempted = True

        if not self.enabled:
            self._load_error = "reranker deshabilitado por configuracion (TAILO_RERANKER_ENABLED=0)"
            return False
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            # OJO: un ImportError aqui NO siempre significa "no instalado". Si
            # sentence-transformers esta pero sus dependencias no cuadran (p.ej.
            # transformers >=4.50 rompe el import con 'GenerationMixin'), el
            # sintoma es el mismo. Reportamos ambas causas para no perder tiempo
            # buscando en el lugar equivocado.
            self._load_error = (
                f"no se pudo importar sentence-transformers: {exc}. "
                "Puede que no este instalado O que sus dependencias no cuadren "
                "(ver los pines de transformers/scipy en requirements-rerank.txt). "
                "Instala: pip install -r requirements-rerank.txt"
            )
            return False
        try:
            # max_length acota el par (pregunta, fragmento); nuestros chunks son
            # de ~500 chars, asi que 512 tokens es holgado.
            self._model = CrossEncoder(
                self.model_name, device=self.device, max_length=512
            )
        except Exception as exc:  # noqa: BLE001 - descarga/RAM/modelo
            self._load_error = f"no se pudo cargar {self.model_name}: {exc.__class__.__name__}: {exc}"
            self._model = None
            return False
        return True

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True si el reranker esta listo para usarse (carga perezosa incluida)."""
        return self._ensure_model()

    def status(self) -> dict:
        """Estado legible para logs / reporte (por que no cargo, si aplica)."""
        ok = self._ensure_model()
        return {
            "available": ok,
            "model": self.model_name,
            "device": self.device,
            "error": None if ok else self._load_error,
        }

    # ------------------------------------------------------------------
    def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> tuple[list[RerankResult], float] | None:
        """Reordena `documents` por relevancia frente a `query`.

        Devuelve (lista ordenada de RerankResult (mejor primero, recortada a
        top_k), latencia_ms) o None si el reranker no esta disponible (el caller
        debe entonces conservar el orden de RRF).
        """
        if not documents:
            return [], 0.0
        if not self._ensure_model():
            return None

        pairs = [(query, doc) for doc in documents]
        t0 = time.perf_counter()
        scores = self._model.predict(pairs)  # type: ignore[union-attr]
        latency_ms = (time.perf_counter() - t0) * 1000

        ranked = sorted(
            (RerankResult(index=i, score=float(s)) for i, s in enumerate(scores)),
            key=lambda r: r.score,
            reverse=True,
        )
        return ranked[:top_k], round(latency_ms, 2)


# Singleton perezoso: una sola instancia por proceso (el modelo pesa; no se
# recarga por peticion). retrieve.py lo importa desde aqui.
_RERANKER: Reranker | None = None


def get_reranker() -> Reranker:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = Reranker()
    return _RERANKER
