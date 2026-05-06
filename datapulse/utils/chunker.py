"""Semantic HTML block splitter.

Splits by block-level elements rather than character count, so each chunk
maps to a coherent structural unit of the page.
"""

from __future__ import annotations

BLOCK_TAGS = {
    "article", "section", "main", "div", "ul", "ol", "table",
    "tbody", "thead", "tr", "dl", "figure", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
}

MAX_CHUNK_CHARS = 6_000


def chunk_html(html: str) -> list[str]:
    """Split cleaned HTML into block-level chunks for LLM processing."""
    try:
        from bs4 import BeautifulSoup, Tag

        soup = BeautifulSoup(html, "lxml")
        body = soup.body or soup

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for child in body.children:
            if not isinstance(child, Tag):
                continue
            text = str(child)
            text_len = len(text)

            if current_len + text_len > MAX_CHUNK_CHARS and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0

            current.append(text)
            current_len += text_len

        if current:
            chunks.append("\n".join(current))

        return chunks or [html[:MAX_CHUNK_CHARS]]

    except Exception:
        # Character-based fallback
        return [html[i : i + MAX_CHUNK_CHARS] for i in range(0, len(html), MAX_CHUNK_CHARS)]
