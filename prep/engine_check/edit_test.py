"""Does an engine keep the title's own signal? Edit one document and see how far it moves.

Engines: A (SAGEO as-is), B1 (passage unit, fields kept separate, field scores summed),
         B2 (control: meta + passage merged into one text, the case SAGEO warned about).
Targets per query and engine: one document ranked 1-100 and one ranked 101-300 (rank = best passage).
Edits:   E1 remove the title            (top target)    -> does it fall out of Top-100?
         E2 add the query to the title  (failed target) -> does it enter Top-100?
         E3 add the query to its best passage (failed target) -> same words, body instead of title
"""
import json, os, random, statistics, sys, time
from collections import defaultdict
from pathlib import Path
HERE = Path(__file__).parent
ROOT = Path("/home/kms/github/doctor/GradGEO")
os.environ.setdefault("OPENAI_API_KEY", "unused")
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
from pyserini.search.lucene import LuceneSearcher
import bm25calc as bc
from rag.retrieval import weighted_rrf_fusion

META = ["title", "meta_description", "headings", "jsonld_text"]
FIELDS_A = ["title", "meta_description", "jsonld_text", "headings", "body_text"]  # SAGEO order
doc_of = lambda c: c.rsplit("_chunk_", 1)[0]
t0 = time.time()

rows = [json.loads(l) for l in open(ROOT / "data/sageo/retrieval_b/bm25_pfk300_top500.jsonl")]
queries = {}
for p in sorted((ROOT / "data/sageo/queries").glob("*_queries_300.jsonl")):
    for l in open(p):
        x = json.loads(l); queries[x["id"]] = x["text"]
sample = random.Random(0).sample(rows, 300)

lb = ROOT / "data/sageo/index_b/lucene"
a_search = {f: LuceneSearcher(str(lb / f)) for f in FIELDS_A}
a_title = bc.Field(lb / "title"); a_body = bc.Field(lb / "body_text")
unit_dir = ROOT / "data/sageo/index_b_unit/lucene"
b1_search = LuceneSearcher(str(unit_dir)); b1_f = {f: bc.Field(unit_dir, f) for f in ["contents"] + META}
merged_dir = ROOT / "data/sageo/index_b_merged/lucene"
b2_search = LuceneSearcher(str(merged_dir)); b2_f = bc.Field(merged_dir)
chunk_to_doc = json.load(open(ROOT / "data/sageo/index_b/jsonl/metadata.json"))["chunk_to_doc"]
doc_to_chunks = defaultdict(list)
for c, d in chunk_to_doc.items():
    doc_to_chunks[d].append(c)
meta = {f: {} for f in META}
for f in META:
    for l in open(ROOT / f"data/sageo/index_b/jsonl/{f}/docs.jsonl"):
        e = json.loads(l); meta[f][e["id"]] = e["contents"]
print("loaded", round(time.time() - t0), flush=True)

def best_rank(ranked_ids, doc):
    for i, c in enumerate(ranked_ids, start=1):
        if doc_of(c) == doc:
            return i
    return None

def insert(hits, item_id, score, k):
    """hits: [(id, score)] sorted desc without item_id; place item after equal scores, cut to k."""
    out = [h for h in hits if h[0] != item_id]
    if score > 0:
        pos = next((i for i, h in enumerate(out) if h[1] < score), len(out))
        out.insert(pos, (item_id, score))
    return out[:k]

# pass 1: rankings and targets
plan = []
for r in sample:
    q = queries[r["query_id"]]
    rng = random.Random(r["query_id"])
    a_hits = {f: [(h.docid, h.score) for h in s.search(q, k=300)] for f, s in a_search.items()}
    a_rank = [c for c, _ in weighted_rrf_fusion({f: [d for d, _ in v] for f, v in a_hits.items()},
                                                {f: 1.0 for f in FIELDS_A}, chunk_to_doc, doc_to_chunks)]
    b1 = [(h.docid, h.score) for h in b1_search.search(q, k=1000, fields={f: 1.0 for f in b1_f})]
    b2 = [(h.docid, h.score) for h in b2_search.search(q, k=1000)]
    eng = {"A": a_rank, "B1": [c for c, _ in b1], "B2": [c for c, _ in b2]}
    targets = {}
    for k, ranked in eng.items():
        first = {}
        for i, c in enumerate(ranked[:300], start=1):
            first.setdefault(doc_of(c), (i, c))
        top = [(d, v) for d, v in first.items() if v[0] <= 100]
        fail = [(d, v) for d, v in first.items() if 100 < v[0] <= 300]
        targets[k] = {"top": rng.choice(top) if top else None, "fail": rng.choice(fail) if fail else None}
    plan.append({"r": r, "q": q, "a_hits": a_hits, "b1": b1, "b2": b2, "eng": eng, "targets": targets})
