"""Tests for Module 1 — Intent Parser (Phase 1 stub + Phase 3 Ollama)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from datapulse.modules.intent_parser import (
    parse_url_intent,
    parse_query,
    _extract_url,
    _parse_heuristic,
    _normalise_url,
    _extract_target_from_query,
)


# ── Phase 1 stub ──────────────────────────────────────────────────────────────

def test_parse_url_intent_defaults():
    intent = parse_url_intent("https://example.com")
    assert intent.url == "https://example.com"
    assert intent.intent_type == "page_content"
    assert intent.max_urls == 25
    assert intent.depth == 1


def test_parse_url_intent_overrides():
    intent = parse_url_intent(
        "https://example.com",
        intent_type="url_list",
        content_target="links",
        max_urls=10,
        depth=0,
    )
    assert intent.intent_type == "url_list"
    assert intent.content_target == "links"
    assert intent.max_urls == 10
    assert intent.depth == 0


# ── URL extraction helper ─────────────────────────────────────────────────────

def test_extract_url_finds_https():
    url = _extract_url("get prices from https://books.toscrape.com/catalogue")
    assert url == "https://books.toscrape.com/catalogue"


def test_extract_url_finds_http():
    url = _extract_url("scrape http://example.com please")
    assert url == "http://example.com"


def test_extract_url_none_when_missing():
    assert _extract_url("no url here") is None


def test_extract_url_strips_trailing_punctuation():
    url = _extract_url("check https://example.com.")
    assert url == "https://example.com"


# ── URL normalisation ─────────────────────────────────────────────────────────

def test_normalise_url_strips_trailing_slash():
    assert _normalise_url("https://example.com/") == "https://example.com"


def test_normalise_url_adds_scheme():
    assert _normalise_url("example.com").startswith("https://")


# ── Heuristic parser ──────────────────────────────────────────────────────────

def test_heuristic_detects_url_list():
    intent = _parse_heuristic("give me all the links on https://example.com")
    assert intent.intent_type == "url_list"
    assert intent.depth == 0


def test_heuristic_detects_structured_data():
    intent = _parse_heuristic("get product name and price from https://books.toscrape.com")
    assert intent.intent_type == "structured_data"


def test_heuristic_detects_deep_content():
    intent = _parse_heuristic("get the title and summary of each article on https://blog.example.com")
    assert intent.intent_type == "deep_content"
    assert intent.depth == 2


def test_heuristic_defaults_to_page_content():
    intent = _parse_heuristic("what is on https://example.com")
    assert intent.intent_type == "page_content"


def test_heuristic_raises_without_url():
    with pytest.raises(ValueError, match="Could not find a URL"):
        _parse_heuristic("get me some data")


def test_heuristic_url_extraction():
    intent = _parse_heuristic("scrape https://news.ycombinator.com please")
    assert "ycombinator" in intent.url


# ── Target extraction ─────────────────────────────────────────────────────────

def test_extract_target_removes_filler():
    target = _extract_target_from_query(
        "get me the product name and price from https://example.com",
        "https://example.com",
    )
    assert "product name" in target or "price" in target
    assert "get" not in target.lower()


# ── LLM path (mocked) ────────────────────────────────────────────────────────

def test_parse_query_uses_llm_when_available():
    mock_response = """{
        "url": "https://books.toscrape.com",
        "intent_type": "structured_data",
        "content_target": "book title and price",
        "max_urls": 25,
        "depth": 0
    }"""

    with patch("datapulse.utils.llm_client.is_available", return_value=True), \
         patch("datapulse.utils.llm_client.call", return_value=mock_response):
        intent = parse_query("get book titles and prices from https://books.toscrape.com")

    assert intent.url == "https://books.toscrape.com"
    assert intent.intent_type == "structured_data"
    assert "title" in intent.content_target or "price" in intent.content_target


def test_parse_query_falls_back_to_heuristic_when_ollama_down():
    with patch("datapulse.utils.llm_client.is_available", return_value=False), \
         patch("datapulse.modules.intent_parser.ollama_available", return_value=False):
        intent = parse_query("get all links from https://news.ycombinator.com")

    assert intent.url == "https://news.ycombinator.com"
    assert intent.intent_type == "url_list"


def test_parse_query_falls_back_on_bad_json():
    with patch("datapulse.utils.llm_client.is_available", return_value=True), \
         patch("datapulse.utils.llm_client.call", return_value="not json at all"):
        intent = parse_query("get prices from https://example.com")

    assert "example.com" in intent.url


def test_parse_query_deep_content_sets_depth_2():
    mock_response = """{
        "url": "https://blog.example.com",
        "intent_type": "deep_content",
        "content_target": "article title and summary",
        "max_urls": 25,
        "depth": 2
    }"""
    with patch("datapulse.utils.llm_client.is_available", return_value=True), \
         patch("datapulse.utils.llm_client.call", return_value=mock_response):
        intent = parse_query("get title and summary of each article on https://blog.example.com")

    assert intent.intent_type == "deep_content"
    assert intent.depth == 2


def test_parse_query_falls_back_to_heuristic_target_when_llm_returns_empty():
    """If LLM returns empty content_target for structured_data, use heuristic extraction."""
    mock_response = """{
        "url": "https://books.toscrape.com",
        "intent_type": "structured_data",
        "content_target": "",
        "max_urls": 25,
        "depth": 0
    }"""
    with patch("datapulse.utils.llm_client.is_available", return_value=True), \
         patch("datapulse.utils.llm_client.call", return_value=mock_response):
        intent = parse_query("get book titles and prices from https://books.toscrape.com")

    assert intent.content_target != ""
    assert "title" in intent.content_target.lower() or "price" in intent.content_target.lower()


def test_parse_query_url_list_depth_is_zero():
    mock_response = """{
        "url": "https://example.com",
        "intent_type": "url_list",
        "content_target": "",
        "max_urls": 25,
        "depth": 0
    }"""
    with patch("datapulse.utils.llm_client.is_available", return_value=True), \
         patch("datapulse.utils.llm_client.call", return_value=mock_response):
        intent = parse_query("get all links from https://example.com")

    assert intent.intent_type == "url_list"
    assert intent.depth == 0
