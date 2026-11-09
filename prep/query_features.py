"""
Query-side features (handoff §5.1, §6; strategy_schema.yaml v0.5).

    embed  Qwen3-Embedding-0.6B vectors for the 2,700 queries (retrieval instruction
           prefix) and for every passage in the union of the Top-N candidate pools
           (no prefix). Last-token pooling, L2-normalized, stored as float16.
           Passages are written in shards so an interrupted run resumes.
    axes   raw values of the query-dependent [Q] axes for every (query, passage) in
           the Top-N pool:
             body_query_alignment (#11)        (a) idf-weighted share of query key terms in the passage
                                               (b) query-passage embedding cosine
             structural_query_alignment (#17)  idf-weighted share of query key terms in each of
                                               title / meta_description / headings / jsonld_text,
                                               averaged with the engine's field weights
           Key terms = the query's Lucene tokens (Porter stem, Lucene English stopwords
           removed), the same analysis as the BM25 index; each is weighted by its BM25 idf
           in the passage-unit index (schema v0.5, revised 2026-11-08). Entities are not
           matched separately. Quantile transforms are fitted later on the train split.
           Diagnostic only: the unweighted shares (*_flat), the definition before the revision.

Outputs under data/sageo/features/:
    embeddings/queries.npy, queries_ids.json
    embeddings/passages_top{N}/shard_*.npy, passages_top{N}_ids.json
    q_axes_top{N}.npz

Usage:
    python prep/query_features.py embed [--n 300]
    python prep/query_features.py axes [--n 300]
"""
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RETR_FILE = ROOT / "data" / "sageo" / "retrieval_b_unit" / "unit_top500.jsonl"
QUERY_DIR = ROOT / "data" / "sageo" / "queries"
FIELD_JSONL = ROOT / "data" / "sageo" / "index_b" / "jsonl"
OUT_DIR = ROOT / "data" / "sageo" / "features"
EMB_DIR = OUT_DIR / "embeddings"

MODEL = "Qwen/Qwen3-Embedding-0.6B"
QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
STRUCT_FIELDS = {"title": 1.0, "meta_description": 1.0, "headings": 1.0, "jsonld_text": 1.0}  # = rag/retrieval.py UNIT_FIELDS
SHARD = 20000
BATCH = 32  # larger batches fail on the 8 GB card under WSL


def doc_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_chunk_", 1)[0]


def load_queries() -> dict[int, str]:
    queries = {}
    for path in sorted(QUERY_DIR.glob("*_queries_300.jsonl")):
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            queries[q["id"]] = q["text"]
    return queries


def load_pool(n: int) -> list[dict]:
    """Top-N passages per query from the passage-unit engine, in rank order."""
    rows = []
    for line in open(RETR_FILE, encoding="utf-8"):
        r = json.loads(line)
        r["results"] = r["results"][:n]
        rows.append(r)
    return rows


def pool_passages(rows) -> list[str]:
    seen = {}
    for r in rows:
        for c, _ in r["results"]:
            seen.setdefault(c, None)
    return list(seen)


