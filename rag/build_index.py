"""
Build the environment-B BM25 indices with SAGEO's indexing code.

Differences from SAGEO's index phase (main.py run_index_phase), all recorded in
the handoff:
    - corpus: data/sageo/corpus/documents_filtered_md.jsonl (markdown body)
    - chunker: rag/chunking.py instead of SAGEO's chunk_text (C21)
    - one document per URL (C28): (query, URL) pairs with the same URL are merged,
      the first record is kept and source_query_ids lists every query it came from;
      doc _id = "u" + sha1(url)[:16]
    - Lucene indexing with one thread so that document order, and therefore BM25
      tie order, is the same on every rebuild

Outputs (under data/sageo/index_b/):
    documents.jsonl   merged documents with _id and source_query_ids
    jsonl/            SAGEO field JSONL files + metadata.json (chunk_to_doc)
    lucene/           one Lucene index per field

Usage:
    python rag/build_index.py
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAGEO_ROOT = ROOT / "third_party" / "SAGEO_Arena"
CORPUS = ROOT / "data" / "sageo" / "corpus" / "documents_filtered_md.jsonl"
OUT_DIR = ROOT / "data" / "sageo" / "index_b"

# pyserini creates an OpenAI client on import; indexing never calls it
os.environ.setdefault("OPENAI_API_KEY", "unused")
sys.path.insert(0, str(SAGEO_ROOT))
sys.path.insert(0, str(ROOT))

from src.modules import retriever_bm25  # noqa: E402
from rag.chunking import chunk_text  # noqa: E402

retriever_bm25.chunk_text = chunk_text  # preprocess_document looks the name up at call time


def doc_id_for(url: str) -> str:
    return "u" + hashlib.sha1(url.encode()).hexdigest()[:16]


def merge_by_url(path: Path) -> list[dict]:
    docs = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            url = doc["link"]
            if url in docs:
                docs[url]["source_query_ids"].append(doc["query_id"])
                continue
            doc["_id"] = doc_id_for(url)
            doc["source_query_ids"] = [doc["query_id"]]
            docs[url] = doc
    return list(docs.values())


def build_lucene(jsonl_dir: Path, lucene_dir: Path):
    lucene_dir.mkdir(parents=True, exist_ok=True)
    for field in retriever_bm25.FIELDS:
        cmd = [
            sys.executable, "-m", "pyserini.index.lucene",
            "--collection", "JsonCollection",
            "--input", str(jsonl_dir / field),
            "--index", str(lucene_dir / field),
            "--generator", "DefaultLuceneDocumentGenerator",
            "--threads", "1",
            "--storeRaw",
        ]
        print(f"indexing {field}", flush=True)
        subprocess.run(cmd, check=True)


def main():
    start = time.time()
    docs = merge_by_url(CORPUS)
    ids = [d["_id"] for d in docs]
    assert len(ids) == len(set(ids)), "doc id collision"
    print(f"{len(docs)} documents after merging by URL ({time.time() - start:.0f}s)", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "documents.jsonl", "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    chunk_to_doc = retriever_bm25.build_field_jsonl(docs, OUT_DIR / "jsonl")
    print(f"{len(chunk_to_doc)} chunks ({time.time() - start:.0f}s)", flush=True)

    build_lucene(OUT_DIR / "jsonl", OUT_DIR / "lucene")
    print(f"done ({time.time() - start:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
