"""HTML cleaning utilities — strips boilerplate, returns clean content."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Tags that are never part of page content
_STRIP_TAGS = [
    "script", "style", "noscript", "iframe", "svg",
    "head", "header", "nav", "footer", "aside",
    "link",  # CSS preload links injected into body by SPAs
]

# id/class substrings that identify boilerplate overlay containers in body
_STRIP_ID_PATTERNS = [
    "onetrust", "consent", "cookie-banner", "cookie-notice",
    "gdpr", "privacy-banner", "overlay-id",
]

# Attributes worth keeping for CSS selector discovery
_KEEP_ATTRS = {
    "class", "id", "href", "role", "type", "name",
    "aria-label",
}


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

    return _bs_extract(html)


def clean_html_keep_tags(html: str) -> str:
    """Return body-only HTML with structural tags preserved for selector discovery.

    Strips: <head>, <script>, <style>, <link>, <nav>, <header>, <footer>,
            <aside>, <svg>, cookie/consent SDK overlay divs.
    Keeps:  class, id, href, role, data-* — the attributes LLMs use for selectors.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        # Pass 1 — remove tags by tag name (all at once before iterating attrs)
        for tag in soup(_STRIP_TAGS):
            tag.decompose()

        # Pass 2 — remove cookie/consent overlay divs by id/class substring.
        # Collect targets FIRST, then decompose — avoids touching orphaned nodes
        # whose attrs are wiped by a parent decompose mid-iteration.
        to_remove = [
            tag for tag in soup.find_all(True)
            if _matches_strip_pattern(tag)
        ]
        for tag in to_remove:
            # Guard: a child may have been removed when its parent was decomposed
            if tag.parent is not None:
                tag.decompose()

        # Pass 3 — compact attributes (fresh scan after all decompositions)
        for tag in soup.find_all(True):
            try:
                keep = {
                    attr: val for attr, val in tag.attrs.items()
                    if attr in _KEEP_ATTRS or attr.startswith("data-")
                }
                tag.attrs = keep
            except Exception:
                pass  # tag in degenerate state after decompose — skip it

        return str(soup.body or soup)

    except Exception as exc:
        logger.debug("bs4 tag-clean failed: %s", exc)
        return html


def _matches_strip_pattern(tag) -> bool:
    try:
        tag_id = (tag.get("id") or "").lower()
        tag_cls = " ".join(tag.get("class") or []).lower()
        combined = tag_id + " " + tag_cls
        return any(p in combined for p in _STRIP_ID_PATTERNS)
    except Exception:
        return False


def _bs_extract(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(_STRIP_TAGS):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return html
