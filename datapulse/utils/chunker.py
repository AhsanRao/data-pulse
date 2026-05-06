"""Semantic HTML block splitter.

Splits by block-level elements rather than character count, so each chunk
maps to a coherent structural unit of the page.

Recurses into elements that are larger than MAX_CHUNK_CHARS. No fixed depth
limit — HTML trees are finite, so recursion naturally terminates. SPAs like
MWC (depth ~12) and Angular catalog pages (depth ~22) both work correctly.
"""

from __future__ import annotations

MAX_CHUNK_CHARS = 6_000


def chunk_html(html: str) -> list[str]:
    """Split cleaned HTML into block-level chunks for LLM processing."""
    try:
        from bs4 import BeautifulSoup, Tag

        soup = BeautifulSoup(html, "lxml")
        body = soup.body or soup

        chunks: list[str] = []
        _collect_chunks(body, chunks)
        return chunks or [html[:MAX_CHUNK_CHARS]]

    except Exception:
        return [html[i : i + MAX_CHUNK_CHARS] for i in range(0, len(html), MAX_CHUNK_CHARS)]


def _collect_chunks(element, chunks: list[str]) -> None:
    """Recursively collect chunks from an element's children.

    Drills into any child that is too large to fit in MAX_CHUNK_CHARS rather
    than emitting it as one opaque blob. Stops when children fit.
    """
    from bs4 import Tag

    current: list[str] = []
    current_len = 0

    for child in element.children:
        if not isinstance(child, Tag):
            continue

        child_text = str(child)
        child_len = len(child_text)

        # Oversized child — drill deeper
        if child_len > MAX_CHUNK_CHARS:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            _collect_chunks(child, chunks)
            continue

        # Normal child — accumulate until chunk is full
        if current_len + child_len > MAX_CHUNK_CHARS and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

        current.append(child_text)
        current_len += child_len

    if current:
        chunks.append("\n".join(current))
