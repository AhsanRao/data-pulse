"""Trafilatura wrapper — strips boilerplate, returns clean body text."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def clean_html(html: str, url: str = "") -> str:
    """Return the main text content extracted from raw HTML.

    Falls back to BeautifulSoup text stripping if Trafilatura finds nothing.
    """
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            url=url or None,
            include_links=False,
            include_images=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,
        )
        if result:
            return result
    except Exception as exc:
        logger.debug("trafilatura failed: %s", exc)

    # BeautifulSoup fallback
    return _bs_extract(html)


def clean_html_keep_tags(html: str) -> str:
    """Return cleaned HTML with structural tags preserved (for selector discovery)."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "head"]):
            tag.decompose()
        return str(soup.body or soup)
    except Exception as exc:
        logger.debug("bs4 tag-clean failed: %s", exc)
        return html


def _bs_extract(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return html
