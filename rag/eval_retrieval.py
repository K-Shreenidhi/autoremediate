"""Retrieval ablation: BM25 vs dense vs hybrid (RRF) vs hybrid + rerank.

Queries are the incident tickets (what a customer wrote).
Run from the project root:  python -m rag.eval_retrieval
"""
import json
import time

from rag.retrieval import MODES, ROOT, get_index

INCIDENTS_DIR = ROOT / "incidents"
RELEVANCE = json.loads((ROOT / "corpus" / "relevance.json").read_text(encoding="utf-8"))
K = 5


def is_relevant(chunk, rel) -> bool:
    return (chunk.kind == "doc" and chunk.source in rel["docs"]) or (chunk.kind == "code" and chunk.title in rel["code"])


def items_found(chunks, rel) -> int:
    found = 0
    for d in rel["docs"]:
        found += any(c.kind == "doc" and c.source == d for c in chunks)
    for f in rel["code"]:
        found += any(c.kind == "code" and c.title == f for c in chunks)
    return found


def evaluate(index, mode, queries):
    hit1 = recall = mrr = 0.0
    t0 = time.perf_counter()
    for inc, ticket in queries.items():
        rel = RELEVANCE[inc]
        ranked = index.search(ticket, k=K, mode=mode)
        if ranked and is_relevant(ranked[0], rel):
            hit1 += 1
        recall += items_found(ranked, rel) / (len(rel["docs"]) + len(rel["code"]))
        for rank, chunk in enumerate(ranked, start=1):
            if is_relevant(chunk, rel):
                mrr += 1.0 / rank
                break
    n = len(queries)
    ms = (time.perf_counter() - t0) * 1000 / n
    return hit1 / n, recall / n, mrr / n, ms


def main() -> None:
    queries = {}
    for d in sorted(INCIDENTS_DIR.iterdir()):
        meta_path = d / "incident.json"
        if d.is_dir() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["id"] in RELEVANCE:
                queries[meta["id"]] = meta["ticket"]

    print("loading models and building the index...", flush=True)
    index = get_index()
    n_doc = sum(c.kind == "doc" for c in index.chunks)
    n_code = sum(c.kind == "code" for c in index.chunks)
    print(f"index: {len(index.chunks)} chunks ({n_doc} doc, {n_code} code), {len(queries)} queries\n")

    print(f"{'mode':<15} {'hit@1':>6} {'recall@5':>9} {'MRR@5':>7} {'ms/query':>9}")
    for mode in MODES:
        if mode == "hybrid+rerank" and index.reranker is None:
            print(f"{mode:<15} skipped (reranker unavailable: {index.rerank_error})")
            continue
        h, r, m, ms = evaluate(index, mode, queries)
        print(f"{mode:<15} {h:>6.2f} {r:>9.2f} {m:>7.2f} {ms:>9.1f}")

    print("\nTop-3 per incident (hybrid):")
    for inc, ticket in queries.items():
        top = index.search(ticket, k=3, mode="hybrid")
        ok = "OK  " if any(is_relevant(c, RELEVANCE[inc]) for c in top) else "MISS"
        print(f"{inc}  {ok}  " + " | ".join(c.id for c in top))


if __name__ == "__main__":
    main()