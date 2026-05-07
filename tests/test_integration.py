"""Integration tests — full pipeline with mocked network layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ── Shared HTML fixtures ──────────────────────────────────────────────────────

_HN_HTML = """
<html><body>
  <tr class="athing">
    <td class="title"><a href="https://example.com/story1" class="storylink">Story One</a></td>
  </tr>
  <tr class="athing">
    <td class="title"><a href="https://example.com/story2" class="storylink">Story Two</a></td>
  </tr>
</body></html>
"""

_BOOKS_HTML = """
<html><body>
  <article class="product_pod">
    <h3><a href="catalogue/book1.html">A Light in the Attic</a></h3>
    <p class="price_color">£51.77</p>
  </article>
  <article class="product_pod">
    <h3><a href="catalogue/book2.html">Tipping the Velvet</a></h3>
    <p class="price_color">£53.74</p>
  </article>
  <article class="product_pod">
    <h3><a href="catalogue/book3.html">Soumission</a></h3>
    <p class="price_color">£50.10</p>
  </article>
</body></html>
"""

_JS_REQUIRED_HTML = """
<html><body>
  <noscript>JavaScript must be enabled for the correct page display</noscript>
  <div id="app"></div>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
  <div class="contact-info">
    <p>Email: info@ericsson.com</p>
    <p>LinkedIn: linkedin.com/company/ericsson</p>
  </div>
</body></html>
"""


def _make_scrape_result(html: str, layer: str = "httpx", status: int = 200):
    from datapulse.modules.scraper import ScrapeResult
    return ScrapeResult(url="https://example.com", html=html, status_code=status, layer_used=layer)


# ── UC-01: Link extraction ────────────────────────────────────────────────────

def test_extract_links_from_html():
    """extract_links returns all valid anchor hrefs from HTML."""
    from datapulse.modules.scraper import extract_links

    links = extract_links(_HN_HTML, "https://news.ycombinator.com")
    hrefs = [l["href"] for l in links]
    assert "https://example.com/story1" in hrefs
    assert "https://example.com/story2" in hrefs


@pytest.mark.asyncio
async def test_scrape_layer1_returns_html():
    """scrape() returns httpx result for a normal 200 page."""
    from datapulse.modules.scraper import scrape

    mock_result = _make_scrape_result(_BOOKS_HTML)
    with patch("datapulse.modules.scraper._fetch_httpx", new=AsyncMock(return_value=mock_result)):
        result = await scrape("https://books.toscrape.com")

    assert result.layer_used == "httpx"
    assert "product_pod" in result.html


# ── UC-02: Structured data extraction ────────────────────────────────────────

def test_extractor_finds_books():
    """extract() discovers selector and returns book items from static HTML."""
    from datapulse.modules.extractor import extract

    selector_resp = '{"selector": "article.product_pod", "xpath": null, "reasoning": "product cards"}'
    validation_resp = '{"valid": true, "reason": "title and price present"}'
    schema_resp = '{"fields": ["title", "price"], "reasoning": "book fields"}'

    with patch("datapulse.modules.extractor._llm_call", side_effect=[selector_resp, validation_resp, schema_resp]), \
         patch("datapulse.utils.litellm_client.is_available", return_value=True):
        result = extract(_BOOKS_HTML, url="https://books.toscrape.com", content_target="book title and price")

    assert result.method == "llm_selector"
    assert result.selector_used == "article.product_pod"
    assert len(result.items) == 3
    assert "title" in result.schema_fields or "price" in result.schema_fields


def test_extractor_text_only_without_target():
    """extract() with no content_target returns cleaned text, method=text_only."""
    from datapulse.modules.extractor import extract

    result = extract(_BOOKS_HTML, url="https://books.toscrape.com", content_target="")
    assert result.method == "text_only"
    assert result.selector_used is None


# ── UC-03: Deduplication ─────────────────────────────────────────────────────

def test_deduplicate_by_url():
    """deduplicate_items removes repeated items sharing the same URL."""
    from datapulse.modules.formatter import deduplicate_items

    items = [
        "name: Acme\nurl: /exhibitors/acme",
        "name: Beta Corp\nurl: /exhibitors/beta",
        "name: Acme\nurl: /exhibitors/acme",   # duplicate
        "name: Gamma\nurl: /exhibitors/gamma",
    ]
    deduped, removed = deduplicate_items(items)
    assert removed == 1
    assert len(deduped) == 3


def test_deduplicate_by_content():
    """deduplicate_items falls back to content dedup when no URL present."""
    from datapulse.modules.formatter import deduplicate_items

    items = ["Book A — £10", "Book B — £20", "Book A — £10"]
    deduped, removed = deduplicate_items(items)
    assert removed == 1
    assert len(deduped) == 2


# ── UC-04: JS-required auto-upgrade ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_js_required_page_upgrades_to_playwright():
    """scrape() auto-upgrades httpx→Playwright when page says 'JavaScript must be enabled'."""
    from datapulse.modules.scraper import scrape, ScrapeResult

    httpx_result = _make_scrape_result(_JS_REQUIRED_HTML)
    playwright_result = _make_scrape_result(_BOOKS_HTML, layer="playwright")

    with patch("datapulse.modules.scraper._fetch_httpx", new=AsyncMock(return_value=httpx_result)), \
         patch("datapulse.modules.scraper._fetch_playwright", new=AsyncMock(return_value=playwright_result)):
        result = await scrape("https://spa-example.com")

    assert result.layer_used == "playwright"
    assert "product_pod" in result.html


@pytest.mark.asyncio
async def test_httpx_ssl_error_falls_through_to_playwright():
    """scrape() falls through to Playwright when httpx raises SSL error."""
    from datapulse.modules.scraper import scrape, ScrapeResult

    playwright_result = _make_scrape_result(_BOOKS_HTML, layer="playwright")

    with patch("datapulse.modules.scraper._fetch_httpx", new=AsyncMock(side_effect=Exception("SSL: CERTIFICATE_VERIFY_FAILED"))), \
         patch("datapulse.modules.scraper._fetch_playwright", new=AsyncMock(return_value=playwright_result)):
        result = await scrape("https://mwcbarcelona.com/exhibitors/")

    assert result.layer_used == "playwright"


# ── UC-05: Follow URL extraction ──────────────────────────────────────────────

def test_extract_follow_urls_absolute():
    """_extract_follow_urls resolves relative hrefs to absolute URLs."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from datapulse.main import _extract_follow_urls

    items = [
        "name: Ericsson\nurl: /exhibitors/ericsson",
        "name: Nokia\nurl: /exhibitors/nokia",
        "name: Ericsson\nurl: /exhibitors/ericsson",   # duplicate
    ]
    urls = _extract_follow_urls(items, "https://www.mwcbarcelona.com/exhibitors/")
    assert len(urls) == 2
    assert "https://www.mwcbarcelona.com/exhibitors/ericsson" in urls


