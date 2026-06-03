"""Inspector minimo de la coleccion ChromaDB de Tailo.

Uso:
    python src/inspect_db.py                 # resumen + 3 chunks de muestra
    python src/inspect_db.py --all           # todos los chunks
    python src/inspect_db.py --source productos.json
    python src/inspect_db.py --search "convulsion"   # busqueda vectorial
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import chromadb
from chromadb.config import Settings

from config import CHROMA_DIR, COLLECTION_NAME


def get_collection():
    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_collection(COLLECTION_NAME)


def summary(col):
    n = col.count()
    sample = col.get(limit=n, include=["metadatas"])
    sources = Counter(m.get("source", "?") for m in sample["metadatas"])
    types = Counter(m.get("doc_type", "?") for m in sample["metadatas"])
    print(f"Coleccion: {COLLECTION_NAME}")
    print(f"Total de chunks: {n}")
    print(f"\nPor fuente:")
    for src, cnt in sources.most_common():
        print(f"  {src:35s} {cnt}")
    print(f"\nPor tipo de documento:")
    for t, cnt in types.most_common():
        print(f"  {t:20s} {cnt}")


def list_chunks(col, source: str | None, limit: int | None):
    where = {"source": source} if source else None
    res = col.get(where=where, include=["documents", "metadatas"], limit=limit)
    ids = res["ids"]
    docs = res["documents"]
    metas = res["metadatas"]
    print(f"\nMostrando {len(ids)} chunks" + (f" de {source}" if source else "") + ":\n")
    for i, (cid, doc, meta) in enumerate(zip(ids, docs, metas), 1):
        print(f"--- [{i}] id={cid} ---")
        print(f"    metadata: {json.dumps(meta, ensure_ascii=False)}")
        text = doc.replace("\n", " ")
        print(f"    texto:    {text[:300]}{'...' if len(text) > 300 else ''}\n")


def search(col, query: str, k: int):
    import ollama
    from config import EMBED_MODEL, OLLAMA_HOST
    client = ollama.Client(host=OLLAMA_HOST)
    qv = client.embeddings(model=EMBED_MODEL, prompt=query)["embedding"]
    res = col.query(query_embeddings=[qv], n_results=k)
    print(f"\nQuery: {query}\nTop-{k} resultados:\n")
    for i, (doc, meta, dist) in enumerate(zip(res["documents"][0], res["metadatas"][0], res["distances"][0]), 1):
        text = doc.replace("\n", " ")
        print(f"[{i}] dist={dist:.4f}  src={meta.get('source')}  id={meta.get('record_id','')}")
        print(f"    {text[:250]}{'...' if len(text) > 250 else ''}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="Listar todos los chunks")
    p.add_argument("--source", help="Filtrar por archivo fuente (productos.json, guias_cuidado.md, ...)")
    p.add_argument("--search", help="Hacer busqueda vectorial con esta query")
    p.add_argument("--k", type=int, default=5)
    args = p.parse_args()

    col = get_collection()
    summary(col)

    if args.search:
        search(col, args.search, args.k)
    elif args.all or args.source:
        list_chunks(col, args.source, None)
    else:
        list_chunks(col, None, 3)
        print("\nTip: usa --all, --source <archivo> o --search 'tu consulta' para ver mas.")


if __name__ == "__main__":
    main()
