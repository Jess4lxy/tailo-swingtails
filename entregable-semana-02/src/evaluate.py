"""
Evaluacion RAGAS para el RAG de Tailo.

Mide 4 dimensiones exigidas por la rubrica:
  - Context Precision
  - Context Recall
  - Faithfulness
  - Answer Relevancy

Juez (LLM evaluador) y embeddings: todo local via Ollama.

Uso:
    python src/evaluate.py
    python src/evaluate.py --sample 5   # evaluacion rapida con 5 preguntas
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import ollama
from datasets import Dataset
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from ragas import evaluate
from ragas.run_config import RunConfig
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from config import (
    EMBED_MODEL,
    EVAL_DIR,
    JUDGE_MODEL,
    LLM_MODEL,
    OLLAMA_HOST,
    TOP_K,
)
from chat import build_prompt
from retrieve import Retriever


def run_pipeline(retriever: Retriever, llm: ollama.Client, questions: list[dict], top_k: int) -> Dataset:
    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for q in questions:
        prompt, chunks, _ = build_prompt(q["pregunta"], retriever, top_k=top_k)
        resp = llm.generate(model=LLM_MODEL, prompt=prompt, stream=False)["response"].strip()

        rows["question"].append(q["pregunta"])
        rows["answer"].append(resp)
        rows["contexts"].append([c.text for c in chunks])
        rows["ground_truth"].append(q["ground_truth"])

        print(f"  [{q['id']}] respondida ({len(resp)} chars)")

    return Dataset.from_dict(rows)


def main(sample: int | None = None) -> None:
    eval_path = EVAL_DIR / "eval_dataset.json"
    ds_raw = json.loads(eval_path.read_text(encoding="utf-8"))
    if sample:
        ds_raw = ds_raw[:sample]

    print(f"[eval] Generando respuestas RAG para {len(ds_raw)} preguntas...")
    retriever = Retriever(top_k=TOP_K)
    llm_client = ollama.Client(host=OLLAMA_HOST)
    t0 = time.perf_counter()
    dataset = run_pipeline(retriever, llm_client, ds_raw, TOP_K)
    print(f"[eval] Respuestas generadas en {time.perf_counter()-t0:.1f}s")

    # Juez y embeddings locales para RAGAS
    judge = LangchainLLMWrapper(
        OllamaLLM(model=JUDGE_MODEL, base_url=OLLAMA_HOST, temperature=0.0)
    )
    embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_HOST)
    )

    print("[eval] Ejecutando RAGAS (puede tardar bastante con juez local)...")
    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    # Timeout amplio y ejecucion secuencial porque el juez es un LLM local
    # de 8B; el default de RAGAS (~60s, 16 workers) genera timeouts.
    run_config = RunConfig(
        timeout=600,
        max_workers=1,
        max_retries=2,
        max_wait=120,
    )
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=judge,
        embeddings=embeddings,
        run_config=run_config,
        raise_exceptions=False,
    )

    summary = {k: round(float(v), 4) for k, v in result._repr_dict.items()}
    print("\n=== Resultados RAGAS ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    out = EVAL_DIR / "ragas_results.json"
    df = result.to_pandas()
    payload = {
        "resumen": summary,
        "detalle": json.loads(df.to_json(orient="records", force_ascii=False)),
        "config": {
            "llm_modelo": LLM_MODEL,
            "embeddings": EMBED_MODEL,
            "judge": JUDGE_MODEL,
            "top_k": TOP_K,
            "n_preguntas": len(ds_raw),
        },
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[eval] Resultados guardados en {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=None, help="Limita a N preguntas")
    args = parser.parse_args()
    main(sample=args.sample)
