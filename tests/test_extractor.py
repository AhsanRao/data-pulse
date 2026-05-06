"""Phase 2 tests — extraction engine."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from datapulse.modules.extractor import (
    ExtractionResult,
    _discover_selector,
    _validate_selector,
    _parse_json_response,
    extract,
)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def test_parse_json_plain():
    raw = '{"selector": "div.item", "xpath": null, "reasoning": "matches items"}'
    result = _parse_json_response(raw)
    assert result["selector"] == "div.item"


def test_parse_json_with_markdown_fence():
    raw = "```json\n{\"selector\": \"li.product\", \"xpath\": null, \"reasoning\": \"ok\"}\n```"
    result = _parse_json_response(raw)
    assert result["selector"] == "li.product"


def test_parse_json_raises_on_invalid():
    with pytest.raises(Exception):
        _parse_json_response("not json at all")


# ── Selector discovery (mocked LLM) ──────────────────────────────────────────

def test_discover_selector_returns_result():
    mock_response = '{"selector": "article.product_pod", "xpath": null, "reasoning": "product card"}'
    with patch("datapulse.modules.extractor._llm_call", return_value=mock_response):
        result = _discover_selector("<article class='product_pod'><h3>Book</h3></article>", "book title")
    assert result.selector == "article.product_pod"
    assert result.reasoning == "product card"


def test_discover_selector_null_returns_none():
    mock_response = '{"selector": null, "xpath": null, "reasoning": "no match"}'
    with patch("datapulse.modules.extractor._llm_call", return_value=mock_response):
        result = _discover_selector("<div>no match</div>", "prices")
    assert result.selector is None


# ── Validation gate (mocked LLM) ─────────────────────────────────────────────

def test_validate_selector_valid():
    html = """
    <html><body>
      <article class="product_pod"><p class="price_color">£51.77</p></article>
      <article class="product_pod"><p class="price_color">£22.00</p></article>
    </body></html>
    """
    mock_response = '{"valid": true, "reason": "prices match target"}'
    with patch("datapulse.modules.extractor._llm_call", return_value=mock_response):
        valid, reason = _validate_selector(html, "p.price_color", "book prices")
    assert valid is True
    assert "prices" in reason


def test_validate_selector_zero_elements():
    html = "<html><body><p>nothing</p></body></html>"
    valid, reason = _validate_selector(html, "div.nonexistent", "anything")
    assert valid is False
    assert "0 elements" in reason


# ── Full extract pipeline ─────────────────────────────────────────────────────

def test_extract_no_target_returns_text():
    html = "<html><body><p>Hello world</p></body></html>"
    result = extract(html, url="https://example.com", content_target="")
    assert result.method == "text_only"
    assert "Hello world" in result.cleaned_text
    assert result.selector_used is None


def test_extract_no_api_key_falls_back():
    html = "<html><body><p>Some content</p></body></html>"
    with patch("datapulse.modules.extractor.cfg") as mock_cfg:
        mock_cfg.anthropic_api_key = None
        mock_cfg.llm = {"max_retries": 3, "selector_model": "test"}
        mock_cfg.log_dir = MagicMock()
        result = extract(html, content_target="prices")
    assert result.method == "text_only"


def test_extract_with_mocked_llm_succeeds():
    html = """
    <html><body>
      <article class="book"><h3>Book One</h3><p class="price">£10.00</p></article>
      <article class="book"><h3>Book Two</h3><p class="price">£20.00</p></article>
      <article class="book"><h3>Book Three</h3><p class="price">£30.00</p></article>
    </body></html>
    """
    selector_resp = '{"selector": "article.book", "xpath": null, "reasoning": "book cards"}'
    valid_resp = '{"valid": true, "reason": "matches book data"}'
    schema_resp = '{"fields": ["title", "price"], "reasoning": "inferred from content"}'

    call_responses = [selector_resp, valid_resp, schema_resp]
    call_iter = iter(call_responses)

    with patch("datapulse.modules.extractor.cfg") as mock_cfg, \
         patch("datapulse.modules.extractor._llm_call", side_effect=lambda *a, **kw: next(call_iter)):
        mock_cfg.anthropic_api_key = "sk-test"
        mock_cfg.llm = {"max_retries": 3, "selector_model": "test"}
        mock_cfg.log_dir = MagicMock()
        mock_cfg.log_dir.__truediv__ = lambda self, x: MagicMock()

        result = extract(html, content_target="book title and price")

    assert result.method == "llm_selector"
    assert result.selector_used == "article.book"
    assert len(result.items) == 3
    assert "title" in result.schema_fields or "price" in result.schema_fields


def test_extract_retries_on_failed_validation():
    """Selector fails validation on first chunk; succeeds on second chunk."""
    html = """
    <html><body>
      <div class="chunk1"><p>Chunk 1 content</p></div>
      <div class="chunk2"><p>Chunk 2 content</p></div>
    </body></html>
    """
    selector_fail = '{"selector": "div.chunk1", "xpath": null, "reasoning": "first try"}'
    valid_fail = '{"valid": false, "reason": "wrong data"}'
    selector_ok = '{"selector": "p", "xpath": null, "reasoning": "second try"}'
    valid_ok = '{"valid": true, "reason": "matches"}'
    schema_ok = '{"fields": ["content"], "reasoning": "simple"}'

    responses = iter([selector_fail, valid_fail, selector_ok, valid_ok, schema_ok])

    # Force the chunker to produce two separate chunks so the retry loop runs twice
    with patch("datapulse.modules.extractor.cfg") as mock_cfg, \
         patch("datapulse.modules.extractor._llm_call", side_effect=lambda *a, **kw: next(responses)), \
         patch("datapulse.modules.extractor.chunk_html", return_value=["<div class='chunk1'><p>Chunk 1</p></div>", "<div class='chunk2'><p>Chunk 2</p></div>"]):
        mock_cfg.anthropic_api_key = "sk-test"
        mock_cfg.llm = {"max_retries": 3, "selector_model": "test"}
        mock_cfg.log_dir = MagicMock()
        mock_cfg.log_dir.__truediv__ = lambda self, x: MagicMock()

        result = extract(html, content_target="paragraph text")

    assert result.method == "llm_selector"
    assert result.selector_used == "p"