assert [c for c, _ in plan[0]["r"]["results"][:300]] == plan[0]["eng"]["A"][:300], "A does not reproduce SAGEO run"
print("pass 1", round(time.time() - t0), flush=True)

need = {c for p in plan for t in p["targets"].values() for v in t.values() if v for c in doc_to_chunks[v[0]]}
body = {}
for l in open(ROOT / "data/sageo/index_b/jsonl/body_text/docs.jsonl"):
    e = json.loads(l)
    if e["id"] in need:
        body[e["id"]] = e["contents"]
print("texts", len(body), round(time.time() - t0), flush=True)

def edited(doc, edit, q, passage):
    title = meta["title"][doc]
    texts = {c: body[c] for c in doc_to_chunks[doc]}
    if edit == "E1":
        title = ""
    elif edit == "E2":
        title = (title + " " + q).strip()
    elif edit == "E3":
        texts[passage] = texts[passage] + " " + q
    return title, texts

def rank_A(p, doc, edit, passage):
    q = p["q"]; hits = dict(p["a_hits"])
    title, texts = edited(doc, edit, q, passage)
    qc = bc.query_counts(q)
    if edit in ("E1", "E2"):
        hits["title"] = insert(hits["title"], doc, a_title.score(qc, bc.tokens(title)), 300)
    else:
        hits["body_text"] = insert(hits["body_text"], passage, a_body.score(qc, bc.tokens(texts[passage])), 300)
    fused = weighted_rrf_fusion({f: [d for d, _ in v] for f, v in hits.items()}, {f: 1.0 for f in FIELDS_A},
                                chunk_to_doc, doc_to_chunks)
    return best_rank([c for c, _ in fused], doc) or 10**6

def rank_unit(p, key, doc, edit, passage):
    q = p["q"]; qc = bc.query_counts(q)
    title, texts = edited(doc, edit, q, passage) if edit else (meta["title"][doc], {c: body[c] for c in doc_to_chunks[doc]})
    others = [s for c, s in p[key] if doc_of(c) != doc]
    m = {"title": title, **{f: meta[f][doc] for f in META[1:]}}
    if key == "b1":
        meta_score = sum(b1_f[f].score(qc, bc.tokens(m[f])) for f in META)
        best = max(meta_score + b1_f["contents"].score(qc, bc.tokens(t)) for t in texts.values())
    else:
        best = max(b2_f.score(qc, bc.tokens("\n".join([m[f] for f in META] + [t]))) for t in texts.values())
    if best < others[-1]:
        return 1001
    return 1 + sum(s > best for s in others)

res = defaultdict(list)
check = defaultdict(list)
for p in plan:
    for k in ("A", "B1", "B2"):
        for kind, edit_list in (("top", ["E1"]), ("fail", ["E2", "E3"])):
            t = p["targets"][k][kind]
            if not t:
                continue
            doc, (before, passage) = t
            if k != "A":  # sanity: recomputed unedited rank equals the engine's rank (ties may differ by one)
                check[k].append(abs(rank_unit(p, k.lower(), doc, None, passage) - before))
            for e in edit_list:
                after = rank_A(p, doc, e, passage) if k == "A" else rank_unit(p, k.lower(), doc, e, passage)
                res[(k, e)].append((before, after))
print("done", round(time.time() - t0), flush=True)
for k, v in check.items():
    print("sanity", k, "exact", sum(x == 0 for x in v), "/", len(v), "max diff", max(v))

summary = {}
for (k, e), v in sorted(res.items()):
    if e == "E1":
        s = {"n": len(v), "fell_out_of_top100": round(sum(a > 100 for _, a in v) / len(v), 3),
             "median_before": statistics.median(b for b, _ in v), "median_after": statistics.median(a for _, a in v)}
    else:
        s = {"n": len(v), "entered_top100": round(sum(a <= 100 for _, a in v) / len(v), 3),
             "median_before": statistics.median(b for b, _ in v), "median_after": statistics.median(a for _, a in v)}
    summary[f"{k} {e}"] = s
    print(k, e, s)
(ROOT / "data/sageo/engine_check" / "edit_test.json").write_text(json.dumps(summary, indent=2))
