"""Compare retrieval methods on the same 300 queries (no LLM).

A  : SAGEO as-is (per-field top-300, RRF, document score copied to every passage)  -> existing pfk300 run
B0 : SAGEO formula (2) with every passage's own body rank (body search depth K_DEEP), meta fields top-300
B1 : one unit per passage (passage + 4 structural fields), BM25 per field summed with equal boosts
"""
import json, os, random, statistics, sys, time
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path("/home/kms/github/doctor/GradGEO")
os.environ.setdefault("OPENAI_API_KEY", "unused")
from pyserini.search.lucene import LuceneSearcher
from pyserini.index.lucene import LuceneIndexReader

K_DEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
META = ["title", "meta_description", "headings", "jsonld_text"]
OUT = ROOT / "data/sageo/engine_check"
OUT.mkdir(exist_ok=True)
doc_of = lambda c: c.rsplit("_chunk_", 1)[0]

rows = [json.loads(l) for l in open(ROOT / "data/sageo/retrieval_b/bm25_pfk300_top500.jsonl")]
queries = {}
for p in sorted((ROOT / "data/sageo/queries").glob("*_queries_300.jsonl")):
    for l in open(p):
        q = json.loads(l); queries[q["id"]] = q["text"]
sample = random.Random(0).sample(rows, 300)

t0 = time.time()
lucene_b = ROOT / "data/sageo/index_b/lucene"
meta_s = {f: LuceneSearcher(str(lucene_b / f)) for f in META}
body_s = LuceneSearcher(str(lucene_b / "body_text"))
body_r = LuceneIndexReader(str(lucene_b / "body_text"))
unit_s = LuceneSearcher(str(ROOT / "data/sageo/index_b_unit/lucene"))
chunk_to_doc = json.load(open(ROOT / "data/sageo/index_b/jsonl/metadata.json"))["chunk_to_doc"]
doc_to_chunks = defaultdict(list)
for c, d in chunk_to_doc.items():
    doc_to_chunks[d].append(c)
del chunk_to_doc
print("loaded", round(time.time() - t0), flush=True)

# sanity: compute_query_document_score must equal the body search score
q0 = queries[sample[0]["query_id"]]
h = body_s.search(q0, k=3)
for x in h:
    assert abs(body_r.compute_query_document_score(x.docid, q0) - x.score) < 1e-4, "score mismatch"

def run_b0(q):
    doc_scores = defaultdict(float)
    for f, s in meta_s.items():
        for rank, hit in enumerate(s.search(q, k=300), start=1):
            doc_scores[hit.docid] += 1.0 / (60 + rank)
    scores, body_part = {}, {}
    for rank, hit in enumerate(body_s.search(q, k=K_DEEP), start=1):
        b = 1.0 / (60 + rank)
        scores[hit.docid] = b + doc_scores.get(doc_of(hit.docid), 0.0)
        body_part[hit.docid] = b
    for d, ds in doc_scores.items():
        for c in doc_to_chunks.get(d, []):
            if c not in scores:
                scores[c] = ds; body_part[c] = 0.0
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:300]
    return ranked, {c: body_part[c] for c, _ in ranked}

def run_b1(q):
    hits = unit_s.search(q, k=300, fields={"contents": 1.0, **{f: 1.0 for f in META}})
    return [(x.docid, x.score) for x in hits]

def metrics(ranked, own, part):
    """ranked: [(cid, score)] top-300; own: cid -> own body BM25; part: cid -> engine's body contribution."""
    n = len(ranked)
    cnt = Counter(s for _, s in ranked)
    m = {"tie_share": sum(cnt[s] > 1 for _, s in ranked) / n}
    s100 = ranked[99][1] if n >= 100 else None
    idx = [i for i, (_, s) in enumerate(ranked) if s == s100]
    m["straddle"] = float(len(idx) > 1 and idx[0] < 99 < idx[-1])
    m["straddle_size"] = len(idx) if m["straddle"] else None
    top100 = ranked[:100]
    m["biggest_doc_top100"] = Counter(doc_of(c) for c, _ in top100).most_common(1)[0][1]
    m["docs_top100"] = len({doc_of(c) for c, _ in top100})
    m["docs_top300"] = len({doc_of(c) for c, _ in ranked})
    m["own_content_top100"] = sum(own[c] > 0 for c, _ in top100) / len(top100)
    m["own_content_top300"] = sum(own[c] > 0 for c, _ in ranked) / n
    m["body_score_share_top100"] = statistics.mean(part[c] / s for c, s in top100 if s > 0)
    return m

res = {k: defaultdict(list) for k in ("A", "B0", "B1")}
overlap = {"B0": [], "B1": []}
dump = open(OUT / "compare_engines_runs.jsonl", "w")
own_cache = {}
for i, r in enumerate(sample, start=1):
    q = queries[r["query_id"]]
    runs = {"A": r["results"][:300]}
    runs["B0"], b0_part = run_b0(q)
    runs["B1"] = run_b1(q)
    need = {c for run in runs.values() for c, _ in run}
    own = {}
    for c in need:
        key = (r["query_id"], c)
        if key not in own_cache:
            own_cache[key] = body_r.compute_query_document_score(c, q)
        own[c] = own_cache[key]
    body_a = {x.docid: 1.0 / (60 + rk) for rk, x in enumerate(body_s.search(q, k=300), start=1)}
    parts = {"A": {c: body_a.get(c, 0.0) for c, _ in runs["A"]},
             "B0": b0_part,
             "B1": {c: own[c] for c, _ in runs["B1"]}}
    for k, run in runs.items():
        for name, v in metrics(run, own, parts[k]).items():
            if v is not None:
                res[k][name].append(v)
    a_docs = {doc_of(c) for c, _ in runs["A"][:100]}
    for k in ("B0", "B1"):
        overlap[k].append(len(a_docs & {doc_of(c) for c, _ in runs[k][:100]}) / len(a_docs))
    dump.write(json.dumps({"query_id": r["query_id"], "dataset": r["dataset"],
                           "runs": {k: [[c, s, own[c]] for c, s in v] for k, v in runs.items()}}) + "\n")
    if i % 50 == 0:
        print(i, round(time.time() - t0), flush=True)

summary = {}
for k, mm in res.items():
    summary[k] = {}
    for name, v in mm.items():
        summary[k][name] = {"median": round(statistics.median(v), 3), "mean": round(statistics.mean(v), 3)} \
            if name not in ("straddle", "tie_share") else round(statistics.mean(v), 3)
for k in overlap:
    summary[k]["top100_doc_overlap_with_A"] = round(statistics.mean(overlap[k]), 3)
print(json.dumps(summary, indent=1))
(OUT / "compare_engines_summary.json").write_text(json.dumps(summary, indent=2))
