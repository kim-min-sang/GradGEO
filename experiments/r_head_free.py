"""
R head on the free axes only (no judge-LLM cost): can [z | PCA(query)] predict
whether a passage of the Top-N pool is in the BM25 Top-100?

Free axes (handoff numbering): #8 unique_words, #11 body_query_alignment,
#14 jsonld, #15 heading_hierarchy, #17 structural_query_alignment, #18 list_table,
#19 emphasis, #20 paragraph_length. Continuous axes (#8, #11, #17) are quantile-
transformed with the train split; #11 = mean of its two transformed parts (key-term
share, embedding cosine) when the cosine is available, else the key-term share alone.
Diagnostic variants marked "idf" replace the key-term shares of #11 and #17 by their
idf-weighted versions (not the schema definition).

Train on the train split, report on the validation split (dev and test untouched):
    pooled AUC         y_R over all validation (query, passage) pairs
    per-query AUC      the same inside each query, averaged (what the gradient uses)
    Spearman           logit vs. BM25 rank inside each query, averaged
    relevant-only AUC  UMBRELA-relevant documents (>= 2) of the pre-check sample:
                       Top-100 vs. 101-N, document logit = its best passage's logit
    permutation importance of each axis (10 shuffles, pooled AUC drop)

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
BOUNDARY = 100


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
        "body_terms_idf": q["body_query_terms_idf"] if "body_query_terms_idf" in q.files else None,
        "struct_terms_idf": q["structural_query_terms_idf"] if "structural_query_terms_idf" in q.files else None,
        "jsonld": rule["jsonld"][idx],
        "heading_hierarchy": rule["heading_hierarchy"][idx],
        "struct_terms": q["structural_query_terms"],
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


def build_z(raw, train_mask, idf=False):
    def qt(x):
        t = QuantileTransformer(n_quantiles=1000, subsample=10**9, random_state=0)
        t.fit(x[train_mask].reshape(-1, 1))
        return t.transform(x.reshape(-1, 1)).ravel()

    suffix = "_idf" if idf else ""
    body = qt(raw["body_terms" + suffix])
    if raw["body_cos"] is not None:
        body = (body + qt(raw["body_cos"])) / 2
    cols = [qt(raw["unique_words"]), body, raw["jsonld"], raw["heading_hierarchy"], qt(raw["struct_terms" + suffix]),
            raw["list_table"], raw["emphasis"], raw["paragraph_length"]]
    return np.stack(cols, 1).astype(np.float32)


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
    z_idf = build_z(raw, train_mask, idf=True) if raw["body_terms_idf"] is not None else None

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
        "free 8 axes": dict(cols=list(range(8)), pca=0),
        "free 8 axes + PCA16": dict(cols=list(range(8)), pca=16),
        "free 8 axes + PCA32": dict(cols=list(range(8)), pca=32),
        "free 8 axes, linear": dict(cols=list(range(8)), pca=0, linear=True),
    }
    if z_idf is not None:
        variants.update({
            "idf: #11 + #17": dict(cols=[1, 4], pca=0, idf=True),
            "idf: free 8 axes + PCA16": dict(cols=list(range(8)), pca=16, idf=True),
        })
    pcas = {d: query_pca(d, train_ids) for d in {v["pca"] for v in variants.values()}}
    report = {"n": args.n, "cosine": raw["body_cos"] is not None, "variants": {}}
    best_model = None
    for name, v in variants.items():
        pq = pcas[v["pca"]]
        zz = z_idf if v.get("idf") else z
        x = np.concatenate([zz[:, v["cols"]], np.stack([pq[q] for q in query_id.tolist()])], 1).astype(np.float32)
        runs = []
        for seed in range(args.seeds):
            model = train_head(x, ranks, scores, train_groups, val_groups, seed, linear=v.get("linear", False))
            res, _ = evaluate(model, x, ranks, val_groups, rel_eval)
            runs.append(res)
            if name == "free 8 axes + PCA16" and seed == 0:
                best_model = (model, x)
        keys = [k for k in runs[0] if k != "relevant_only_n"]
        summary = {k: round(float(np.mean([r[k] for r in runs])), 4) for k in keys}
        summary.update({f"{k}_sd": round(float(np.std([r[k] for r in runs])), 4) for k in keys})
        summary["relevant_only_n"] = runs[0].get("relevant_only_n")
        report["variants"][name] = summary
        print(name, summary, f"({time.time() - start:.0f}s)", flush=True)

    model, x = best_model
    base, _ = evaluate(model, x, ranks, val_groups, None)
    idx = np.concatenate(val_groups)
    rng = np.random.default_rng(0)
    importance = {}
    for c, axis in enumerate(AXES):
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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"report_top{args.n}{'_cos' if report['cosine'] else ''}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"-> {out} ({time.time() - start:.0f}s)")


if __name__ == "__main__":
    main()
