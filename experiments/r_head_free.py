"""
R head on the free axes only (no judge-LLM cost): can [z | PCA(query)] predict
whether a passage of the Top-N pool is in the BM25 Top-100?

Free axes (handoff numbering): #8 unique_words, #11 body_query_alignment,
#14 jsonld, #15 heading_hierarchy, #17 structural_query_alignment, #18 list_table,
#19 emphasis, #20 paragraph_length. Continuous axes #8 and #11 are quantile-
transformed with the train split; #11 = mean of its two transformed parts (key-term
share, embedding cosine) when the cosine is available, else the key-term share alone.
#17 is not quantile-transformed (schema v0.6): since v0.9 it is the mean over its four fields
of min(share / 0.75, 1); diagnostic variants use the v0.8 value (mean of the raw shares) and
the mean of discrete field levels (full >= 0.75, half > 0.25).
The key-term shares of #11 and #17 are idf-weighted (schema v0.5, revised 2026-11-08);
diagnostic variants marked "flat" use the unweighted shares (the earlier definition).
Visibility mask (schema v0.10 `hidden_from`, C37): the "free axes" variants leave out the axes
the BM25 stage cannot see (#15: the index drops heading tags; #19: the analyzer drops the
markdown marks); one diagnostic variant keeps all 8.

Train on the train split, report on the validation split (test untouched):
    pooled AUC         y_R over all validation (query, passage) pairs
    per-query AUC      the same inside each query, averaged (what the gradient uses)
    Spearman           logit vs. BM25 rank inside each query, averaged
    relevant-only AUC  UMBRELA-relevant documents (>= 2) of the pre-check sample:
                       Top-100 vs. 101-N, document logit = its best passage's logit
    permutation importance of each axis (10 shuffles, pooled AUC drop)

The head used for prescriptions (experiments/compare_mini.py) is the seed-0 model of
the "free axes" variant whose PCA size (0 / 16 / 32) has the best validation
per-query AUC (handoff §6); it is saved with its axes (columns of z) and the z of
all 8 free axes for every pool pair.

Usage:
    python experiments/r_head_free.py [--n 300] [--seeds 3]
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import yaml
from scipy.stats import spearmanr
from sklearn.decomposition import PCA
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import QuantileTransformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models.stage_heads import StageHead  # noqa: E402
from training.pairwise_losses import ranked_pair_bce_loss  # noqa: E402

DATA = ROOT / "data" / "sageo"
FEAT = DATA / "features"
OUT_DIR = DATA / "r_head_free"
AXES = ["#8 unique_words", "#11 body_query_alignment", "#14 jsonld", "#15 heading_hierarchy",
        "#17 structural_query_alignment", "#18 list_table", "#19 emphasis", "#20 paragraph_length"]
SCHEMA_AXES = {a["id"]: a for a in yaml.safe_load(open(ROOT / "configs" / "strategy_schema.yaml", encoding="utf-8"))["strategies"]}
R_COLS = [c for c, a in enumerate(AXES) if "R" not in SCHEMA_AXES[a.split()[1]].get("hidden_from", [])]  # visibility mask
BOUNDARY = 100
# #17 keeps its own scale (schema v0.6, 2026-11-09; since v0.9 the mean of min(field share / 0.75, 1)): its
# rubric names absolute levels (none / some / all key terms), and the quantile scale called a share of 0.58 "all"
RAW_SCALE = {"struct_terms"}


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def load(n):
    split = json.loads((DATA / "split.json").read_text())
    rows = {}
    for line in open(DATA / "retrieval_b_unit" / "unit_top500.jsonl", encoding="utf-8"):
        r = json.loads(line)
        rows[r["query_id"]] = r["results"][:n]
    q = np.load(FEAT / f"q_axes_top{n}.npz")
    rule = np.load(FEAT / f"rule_axes_top{n}.npz")
    rule_row = {c: i for i, c in enumerate(rule["chunk_id"])}
    idx = np.array([rule_row[c] for c in q["chunk_id"]])
    raw = {
        "unique_words": rule["unique_words"][idx],
        "body_terms": q["body_query_terms"],
        "body_cos": q["body_query_cosine"] if "body_query_cosine" in q.files else None,
        "body_terms_flat": q["body_query_terms_flat"] if "body_query_terms_flat" in q.files else None,
        "struct_terms_flat": q["structural_query_terms_flat"] if "structural_query_terms_flat" in q.files else None,
        "jsonld": rule["jsonld"][idx],
        "heading_hierarchy": rule["heading_hierarchy"][idx],
        "struct_terms": q["structural_query_terms"],
        "struct_terms_share": q["structural_query_terms_share"] if "structural_query_terms_share" in q.files else None,
        "struct_field_shares": q["structural_field_shares"] if "structural_field_shares" in q.files else None,
        "list_table": rule["list_table"][idx],
        "emphasis": rule["emphasis"][idx],
        "paragraph_length": rule["paragraph_length"][idx],
    }
    # the q_axes file lists (query, passage) pairs in the pool's rank order
    query_id, chunk_id = q["query_id"], q["chunk_id"]
    ranks = np.concatenate([np.arange(1, len(rows[qid]) + 1) for qid in dict.fromkeys(query_id.tolist())])
    scores = np.concatenate([[s for _, s in rows[qid]] for qid in dict.fromkeys(query_id.tolist())])
    assert len(ranks) == len(query_id)
    return split, query_id, chunk_id, ranks, scores, raw


def fit_quantiles(raw, train_mask, flat=False) -> dict:
    """Quantile transforms of the continuous raw values, fitted on the train split
    (axes in RAW_SCALE keep their raw value)."""
    suffix = "_flat" if flat else ""
    out = {}
    for key in ("unique_words", "body_terms", "body_cos", "struct_terms"):
        src = key + suffix if key in ("body_terms", "struct_terms") else key
        if key not in RAW_SCALE and raw[src] is not None:
            t = QuantileTransformer(n_quantiles=1000, subsample=10**9, random_state=0)
            out[key] = t.fit(raw[src][train_mask].reshape(-1, 1))
    return out


def z_from_raw(raw, qts, flat=False) -> np.ndarray:
    def qt(key, x):
        x = np.asarray(x, dtype=np.float64)
        if key in RAW_SCALE:
            return np.clip(x, 0, 1)
        return qts[key].transform(x.reshape(-1, 1)).ravel()

    suffix = "_flat" if flat else ""
    body = qt("body_terms", raw["body_terms" + suffix])
    if "body_cos" in qts and raw.get("body_cos") is not None:
        body = (body + qt("body_cos", raw["body_cos"])) / 2
    cols = [qt("unique_words", raw["unique_words"]), body, raw["jsonld"], raw["heading_hierarchy"],
            qt("struct_terms", raw["struct_terms" + suffix]), raw["list_table"], raw["emphasis"], raw["paragraph_length"]]
    return np.stack(cols, 1).astype(np.float32)


def build_z(raw, train_mask, flat=False):
    return z_from_raw(raw, fit_quantiles(raw, train_mask, flat), flat)


def query_pca(dim, train_queries):
    ids = json.loads((FEAT / "embeddings" / "queries_ids.json").read_text())
    vecs = np.load(FEAT / "embeddings" / "queries.npy").astype(np.float32)
    row = {q: i for i, q in enumerate(ids)}
    if dim == 0:
        return {q: np.zeros(0, np.float32) for q in ids}
    pca = PCA(dim, random_state=0).fit(vecs[[row[q] for q in train_queries]])
    out = pca.transform(vecs)
    return {q: out[row[q]].astype(np.float32) for q in ids}


def to_batches(groups, x, ranks, scores):
    """groups: list of index arrays (one per query) -> padded tensors."""
    n = max(len(g) for g in groups)
    b = len(groups)
    X = torch.zeros(b, n, x.shape[1])
    R = torch.ones(b, n, dtype=torch.long)
    S = torch.zeros(b, n)
    M = torch.zeros(b, n, dtype=torch.bool)
    for k, g in enumerate(groups):
        X[k, :len(g)] = torch.from_numpy(x[g])
        R[k, :len(g)] = torch.from_numpy(ranks[g])
        S[k, :len(g)] = torch.from_numpy(scores[g].astype(np.float32))
        M[k, :len(g)] = True
    return X, R, S, (R <= BOUNDARY) & M, M


def train_head(x, ranks, scores, train_groups, val_groups, seed, linear=False, epochs=40, patience=6, bs=32):
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    model = StageHead(x.shape[1], linear=linear)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    val_x = torch.from_numpy(x[np.concatenate(val_groups)])
    best, best_state, bad = -1.0, None, 0
    for _ in range(epochs):
        model.train()
        order = rng.permutation(len(train_groups))
        for s in range(0, len(order), bs):
            X, R, S, Y, M = to_batches([train_groups[i] for i in order[s:s + bs]], x, ranks, scores)
            loss, _, _ = ranked_pair_bce_loss(model(X), R, S, Y, M)
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            logits = model(val_x).numpy()
        score = per_query_auc(logits, val_groups, ranks)
        if score > best + 1e-4:
            best, best_state, bad = score, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    return model


def per_query_auc(logits, groups, ranks):
    """logits are aligned with np.concatenate(groups)."""
    out, start = [], 0
    for g in groups:
        y = ranks[g] <= BOUNDARY
        lg = logits[start:start + len(g)]
        start += len(g)
        if 0 < y.sum() < len(y):
            out.append(roc_auc_score(y, lg))
    return float(np.mean(out))


def evaluate(model, x, ranks, groups, rel_eval):
    idx = np.concatenate(groups)
    with torch.no_grad():
        logits = model(torch.from_numpy(x[idx])).numpy()
    y = ranks[idx] <= BOUNDARY
    sp, start = [], 0
    for g in groups:
        lg = logits[start:start + len(g)]
        start += len(g)
        sp.append(spearmanr(lg, -ranks[g]).correlation)
    res = {"pooled_auc": round(float(roc_auc_score(y, logits)), 4),
           "per_query_auc": round(per_query_auc(logits, groups, ranks), 4),
           "spearman": round(float(np.nanmean(sp)), 4)}
    if rel_eval:
        pos = dict(zip(idx.tolist(), logits))
        ys, ls = [], []
        for members, top in rel_eval:
            ys.append(top)
            ls.append(max(pos[i] for i in members))
        res["relevant_only_auc"] = round(float(roc_auc_score(ys, ls)), 4)
        res["relevant_only_n"] = f"{sum(ys)} top-100 / {len(ys) - sum(ys)} 101-N"
    return res, logits


def relevant_docs(groups, query_of_group, chunk_id, ranks, n):
    """UMBRELA-relevant documents in validation queries: (passage indices in pool, is Top-100)."""
    judged = [json.loads(line) for line in open(DATA / "precheck_b_unit" / "umbrela_sample.jsonl")]
    rel = {(r["query_id"], r["doc_id"]) for r in judged if r["score"] is not None and r["score"] >= 2}
    out = []
    for qid, g in zip(query_of_group, groups):
        by_doc = defaultdict(list)
        for i in g:
            by_doc[doc_of(chunk_id[i])].append(i)
        for d, members in by_doc.items():
            if (qid, d) in rel:
                out.append((members, bool(ranks[members].min() <= BOUNDARY)))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    torch.set_num_threads(16)
    start = time.time()
    split, query_id, chunk_id, ranks, scores, raw = load(args.n)
    train_q, val_q = set(split["train"]), set(split["val"])
    train_mask = np.isin(query_id, list(train_q))
    z = build_z(raw, train_mask)
    z_flat = build_z(raw, train_mask, flat=True) if raw["body_terms_flat"] is not None else None
    z_alt = {}  # earlier #17 definitions, for comparison
    if raw["struct_terms_share"] is not None:
        z_alt["share"] = build_z({**raw, "struct_terms": raw["struct_terms_share"]}, train_mask)
    if raw["struct_field_shares"] is not None:
        s = raw["struct_field_shares"]
        z_alt["levels"] = build_z({**raw, "struct_terms": np.where(s >= 0.75, 1.0, np.where(s > 0.25, 0.5, 0.0)).mean(1)},
                                  train_mask)

    groups = defaultdict(list)
    for i, qid in enumerate(query_id.tolist()):
        groups[qid].append(i)
    train_ids = [q for q in groups if q in train_q]
    val_ids = [q for q in groups if q in val_q]
    train_groups = [np.array(groups[q]) for q in train_ids]
    val_groups = [np.array(groups[q]) for q in val_ids]
    rel_eval = relevant_docs(val_groups, val_ids, chunk_id, ranks, args.n)
    print(f"loaded: train {len(train_groups)} queries, val {len(val_groups)} queries, "
          f"{len(rel_eval)} relevant val documents, #11 cosine {'on' if raw['body_cos'] is not None else 'off'} "
          f"({time.time() - start:.0f}s)", flush=True)

    variants = {
        "#17 only": dict(cols=[4], pca=0),
        "#11 only": dict(cols=[1], pca=0),
        "#11 + #17": dict(cols=[1, 4], pca=0),
        "free axes": dict(cols=R_COLS, pca=0),
        "free axes + PCA16": dict(cols=R_COLS, pca=16),
        "free axes + PCA32": dict(cols=R_COLS, pca=32),
        "free axes, linear": dict(cols=R_COLS, pca=0, linear=True),
        "free 8 axes + PCA32, no visibility mask": dict(cols=list(range(8)), pca=32),
    }
    if z_flat is not None:
        variants.update({
            "flat: #11 + #17": dict(cols=[1, 4], pca=0, flat=True),
            "flat: free axes + PCA16": dict(cols=R_COLS, pca=16, flat=True),
        })
    for alt, label in (("share", "v0.8 #17 (mean share)"), ("levels", "#17 as mean of field levels")):
        if alt in z_alt:
            variants.update({f"{label}: #17 only": dict(cols=[4], pca=0, alt=alt),
                             f"{label}: free axes + PCA32": dict(cols=R_COLS, pca=32, alt=alt)})
    pcas = {d: query_pca(d, train_ids) for d in {v["pca"] for v in variants.values()}}
    report = {"n": args.n, "cosine": raw["body_cos"] is not None, "variants": {}}
    candidates = {}
    for name, v in variants.items():
        pq = pcas[v["pca"]]
        zz = z_flat if v.get("flat") else z_alt[v["alt"]] if v.get("alt") else z
        x = np.concatenate([zz[:, v["cols"]], np.stack([pq[q] for q in query_id.tolist()])], 1).astype(np.float32)
        runs = []
        for seed in range(args.seeds):
            model = train_head(x, ranks, scores, train_groups, val_groups, seed, linear=v.get("linear", False))
            res, _ = evaluate(model, x, ranks, val_groups, rel_eval)
            runs.append(res)
            if name.startswith("free axes") and "linear" not in name and seed == 0:
                candidates[name] = (model, x, v["pca"], v["cols"])
        keys = [k for k in runs[0] if k != "relevant_only_n"]
        summary = {k: round(float(np.mean([r[k] for r in runs])), 4) for k in keys}
        summary.update({f"{k}_sd": round(float(np.std([r[k] for r in runs])), 4) for k in keys})
        summary["relevant_only_n"] = runs[0].get("relevant_only_n")
        report["variants"][name] = summary
        print(name, summary, f"({time.time() - start:.0f}s)", flush=True)

    chosen = max(candidates, key=lambda k: report["variants"][k]["per_query_auc"])
    model, x, pca_dim, cols = candidates[chosen]
    head_axes = [AXES[c] for c in cols]
    report["prescription_head"] = chosen
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq = pcas[pca_dim]
    torch.save({"state_dict": model.state_dict(), "in_dim": x.shape[1], "pca_dim": pca_dim, "axes": head_axes,
                "cols": cols, "pca_by_query": {q: v for q, v in pq.items()}}, OUT_DIR / f"r_head_top{args.n}.pt")
    np.savez(OUT_DIR / f"z_top{args.n}.npz", z=z, axes=AXES, query_id=query_id, chunk_id=chunk_id, ranks=ranks)
    base, _ = evaluate(model, x, ranks, val_groups, None)
    idx = np.concatenate(val_groups)
    rng = np.random.default_rng(0)
    importance = {}
    for c, axis in enumerate(head_axes):
        drops = []
        for _ in range(10):
            xp = x.copy()
            xp[idx, c] = xp[idx[rng.permutation(len(idx))], c]
            res, _ = evaluate(model, xp, ranks, val_groups, None)
            drops.append(base["pooled_auc"] - res["pooled_auc"])
        importance[axis] = round(float(np.mean(drops)), 4)
    report["permutation_importance_pooled_auc_drop"] = importance
    print("importance", importance, flush=True)

    # axis means by rank band in validation, to read what the head learned
    bands = [(1, 100), (101, 200), (201, args.n)]
    report["val_axis_mean_by_band"] = {
        f"{a}-{b}": {axis: round(float(z[idx][(ranks[idx] >= a) & (ranks[idx] <= b), c].mean()), 3)
                     for c, axis in enumerate(AXES)} for a, b in bands}
    out = OUT_DIR / f"report_top{args.n}{'_cos' if report['cosine'] else ''}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"-> {out} ({time.time() - start:.0f}s)")


if __name__ == "__main__":
    main()
