"""
Environment-B chunker.

Same as SAGEO's chunk_text (src/modules/retriever_bm25.py): units of text packed
greedily into chunks of at most 256 cl100k tokens, the last units of a chunk
(at most 64 tokens) repeated at the start of the next one, and a unit longer than
256 tokens cut into token windows with step 192.

Two changes for markdown bodies:
    - a line break is also a unit boundary, besides SAGEO's sentence regex
      (headings, list items and table rows often end without . ! ?)
    - inside a chunk, units from different lines are joined with the original
      line break ("\n" or "\n\n") instead of a space

On text without line breaks (environment A) the chunks are identical to SAGEO's.
"""
import re

import tiktoken

CHUNK_SIZE = 256
CHUNK_OVERLAP = 64

enc = tiktoken.get_encoding("cl100k_base")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LINE_SPLIT = re.compile(r"(\s*\n\s*)")


def split_units(text: str) -> list[tuple[str, str]]:
    """Split text into units (sentences, or lines without end punctuation).

    Returns:
        List of (unit, sep) where sep is what joins the unit to the previous one
        in the same chunk: " " on the same line, "\n" or "\n\n" across lines.
    """
    units = []
    sep = " "
    for piece in _LINE_SPLIT.split(text):
        if "\n" in piece:
            if units:
                sep = "\n\n" if piece.count("\n") >= 2 else "\n"
            continue
        for sentence in _SENTENCE_SPLIT.split(piece):
            sentence = sentence.strip()
            if sentence:
                units.append((sentence, sep))
                sep = " "
    return units


def _join(units: list[tuple[str, str]]) -> str:
    return units[0][0] + "".join(sep + unit for unit, sep in units[1:])


def chunk_units(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> tuple[list[tuple[str, str]], list[dict]]:
    """Chunk text and keep which units each chunk came from.

    Returns:
        (units, chunks). Each chunk is {"text": str, "units": [unit ids]} and,
        for a token window of a long unit, also {"window": (start, end)} in tokens.
    """
    if not text or not text.strip():
        return [], [{"text": "", "units": []}]

    units = split_units(text)
    if not units:
        return [], [{"text": text, "units": []}]

    chunks = []
    current = []  # unit ids
    current_tokens = 0

    def flush(ids):
        chunks.append({"text": _join([units[i] for i in ids]), "units": list(ids)})

    for uid, (unit, _) in enumerate(units):
        tokens = enc.encode(unit)

        if len(tokens) > size:
            if current:
                flush(current)
            current = []
            current_tokens = 0
            for start in range(0, len(tokens), size - overlap):
                window = tokens[start : start + size]
                chunks.append({"text": enc.decode(window), "units": [uid],
                               "window": (start, start + len(window))})
            continue

        if current_tokens + len(tokens) > size:
            if current:
                flush(current)
            kept = []
            kept_tokens = 0
            for i in reversed(current):
                t = len(enc.encode(units[i][0]))
                if kept_tokens + t <= overlap:
                    kept.insert(0, i)
                    kept_tokens += t
                else:
                    break
            current = kept
            current_tokens = kept_tokens

        current.append(uid)
        current_tokens += len(tokens)

    if current:
        flush(current)

    return units, chunks if chunks else [{"text": "", "units": []}]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Drop-in replacement for SAGEO's chunk_text."""
    return [chunk["text"] for chunk in chunk_units(text, size, overlap)[1]]
