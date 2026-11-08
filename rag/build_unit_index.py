"""
Build the passage-unit BM25 index (C29): one Lucene document per passage with five
fields, searched later with equal boosts and the per-field BM25 scores summed.

    contents          the passage (environment-B chunk)
    title             \
    meta_description   | the passage's document fields, the same text for every
    headings           | passage of a document (SAGEO's "semantic unit")
    jsonld_text       /

Reads the field JSONL written by rag/build_index.py (same chunk ids, same field
texts as SAGEO's preprocess_document), so rag/build_index.py must run first.
Lucene indexing uses one thread so that document order, and therefore BM25 tie
order, is the same on every rebuild.

Outputs (under data/sageo/index_b_unit/):
    jsonl/docs.jsonl  one record per passage with the five fields
    lucene/           the index

Usage:
    python rag/build_unit_index.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELD_JSONL = ROOT / "data" / "sageo" / "index_b" / "jsonl"
OUT_DIR = ROOT / "data" / "sageo" / "index_b_unit"
META_FIELDS = ["title", "meta_description", "headings", "jsonld_text"]


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def write_units(out_path: Path) -> int:
    meta = {field: {} for field in META_FIELDS}
    for field in META_FIELDS:
        with open(FIELD_JSONL / field / "docs.jsonl", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                meta[field][entry["id"]] = entry["contents"]
    n = 0
    with open(FIELD_JSONL / "body_text" / "docs.jsonl", encoding="utf-8") as fin, \
            open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            entry = json.loads(line)
            doc_id = doc_of(entry["id"])
            unit = {"id": entry["id"], "contents": entry["contents"]}
            for field in META_FIELDS:
                unit[field] = meta[field].get(doc_id, "")
            fout.write(json.dumps(unit, ensure_ascii=False) + "\n")
            n += 1
    return n


def main():
    start = time.time()
    (OUT_DIR / "jsonl").mkdir(parents=True, exist_ok=True)
    n = write_units(OUT_DIR / "jsonl" / "docs.jsonl")
    print(f"{n} units written ({time.time() - start:.0f}s)", flush=True)
    cmd = [
        sys.executable, "-m", "pyserini.index.lucene",
        "--collection", "JsonCollection",
        "--input", str(OUT_DIR / "jsonl"),
        "--index", str(OUT_DIR / "lucene"),
        "--generator", "DefaultLuceneDocumentGenerator",
        "--threads", "1",
        "--fields", *META_FIELDS,
    ]
    subprocess.run(cmd, check=True)
    print(f"done ({time.time() - start:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
