"""
BM25 retrieval over the environment-B indices.

unit (main engine, C29): one index entry per passage with five fields (passage,
    title, meta_description, headings, jsonld_text); BM25 per field, scores
    summed with equal boosts (rag/build_unit_index.py).
sageo (engine A, kept for comparison): SAGEO's per-field indices fused with
    weighted RRF, same results as SAGEO's search(). SAGEO's weighted_rrf_fusion
    rebuilds the doc -> chunks map from all ~5M chunks on every query; here the
    map is built once, in the same order, and the fusion is otherwise copied line
    for line, so scores, ranks and tie order are unchanged (checked with --check).

Usage:
    python rag/retrieval.py --top_k 500
    python rag/retrieval.py --engine sageo --per_field_k 300 --top_k 500
    python rag/retrieval.py --engine sageo --check 20
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAGEO_ROOT = ROOT / "third_party" / "SAGEO_Arena"
INDEX_DIR = ROOT / "data" / "sageo" / "index_b"
UNIT_INDEX_DIR = ROOT / "data" / "sageo" / "index_b_unit" / "lucene"
QUERY_DIR = ROOT / "data" / "sageo" / "queries"
OUT_DIR = ROOT / "data" / "sageo" / "retrieval_b"
UNIT_OUT_DIR = ROOT / "data" / "sageo" / "retrieval_b_unit"
UNIT_FIELDS = {"contents": 1.0, "title": 1.0, "meta_description": 1.0, "headings": 1.0, "jsonld_text": 1.0}

os.environ.setdefault("OPENAI_API_KEY", "unused")  # pyserini creates an OpenAI client on import
sys.path.insert(0, str(SAGEO_ROOT))

from src.modules import retriever_bm25  # noqa: E402
from src.modules.retriever_bm25 import FIELD_WEIGHTS, load_searchers  # noqa: E402
from pyserini.search.lucene import LuceneSearcher  # noqa: E402


def load_index(index_dir: Path = INDEX_DIR):
    searchers = load_searchers(index_dir / "lucene")
    with open(index_dir / "jsonl" / "metadata.json", encoding="utf-8") as f:
        chunk_to_doc = json.load(f)["chunk_to_doc"]
    doc_to_chunks = defaultdict(list)
    for chunk_id, doc_id in chunk_to_doc.items():
        doc_to_chunks[doc_id].append(chunk_id)
    return searchers, chunk_to_doc, doc_to_chunks


def weighted_rrf_fusion(field_results, weights, chunk_to_doc, doc_to_chunks, k=60):
    """SAGEO's weighted_rrf_fusion with doc_to_chunks passed in."""
    doc_scores = defaultdict(float)
    for field, ranking in field_results.items():
        if field == "body_text":
            continue
        weight = weights.get(field, 1.0)
        for rank, doc_id in enumerate(ranking, start=1):
            doc_scores[doc_id] += weight / (k + rank)

    chunk_scores = defaultdict(float)
    body_ranking = field_results.get("body_text", [])
    body_weight = weights.get("body_text", 1.0)
    for rank, chunk_id in enumerate(body_ranking, start=1):
        chunk_scores[chunk_id] += body_weight / (k + rank)
        doc_id = chunk_to_doc.get(chunk_id, chunk_id)
        chunk_scores[chunk_id] += doc_scores.get(doc_id, 0.0)

    for doc_id, doc_score in doc_scores.items():
        if doc_score > 0:
            for chunk_id in doc_to_chunks.get(doc_id, []):
                if chunk_id not in chunk_scores:
                    chunk_scores[chunk_id] = doc_score

    return sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)


def search(query, searchers, chunk_to_doc, doc_to_chunks, top_k=100, per_field_k=200):
    field_results = {}
    for field, searcher in searchers.items():
        hits = searcher.search(query, k=per_field_k)
        field_results[field] = [hit.docid for hit in hits]
    fused = weighted_rrf_fusion(field_results, FIELD_WEIGHTS, chunk_to_doc, doc_to_chunks)
    return fused[:top_k]


def load_unit_searcher(index_dir: Path = UNIT_INDEX_DIR) -> LuceneSearcher:
    return LuceneSearcher(str(index_dir))


def search_unit(query, searcher, top_k=100):
    """Passage-unit search: per-field BM25 scores summed (Lucene BooleanQuery of field clauses)."""
    hits = searcher.search(query, k=top_k, fields=UNIT_FIELDS)
    return [(hit.docid, hit.score) for hit in hits]


def load_queries():
    queries = []
    for path in sorted(QUERY_DIR.glob("*_queries_300.jsonl")):
        with open(path, encoding="utf-8") as f:
            queries += [json.loads(line) for line in f if line.strip()]
    return queries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["unit", "sageo"], default="unit")
    parser.add_argument("--per_field_k", type=int, default=300, help="sageo engine only")
    parser.add_argument("--top_k", type=int, default=500)
    parser.add_argument("--check", type=int, default=0, help="sageo engine: compare N queries with SAGEO's search() and exit")
    args = parser.parse_args()
    queries = load_queries()

    if args.engine == "unit":
        searcher = load_unit_searcher()
        UNIT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = UNIT_OUT_DIR / f"unit_top{args.top_k}.jsonl"
        start = time.time()
        with open(out_path, "w", encoding="utf-8") as out:
            for i, q in enumerate(queries, start=1):
                results = search_unit(q["text"], searcher, args.top_k)
                out.write(json.dumps({"query_id": q["id"], "dataset": q["dataset"], "results": results}) + "\n")
                if i % 300 == 0:
                    print(f"{i}/{len(queries)} {time.time() - start:.0f}s", flush=True)
        print(f"done -> {out_path}")
        return

    searchers, chunk_to_doc, doc_to_chunks = load_index()

    if args.check:
        for q in queries[:: max(1, len(queries) // args.check)][: args.check]:
            ours = search(q["text"], searchers, chunk_to_doc, doc_to_chunks, args.top_k, args.per_field_k)
            theirs = retriever_bm25.search(q["text"], searchers, chunk_to_doc, args.top_k, args.per_field_k)
            assert ours == theirs, f"mismatch on query {q['id']}"
        print(f"identical to SAGEO search() on {args.check} queries")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"bm25_pfk{args.per_field_k}_top{args.top_k}.jsonl"
    start = time.time()
    with open(out_path, "w", encoding="utf-8") as out:
        for i, q in enumerate(queries, start=1):
            results = search(q["text"], searchers, chunk_to_doc, doc_to_chunks, args.top_k, args.per_field_k)
            out.write(json.dumps({"query_id": q["id"], "dataset": q["dataset"], "results": results}) + "\n")
            if i % 300 == 0:
                print(f"{i}/{len(queries)} {time.time() - start:.0f}s", flush=True)
    print(f"done -> {out_path}")


if __name__ == "__main__":
    main()
