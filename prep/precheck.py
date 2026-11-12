"""
Pre-checks before the main experiment (handoff §10.2), environment B, passage-unit
engine (C29, rag/retrieval.py --engine unit).

    stats    no LLM: for N = 200 and N = 300 (both cut from the same ranking)
             ② unique documents in Top-100 / Top-N, ③ share that are the query's own
             Google results, ⑤ tied passages by rank band and tie groups that
             straddle rank 100, ⑥ unique passages / documents in the union of
             candidate pools and a judging-cost estimate
    umbrela  LLM sample for ① and ④: 30 queries per dataset, up to 3 documents per
             rank band (1-100, 101-200, 201-300, 301-500; document rank = its best
             passage), judged with the UMBRELA prompt on title + meta description + that
             best passage. Results are cached. The pre-check itself (2026-11) was judged
             by gpt-5-mini; new calls use the model of configs/llm.yaml (C36).

Usage:
    python prep/precheck.py stats
    python prep/precheck.py umbrela [--per_dataset 30] [--per_band 3]
"""
import argparse
import asyncio
import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / "data" / "sageo" / "index_b"
RETR_FILE = ROOT / "data" / "sageo" / "retrieval_b_unit" / "unit_top500.jsonl"
OUT_DIR = ROOT / "data" / "sageo" / "precheck_b_unit"
PROMPT = ROOT / "configs" / "prompts" / "umbrela_zeroshot_bing.yaml"

