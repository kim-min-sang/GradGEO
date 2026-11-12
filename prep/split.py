"""
Query split (handoff §4.3, C33): per dataset train 60 / validation 20 / test 20
(180 / 60 / 60 of each dataset's 300 queries). Queries are placed into four parts
(train 60 / val 10 / val_mini 10 / test 20, the split made before C33) and the two middle
parts form the validation split; `val_mini` (the former dev split) stays as the subset the
mini comparison (experiments/compare_mini.py) uses. Queries whose
Qwen3-Embedding-0.6B cosine is >= 0.9 are linked, and each connected group goes to
one split. Groups are placed in a seeded random order into the split with the most
room left for their datasets.

Needs data/sageo/features/embeddings/queries.npy (prep/query_features.py embed).
Output: data/sageo/split.json  {"train": [query ids], "val": [...], "val_mini": [...], "test": [...]}
(val_mini is a subset of val)

Usage:
    python prep/split.py
"""
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EMB_DIR = ROOT / "data" / "sageo" / "features" / "embeddings"
QUERY_DIR = ROOT / "data" / "sageo" / "queries"
OUT = ROOT / "data" / "sageo" / "split.json"
SPLITS = {"train": 0.6, "val": 0.1, "val_mini": 0.1, "test": 0.2}
THRESHOLD = 0.9
SEED = 42


def groups(ids, vecs):
    parent = list(range(len(ids)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sim = vecs @ vecs.T
    for i, j in zip(*np.where(np.triu(sim, 1) >= THRESHOLD)):
        parent[find(i)] = find(j)
    out = defaultdict(list)
    for i, q in enumerate(ids):
        out[find(i)].append(q)
    return list(out.values())


def main():
    ids = json.loads((EMB_DIR / "queries_ids.json").read_text())
    vecs = np.load(EMB_DIR / "queries.npy").astype(np.float32)
    dataset = {}
    for path in sorted(QUERY_DIR.glob("*_queries_300.jsonl")):
        for line in open(path, encoding="utf-8"):
            q = json.loads(line)
            dataset[q["id"]] = q["dataset"]
    sizes = Counter(dataset.values())
    room = {s: {d: round(n * share) for d, n in sizes.items()} for s, share in SPLITS.items()}

    rng = random.Random(SEED)
    comps = groups(ids, vecs)
    rng.shuffle(comps)
    comps.sort(key=len, reverse=True)  # stable: random order within the same size
    split = defaultdict(list)
    for comp in comps:
        need = Counter(dataset[q] for q in comp)
        fits = [s for s in SPLITS if all(room[s][d] >= k for d, k in need.items())]
        best = max(fits or SPLITS, key=lambda s: sum(room[s][d] for d in need))
        split[best].extend(comp)
        for d, k in need.items():
            room[best][d] -= k

    out = {s: sorted(split[s]) for s in SPLITS}
    out["val"] = sorted(out["val"] + out["val_mini"])
    OUT.write_text(json.dumps(out))
    print(f"{sum(len(c) > 1 for c in comps)} linked groups ({sum(len(c) for c in comps if len(c) > 1)} queries)")
    for s, qs in out.items():
        print(s, len(qs), dict(sorted(Counter(dataset[q] for q in qs).items())))


if __name__ == "__main__":
    main()
