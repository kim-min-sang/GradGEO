"""
Rewriter prompt (handoff §9.1, strategy_schema.yaml `rewriter`): query, the META block as
the engine shows it, the representative passage, and one block per axis to change with
its full rubric (current level ▶, target level ★) and its allow / forbid rules, then the
global rules. Only the fields the chosen axes act on may change.
"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = yaml.safe_load(open(ROOT / "configs" / "strategy_schema.yaml", encoding="utf-8"))
AXES = {a["id"]: a for a in SCHEMA["strategies"]}

# which output fields an axis may change (schema `fields`: body -> passage)
AXIS_FIELDS = {
    "title_informativeness": ["title"],
    "meta_description": ["meta_description"],
    "jsonld": ["jsonld"],
    "heading_hierarchy": ["headings"],
    "structural_query_alignment": ["title", "meta_description", "headings", "jsonld"],
}


def axis_fields(axis_id: str) -> list[str]:
    return AXIS_FIELDS.get(axis_id, ["passage"])


def rubric_lines(axis: dict) -> dict[float, str]:
    if axis["levels"] == "continuous":
        return {float(k): v for k, v in axis["rewriter_bins"].items()}
    if axis.get("rubric"):
        return {float(k): v for k, v in axis["rubric"].items()}
    template = SCHEMA["levels_from_ids"]["rubric_template"]
    item = axis["item"]
    lines = [template[0].format(one=item["one"]), template[1].format(one=item["one"]),
             template[2].format(many=item["many"]), template[3].format(many=item["many"]),
             template["4+"].format(many=item["many"])]
    levels = [0.0, 0.25, 0.5, 0.75, 1.0]
    if axis["levels"] == "reverse_count5":
        levels = levels[::-1]
    return dict(zip(levels, lines))


def axis_block(axis_id: str, current: float, target: float) -> str:
    axis = AXES[axis_id]
    lines = [f"## {axis['name_en']} (you may change: {', '.join(axis_fields(axis_id))})"]
    for level, text in sorted(rubric_lines(axis).items()):
        mark = "★" if level == target else "▶" if level == current else " "
        lines.append(f"{mark} {text}")
    safety = axis.get("safety") or {}
    if safety.get("allow"):
        lines.append("Allowed: " + " ".join(safety["allow"]))
    if safety.get("forbid"):
        lines.append("Forbidden: " + " ".join(safety["forbid"]))
    if axis_id == "jsonld":
        from src.modules.utils.jsonld_extractor import JSONLD_WHITELIST
        lines.append("Only these fields of each type are visible to the engine: "
                     + "; ".join(f"{t}: {', '.join(f)}" for t, f in JSONLD_WHITELIST.items()))
    return "\n".join(lines)


def build_prompt(query: str, meta: dict, passage: str, changes: list[tuple[str, float, float]]) -> tuple[str, list[str]]:
    """meta: title, meta_description, headings (visible, with tags), jsonld (visible, filtered).
    changes: (axis id, current level, target level). Returns (prompt, fields that may change)."""
    fields = []
    for axis_id, _, _ in changes:
        fields += [f for f in axis_fields(axis_id) if f not in fields]
    meta_block = [f"Title: {meta.get('title') or ''}",
                  f"Meta description: {meta.get('meta_description') or ''}",
                  "Headings:", *(meta.get("headings") or []),
                  f"JSON-LD: {json.dumps(meta.get('jsonld') or [], ensure_ascii=False)}"]
    rules = SCHEMA["rewriter"]["global_safety"] + [f"Only these fields may change: {', '.join(fields)}."]
    parts = [
        "Rewrite part of a web page for the search query below. The page is shown as the search engine sees it.",
        f"Query: {query}",
        "[META]\n" + "\n".join(meta_block),
        "[PASSAGE]\n" + passage,
        "[CHANGES]\nFor each axis, the current level is marked ▶ and the target level ★. "
        "Rewrite so that the text reaches the target level.\n\n" + "\n\n".join(axis_block(a, c, t) for a, c, t in changes),
        "[RULES]\n" + "\n".join(f"- {r}" for r in rules),
        "Return JSON with the keys passage, title, meta_description, headings, jsonld. Give the new value for a field "
        "you changed and null for every other field. headings is the full list in the input format (e.g. \"<h2>Text</h2>\"); "
        "jsonld is the JSON-LD list as a JSON string.",
    ]
    return "\n\n".join(parts), fields
