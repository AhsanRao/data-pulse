"""Selector validation logic — checks extracted samples against user intent."""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


def apply_selector(html: str, selector: str) -> list[str]:
    """Apply a CSS selector and return text of matched elements.

    For anchor elements: emits "name: <heading>\nurl: <href>" so the formatter
    can parse them as separate fields rather than one blob of text.
    For all other elements: newline-separated text so block children are distinct.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(selector)
    results = []
    for el in elements:
        href = el.get("href") or el.get("data-href")
        if href:
            # Extract primary name from heading tags first, else first text line
            heading = el.find(["h1", "h2", "h3", "h4", "h5", "strong"])
            if heading:
                name = heading.get_text(strip=True)
            else:
                raw = el.get_text(separator="\n", strip=True)
                lines = [l for l in raw.splitlines() if l.strip()]
                name = lines[0] if lines else ""
            if name:
                results.append(f"name: {name}\nurl: {href}")
            elif href:
                results.append(f"url: {href}")
        else:
            text = el.get_text(separator="\n", strip=True)
            if text:
                results.append(text)
    return results


def sample_elements(elements: list[Any], k: int = 5) -> list[Any]:
    if len(elements) <= k:
        return elements
    return random.sample(elements, k)
