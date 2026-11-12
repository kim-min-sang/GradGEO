"""
Reranker stage (handoff §4.2): SAGEO's Qwen3-Reranker-4B over the BM25 Top-100 passages.
The scores come from a GPU server (rag/rerank_score.py); this file prepares its input
and packs the job folder.

    prepare   (query, passage) pairs as the reranker sees them: SAGEO's
              QwenReranker.format_passage (title, description, headings, JSON-LD text,
              body passage), sorted by query and document so that vLLM's prefix cache
              reuses each document's metadata. Key = "<query_id>|<chunk_id>", unique over
              all parts (a pair already in an earlier part is left out). Parts, in the
              order the job scores them:
                mini      written by `python experiments/compare_mini.py rerank_pairs`
                trainval  train + val queries, Top-100 (K head labels)
                val_deep  val queries, ranks 101-300 (K head selection-bias check, §4.2)
                test      test queries, Top-100 (target strata of the final test, §10.4)
    package   data/sageo/rerank_job/: parts/, rerank_score.py and run.sh, the folder to
              upload; run.sh writes the scores to its first argument (default ./out)

Usage:
    python rag/reranking.py prepare [--parts trainval val_deep test]
    python rag/reranking.py package
"""
import argparse
import gzip
import json
import shutil
import sys
import types
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAGEO_ROOT = ROOT / "third_party" / "SAGEO_Arena"
DATA = ROOT / "data" / "sageo"
JOB = DATA / "rerank_job"
PARTS = JOB / "parts"
PART_ORDER = ["mini", "trainval", "val_deep", "test"]
RANGES = {"trainval": (["train", "val"], 1, 100), "val_deep": (["val"], 101, 300), "test": (["test"], 1, 100)}
TOKENIZER = "Qwen/Qwen3-Embedding-0.6B"  # same Qwen3 vocabulary as the reranker; used for token counts only

RUN_SH = """#!/bin/bash
# GradGEO reranker job (made by rag/reranking.py package). Usage: bash run.sh [out_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/out}"
mkdir -p "$OUT"
exec > >(tee -a "$OUT/job.log") 2>&1
nvidia-smi
python -c "import sys, torch; print(sys.version); print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))"
python -c "import vllm" 2>/dev/null || pip install -q vllm || echo "vllm install failed: transformers backend"
python -c "import transformers" 2>/dev/null || pip install -q transformers
python "$HERE/rerank_score.py" --in_dir "$HERE/parts" --out_dir "$OUT" --parts PARTS
"""


def format_passage():
    """SAGEO's reranker passage format. reranker.py imports vllm at the top, which the
    machine preparing the pairs does not need, so a stub stands in for it."""
    if "vllm" not in sys.modules:
        stub = types.ModuleType("vllm")
        stub.LLM = stub.SamplingParams = None
        inputs, data = types.ModuleType("vllm.inputs"), types.ModuleType("vllm.inputs.data")
        data.TokensPrompt = None
        sys.modules.update({"vllm": stub, "vllm.inputs": inputs, "vllm.inputs.data": data})
    sys.path.insert(0, str(SAGEO_ROOT))
    from src.modules.reranker import QwenReranker
    return QwenReranker.format_passage


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def load_queries() -> dict[int, str]:
    queries = {}
    for path in sorted((DATA / "queries").glob("*_queries_300.jsonl")):
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            queries[q["id"]] = q["text"]
    return queries


