"""Semantic HTML block splitter.

Splits by block-level elements rather than character count, so each chunk
maps to a coherent structural unit of the page.

Recurses into elements that are larger than MAX_CHUNK_CHARS instead of
treating them as one opaque block — this handles SPAs where the entire page
is wrapped in a single top-level div (e.g. rf-org-footer-container on RSAC).
"""

from __future__ import annotations

MAX_CHUNK_CHARS = 6_000
_MAX_RECURSE_DEPTH = 15  # how deep to drill — SPAs can nest 10-12 levels deep


def chunk_html(html: str) -> list[str]:
    """Split cleaned HTML into block-level chunks for LLM processing."""
    try:
        from bs4 import BeautifulSoup, Tag

        soup = BeautifulSoup(html, "lxml")
        body = soup.body or soup

        chunks: list[str] = []
        _collect_chunks(body, chunks, depth=0)
        return chunks or [html[:MAX_CHUNK_CHARS]]

    except Exception:
        return [html[i : i + MAX_CHUNK_CHARS] for i in range(0, len(html), MAX_CHUNK_CHARS)]


def _collect_chunks(element, chunks: list[str], depth: int) -> None:
    """Recursively collect chunks from an element's children.

    If a child element is too large to fit in a chunk and we haven't hit the
    recursion limit, we drill into its children instead of emitting it whole.
    """
    from bs4 import Tag

    current: list[str] = []
    current_len = 0

    for child in element.children:
        if not isinstance(child, Tag):
            continue

        child_text = str(child)
        child_len = len(child_text)

        # Oversized child — recurse into it if depth allows
        if child_len > MAX_CHUNK_CHARS and depth < _MAX_RECURSE_DEPTH:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            _collect_chunks(child, chunks, depth + 1)
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
