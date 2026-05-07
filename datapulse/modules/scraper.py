"""Module 3 — Scraping Pipeline.

Layer 1: httpx + BeautifulSoup (fast, static HTML)
Layer 2: Playwright headless (JS/dynamic pages)
Layer 3: ScraperAPI / Zyte (anti-bot fallback)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlparse

from datapulse.config import cfg
from datapulse.utils.retry import async_retry

logger = logging.getLogger(__name__)

ScrapeLayer = Literal["httpx", "playwright", "scraperapi", "zyte"]

_CLOUDFLARE_SIGNATURES = [
    "cf-ray",
    "Just a moment",
    "Enable JavaScript and cookies",
    "Checking if the site connection is secure",
]

_JS_REQUIRED_PATTERNS = [
    "javascript must be enabled",
    "javascript is required",
    "please enable javascript",
    "this site requires javascript",
    "enable javascript to continue",
    "requires javascript to function",
    "javascript needs to be enabled",
    "javascript to be enabled",
]

_BOT_STATUS_CODES = {403, 429, 503}


@dataclass
class ScrapeResult:
    url: str
    html: str
    status_code: int
    layer_used: ScrapeLayer
    error: str | None = None


# ── Layer 1: httpx ────────────────────────────────────────────────────────────

@async_retry(max_attempts=2, base_delay=1.0, exceptions=(Exception,))
async def _fetch_httpx(url: str, timeout: int) -> ScrapeResult:
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        return ScrapeResult(
            url=str(resp.url),
            html=resp.text,
            status_code=resp.status_code,
            layer_used="httpx",
        )


def _is_cloudflare_blocked(result: ScrapeResult) -> bool:
    if result.status_code in _BOT_STATUS_CODES:
        return True
    lower = result.html.lower()
    return any(sig.lower() in lower for sig in _CLOUDFLARE_SIGNATURES)


def _is_js_required(result: ScrapeResult) -> bool:
    lower = result.html.lower()
    return any(p in lower for p in _JS_REQUIRED_PATTERNS)


# ── Layer 2: Playwright ───────────────────────────────────────────────────────

_NEXT_PAGE_SELECTORS = [
    'a[aria-label="Next Page"]',
    'a[aria-label="Next page"]',
    'button[aria-label="Next Page"]',
    'a[rel="next"]',
    'a.next-page',
    'button.next-page',
    '.pagination a.next',
    'a.ais-Pagination-item--nextPage',
]


_CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    "[aria-label='Accept all cookies']",
    "[aria-label='Accept All']",
    "button.cookie-accept",
    "button#accept-cookies",
    "#CybotCookiebotDialogBodyButtonAccept",
]


async def _dismiss_consent(page) -> None:
    """Best-effort: click any visible cookie consent accept button."""
    for sel in _CONSENT_SELECTORS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click(timeout=3000)
                await asyncio.sleep(0.5)
                logger.debug("Dismissed consent popup: %s", sel)
                return
        except Exception:
            continue


async def _paginate_playwright(page, first_html: str, pw_cfg: dict) -> str:
    """Click through Next Page buttons and return combined HTML from all pages."""
    max_pages = pw_cfg.get("max_pages", 10)
    all_bodies: list[str] = [first_html]

    # Dismiss any consent popup that might block clicks
    await _dismiss_consent(page)

    for page_num in range(1, max_pages):
        next_btn = None
        for sel in _NEXT_PAGE_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    next_btn = btn
                    break
            except Exception:
                continue

        if not next_btn:
            logger.debug("Pagination: no Next button found — done at page %d", page_num)
            break

        try:
            await next_btn.click(timeout=5000)
        except Exception:
            # Popup may have come back — try dismissing again and retry once
            await _dismiss_consent(page)
            try:
                await next_btn.click(timeout=5000)
            except Exception:
                logger.debug("Pagination: click failed on page %d — stopping", page_num)
                break

        try:
            from playwright.async_api import TimeoutError as PWTimeout
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            await asyncio.sleep(2.0)

        all_bodies.append(await page.content())
        logger.debug("Pagination: captured page %d", page_num + 1)

    if len(all_bodies) == 1:
        return all_bodies[0]

    # Combine: extract <body> contents and wrap in a single document so
    # the cleaner/chunker can process all pages as one HTML
    from bs4 import BeautifulSoup

    combined_parts: list[str] = []
    for h in all_bodies:
        soup = BeautifulSoup(h, "lxml")
        body = soup.body
        combined_parts.append(str(body) if body else h)

    return "<html><body>" + "\n".join(combined_parts) + "</body></html>"


async def _fetch_playwright(url: str, timeout: int, paginate: bool = False) -> ScrapeResult:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.warning("Playwright not installed — cannot use dynamic scraping layer.")
        return ScrapeResult(url=url, html="", status_code=0, layer_used="playwright",
                            error="Playwright not installed")

    pw_cfg = cfg.playwright
    headless: bool = pw_cfg.get("headless", True)
    scroll_px: int = pw_cfg.get("scroll_increment_px", 500)
    scroll_ms: int = pw_cfg.get("scroll_pause_ms", 800)
    max_scrolls: int = pw_cfg.get("max_scroll_attempts", 30)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page()
        page.set_default_timeout(timeout * 1000)

        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else 0

            # Give SPA frameworks time to finish hydration after DOM is ready
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass

            # Scroll to load lazy/infinite content
            prev_height = -1
            scroll_count = 0
            no_change_streak = 0
            while scroll_count < max_scrolls:
                curr_height = await page.evaluate("document.body.scrollHeight")
                if curr_height == prev_height:
                    no_change_streak += 1
                    if no_change_streak >= 3:
                        break
                else:
                    no_change_streak = 0
                prev_height = curr_height
                await page.evaluate(f"window.scrollBy(0, {scroll_px})")
                try:
                    await page.wait_for_load_state("networkidle", timeout=scroll_ms * 2)
                except PWTimeout:
                    pass
                await asyncio.sleep(scroll_ms / 1000)
                scroll_count += 1

            html = await page.content()
            if paginate:
                html = await _paginate_playwright(page, html, pw_cfg)
            return ScrapeResult(url=url, html=html, status_code=status, layer_used="playwright")

        except Exception as exc:
            logger.debug("Playwright fetch failed for %s: %s", url, exc)
            return ScrapeResult(url=url, html="", status_code=0, layer_used="playwright",
                                error=str(exc))
        finally:
            await browser.close()


# ── Layer 3: ScraperAPI / Zyte ────────────────────────────────────────────────

async def _fetch_scraperapi(url: str, timeout: int) -> ScrapeResult:
    api_key = cfg.scraperapi_key
    if not api_key:
        return ScrapeResult(
            url=url, html="", status_code=0, layer_used="scraperapi",
            error=(
                "No SCRAPERAPI_KEY found in .env. "
                "Sign up at https://www.scraperapi.com (free tier: 1000 req/month) "
                "and add SCRAPERAPI_KEY=your-key to your .env file."
            ),
        )

    import httpx

    api_url = f"http://api.scraperapi.com?api_key={api_key}&url={url}&render=true"
    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        resp = await client.get(api_url)
        return ScrapeResult(url=url, html=resp.text, status_code=resp.status_code,
                            layer_used="scraperapi")


async def _fetch_zyte(url: str, timeout: int) -> ScrapeResult:
    api_key = cfg.zyte_api_key
    if not api_key:
        return ScrapeResult(
            url=url, html="", status_code=0, layer_used="zyte",
            error="No ZYTE_API_KEY found in .env.",
        )

    import httpx

    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        resp = await client.post(
            "https://api.zyte.com/v1/extract",
            auth=(api_key, ""),
            json={"url": url, "httpResponseBody": True, "browserHtml": True},
        )
        data = resp.json()
        html = data.get("browserHtml", "")
        return ScrapeResult(url=url, html=html, status_code=resp.status_code, layer_used="zyte")


# ── Public interface ──────────────────────────────────────────────────────────

async def scrape(url: str, force_playwright: bool = False, paginate: bool = False) -> ScrapeResult:
    """Fetch URL through the progressive fallback pipeline.

    Returns the first successful ScrapeResult.
    paginate=True clicks through Next Page buttons (Playwright only).
    """
    timeout: int = cfg.scraping.get("timeout_seconds", 30)

    # Layer 1 — fast path (skip if forced playwright or paginating)
    if not force_playwright and not paginate:
        logger.debug("Layer 1 (httpx): %s", url)
        try:
            result = await _fetch_httpx(url, timeout)
            if result.status_code == 200 and not _is_cloudflare_blocked(result) and not _is_js_required(result):
                return result
            if _is_js_required(result):
                logger.debug("httpx got JS-required page — upgrading to Playwright automatically")
            else:
                logger.debug("httpx returned %d or bot-blocked — trying Playwright", result.status_code)
        except Exception as exc:
            logger.debug("httpx failed (%s) — falling through to Playwright", exc)

    # Layer 2 — Playwright
    logger.debug("Layer 2 (Playwright): %s", url)
    result = await _fetch_playwright(url, timeout, paginate=paginate)
    if result.html and not _is_cloudflare_blocked(result) and not result.error:
        return result
    logger.debug("Playwright blocked or failed — trying anti-bot fallback")

    # Layer 3 — ScraperAPI, then Zyte
    if cfg.scraperapi_key:
        logger.debug("Layer 3 (ScraperAPI): %s", url)
        result = await _fetch_scraperapi(url, timeout)
        if result.html and not result.error:
            return result

    if cfg.zyte_api_key:
        logger.debug("Layer 3 (Zyte): %s", url)
        result = await _fetch_zyte(url, timeout)
        if result.html and not result.error:
            return result

    # Return last result even if it has an error — caller handles it
    return result


# ── URL utilities ─────────────────────────────────────────────────────────────

def extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Return normalised {text, href} links found in HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc
    seen: set[str] = set()
    links: list[dict[str, str]] = []

    for a in soup.find_all("a", href=True):
        href: str = a["href"].strip()
        # Skip fragments, javascript, mailto
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        abs_href = urljoin(base_url, href)
        # Strip fragment
        abs_href = re.sub(r"#.*$", "", abs_href)

        if abs_href in seen:
            continue
        seen.add(abs_href)

        text = a.get_text(strip=True)
        links.append({"text": text, "href": abs_href})

    return links


def same_domain(url: str, base_url: str) -> bool:
    return urlparse(url).netloc == urlparse(base_url).netloc