SETTINGS = [200, 300]
BANDS = [(1, 100), (101, 200), (201, 300), (301, 500)]


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def load_results() -> list[dict]:
    with open(RETR_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def doc_ranks(results: list) -> dict[str, int]:
    """Document rank = rank of its best passage (MaxP)."""
    ranks = {}
    for rank, (chunk_id, _) in enumerate(results, start=1):
        ranks.setdefault(doc_of(chunk_id), rank)
    return ranks


def load_documents(doc_ids=None) -> dict[str, dict]:
    docs = {}
    with open(INDEX_DIR / "documents.jsonl", encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            if doc_ids is None or doc["_id"] in doc_ids:
                docs[doc["_id"]] = doc
    return docs


def summarize(values):
    values = sorted(values)
    return {"median": statistics.median(values), "mean": round(sum(values) / len(values), 1),
            "p10": values[len(values) // 10], "p90": values[9 * len(values) // 10]}


def run_stats():
    sys.path.insert(0, str(ROOT))
    from rag.chunking import enc

    rows = load_results()
    report = {}
    for n in SETTINGS:
        all_ids = {doc_of(c) for r in rows for c, _ in r["results"][:n]}
        docs = load_documents(all_ids)
        top100_docs, topn_docs, own100, ownn = [], [], [], []
        tied = defaultdict(lambda: [0, 0])
        straddle_sizes = []
        union_passages = set()
        for r in rows:
            res = r["results"][:n]
            union_passages.update(c for c, _ in res)
            ranks = doc_ranks(res)
            d100 = [d for d, k in ranks.items() if k <= 100]
            top100_docs.append(len(d100))
            topn_docs.append(len(ranks))
            own = lambda ds: sum(r["query_id"] in docs[d]["source_query_ids"] for d in ds) / max(1, len(ds))
            own100.append(own(d100))
            ownn.append(own(list(ranks)))
            score_count = defaultdict(int)
            for _, s in res:
                score_count[s] += 1
            for rank, (c, s) in enumerate(res, start=1):
                band = next(f"{a}-{b}" for a, b in BANDS if a <= rank <= b)
                tied[band][0] += score_count[s] > 1
                tied[band][1] += 1
            if len(res) > 100:
                group = [i for i, (_, s) in enumerate(res) if s == res[99][1]]
                if group[0] < 99 < group[-1]:
                    straddle_sizes.append(len(group))

        # judging cost (§10.2 ⑥): unique passages in the union of Top-N pools
        passage_tokens = 0
        with open(INDEX_DIR / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry["id"] in union_passages:
                    passage_tokens += len(enc.encode(entry["contents"]))
        meta_tokens = 0
        for d in docs.values():
            headings = " ".join(d.get("headings") or [])[:3500]
            meta = " ".join([d.get("title") or "", d.get("meta_description") or "", headings])
            meta_tokens += len(enc.encode(meta))

        report[f"N={n}"] = {
            "unique_docs_top100": summarize(top100_docs),
            "unique_docs_topN": summarize(topn_docs),
            "own_google_share_top100": round(statistics.mean(own100), 3),
            "own_google_share_topN": round(statistics.mean(ownn), 3),
            "tied_share_by_band": {b: round(x / y, 3) for b, (x, y) in tied.items() if y},
            "queries_with_tie_group_straddling_100": round(len(straddle_sizes) / len(rows), 4),
            "straddling_group_size": summarize(straddle_sizes) if straddle_sizes else None,
            "union_unique_passages": len(union_passages),
            "union_unique_docs": len(all_ids),
            "union_passage_tokens": passage_tokens,
            "union_meta_tokens_once_per_doc": meta_tokens,
        }
        print(json.dumps(report[f"N={n}"], indent=1), flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "stats.json").write_text(json.dumps(report, indent=2))


def parse_score(text: str):
    m = re.search(r"##\s*final score:\s*([0-3])", text, re.I)
    return int(m.group(1)) if m else None


async def judge_all(items, out_path, concurrency=16):
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    import yaml

    load_dotenv(ROOT / ".env.local")
    template = yaml.safe_load(open(PROMPT))["prefix_user"]
    llm = yaml.safe_load(open(ROOT / "configs" / "llm.yaml"))
    client = AsyncOpenAI()
    done = set()
    if out_path.exists():
        done = {(r["query_id"], r["doc_id"]) for r in map(json.loads, open(out_path))}
    todo = [it for it in items if (it["query_id"], it["doc_id"]) not in done]
    sem = asyncio.Semaphore(concurrency)
    usage = {"input": 0, "output": 0}

    async def one(it, out):
        async with sem:
            prompt = template.format(query=it["query"], passage=it["text"])
            for attempt in range(4):
                try:
                    resp = await client.responses.create(model=llm["model"], input=[{"role": "user", "content": prompt}],
                                                         reasoning={"effort": llm["reasoning_effort"]["umbrela"]})
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 ** attempt)
            usage["input"] += resp.usage.input_tokens
            usage["output"] += resp.usage.output_tokens
            rec = {k: it[k] for k in ("query_id", "dataset", "doc_id", "doc_rank", "band")}
            rec["score"] = parse_score(resp.output_text)
            out.write(json.dumps(rec) + "\n")
            out.flush()

    with open(out_path, "a", encoding="utf-8") as out:
        await asyncio.gather(*(one(it, out) for it in todo))
    return usage


def run_umbrela(per_dataset: int, per_band: int, seed: int = 42):
    rows = load_results()
    queries = {}
    for path in sorted((ROOT / "data" / "sageo" / "queries").glob("*_queries_300.jsonl")):
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            queries[q["id"]] = q["text"]
    rng = random.Random(seed)
    by_dataset = defaultdict(list)
    for r in rows:
        by_dataset[r["dataset"]].append(r)
    sample = [r for ds in sorted(by_dataset) for r in rng.sample(by_dataset[ds], per_dataset)]

    picks = []
    for r in sample:
        best = {}
        for rank, (c, _) in enumerate(r["results"], start=1):
            best.setdefault(doc_of(c), (rank, c))
        for a, b in BANDS:
            in_band = [(d, rk, c) for d, (rk, c) in best.items() if a <= rk <= b]
            for d, rk, c in rng.sample(in_band, min(per_band, len(in_band))):
                picks.append({"query_id": r["query_id"], "dataset": r["dataset"], "query": queries[r["query_id"]],
                              "doc_id": d, "doc_rank": rk, "band": f"{a}-{b}", "chunk_id": c})

    chunk_ids = {p["chunk_id"] for p in picks}
    texts = {}
    with open(INDEX_DIR / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["id"] in chunk_ids:
                texts[entry["id"]] = entry["contents"]
    docs = load_documents({p["doc_id"] for p in picks})
    for p in picks:
        d = docs[p["doc_id"]]
        p["text"] = f"Title: {d.get('title') or ''}\nDescription: {d.get('meta_description') or ''}\n\n{texts[p['chunk_id']]}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "umbrela_sample.jsonl"
    print(f"{len(sample)} queries, {len(picks)} judgments", flush=True)
    usage = asyncio.run(judge_all(picks, out_path))
    print("tokens", usage, flush=True)

    results = [json.loads(line) for line in open(out_path)]
    band_counts = defaultdict(lambda: [0, 0, 0])  # relevant, judged, unparsed
    for rec in results:
        c = band_counts[rec["band"]]
        if rec["score"] is None:
            c[2] += 1
            continue
        c[0] += rec["score"] >= 2
        c[1] += 1
    summary = {b: {"relevant_share": round(x / y, 3) if y else None, "judged": y, "unparsed": u}
               for b, (x, y, u) in sorted(band_counts.items(), key=lambda kv: int(kv[0].split("-")[0]))}
    # ④ expected relevant documents per query in each band = documents in band x relevant share
    docs_in_band = defaultdict(list)
    for r in sample:
        ranks = doc_ranks(r["results"])
        for a, b in BANDS:
            docs_in_band[f"{a}-{b}"].append(sum(a <= k <= b for k in ranks.values()))
    for b, s in summary.items():
        s["docs_per_query_mean"] = round(statistics.mean(docs_in_band[b]), 1)
        if s["relevant_share"] is not None:
            s["relevant_docs_per_query_est"] = round(s["docs_per_query_mean"] * s["relevant_share"], 1)
    print(json.dumps(summary, indent=1))
    (OUT_DIR / "umbrela_summary.json").write_text(json.dumps({"usage": usage, "bands": summary}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["stats", "umbrela"])
    parser.add_argument("--per_dataset", type=int, default=30)
    parser.add_argument("--per_band", type=int, default=3)
    args = parser.parse_args()
    if args.step == "stats":
        run_stats()
    else:
        run_umbrela(args.per_dataset, args.per_band)


if __name__ == "__main__":
    main()
