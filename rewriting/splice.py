"""
Put a rewritten representative passage back into the body (handoff §9.2): find the
environment-B chunker units of p* and replace that unit range with the new text. Units
p* shares with its neighbours (overlap) belong to p* and change with it. A p* that is a
token window of one over-long unit replaces that whole unit (counted, rare).
"""
from rag.chunking import _join, chunk_units


def splice_passage(body_text: str, chunk_index: int, new_text: str) -> tuple[str, dict]:
    units, chunks = chunk_units(body_text)
    ids = chunks[chunk_index]["units"]
    first, last = ids[0], ids[-1]
    new_units = units[:first] + [(new_text.strip(), units[first][1])] + units[last + 1:]
    return _join(new_units), {"window": "window" in chunks[chunk_index], "units_replaced": len(ids)}