def load_field(field: str, ids=None) -> dict[str, str]:
    out = {}
    with open(FIELD_JSONL / field / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if ids is None or entry["id"] in ids:
                out[entry["id"]] = entry["contents"]
    return out


class Embedder:
    def __init__(self):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
        self.model = AutoModel.from_pretrained(MODEL, dtype=torch.float16, attn_implementation="sdpa").cuda().eval()

    def __call__(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.zeros((len(texts), self.model.config.hidden_size), dtype=np.float16)
        with torch.inference_mode():
            for s in range(0, len(order), BATCH):
                idx = order[s:s + BATCH]
                enc = self.tok([texts[i] for i in idx], padding=True, truncation=True, max_length=512,
                               return_tensors="pt").to("cuda")
                vec = self.model(**enc).last_hidden_state[:, -1].float()
                out[idx] = torch.nn.functional.normalize(vec, dim=-1).cpu().numpy().astype(np.float16)
        return out


def run_embed(n: int):
    EMB_DIR.mkdir(parents=True, exist_ok=True)
    embedder = Embedder()
    queries = load_queries()
    if not (EMB_DIR / "queries.npy").exists():
        ids = sorted(queries)
        vecs = embedder([f"Instruct: {QUERY_INSTRUCTION}\nQuery:{queries[i]}" for i in ids])
        np.save(EMB_DIR / "queries.npy", vecs)
        (EMB_DIR / "queries_ids.json").write_text(json.dumps(ids))
        print(f"{len(ids)} queries embedded", flush=True)

    passages = pool_passages(load_pool(n))
    (EMB_DIR / f"passages_top{n}_ids.json").write_text(json.dumps(passages))
    shard_dir = EMB_DIR / f"passages_top{n}"
    shard_dir.mkdir(exist_ok=True)
    texts = load_field("body_text", set(passages))
    start = time.time()
    for k, s in enumerate(range(0, len(passages), SHARD)):
        path = shard_dir / f"shard_{k:03d}.npy"
        if path.exists():
            continue
        np.save(path, embedder([texts[c] for c in passages[s:s + SHARD]]))
        print(f"{min(s + SHARD, len(passages))}/{len(passages)} passages {time.time() - start:.0f}s", flush=True)
    (shard_dir / "DONE").touch()
    print("done", flush=True)


def load_passage_embeddings(n: int):
    ids = json.loads((EMB_DIR / f"passages_top{n}_ids.json").read_text())
    shards = sorted((EMB_DIR / f"passages_top{n}").glob("shard_*.npy"))
    vecs = np.concatenate([np.load(p) for p in shards])
    assert len(vecs) == len(ids), "passage embeddings incomplete"
    return ids, vecs


def run_axes(n: int):
    os.environ.setdefault("OPENAI_API_KEY", "unused")  # pyserini creates an OpenAI client on import
    from pyserini.analysis import Analyzer, get_lucene_analyzer
    from pyserini.index.lucene import LuceneIndexReader
    tokens = Analyzer(get_lucene_analyzer()).analyze  # the BM25 index's analysis (Porter, Lucene stopwords)
    reader = LuceneIndexReader(str(ROOT / "data" / "sageo" / "index_b_unit" / "lucene"))
    n_units = reader.stats()["documents"]
    idf_cache = {}

    def idf(t):
        if t not in idf_cache:
            df = reader.get_term_counts(t, analyzer=None)[0]
            idf_cache[t] = float(np.log(1 + (n_units - df + 0.5) / (df + 0.5)))
        return idf_cache[t]

    def share(terms, weights, field_terms):
        total = sum(weights.values())
        return sum(weights[t] for t in terms & field_terms) / total if total else 0.0

    rows = load_pool(n)
    queries = load_queries()
    passages = pool_passages(rows)
    docs = {doc_of(c) for c in passages}
    start = time.time()
    body = {c: set(tokens(t)) for c, t in load_field("body_text", set(passages)).items()}
    struct = {f: {d: set(tokens(t)) for d, t in load_field(f, docs).items()} for f in STRUCT_FIELDS}
    print(f"tokenized {len(body)} passages, {len(docs)} documents ({time.time() - start:.0f}s)", flush=True)

    have_emb = (EMB_DIR / f"passages_top{n}" / "DONE").exists()
    if have_emb:
        q_ids = json.loads((EMB_DIR / "queries_ids.json").read_text())
        q_vec = dict(zip(q_ids, np.load(EMB_DIR / "queries.npy").astype(np.float32)))
        p_ids, p_vecs = load_passage_embeddings(n)
        p_row = {c: i for i, c in enumerate(p_ids)}

    field_w = np.array(list(STRUCT_FIELDS.values()))
    query_id, chunk_id, body_cos = [], [], []
    cols = {k: [] for k in ("body_query_terms", "structural_query_terms",
                            "body_query_terms_flat", "structural_query_terms_flat")}
    for r in rows:
        terms = set(tokens(queries[r["query_id"]]))
        flat = {t: 1.0 for t in terms}
        weighted = {t: idf(t) for t in terms}
        cids = [c for c, _ in r["results"]]
        for c in cids:
            d = doc_of(c)
            query_id.append(r["query_id"])
            chunk_id.append(c)
            for suffix, w in (("", weighted), ("_flat", flat)):
                cols["body_query_terms" + suffix].append(share(terms, w, body[c]))
                cover = [share(terms, w, struct[f].get(d, set())) for f in STRUCT_FIELDS]
                cols["structural_query_terms" + suffix].append(float(np.dot(cover, field_w) / field_w.sum()))
        if have_emb:
            body_cos.extend(p_vecs[[p_row[c] for c in cids]].astype(np.float32) @ q_vec[r["query_id"]])
    out = {"query_id": np.array(query_id), "chunk_id": np.array(chunk_id),
           **{k: np.array(v, dtype=np.float32) for k, v in cols.items()}}
    if have_emb:
        out["body_query_cosine"] = np.array(body_cos, dtype=np.float32)
    np.savez(OUT_DIR / f"q_axes_top{n}.npz", **out)
    print(f"done ({'with' if have_emb else 'without'} embeddings, {time.time() - start:.0f}s)", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["embed", "axes"])
    parser.add_argument("--n", type=int, default=300)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_embed(args.n) if args.step == "embed" else run_axes(args.n)


if __name__ == "__main__":
    main()
