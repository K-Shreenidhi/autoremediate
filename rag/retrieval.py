"""Hybrid retrieval: BM25 + dense embeddings fused with reciprocal rank fusion,
plus an optional cross-encoder reranker."""
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from mcp_servers.common import VICTIM
from rag.chunking import chunk_markdown, chunk_python

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "corpus" / "docs"
CACHE_DIR = ROOT / ".cache" / "fastembed"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RRF_K = 60
POOL = 20  # candidates taken from each ranker before fusion
RERANK_TOP = 10  # how many fused candidates the cross-encoder re-scores
MODES = ["bm25", "dense", "hybrid", "hybrid+rerank"]


def tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


def load_corpus() -> list:
    """Doc sections from corpus/docs (plus corpus/docs_v2 when AUTOREMEDIATE_RAG_CORPUS=all) and function-level chunks of the victim's main.py (main branch)."""
    chunks = []
    doc_dirs = [DOCS_DIR]
    if os.getenv("AUTOREMEDIATE_RAG_CORPUS", "").strip().lower() == "all":
        doc_dirs.append(ROOT / "corpus" / "docs_v2")
    for doc_dir in doc_dirs:
        for path in sorted(doc_dir.glob("*.md")):
            chunks += chunk_markdown(path.read_text(encoding="utf-8"), path.name)
    code = subprocess.run(["git", "show", "main:main.py"], cwd=VICTIM, capture_output=True, check=True).stdout.decode("utf-8")
    chunks += chunk_python(code, "main.py")
    return chunks


def load_reranker():
    """Returns (reranker, error_message). The reranker is optional."""
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            return TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=str(CACHE_DIR)), ""
        except TypeError:
            return TextCrossEncoder(model_name=RERANK_MODEL), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def rrf(rankings: list, k: int = RRF_K) -> list:
    """Reciprocal rank fusion: each ranker votes 1 / (k + rank) for every chunk it returns."""
    scores = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


class Index:
    def __init__(self, chunks: list, use_rerank: bool = True):
        from fastembed import TextEmbedding

        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.embedder = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(CACHE_DIR))
        vecs = np.array(list(self.embedder.embed([c.text for c in chunks])), dtype=np.float32)
        self.matrix = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        self.reranker, self.rerank_error = (load_reranker() if use_rerank else (None, "disabled"))

    def _bm25_rank(self, query: str, n: int) -> list:
        scores = self.bm25.get_scores(tokenize(query))
        order = np.argsort(-scores, kind="stable")[:n]
        return [int(i) for i in order if scores[i] > 0]

    def _dense_rank(self, query: str, n: int) -> list:
        q = np.array(list(self.embedder.embed([QUERY_PREFIX + query]))[0], dtype=np.float32)
        q = q / np.linalg.norm(q)
        scores = self.matrix @ q
        order = np.argsort(-scores, kind="stable")[:n]
        return [int(i) for i in order]

    def search(self, query: str, k: int = 5, mode: str = "hybrid") -> list:
        if mode == "bm25":
            order = self._bm25_rank(query, POOL)
        elif mode == "dense":
            order = self._dense_rank(query, POOL)
        elif mode in ("hybrid", "hybrid+rerank"):
            order = rrf([self._bm25_rank(query, POOL), self._dense_rank(query, POOL)])
            if mode == "hybrid+rerank":
                if self.reranker is None:
                    raise RuntimeError("reranker unavailable: " + self.rerank_error)
                head = order[:RERANK_TOP]
                scores = list(self.reranker.rerank(query, [self.chunks[i].text for i in head]))
                ranked = sorted(zip(scores, head), key=lambda pair: -float(pair[0]))
                order = [i for _, i in ranked] + order[RERANK_TOP:]
        else:
            raise ValueError(f"unknown mode: {mode}")
        return [self.chunks[i] for i in order[:k]]


_INDEX = None


def get_index() -> Index:
    global _INDEX
    if _INDEX is None:
        _INDEX = Index(load_corpus())
    return _INDEX