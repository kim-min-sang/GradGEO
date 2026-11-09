"""
Mini comparison on the retrieval layer (dev split), before the reranker cache and GE
runs exist. The same rewrites can later be scored by the reranker on the GPU server.

    targets   dev queries: one UMBRELA-relevant (>= 2) document whose best passage is
              ranked 101-300 by the passage-unit engine (C29), tried in seeded random
              order, at most 6 judgments per query; documents with more passages than
              the corpus p99 are skipped (handoff §10.4). p* = its best-ranked passage
              (§8.1). UMBRELA text = title + meta description + p*, as in prep/precheck.py.
    rewrite   --method
                stage_aware, content_improvement   SAGEO protocol: whole document,
                    scope all, the method's original prompt, SAGEO's DocumentOptimizer
                    (gpt-5-mini, flex tier with fallback), one call per target
                stage_aware_q   StageAware with "Target query: {q}" and one sentence
                    asking to optimize for it put in front of the original prompt (§11.2)
                gradgeo_r   GradGEO with the free-axis R head only (experiments/r_head_free.py):
                    gradient at p* picks the axes, the head picks each target level
                    from the current level (optimization/gradient_strategy.py, §8.2 C32),
                    schema Rewriter on p* + the fields of the chosen axes (§9)
                random      same k as gradgeo_r, axes and target levels drawn at random (§11.1)
              gradgeo_r and random use the schema's Rewriter text and need --schema_reviewed
              (handoff §15 step 0). Prescription settings here are placeholders until the
              dev intervention gate sets them: tau 0.25, k_max 5.
    evaluate  each rewritten document d' is processed like the index (SAGEO
              preprocess_document with the environment-B chunker); every d' passage is
              scored with the B1 formula (per-field BM25 summed, collection statistics of
              the current index); d's new rank = its best passage among the original
              Top-500 scores of the other documents (> 500 when below all of them).
              Reports H@100, H@20, rank change and body length ratio per method.
    shift     the free axes of d' at its best B1 passage, measured as for the R head
              (raw values, train-split quantile transforms), against z(p*) before the
              rewrite; for the GradGEO arms, whether each prescribed level was reached;
              the R head's p before and after; query-term density (query-term tokens per
              100 tokens) of that passage and of the four structural fields. Check: the
              unchanged document reproduces z(p*).

Outputs under data/sageo/compare_mini/.

Usage:
    python experiments/compare_mini.py targets
    python experiments/compare_mini.py rewrite --method stage_aware
    python experiments/compare_mini.py prescribe
    python experiments/compare_mini.py rewrite --method gradgeo_r --schema_reviewed
    python experiments/compare_mini.py evaluate
    python experiments/compare_mini.py shift
"""
import argparse
import asyncio
import contextvars
import json
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAGEO_ROOT = ROOT / "third_party" / "SAGEO_Arena"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SAGEO_ROOT))

DATA = ROOT / "data" / "sageo"
OUT = DATA / "compare_mini"
UNIT_INDEX = DATA / "index_b_unit" / "lucene"
META_FIELDS = ["title", "meta_description", "headings", "jsonld_text"]
BAND = (101, 300)
MAX_TRIES = 6
SEED = 42
SAGEO_METHODS = ["stage_aware", "stage_aware_q", "content_improvement"]
GRAD_METHODS = ["gradgeo_r", "random"]
FREE_AXES = ["unique_words", "body_query_alignment", "jsonld", "heading_hierarchy",
             "structural_query_alignment", "list_table", "emphasis", "paragraph_length"]  # = r_head_free.AXES
QUERY_PREFIX = "Target query: {query}\nOptimize the document for this target query.\n\n"


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def load_queries() -> dict[int, dict]:
    queries = {}
    for path in sorted((DATA / "queries").glob("*_queries_300.jsonl")):
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            queries[q["id"]] = q
    return queries


