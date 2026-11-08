"""
Build the environment-B corpus: same documents as documents_filtered.jsonl, but
body_text re-extracted from the saved raw HTML as markdown, so headings, lists,
tables, emphasis and paragraph breaks stay visible to the engine.

SAGEO extracts the body with trafilatura.extract(html) and then replaces every
newline with a space. Here the extractor is the same, only the output format
changes (output_format="markdown", formatting and tables kept, links dropped as
in SAGEO's default) and newlines are kept. All other fields are copied as-is.

A document falls back to its environment-A plain body when
    - no raw HTML is found, extraction fails, returns nothing, or times out
    - the markdown body has fewer than half the words of the plain body
      (keeps the content of the two environments the same)
The reason is recorded per document in "body_format".

Usage:
    python crawl/extract_markdown.py [--workers 12] [--limit 1000]
"""
import argparse
import gzip
import hashlib
import json
import re
import signal
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import trafilatura

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data" / "sageo" / "corpus"
RAW_DIR = ROOT / "data" / "sageo" / "crawl" / "raw_html"
SRC = CORPUS_DIR / "documents_filtered.jsonl"
DST = CORPUS_DIR / "documents_filtered_md.jsonl"
STATS = CORPUS_DIR / "documents_filtered_md.stats.json"

TIMEOUT_SEC = 60
MIN_WORD_RATIO = 0.5


class ExtractTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise ExtractTimeout()


def read_raw_html(url: str) -> str | None:
    key = hashlib.sha1(url.encode()).hexdigest()
    for method in ("httpx", "browser"):
        path = RAW_DIR / method / key[:2] / f"{key}.html.gz"
        if path.exists():
            return gzip.open(path, "rt", encoding="utf-8").read()
    return None


def normalize_markdown(md: str) -> str:
    lines = [line.rstrip() for line in md.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(line: str) -> tuple[str, str]:
    doc = json.loads(line)
    plain = doc.get("body_text") or ""
    html = read_raw_html(doc["link"])
    if html is None:
        fmt, md = "plain_fallback_no_html", None
    else:
        signal.alarm(TIMEOUT_SEC)
        try:
            md = trafilatura.extract(
                html,
                output_format="markdown",
                include_formatting=True,
                include_tables=True,
                include_links=False,
            )
            fmt = "markdown" if md else "plain_fallback_empty"
        except ExtractTimeout:
            fmt, md = "plain_fallback_timeout", None
        except Exception:
            fmt, md = "plain_fallback_error", None
        finally:
            signal.alarm(0)
    if md:
        md = normalize_markdown(md)
        if len(md.split()) < MIN_WORD_RATIO * len(plain.split()):
            fmt, md = "plain_fallback_short", None
    doc["body_text"] = md if md else plain
    doc["body_format"] = fmt
    return json.dumps(doc, ensure_ascii=False), fmt


def init_worker():
    signal.signal(signal.SIGALRM, _on_alarm)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    with open(SRC, encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]
    if args.limit:
        lines = lines[: args.limit]
    dst = DST.with_suffix(".partial.jsonl") if not args.limit else DST.with_name(f"documents_filtered_md.limit{args.limit}.jsonl")

    counts = Counter()
    start = time.time()
    with Pool(args.workers, initializer=init_worker) as pool, open(dst, "w", encoding="utf-8") as out:
        for i, (record, fmt) in enumerate(pool.imap(extract, lines, chunksize=32), start=1):
            out.write(record + "\n")
            counts[fmt] += 1
            if i % 10000 == 0:
                print(f"{i}/{len(lines)} {time.time() - start:.0f}s {dict(counts)}", flush=True)

    if not args.limit:
        dst.rename(DST)
        STATS.write_text(json.dumps({"documents": len(lines), "body_format": dict(counts),
                                     "seconds": round(time.time() - start)}, indent=2))
    print(f"done {len(lines)} docs in {time.time() - start:.0f}s {dict(counts)} -> {DST if not args.limit else dst}")


if __name__ == "__main__":
    main()
