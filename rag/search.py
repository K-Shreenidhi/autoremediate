"""Try the retriever.

Run from the project root:  python -m rag.search "your question here"
"""
import sys

from rag.retrieval import MODES, get_index

query = " ".join(sys.argv[1:]).strip()
if not query:
    raise SystemExit('Usage: python -m rag.search "your question"')
index = get_index()
for mode in MODES:
    if mode == "hybrid+rerank" and index.reranker is None:
        print(f"{mode}: skipped (reranker unavailable)")
        continue
    print(f"{mode}:")
    for chunk in index.search(query, k=3, mode=mode):
        print(f"  {chunk.id}")