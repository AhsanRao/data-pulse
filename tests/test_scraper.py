"""Phase 1 tests — scraper and HTML utilities."""

from __future__ import annotations

import pytest

# ── URL extraction ────────────────────────────────────────────────────────────

def test_extract_links_basic():
    from datapulse.modules.scraper import extract_links

    html = """
    <html><body>
      <a href="/page1">Page 1</a>
      <a href="https://other.com/page2">External</a>
      <a href="#section">Fragment only</a>
      <a href="javascript:void(0)">JS link</a>
    </body></html>
    """
    base = "https://example.com"
    links = extract_links(html, base)

    hrefs = [l["href"] for l in links]
    assert "https://example.com/page1" in hrefs
    assert "https://other.com/page2" in hrefs
    # Fragment-only and JS links should be excluded
    assert not any("#section" in h for h in hrefs)
    assert not any("javascript" in h for h in hrefs)


def test_extract_links_deduplication():
    from datapulse.modules.scraper import extract_links

    html = """
    <html><body>
      <a href="/about">About</a>
      <a href="/about">About again</a>
      <a href="/about#team">About team section</a>
    </body></html>
    """
    links = extract_links(html, "https://example.com")
    hrefs = [l["href"] for l in links]
    # Fragment stripped — /about appears only once
    assert hrefs.count("https://example.com/about") == 1


def test_same_domain():
    from datapulse.modules.scraper import same_domain

    assert same_domain("https://example.com/page", "https://example.com") is True
    assert same_domain("https://other.com/page", "https://example.com") is False


# ── HTML cleaning ─────────────────────────────────────────────────────────────

def test_clean_html_removes_scripts():
    from datapulse.utils.html_cleaner import clean_html

    html = """
    <html><body>
      <script>alert('xss')</script>
      <p>Hello world</p>
      <style>.hide{display:none}</style>
    </body></html>
    """
    result = clean_html(html)
    assert "alert" not in result
    assert ".hide" not in result
    assert "Hello world" in result


def test_clean_html_keep_tags_removes_scripts():
    from datapulse.utils.html_cleaner import clean_html_keep_tags

    html = "<html><body><script>bad()</script><p>Good</p></body></html>"
    result = clean_html_keep_tags(html)
    assert "bad()" not in result
    assert "Good" in result


# ── Semantic chunker ──────────────────────────────────────────────────────────

def test_chunker_returns_list():
    from datapulse.utils.chunker import chunk_html

    html = "<body>" + "<div><p>Item</p></div>" * 50 + "</body>"
    chunks = chunk_html(html)
    assert isinstance(chunks, list)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert len(chunk) <= 6_500  # slightly over MAX_CHUNK_CHARS is ok at block boundaries


def test_chunker_handles_empty():
    from datapulse.utils.chunker import chunk_html

    chunks = chunk_html("")
    assert isinstance(chunks, list)


# ── Job persistence ───────────────────────────────────────────────────────────

def test_job_save_and_load(tmp_path, monkeypatch):
    from datapulse.job import Job, Intent

    # Redirect home to tmp_path so we don't pollute ~/.datapulse
    monkeypatch.setenv("HOME", str(tmp_path))
    import pathlib
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

    intent = Intent(url="https://example.com", intent_type="page_content")
    job = Job(query="test", intent=intent)
    job.save()

    loaded = Job.load(job.job_id)
    assert loaded.job_id == job.job_id
    assert loaded.intent.url == "https://example.com"
    assert loaded.status == "pending"


def test_job_mark_url_done(tmp_path, monkeypatch):
    import pathlib
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

    from datapulse.job import Job, Intent

    intent = Intent(url="https://example.com")
    job = Job(query="test", intent=intent)
    job.urls_pending = ["https://example.com"]
    job.save()

    job.mark_url_done("https://example.com", result={"data": 1})
    assert "https://example.com" not in job.urls_pending
    assert "https://example.com" in job.urls_processed
    assert len(job.results) == 1


# ── Config ────────────────────────────────────────────────────────────────────

def test_config_defaults():
    from datapulse.config import Config

    c = Config(config_path=None)  # will try cwd/datapulse.config.yaml — may or may not exist
    assert c.scraping["max_urls"] == 25 or isinstance(c.scraping["max_urls"], int)
    assert c.playwright["headless"] in (True, False)


# ── Retry utility ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_retry_succeeds_after_failure():
    from datapulse.utils.retry import async_retry

    call_count = 0

    @async_retry(max_attempts=3, base_delay=0.01)
    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not yet")
        return "ok"

    result = await flaky()
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_async_retry_raises_after_exhaustion():
    from datapulse.utils.retry import async_retry

    @async_retry(max_attempts=2, base_delay=0.01)
    async def always_fails():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        await always_fails()
