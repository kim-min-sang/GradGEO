"""B2 (positive control for "blurred signal"): meta fields and passage merged into ONE text field."""
import json, subprocess, sys, time
from pathlib import Path
ROOT = Path("/home/kms/github/doctor/GradGEO")
SRC = ROOT / "data/sageo/index_b_unit/jsonl/docs.jsonl"
OUT = ROOT / "data/sageo/index_b_merged"
META = ["title", "meta_description", "headings", "jsonld_text"]
start = time.time()
(OUT / "jsonl").mkdir(parents=True, exist_ok=True)
with open(SRC, encoding="utf-8") as fin, open(OUT / "jsonl/docs.jsonl", "w", encoding="utf-8") as fout:
    for line in fin:
        e = json.loads(line)
        text = "\n".join([e[f] for f in META] + [e["contents"]])
        fout.write(json.dumps({"id": e["id"], "contents": text}, ensure_ascii=False) + "\n")
print("written", time.time() - start, flush=True)
subprocess.run([sys.executable, "-m", "pyserini.index.lucene", "--collection", "JsonCollection",
                "--input", str(OUT / "jsonl"), "--index", str(OUT / "lucene"),
                "--generator", "DefaultLuceneDocumentGenerator", "--threads", "16"], check=True)
print("done", time.time() - start, flush=True)