def load_documents(doc_ids) -> dict[str, dict]:
    """The fields format_passage reads (the full body text is not kept)."""
    docs = {}
    with open(DATA / "index_b" / "documents.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["_id"] in doc_ids:
                docs[d["_id"]] = {k: d.get(k) or ([] if k in ("headings", "jsonld") else "")
                                  for k in ("title", "meta_description", "headings", "jsonld")}
    return docs


def load_passages(chunk_ids) -> dict[str, str]:
    texts = {}
    with open(DATA / "index_b" / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["id"] in chunk_ids:
                texts[entry["id"]] = entry["contents"]
    return texts


def sort_rows(rows: list[dict]) -> list[dict]:
    """By query, then document (in order of first appearance), keeping passage order."""
    first = {}
    for i, r in enumerate(rows):
        first.setdefault((r["query_id"], r["doc"]), i)
    return sorted(rows, key=lambda r: (r["query_id"], first[(r["query_id"], r["doc"])]))


def write_part(name: str, rows: list[dict]):
    """rows: {"key", "query_id", "doc", "query", "passage"}; only key, query and passage are written."""
    PARTS.mkdir(parents=True, exist_ok=True)
    with gzip.open(PARTS / f"{name}.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in sort_rows(rows):
            f.write(json.dumps({"key": r["key"], "query": r["query"], "passage": r["passage"]}, ensure_ascii=False) + "\n")


def part_keys(name: str) -> set[str]:
    path = PARTS / f"{name}.jsonl.gz"
    if not path.exists():
        return set()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {json.loads(line)["key"] for line in f}


def load_scores(out_dir: Path) -> dict[str, float]:
    """All scores a job wrote, over every part."""
    scores = {}
    for path in Path(out_dir).glob("*.jsonl"):
        for line in open(path):
            r = json.loads(line)
            scores[r["key"]] = r["score"]
    return scores


def token_stats(rows: list[dict], sample: int = 3000) -> str:
    import random
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(TOKENIZER)
    picked = random.Random(0).sample(rows, min(sample, len(rows)))
    lengths = sorted(len(tok(r["query"] + " " + r["passage"], add_special_tokens=False)["input_ids"]) + 70 for r in picked)
    mean = sum(lengths) / len(lengths)
    return (f"tokens per pair (sample of {len(lengths)}): mean {mean:,.0f}, median {lengths[len(lengths) // 2]:,}, "
            f"p99 {lengths[int(len(lengths) * 0.99)]:,}; whole part about {mean * len(rows) / 1e6:,.0f}M tokens")


def run_prepare(parts: list[str]):
    fmt = format_passage()
    split = {s: set(ids) for s, ids in json.loads((DATA / "split.json").read_text()).items()}
    queries = load_queries()
    wanted = defaultdict(list)  # part -> [(query_id, chunk_id)]
    for line in open(DATA / "retrieval_b_unit" / "unit_top500.jsonl", encoding="utf-8"):
        r = json.loads(line)
        for part in parts:
            splits, lo, hi = RANGES[part]
            if any(r["query_id"] in split[s] for s in splits):
                wanted[part].extend((r["query_id"], c) for c, _ in r["results"][lo - 1:hi])
    chunks = {c for pairs in wanted.values() for _, c in pairs}
    print(f"{len(chunks):,} passages from {len({doc_of(c) for c in chunks}):,} documents to load")
    texts = load_passages(chunks)
    docs = load_documents({doc_of(c) for c in chunks})
    seen = set()
    for part in PART_ORDER:
        if part not in parts:
            seen |= part_keys(part)
            continue
        rows = []
        for q, c in wanted[part]:
            key = f"{q}|{c}"
            if key in seen:
                continue
            seen.add(key)
            rows.append({"key": key, "query_id": q, "doc": doc_of(c), "query": queries[q],
                         "passage": fmt(docs[doc_of(c)], texts[c])})
        write_part(part, rows)
        print(f"{part}: {len(rows):,} pairs; {token_stats(rows)}")


def run_package():
    names = [p for p in PART_ORDER if (PARTS / f"{p}.jsonl.gz").exists()]
    shutil.copy(ROOT / "rag" / "rerank_score.py", JOB / "rerank_score.py")
    (JOB / "run.sh").write_text(RUN_SH.replace("PARTS", " ".join(names)))
    size = sum(p.stat().st_size for p in JOB.rglob("*") if p.is_file())
    print(f"{JOB}: parts {names}, {size / 1e9:.2f} GB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["prepare", "package"])
    parser.add_argument("--parts", nargs="+", choices=list(RANGES), default=list(RANGES))
    args = parser.parse_args()
    if args.step == "prepare":
        run_prepare(args.parts)
    else:
        run_package()


if __name__ == "__main__":
    main()
