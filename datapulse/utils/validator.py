"""Selector validation logic — checks extracted samples against user intent."""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


def apply_selector(html: str, selector: str) -> list[str]:
    """Apply a CSS selector and return text of matched elements."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(selector)
    return [el.get_text(strip=True) for el in elements if el.get_text(strip=True)]


def sample_elements(elements: list[Any], k: int = 5) -> list[Any]:
    if len(elements) <= k:
        return elements
    return random.sample(elements, k)