def load_documents(doc_ids) -> dict[str, dict]:
    docs = {}
    with open(DATA / "index_b" / "documents.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["_id"] in doc_ids:
                docs[d["_id"]] = d
    return docs


def load_passages(chunk_ids) -> dict[str, str]:
    texts = {}
    with open(DATA / "index_b" / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["id"] in chunk_ids:
                texts[entry["id"]] = entry["contents"]
    return texts


def dev_results() -> dict[int, list]:
    dev = set(json.loads((DATA / "split.json").read_text())["dev"])
    rows = {}
    for line in open(DATA / "retrieval_b_unit" / "unit_top500.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["query_id"] in dev:
            rows[r["query_id"]] = r["results"]
    return rows


# ---------------------------------------------------------------- targets

def run_targets():
    from prep.precheck import judge_all

    OUT.mkdir(parents=True, exist_ok=True)
    queries = load_queries()
    rows = dev_results()
    rng = random.Random(SEED)
    candidates = {}
    for qid in sorted(rows):
        best = {}
        for rank, (c, _) in enumerate(rows[qid], start=1):
            best.setdefault(doc_of(c), (rank, c))
        cands = [(d, rk, c) for d, (rk, c) in best.items() if BAND[0] <= rk <= BAND[1]]
        rng.shuffle(cands)
        candidates[qid] = cands
    need_c = {c for cands in candidates.values() for _, _, c in cands}
    n_passages, texts = Counter(), {}
    with open(DATA / "index_b" / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            n_passages[doc_of(entry["id"])] += 1
            if entry["id"] in need_c:
                texts[entry["id"]] = entry["contents"]
    values = sorted(n_passages.values())
    p99 = values[int(0.99 * (len(values) - 1))]
    for qid in candidates:
        candidates[qid] = [x for x in candidates[qid] if n_passages[x[0]] <= p99]
    docs = load_documents({d for cands in candidates.values() for d, _, _ in cands[:MAX_TRIES]})

    cache = OUT / "umbrela_targets.jsonl"
    targets = {}
    usage_total = {"input": 0, "output": 0}
    for attempt in range(MAX_TRIES):
        todo = [(qid, candidates[qid][attempt]) for qid in candidates
                if qid not in targets and len(candidates[qid]) > attempt]
        if not todo:
            break
        items = []
        for qid, (d, rk, c) in todo:
            doc = docs[d]
            items.append({"query_id": qid, "dataset": queries[qid]["dataset"], "query": queries[qid]["text"],
                          "doc_id": d, "doc_rank": rk, "band": f"{BAND[0]}-{BAND[1]}",
                          "text": f"Title: {doc.get('title') or ''}\nDescription: {doc.get('meta_description') or ''}\n\n{texts[c]}"})
        usage = asyncio.run(judge_all(items, cache))
        for k in usage_total:
            usage_total[k] += usage[k]
        judged = {(r["query_id"], r["doc_id"]): r["score"] for r in map(json.loads, open(cache))}
        for qid, (d, rk, c) in todo:
            if (judged.get((qid, d)) or 0) >= 2:
                targets[qid] = {"query_id": qid, "dataset": queries[qid]["dataset"], "doc_id": d,
                                "rank": rk, "p_star": c}
        print(f"round {attempt + 1}: {len(todo)} judged, {len(targets)} targets", flush=True)

    with open(OUT / "targets.jsonl", "w", encoding="utf-8") as f:
        for qid in sorted(targets):
            f.write(json.dumps(targets[qid]) + "\n")
    per_ds = Counter(t["dataset"] for t in targets.values())
    print(f"{len(targets)} targets from {len(rows)} dev queries, p99 passages per document = {p99}")
    print("per dataset:", dict(sorted(per_ds.items())))
    print("UMBRELA tokens this run:", usage_total)


def load_targets() -> list[dict]:
    return [json.loads(line) for line in open(OUT / "targets.jsonl", encoding="utf-8")]


# ---------------------------------------------------------------- SAGEO rewrites

def run_sageo_rewrite(method: str, concurrency: int = 12):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local")
    from src.modules.optimizer import DocumentOptimizer
    from src.modules.utils import sageo_methods
    from src.modules.utils.sageo_methods import TARGET_FIELDS_ALL, StageAware

    usage_var = contextvars.ContextVar("usage")

    class Optimizer(DocumentOptimizer):
        async def _call_api_with_flex(self, prompt, output_format):
            response = await super()._call_api_with_flex(prompt, output_format)
            usage_var.get().append((response.usage.input_tokens, response.usage.output_tokens))
            return response

    queries = load_queries()
    targets = load_targets()
    out_path = OUT / f"rewrites_{method}.jsonl"
    done = {json.loads(line)["query_id"] for line in open(out_path)} if out_path.exists() else set()
    todo = [t for t in targets if t["query_id"] not in done]
    docs = load_documents({t["doc_id"] for t in todo})
    optimizer = Optimizer()
    sem = asyncio.Semaphore(concurrency)
    start = time.time()

    async def one(t, out):
        name = method
        if method == "stage_aware_q":
            q = queries[t["query_id"]]["text"].replace("{", "{{").replace("}", "}}")
            name = f"stage_aware_q_{t['query_id']}"
            cls = type(name, (StageAware,), {"name": name, "prompt_template_all":
                                              QUERY_PREFIX.format(query=q) + StageAware.prompt_template_all})
            sageo_methods.GEO_METHODS[name] = cls
        async with sem:
            usage_var.set([])
            rec = {"query_id": t["query_id"], "doc_id": t["doc_id"], "method": method}
            try:
                d2 = await optimizer.optimize_document(docs[t["doc_id"]], name, TARGET_FIELDS_ALL)
                rec["fields"] = {k: d2.get(k) for k in ("title", "meta_description", "headings", "jsonld_text", "jsonld", "body_text")}
                rec["ok"] = True
            except Exception as e:  # reported, not retried (one call per target)
                rec["ok"] = False
                rec["error"] = repr(e)[:500]
            rec["usage"] = usage_var.get()
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

    async def main():
        with open(out_path, "a", encoding="utf-8") as out:
            await asyncio.gather(*(one(t, out) for t in todo))

    asyncio.run(main())
    recs = [json.loads(line) for line in open(out_path)]
    tokens_in = sum(u[0] for r in recs for u in r["usage"])
    tokens_out = sum(u[1] for r in recs for u in r["usage"])
    print(f"{method}: {sum(r['ok'] for r in recs)}/{len(recs)} ok, tokens in {tokens_in:,} out {tokens_out:,} "
          f"({time.time() - start:.0f}s)")


# ---------------------------------------------------------------- GradGEO-R and random rewrites

def visible_split(headings) -> tuple[list[str], list[str]]:
    """Headings the generator shows (prep/rule_axes.visible_headings) and the raw headings after them."""
    from prep.rule_axes import visible_headings
    shown = visible_headings(headings)
    used, seen = 0, 0
    for h in headings or []:
        used += 1
        if h and h.strip():
            seen += 1
            if seen == len(shown):
                break
    return shown, list((headings or [])[used:]) if shown else list(headings or [])


def hidden_jsonld(jsonld) -> list:
    """JSON-LD objects the generator view drops but the engine's jsonld_text still reads
    (e.g. a bare {"@type": "BreadcrumbList"}). The Rewriter never sees them, so they are
    kept when it returns a new list, like the headings after the visible ones."""
    from src.modules.utils.jsonld_extractor import filter_jsonld_for_generator
    out = []

    def walk(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            if not filter_jsonld_for_generator([item]):
                out.append(item)
            elif isinstance(item.get("@graph"), list):
                walk(item["@graph"])

    walk(jsonld or [])
    return out


def run_prescriptions():
    import numpy as np
    import torch
    from models.stage_heads import StageHead
    from optimization.gradient_strategy import axis_levels, gradient, log_p_final, prescribe, random_prescription
    from rewriting.prompt_builder import AXES

    saved = torch.load(DATA / "r_head_free" / "r_head_top300.pt", weights_only=False)
    head = StageHead(saved["in_dim"])
    head.load_state_dict(saved["state_dict"])
    head.eval()
    zf = np.load(DATA / "r_head_free" / "z_top300.npz")
    row = {(int(q), c): i for i, (q, c) in enumerate(zip(zf["query_id"], zf["chunk_id"]))}
    axes = [AXES[a] for a in FREE_AXES]
    out = []
    for t in load_targets():
        z = zf["z"][row[(t["query_id"], t["p_star"])]].astype(np.float64)
        x = np.concatenate([z, saved["pca_by_query"][t["query_id"]]])
        g = gradient([head], x, len(z))
        rec = {"query_id": t["query_id"], "z": z.round(3).tolist(), "g": g.round(4).tolist()}
        grad = prescribe(g, z, axes, log_p_final([head], x[len(z):]))
        rand = random_prescription(len(grad), z, axes, np.random.default_rng([SEED, t["query_id"]]))
        rec["gradgeo_r"] = [(FREE_AXES[i], c, v, gain) for i, c, v, gain in grad]
        rec["random"] = [(FREE_AXES[i], c, v, None) for i, c, v, _ in rand]
        out.append(rec)
    (OUT / "prescriptions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    levels = {a["id"]: axis_levels(a) for a in axes}
    steps = Counter((a, abs(levels[a].index(v) - levels[a].index(c))) for r in out for a, c, v, _ in r["gradgeo_r"])
    print(f"gradgeo_r: k distribution {dict(sorted(Counter(len(r['gradgeo_r']) for r in out).items()))}, "
          f"(axis, levels moved) {dict(sorted(steps.items()))}")


def run_grad_rewrite(method: str, concurrency: int = 16):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local")
    from pydantic import BaseModel
    from src.modules.optimizer import DocumentOptimizer
    from src.modules.utils.jsonld_extractor import filter_jsonld_for_generator
    from rewriting.prompt_builder import build_prompt
    from rewriting.splice import splice_passage
    from rag.chunking import enc

    class Patch(BaseModel):
        passage: str | None
        title: str | None
        meta_description: str | None
        headings: list[str] | None
        jsonld: str | None

    queries = load_queries()
    targets = {t["query_id"]: t for t in load_targets()}
    plans = {r["query_id"]: r[method] for r in map(json.loads, open(OUT / "prescriptions.jsonl"))}
    out_path = OUT / f"rewrites_{method}.jsonl"
    done = {json.loads(line)["query_id"] for line in open(out_path)} if out_path.exists() else set()
    todo = [targets[q] for q in sorted(targets) if q not in done]
    docs = load_documents({t["doc_id"] for t in todo})
    texts = load_passages({t["p_star"] for t in todo})
    optimizer = DocumentOptimizer()
    sem = asyncio.Semaphore(concurrency)
    start = time.time()

    async def one(t, out):
        doc = docs[t["doc_id"]]
        changes = [(a, c, v) for a, c, v, _ in plans[t["query_id"]]]
        rec = {"query_id": t["query_id"], "doc_id": t["doc_id"], "method": method, "changes": changes, "usage": []}
        if not changes:  # k = 0: the document is left as it is
            rec.update(ok=True, fields={}, unchanged=True)
            out.write(json.dumps(rec) + "\n")
            return
        shown, rest = visible_split(doc.get("headings"))
        meta = {"title": doc.get("title"), "meta_description": doc.get("meta_description"), "headings": shown,
                "jsonld": filter_jsonld_for_generator(doc.get("jsonld") or [])}
        passage = texts[t["p_star"]]
        prompt, allowed = build_prompt(queries[t["query_id"]]["text"], meta, passage, changes)
        async with sem:
            try:
                response = await optimizer._call_api_with_flex(prompt, Patch)
                rec["usage"].append((response.usage.input_tokens, response.usage.output_tokens))
                patch = response.output_parsed
                fields, notes = {}, []
                if "passage" in allowed and patch.passage:
                    body, info = splice_passage(doc.get("body_text") or "", int(t["p_star"].rsplit("_chunk_", 1)[1]), patch.passage)
                    fields["body_text"] = body
                    rec["passage_length_ratio"] = len(enc.encode(patch.passage)) / max(1, len(enc.encode(passage)))
                    rec["passage_new"] = patch.passage
                    rec["splice"] = info
                for f in ("title", "meta_description"):
                    if f in allowed and getattr(patch, f):
                        fields[f] = getattr(patch, f)
                if "headings" in allowed and patch.headings:
                    fields["headings"] = patch.headings + rest
                if "jsonld" in allowed and patch.jsonld:
                    try:
                        value = json.loads(patch.jsonld)
                        fields["jsonld"] = (value if isinstance(value, list) else [value]) + hidden_jsonld(doc.get("jsonld"))
                        rec["jsonld_hidden_kept"] = True
                    except json.JSONDecodeError:
                        notes.append("jsonld not valid JSON, kept original")
                rec.update(ok=True, fields=fields, notes=notes)
            except Exception as e:  # reported, not retried (one call per target)
                rec.update(ok=False, error=repr(e)[:500])
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

    async def main():
        with open(out_path, "a", encoding="utf-8") as out:
            await asyncio.gather(*(one(t, out) for t in todo))

    asyncio.run(main())
    recs = [json.loads(line) for line in open(out_path)]
    tokens_in = sum(u[0] for r in recs for u in r["usage"])
    tokens_out = sum(u[1] for r in recs for u in r["usage"])
    print(f"{method}: {sum(r['ok'] for r in recs)}/{len(recs)} ok, {sum(bool(r.get('unchanged')) for r in recs)} unchanged (k = 0), "
          f"tokens in {tokens_in:,} out {tokens_out:,} ({time.time() - start:.0f}s)")


# ---------------------------------------------------------------- evaluation

class B1Scorer:
    """B1 score of arbitrary passages: per-field BM25 against the current index statistics, summed."""

    def __init__(self):
        os.environ.setdefault("OPENAI_API_KEY", "unused")
        from pyserini.search.lucene import LuceneSearcher  # noqa: F401  (sets the JVM classpath before jnius)
        sys.path.insert(0, str(ROOT / "prep" / "engine_check"))
        import bm25calc
        from rag.chunking import chunk_text
        from src.modules import retriever_bm25
        retriever_bm25.chunk_text = chunk_text  # same as rag/build_index.py
        self.bc = bm25calc
        self.preprocess = retriever_bm25.preprocess_document
        self.fields = {f: bm25calc.Field(UNIT_INDEX, f) for f in ["contents"] + META_FIELDS}

    def passage_scores(self, query: str, doc: dict) -> list[float]:
        p = self.preprocess(doc, 0)
        qc = self.bc.query_counts(query)
        meta = sum(self.fields[f].score(qc, self.bc.tokens(p[f][1])) for f in META_FIELDS)
        return [meta + self.fields["contents"].score(qc, self.bc.tokens(text)) for _, text in p["body_text"]]


def apply_fields(doc: dict, fields: dict) -> dict:
    d2 = dict(doc)
    for k, v in fields.items():
        if v is not None:
            d2[k] = v
    return d2


def run_evaluate():
    from rag.chunking import enc

    queries = load_queries()
    targets = {t["query_id"]: t for t in load_targets()}
    rows = dev_results()
    docs = load_documents({t["doc_id"] for t in targets.values()})
    scorer = B1Scorer()
    methods = sorted(p.stem.replace("rewrites_", "") for p in OUT.glob("rewrites_*.jsonl"))
    rewrites = {m: {r["query_id"]: r for r in map(json.loads, open(OUT / f"rewrites_{m}.jsonl"))} for m in methods}

    def rank_of(qid, doc_id, score):
        others = [s for c, s in rows[qid] if doc_of(c) != doc_id]
        if score < others[-1]:
            return 501
        return 1 + sum(o > score for o in others)

    per_target = []
    check = []
    for qid, t in sorted(targets.items()):
        q = queries[qid]["text"]
        doc = docs[t["doc_id"]]
        orig_best = max(scorer.passage_scores(q, doc))
        engine_best = next(s for c, s in rows[qid] if doc_of(c) == t["doc_id"])
        check.append(abs(orig_best - engine_best) < 1e-3)
        rec = {"query_id": qid, "dataset": t["dataset"], "orig_rank": rank_of(qid, t["doc_id"], orig_best)}
        body_tokens = len(enc.encode(doc.get("body_text") or ""))
        for m in methods:
            r = rewrites[m].get(qid)
            if r is None or not r["ok"]:
                rec[m] = {"ok": False, "rank": rec["orig_rank"]}  # failure = document left as it was
                continue
            d2 = apply_fields(doc, r["fields"])
            rec[m] = {"ok": True, "rank": rank_of(qid, t["doc_id"], max(scorer.passage_scores(q, d2))),
                      "length_ratio": len(enc.encode(d2.get("body_text") or "")) / max(1, body_tokens)}
        per_target.append(rec)

    summary = {"targets": len(per_target), "score_check_orig_equals_engine": f"{sum(check)}/{len(check)}",
               "orig": {"H@100": 0.0, "H@20": 0.0, "median_rank": statistics.median(r["orig_rank"] for r in per_target)}}
    for m in methods:
        ranks = [r[m]["rank"] for r in per_target]
        deltas = [r["orig_rank"] - r[m]["rank"] for r in per_target]
        ratios = [r[m]["length_ratio"] for r in per_target if r[m]["ok"]]
        summary[m] = {
            "ok": f"{sum(r[m]['ok'] for r in per_target)}/{len(per_target)}",
            "H@100": round(sum(k <= 100 for k in ranks) / len(ranks), 3),
            "H@20": round(sum(k <= 20 for k in ranks) / len(ranks), 3),
            "median_rank": statistics.median(ranks),
            "median_rank_gain": statistics.median(deltas),
            "worse_than_orig": round(sum(d < 0 for d in deltas) / len(deltas), 3),
            "median_body_length_ratio": round(statistics.median(ratios), 2) if ratios else None,
        }
    by_ds = defaultdict(lambda: defaultdict(list))
    for r in per_target:
        for m in methods:
            by_ds[r["dataset"]][m].append(r[m]["rank"] <= 100)
    summary["H@100_by_dataset"] = {ds: {m: f"{sum(v)}/{len(v)}" for m, v in ms.items()} for ds, ms in sorted(by_ds.items())}
    # datasets have 10-27 targets each; the macro average weighs every dataset equally
    for m in methods:
        summary[m]["H@100_macro"] = round(statistics.mean(sum(ms[m]) / len(ms[m]) for ms in by_ds.values()), 3)
    (OUT / "eval_per_target.jsonl").write_text("".join(json.dumps(r) + "\n" for r in per_target))
    (OUT / "eval_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=1))


def run_shift():
    import numpy as np
    import torch
    from scipy.stats import spearmanr
    from experiments.r_head_free import fit_quantiles, load as load_pool, z_from_raw
    from models.stage_heads import StageHead
    from optimization.gradient_strategy import level_of
    from prep import rule_axes as ra
    from prep.query_features import EMB_DIR, Embedder
    from rewriting.prompt_builder import AXES

    scorer = B1Scorer()
    from pyserini.analysis import Analyzer, get_lucene_analyzer
    from pyserini.index.lucene import LuceneIndexReader
    tokens = Analyzer(get_lucene_analyzer()).analyze
    reader = LuceneIndexReader(str(UNIT_INDEX))
    n_units = reader.stats()["documents"]
    idf_cache = {}

    def idf(t):  # = prep/query_features.run_axes
        if t not in idf_cache:
            df = reader.get_term_counts(t, analyzer=None)[0]
            idf_cache[t] = float(np.log(1 + (n_units - df + 0.5) / (df + 0.5)))
        return idf_cache[t]

    def share(terms, field_tokens):
        total = sum(idf(t) for t in terms)
        return sum(idf(t) for t in terms & set(field_tokens)) / total if total else 0.0

    def density(terms, field_tokens):
        return 100 * sum(t in terms for t in field_tokens) / max(1, len(field_tokens))

    split, query_id, _, _, _, raw_pool = load_pool(300)
    qts = fit_quantiles(raw_pool, np.isin(query_id, split["train"]))
    del raw_pool
    saved = torch.load(DATA / "r_head_free" / "r_head_top300.pt", weights_only=False)
    head = StageHead(saved["in_dim"])
    head.load_state_dict(saved["state_dict"])
    head.eval()
    q_ids = json.loads((EMB_DIR / "queries_ids.json").read_text())
    q_vec = dict(zip(q_ids, np.load(EMB_DIR / "queries.npy").astype(np.float32)))

    queries = load_queries()
    targets = {t["query_id"]: t for t in load_targets()}
    docs = load_documents({t["doc_id"] for t in targets.values()})
    plans = {r["query_id"]: r for r in map(json.loads, open(OUT / "prescriptions.jsonl"))}
    ranks = {r["query_id"]: r for r in map(json.loads, open(OUT / "eval_per_target.jsonl"))}
    methods = sorted(p.stem.replace("rewrites_", "") for p in OUT.glob("rewrites_*.jsonl"))
    rewrites = {m: {r["query_id"]: r for r in map(json.loads, open(OUT / f"rewrites_{m}.jsonl"))} for m in methods}

    recs, texts = [], []
    for m in ["orig"] + methods:
        for qid, t in sorted(targets.items()):
            r = rewrites[m].get(qid) if m != "orig" else None
            d2 = apply_fields(docs[t["doc_id"]], r["fields"]) if r and r["ok"] else docs[t["doc_id"]]
            p = scorer.preprocess(d2, 0)
            scores = scorer.passage_scores(queries[qid]["text"], d2)
            best = int(np.argmax(scores))
            text = p["body_text"][best][1]
            terms = set(tokens(queries[qid]["text"]))
            body_tok = tokens(text)
            meta_tok = {f: tokens(p[f][1] or "") for f in META_FIELDS}
            recs.append({"method": m, "query_id": qid, "dataset": t["dataset"], "best_passage": best,
                         "raw": {"unique_words": ra.mtld(text), "body_terms": share(terms, body_tok),
                                 "jsonld": ra.level_jsonld(d2.get("jsonld")),
                                 "heading_hierarchy": ra.level_heading_hierarchy(d2.get("headings")),
                                 "struct_terms": float(np.mean([share(terms, meta_tok[f]) for f in META_FIELDS])),
                                 "list_table": ra.level_list_table(ra.count_list_table(text)),
                                 "emphasis": ra.level_emphasis(ra.count_emphasis(text)),
                                 "paragraph_length": ra.level_paragraph_length(ra.longest_paragraph_sentences(text))},
                         "density_passage": density(terms, body_tok),
                         "density_struct": density(terms, [x for f in META_FIELDS for x in meta_tok[f]])})
            texts.append(text)
        print(f"{m}: measured ({len(recs)} documents)", flush=True)
    vecs = Embedder()(texts).astype(np.float32)
    raw = {k: np.array([r["raw"][k] for r in recs], dtype=np.float64) for k in recs[0]["raw"]}
    raw["body_cos"] = np.array([v @ q_vec[r["query_id"]] for v, r in zip(vecs, recs)])
    z_after = z_from_raw(raw, qts)
    x = np.concatenate([z_after, np.stack([saved["pca_by_query"][r["query_id"]] for r in recs])], 1)
    with torch.no_grad():
        p_after = torch.sigmoid(head(torch.tensor(x, dtype=torch.float32))).numpy().ravel()

    axes = [AXES[a] for a in FREE_AXES]
    p_before = {}
    for r, z, pa in zip(recs, z_after, p_after):
        r.pop("raw")
        r["z_after"] = z.round(3).tolist()
        r["p_after"] = float(pa)
        if r["method"] == "orig":
            p_before[r["query_id"]] = float(pa)
    summary = {}
    orig = [r for r in recs if r["method"] == "orig"]
    diffs = [np.abs(np.array(r["z_after"]) - np.array(plans[r["query_id"]]["z"])).max() for r in orig]
    summary["check_unchanged_reproduces_z"] = f"{sum(d < 2e-3 for d in diffs)}/{len(diffs)} (max diff {max(diffs):.4f})"
    for m in ["orig"] + methods:
        rs = [r for r in recs if r["method"] == m]
        dz = np.array([np.array(r["z_after"]) - np.array(plans[r["query_id"]]["z"]) for r in rs])
        moved = np.array([[level_of(axes[i], r["z_after"][i]) != level_of(axes[i], plans[r["query_id"]]["z"][i])
                           for i in range(len(FREE_AXES))] for r in rs])
        s = {"mean_dz": dict(zip(FREE_AXES, dz.mean(0).round(3).tolist())),
             "level_changed": dict(zip(FREE_AXES, moved.mean(0).round(3).tolist())),
             "density_passage": round(float(np.mean([r["density_passage"] for r in rs])), 2),
             "density_struct": round(float(np.mean([r["density_struct"] for r in rs])), 2),
             "mean_p_R": round(float(np.mean([r["p_after"] for r in rs])), 3)}
        if m != "orig":
            gain = [ranks[r["query_id"]]["orig_rank"] - ranks[r["query_id"]][m]["rank"] for r in rs]
            dlogit = [np.log(r["p_after"] / (1 - r["p_after"])) - np.log(p_before[r["query_id"]] / (1 - p_before[r["query_id"]]))
                      for r in rs]
            s["spearman_dlogit_vs_rank_gain"] = round(float(spearmanr(dlogit, gain).correlation), 3)
        if m in plans[next(iter(plans))]:
            reached, toward, per_axis = [], [], defaultdict(list)
            for r in rs:
                for a, cur, tgt, _ in plans[r["query_id"]][m]:
                    i = FREE_AXES.index(a)
                    before, after = plans[r["query_id"]]["z"][i], r["z_after"][i]
                    ok = level_of(axes[i], after) == tgt
                    reached.append(ok)
                    toward.append((after - before) * (tgt - cur) > 0)
                    per_axis[a].append(ok)
            s["prescribed_changes"] = len(reached)
            s["reached_target_level"] = round(float(np.mean(reached)), 3) if reached else None
            s["moved_toward_target"] = round(float(np.mean(toward)), 3) if toward else None
            s["reached_by_axis"] = {a: f"{sum(v)}/{len(v)}" for a, v in sorted(per_axis.items())}
        summary[m] = s
    (OUT / "shift_per_target.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (OUT / "shift_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["targets", "prescribe", "rewrite", "evaluate", "shift"])
    parser.add_argument("--method", choices=SAGEO_METHODS + GRAD_METHODS)
    parser.add_argument("--schema_reviewed", action="store_true",
                        help="the researcher has reviewed configs/strategy_schema.yaml (handoff §15 step 0)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step == "targets":
        run_targets()
    elif args.step == "prescribe":
        run_prescriptions()
    elif args.step == "rewrite" and args.method in GRAD_METHODS:
        if not args.schema_reviewed:
            sys.exit("gradgeo_r / random use the schema's Rewriter text: run with --schema_reviewed after the review")
        run_grad_rewrite(args.method)
    elif args.step == "rewrite":
        run_sageo_rewrite(args.method)
    elif args.step == "evaluate":
        run_evaluate()
    else:
        run_shift()


if __name__ == "__main__":
    main()
