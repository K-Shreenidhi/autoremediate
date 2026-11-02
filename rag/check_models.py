"""Downloads and checks the embedding model and the reranker.

Run from the project root:  python -m rag.check_models
"""
from fastembed import TextEmbedding

from rag.retrieval import CACHE_DIR, EMBED_MODEL, RERANK_MODEL, load_reranker

CACHE_DIR.mkdir(parents=True, exist_ok=True)
print("loading embedding model (the first run downloads about 130 MB)...", flush=True)
embedder = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(CACHE_DIR))
vectors = list(embedder.embed(["checkout fails with a promo code", "pagination skips the first page"]))
print(f"embedding model OK: dimension {len(vectors[0])}")

print("loading reranker (the first run downloads about 90 MB)...", flush=True)
reranker, error = load_reranker()
if reranker is None:
    print(f"reranker unavailable ({error}); the ablation will skip it")
else:
    scores = list(reranker.rerank("promo code checkout error",
                                  ["Checkout returns 500 for unknown promo codes", "How to export customer data"]))
    print(f"reranker OK: the relevant passage scored higher: {bool(scores[0] > scores[1])}")