def test_merge_with_details():
    """_merge_with_details concatenates listing + detail text by URL."""
    from datapulse.main import _merge_with_details

    listing = [
        "name: Ericsson\nurl: /exhibitors/ericsson",
        "name: Nokia\nurl: /exhibitors/nokia",
    ]
    detail_by_url = {
        "https://mwc.com/exhibitors/ericsson": ["email: info@ericsson.com\nlinkedin: linkedin.com/ericsson"],
    }
    merged = _merge_with_details(listing, detail_by_url, "https://mwc.com/exhibitors/")
    assert "email: info@ericsson.com" in merged[0]
    assert "Nokia" in merged[1]
    assert "email" not in merged[1]


# ── UC-06: Lenient validation ─────────────────────────────────────────────────

def test_lenient_validation_accepts_partial_fields():
    """_validate_selector with strict=False accepts partial field matches."""
    from datapulse.modules.extractor import _validate_selector

    html = "<html><body><div class='contact'><p>email: info@company.com</p></div></body></html>"
    validation_resp = '{"valid": true, "reason": "email present"}'

    with patch("datapulse.modules.extractor._llm_call", return_value=validation_resp):
        valid, reason = _validate_selector(html, "div.contact", "email and social media", strict=False)

    assert valid is True


def test_strict_validation_rejects_partial_fields():
    """_validate_selector with strict=True rejects if not all fields present."""
    from datapulse.modules.extractor import _validate_selector

    html = "<html><body><div class='contact'><p>email: info@company.com</p></div></body></html>"
    validation_resp = '{"valid": false, "reason": "social media missing"}'

    with patch("datapulse.modules.extractor._llm_call", return_value=validation_resp):
        valid, reason = _validate_selector(html, "div.contact", "email and social media", strict=True)

    assert valid is False
    assert "social media" in reason
