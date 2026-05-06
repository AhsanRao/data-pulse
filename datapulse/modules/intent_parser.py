"""Module 1 — Intent Parser.

Converts a raw natural language query into a structured Intent object using
Ollama + qwen2.5:1.5b running locally.

Intent types:
  url_list        — user wants links/URLs from a page
  page_content    — user wants text content from one page
  structured_data — user names specific fields (name, price, etc.)
  deep_content    — user wants content from pages linked within a page
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from datapulse.config import cfg
from datapulse.job import Intent, IntentType
from datapulse.utils.ollama_client import generate, parse_json_response, is_available

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

_INTENT_PROMPT = """\
You are a web scraping intent classifier. Parse the user's query and extract structured parameters.

Intent type rules:
- "url_list": user wants links, URLs, or a list of hrefs from a page
- "page_content": user wants the text/article content of one page
- "structured_data": user names specific fields to extract (price, title, rating, etc.)
- "deep_content": user wants to follow links and scrape each linked page

Query: "{query}"

Respond ONLY with this JSON (no markdown, no explanation):
{{
  "url": "<the URL from the query, or null if none found>",
  "intent_type": "<url_list | page_content | structured_data | deep_content>",
  "content_target": "<what the user wants to extract, empty string if not specified>",
  "max_urls": <integer, default 25>,
  "depth": <0 for single page, 1 for follow links once, 2 for deep_content>
}}"""

# Keywords that hint at each intent type
_URL_LIST_HINTS = ["links", "urls", "hrefs", "all links", "list of links", "anchor"]
_DEEP_HINTS = ["each", "every", "all articles", "all posts", "follow", "linked pages",
               "each page", "all pages", "every article"]
_STRUCTURED_HINTS = ["price", "rating", "title", "name", "salary", "company",
                     "product", "headline", "score", "date", "author", "summary",
                     "description", "location", "review"]


# ── Public interface ──────────────────────────────────────────────────────────

def parse_query(query: str) -> Intent:
    """Parse a natural language query into a structured Intent.

    Tries Ollama first. Falls back to heuristic parsing if Ollama is unavailable
    or returns invalid JSON.
    """
    if is_available():
        try:
            return _parse_with_ollama(query)
        except Exception as exc:
            logger.warning("Ollama intent parsing failed (%s) — using heuristic fallback", exc)
    else:
        logger.warning("Ollama not available — using heuristic intent parser")

    return _parse_heuristic(query)


def parse_url_intent(
    url: str,
    intent_type: IntentType = "page_content",
    content_target: str = "",
    max_urls: int = 25,
    depth: int = 1,
) -> Intent:
    """Build an Intent directly from explicit parameters (used by --url CLI path)."""
    return Intent(
        url=url,
        intent_type=intent_type,
        content_target=content_target,
        max_urls=max_urls,
        depth=depth,
    )


# ── Ollama parsing ────────────────────────────────────────────────────────────

def _parse_with_ollama(query: str) -> Intent:
    model = cfg.llm.get("local_model", "qwen2.5:1.5b")
    log_path = cfg.log_dir / "llm_calls.log"

    prompt = _INTENT_PROMPT.format(query=query)
    raw = generate(prompt, model=model, log_path=log_path)
    data = parse_json_response(raw)

    url = data.get("url") or _extract_url(query)
    if not url:
        raise ValueError("No URL found in query")

    intent_type = _validated_intent_type(data.get("intent_type", "page_content"))
    depth = _depth_for_intent(intent_type, data.get("depth"))

    content_target = str(data.get("content_target", "")).strip()
    if not content_target and intent_type in ("structured_data", "deep_content"):
        content_target = _extract_target_from_query(query, url)

    return Intent(
        url=_normalise_url(url),
        intent_type=intent_type,
        content_target=content_target,
        max_urls=int(data.get("max_urls") or cfg.scraping.get("max_urls", 25)),
        depth=depth,
    )


# ── Heuristic fallback ────────────────────────────────────────────────────────

def _parse_heuristic(query: str) -> Intent:
    """Simple rule-based parser used when Ollama is unavailable."""
    url = _extract_url(query)
    if not url:
        raise ValueError(
            "Could not find a URL in your query and Ollama is not available.\n"
            "Either include a URL in your query or start Ollama: brew services start ollama"
        )

    q_lower = query.lower()

    # Detect intent type
    if any(h in q_lower for h in _URL_LIST_HINTS):
        intent_type: IntentType = "url_list"
        depth = 0
    elif any(h in q_lower for h in _DEEP_HINTS):
        intent_type = "deep_content"
        depth = 2
    elif any(h in q_lower for h in _STRUCTURED_HINTS):
        intent_type = "structured_data"
        depth = 0
    else:
        intent_type = "page_content"
        depth = 0

    # Extract content target: words between "get/extract/find" and the URL
    content_target = _extract_target_from_query(query, url)

    return Intent(
        url=_normalise_url(url),
        intent_type=intent_type,
        content_target=content_target,
        max_urls=cfg.scraping.get("max_urls", 25),
        depth=depth,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_url(text: str) -> str | None:
    """Pull the first http(s) URL out of a string."""
    match = re.search(r"https?://[^\s\"']+", text)
    return match.group(0).rstrip(".,)") if match else None


def _normalise_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url.rstrip("/")


def _validated_intent_type(value: str) -> IntentType:
    valid: set[IntentType] = {"url_list", "page_content", "structured_data", "deep_content"}
    return value if value in valid else "page_content"  # type: ignore[return-value]


def _depth_for_intent(intent_type: IntentType, suggested: int | None) -> int:
    """Return sensible depth, overriding LLM suggestion for deep_content."""
    if intent_type == "deep_content":
        return 2
    if intent_type == "url_list":
        return 0
    if suggested is not None:
        return max(0, int(suggested))
    return 0


def _extract_target_from_query(query: str, url: str) -> str:
    """Best-effort: remove the URL and common filler words, return the target phrase."""
    text = query.replace(url, "").strip()
    filler = r"\b(get|extract|find|give me|fetch|scrape|show me|list|all|the|from|on|at|of|a|an)\b"
    text = re.sub(filler, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.?!")
    return text
