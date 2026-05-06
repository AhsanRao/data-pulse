"""Module 5 — Output Formatter.

Supports: json | csv | md (Markdown table) | text
Includes extraction metadata when configured.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from datapulse.modules.extractor import ExtractionResult


def format_result(
    result: ExtractionResult,
    url: str,
    fmt: str = "json",
    include_metadata: bool = True,
) -> str:
    """Render an ExtractionResult to the requested output format."""
    fmt = fmt.lower().strip()

    if fmt == "json":
        return _to_json(result, url, include_metadata)
    elif fmt == "csv":
        return _to_csv(result, url, include_metadata)
    elif fmt in ("md", "markdown"):
        return _to_markdown(result, url, include_metadata)
    else:
        return _to_text(result, url, include_metadata)


# ── JSON ──────────────────────────────────────────────────────────────────────

def _to_json(result: ExtractionResult, url: str, include_metadata: bool) -> str:
    items = _normalise_items(result.items, result.schema_fields)
    payload: dict[str, Any] = {"data": items}
    if include_metadata:
        payload["metadata"] = _metadata(result, url)
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ── CSV ───────────────────────────────────────────────────────────────────────

def _to_csv(result: ExtractionResult, url: str, include_metadata: bool) -> str:
    items = _normalise_items(result.items, result.schema_fields)
    if not items:
        return ""

    buf = io.StringIO()
    fields = list(items[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(items)

    if include_metadata:
        meta = _metadata(result, url)
        buf.write(f"\n# url={meta['url']},items={meta['item_count']},method={meta['method']}\n")

    return buf.getvalue()


# ── Markdown table ────────────────────────────────────────────────────────────

def _to_markdown(result: ExtractionResult, url: str, include_metadata: bool) -> str:
    items = _normalise_items(result.items, result.schema_fields)
    if not items:
        return "_No data extracted._"

    fields = list(items[0].keys())
    header = "| " + " | ".join(fields) + " |"
    separator = "| " + " | ".join("---" for _ in fields) + " |"
    rows = [
        "| " + " | ".join(str(item.get(f, "")).replace("|", "\\|")[:120] for f in fields) + " |"
        for item in items
    ]

    lines = [header, separator] + rows
    if include_metadata:
        meta = _metadata(result, url)
        lines.insert(0, f"**Source:** {meta['url']}  |  **Items:** {meta['item_count']}  |  **Method:** {meta['method']}\n")
    return "\n".join(lines)


# ── Plain text ────────────────────────────────────────────────────────────────

def _to_text(result: ExtractionResult, url: str, include_metadata: bool) -> str:
    lines: list[str] = []
    if include_metadata:
        meta = _metadata(result, url)
        lines += [
            f"# Source: {meta['url']}",
            f"# Extracted: {meta['timestamp']}",
            f"# Items: {meta['item_count']}  Method: {meta['method']}",
            "",
        ]

    if result.method == "llm_selector":
        for i, item in enumerate(result.items, 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append(result.cleaned_text)

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_items(items: list[Any], fields: list[str]) -> list[dict]:
    """Convert raw extracted strings to dicts using inferred schema fields."""
    if not items:
        return []

    # Already-dict items (future use)
    if items and isinstance(items[0], dict):
        return items

    # Single-field schema
    if len(fields) == 1:
        return [{fields[0]: str(item)} for item in items]

    # Multi-field: try to split each item by common delimiters
    normalised: list[dict] = []
    for item in items:
        text = str(item)
        # Try splitting by newline or pipe first
        parts = [p.strip() for p in re.split(r"\n|\|", text) if p.strip()]
        row: dict = {}
        for i, field in enumerate(fields):
            row[field] = parts[i] if i < len(parts) else ""
        normalised.append(row)
    return normalised


def _metadata(result: ExtractionResult, url: str) -> dict:
    return {
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "item_count": len(result.items),
        "selector": result.selector_used,
        "method": result.method,
        "confidence": "high" if result.method == "llm_selector" else "low",
    }
