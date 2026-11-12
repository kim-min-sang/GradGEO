"""
Rule-computed axes for environment B (strategy_schema.yaml v0.5).

Passage axes: unique_words (MTLD), list_table, emphasis, paragraph_length.
Input is one passage as produced by rag/chunking.py (markdown, line breaks kept).
trafilatura's markdown marks bold with ** and italics with *, and escapes
literal characters with a backslash (\\*, \\_), so escaped marks are not spans.

Document axes: jsonld, heading_hierarchy, computed on what SAGEO's generator shows
(filter_jsonld_for_generator; headings cut at 500 chars, 3,500 chars in total).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party" / "SAGEO_Arena"))
from src.modules.utils.jsonld_extractor import DEFAULT_KEYS, JSONLD_WHITELIST, filter_jsonld_for_generator  # noqa: E402

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_BOLD = re.compile(r"(?<![\\*])\*\*(?=\S)[^*\n]+?(?<=\S)(?<!\\)\*\*(?!\*)")
_ITALIC = re.compile(r"(?<![\\*\w])\*(?=[^\s*])[^*\n]+?(?<=[^\s\\*])\*(?![*\w])")


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW.match(line)) and not _TABLE_SEPARATOR.match(line)


def count_list_table(passage: str) -> int:
    """Lines that are list items or table data rows."""
    return sum(1 for line in passage.split("\n") if _LIST_ITEM.match(line) or _is_table_row(line))


def count_emphasis(passage: str) -> int:
    """Bold (**...**) and italic (*...*) spans."""
    without_bold = _BOLD.sub(" ", passage)
    return len(_BOLD.findall(passage)) + len(_ITALIC.findall(without_bold))


def longest_paragraph_sentences(passage: str) -> int:
    """Sentences in the longest prose paragraph (0 if the passage has none).

    A paragraph is a block between blank lines; a block made only of headings,
    list items, or table lines is not prose.
    """
    longest = 0
    for block in re.split(r"\n\s*\n", passage):
        prose = [line for line in block.split("\n")
                 if line.strip() and not (_HEADING.match(line) or _LIST_ITEM.match(line) or _TABLE_ROW.match(line))]
        if prose:
            sentences = [s for s in _SENTENCE_SPLIT.split(" ".join(prose)) if s.strip()]
            longest = max(longest, len(sentences))
    return longest


def level_list_table(n: int) -> float:
    return 0.0 if n == 0 else 0.25 if n <= 2 else 0.5 if n <= 4 else 0.75 if n <= 7 else 1.0


def level_emphasis(n: int) -> float:
    return min(n, 4) / 4


def level_paragraph_length(n: int) -> float:
    return 1.0 if n <= 3 else 0.5 if n <= 7 else 0.0


_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _mtld_pass(words, threshold=0.72):
    factors, types, count = 0.0, set(), 0
    for w in words:
        types.add(w)
        count += 1
        if len(types) / count <= threshold:
            factors += 1
            types, count = set(), 0
    if count:
        ttr = len(types) / count
        factors += (1 - ttr) / (1 - threshold)
    return len(words) / factors if factors else float(len(words))


def mtld(passage: str) -> float:
    """MTLD (McCarthy & Jarvis 2010), mean of the forward and backward passes."""
    words = _WORD.findall(passage.lower())
    if not words:
        return 0.0
    return (_mtld_pass(words) + _mtld_pass(words[::-1])) / 2


JSONLD_CONTENT_TYPES = {
    "Article", "NewsArticle", "BlogPosting", "ScholarlyArticle", "MedicalWebPage", "DiscussionForumPosting",
    "Product", "Review", "FAQPage", "QAPage", "HowTo", "Recipe", "Event", "Course", "Dataset", "VideoObject",
    "Movie", "TVSeries", "SoftwareApplication", "Service", "CreativeWork",
}


def _jsonld_objects(items):
    for obj in items:
        if isinstance(obj, dict):
            yield obj
            if isinstance(obj.get("@graph"), list):
                yield from _jsonld_objects(obj["@graph"])


def level_jsonld(jsonld) -> float:
    visible = filter_jsonld_for_generator(jsonld or [])
    if not visible:
        return 0.0
    for obj in _jsonld_objects(visible):
        types = obj.get("@type")
        types = {types} if isinstance(types, str) else {t for t in types if isinstance(t, str)} if isinstance(types, list) else set()
        filled = [k for k, v in obj.items() if k not in ("@type", "@context", "@graph") and v not in (None, "", [], {})]
        content = types & JSONLD_CONTENT_TYPES
        # 2 filled visible fields, or all of them for a type with one (FAQPage: mainEntity), schema v0.9
        if content and len(filled) >= min(2, *(len(JSONLD_WHITELIST.get(t, DEFAULT_KEYS)) for t in content)):
            return 1.0
    return 0.5


_HEADING_TAG = re.compile(r"^<(h[1-6])>", re.I)


def visible_headings(headings) -> list[str]:
    """SAGEO generator format_passage: each heading cut at 500 chars, stop before 3,500 in total."""
    out, total = [], 0
    for h in headings or []:
        if not h:
            continue
        text = h.strip()
        if len(text) > 500:
            tag_match = _HEADING_TAG.match(text)
            tag_name = tag_match.group(1) if tag_match else None
            text = text[:500]
            if tag_name and not text.endswith(f"</{tag_name}>"):
                text = re.sub(r"</?[^>]*$", "", text) + f"</{tag_name}>"
        if text:
            if total + len(text) > 3500:
                break
            out.append(text)
            total += len(text)
    return out


def level_heading_hierarchy(headings) -> float:
    levels = [int(m.group(1)[1]) for h in visible_headings(headings) if (m := _HEADING_TAG.match(h))]
    if not levels:
        return 0.0
    flat = len(set(levels)) == 1
    skipped = any(b > a + 1 for a, b in zip(levels, levels[1:]))
    if flat or skipped or levels.count(1) > 1:
        return 0.5
    return 1.0


def main():
    """Rule axes for every passage (and its document) in the Top-N candidate pools."""
    import argparse
    import json
    import time
    import numpy as np

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    args = parser.parse_args()
    passages = {}
    for line in open(root / "data" / "sageo" / "retrieval_b_unit" / "unit_top500.jsonl", encoding="utf-8"):
        for c, _ in json.loads(line)["results"][:args.n]:
            passages.setdefault(c, None)
    docs = {c.rsplit("_chunk_", 1)[0] for c in passages}
    start = time.time()
    doc_levels = {}
    with open(root / "data" / "sageo" / "index_b" / "documents.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["_id"] in docs:
                doc_levels[d["_id"]] = (level_jsonld(d.get("jsonld")), level_heading_hierarchy(d.get("headings")))
    out = {k: [] for k in ("chunk_id", "unique_words", "list_table", "emphasis", "paragraph_length",
                           "jsonld", "heading_hierarchy")}
    with open(root / "data" / "sageo" / "index_b" / "jsonl" / "body_text" / "docs.jsonl", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            c = entry["id"]
            if c not in passages:
                continue
            text = entry["contents"]
            jl, hh = doc_levels[c.rsplit("_chunk_", 1)[0]]
            for k, v in (("chunk_id", c), ("unique_words", mtld(text)),
                         ("list_table", level_list_table(count_list_table(text))),
                         ("emphasis", level_emphasis(count_emphasis(text))),
                         ("paragraph_length", level_paragraph_length(longest_paragraph_sentences(text))),
                         ("jsonld", jl), ("heading_hierarchy", hh)):
                out[k].append(v)
    out_path = root / "data" / "sageo" / "features" / f"rule_axes_top{args.n}.npz"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_path, **{k: np.array(v) if k == "chunk_id" else np.array(v, dtype=np.float32) for k, v in out.items()})
    print(f"{len(out['chunk_id'])} passages, {len(doc_levels)} documents ({time.time() - start:.0f}s)")


if __name__ == "__main__":
    main()
