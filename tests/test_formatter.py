"""Phase 2 tests — output formatter."""

from __future__ import annotations

import csv
import io
import json

import pytest

from datapulse.modules.extractor import ExtractionResult
from datapulse.modules.formatter import format_result


def _make_result(items, selector="div.item", fields=None, method="llm_selector"):
    return ExtractionResult(
        items=items,
        selector_used=selector,
        schema_fields=fields or ["value"],
        cleaned_text="\n".join(str(i) for i in items),
        method=method,
    )


# ── JSON ──────────────────────────────────────────────────────────────────────

def test_json_format_structure():
    result = _make_result(["Book One", "Book Two"])
    out = format_result(result, url="https://example.com", fmt="json")
    data = json.loads(out)
    assert "data" in data
    assert "metadata" in data
    assert data["metadata"]["item_count"] == 2
    assert data["metadata"]["method"] == "llm_selector"


def test_json_format_no_metadata():
    result = _make_result(["a", "b"])
    out = format_result(result, url="https://x.com", fmt="json", include_metadata=False)
    data = json.loads(out)
    assert "metadata" not in data
    assert "data" in data


def test_json_empty_items():
    result = _make_result([])
    out = format_result(result, url="https://x.com", fmt="json")
    data = json.loads(out)
    assert data["data"] == []


# ── CSV ───────────────────────────────────────────────────────────────────────

def test_csv_format_has_header():
    result = _make_result(["£10.00", "£20.00"], fields=["price"])
    out = format_result(result, url="https://x.com", fmt="csv")
    reader = csv.DictReader(io.StringIO(out.split("\n# ")[0]))
    rows = list(reader)
    assert rows[0]["price"] == "£10.00"
    assert rows[1]["price"] == "£20.00"


def test_csv_empty_returns_empty_string():
    result = _make_result([])
    out = format_result(result, url="https://x.com", fmt="csv")
    assert out == ""


# ── Markdown ──────────────────────────────────────────────────────────────────

def test_markdown_has_table_separator():
    result = _make_result(["Row 1", "Row 2"], fields=["content"])
    out = format_result(result, url="https://x.com", fmt="md")
    assert "| content |" in out
    assert "| --- |" in out
    assert "Row 1" in out


def test_markdown_pipes_escaped():
    result = _make_result(["val|with|pipes"], fields=["content"])
    out = format_result(result, url="https://x.com", fmt="md", include_metadata=False)
    # Pipe chars inside cell values should be escaped
    lines = [l for l in out.split("\n") if "val" in l]
    assert lines, "No data row found"
    assert "\\|" in lines[0]


def test_markdown_empty_returns_placeholder():
    result = _make_result([])
    out = format_result(result, url="https://x.com", fmt="md")
    assert "_No data extracted._" in out


# ── Plain text ────────────────────────────────────────────────────────────────

def test_text_format_with_metadata():
    result = _make_result(["item 1", "item 2"])
    out = format_result(result, url="https://example.com", fmt="text")
    assert "# Source:" in out
    assert "1. item 1" in out
    assert "2. item 2" in out


def test_text_format_text_only_method():
    result = _make_result(
        ["full page text content"],
        selector=None,
        method="text_only",
    )
    result.cleaned_text = "full page text content"
    out = format_result(result, url="https://x.com", fmt="text", include_metadata=False)
    assert "full page text content" in out
