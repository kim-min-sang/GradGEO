"""B1: how much each field contributes to the score of Top-100 passages (300 queries)."""
import json, os, random, statistics, sys
from collections import defaultdict
from pathlib import Path
HERE = Path(__file__).parent; ROOT = Path("/home/kms/github/doctor/GradGEO")
os.environ.setdefault("OPENAI_API_KEY", "unused"); sys.path.insert(0, str(HERE))
from pyserini.search.lucene import LuceneSearcher
import bm25calc as bc
META = ["title", "meta_description", "headings", "jsonld_text"]; FIELDS = ["contents"] + META
doc_of = lambda c: c.rsplit("_chunk_", 1)[0]
rows = [json.loads(l) for l in open(ROOT / "data/sageo/retrieval_b/bm25_pfk300_top500.jsonl")]
queries = {}
for p in sorted((ROOT / "data/sageo/queries").glob("*_queries_300.jsonl")):
    for l in open(p):
        x = json.loads(l); queries[x["id"]] = x["text"]
sample = random.Random(0).sample(rows, 300)
unit = ROOT / "data/sageo/index_b_unit/lucene"
s = LuceneSearcher(str(unit)); f = {k: bc.Field(unit, k) for k in FIELDS}
hits = {r["query_id"]: [(h.docid, h.score) for h in s.search(queries[r["query_id"]], k=100, fields={k: 1.0 for k in FIELDS})] for r in sample}
need = {c for v in hits.values() for c, _ in v}
meta = {k: {} for k in META}
for k in META:
    for l in open(ROOT / f"data/sageo/index_b/jsonl/{k}/docs.jsonl"):
        e = json.loads(l); meta[k][e["id"]] = e["contents"]
body = {}
for l in open(ROOT / "data/sageo/index_b/jsonl/body_text/docs.jsonl"):
    e = json.loads(l)
    if e["id"] in need: body[e["id"]] = e["contents"]
share = defaultdict(list); raw = defaultdict(list); top_field = defaultdict(int); n = 0
for qid, hs in hits.items():
    qc = bc.query_counts(queries[qid])
    for c, total in hs:
        d = doc_of(c)
        parts = {"contents": f["contents"].score(qc, bc.tokens(body[c])), **{k: f[k].score(qc, bc.tokens(meta[k][d])) for k in META}}
        assert abs(sum(parts.values()) - total) < 1e-3 * max(1, total)
        for k, v in parts.items():
            share[k].append(v / total); raw[k].append(v)
        top_field[max(parts, key=parts.get)] += 1; n += 1
print(f"{'field':18s} mean_share  median_score  p90_score  largest_field_share")
for k in FIELDS:
    v = sorted(raw[k])
    print(f"{k:18s} {statistics.mean(share[k]):.3f}       {statistics.median(v):6.2f}        {v[9*len(v)//10]:6.2f}     {top_field[k]/n:.3f}")
