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
from datapulse.utils import litellm_client
from datapulse.utils.ollama_client import generate as ollama_generate, parse_json_response, is_available as ollama_available

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

_INTENT_PROMPT = """\
You are a web scraping intent classifier. Parse the user's query and extract structured parameters.

Intent type rules:
- "url_list": user wants links, URLs, or a list of hrefs from a page
- "page_content": user wants the text/article content of one page
- "structured_data": user names specific fields to extract (price, title, rating, etc.)
- "deep_content": user wants to follow links and scrape each linked page

Output format rules:
- Set "output_format" only if the user explicitly mentions a format (json, csv, markdown, text).
- Set "output_file" if the user mentions saving to a file. Use the filename they gave, or auto-generate
  one like "<domain>.<ext>" (e.g. "cyberab_org.json") if they just say "in a json file".
- Both default to null if not mentioned.

Pagination rules:
- Set "paginate" to true if the user mentions paginating, clicking through pages, "all pages",
  "multiple pages", "next page", or a specific page count like "2 pages" / "first 3 pages".
- Set "max_pages" to the integer page count if the user specifies one (e.g. "2 pages" → 2), else null.

Query: "{query}"

Respond ONLY with this JSON (no markdown, no explanation):
{{
  "url": "<the URL from the query, or null if none found>",
  "intent_type": "<url_list | page_content | structured_data | deep_content>",
  "content_target": "<what the user wants to extract, empty string if not specified>",
  "max_urls": <integer, default 25>,
  "depth": <0 for single page, 1 for follow links once, 2 for deep_content>,
  "output_format": "<json | csv | md | text | null>",
  "output_file": "<filename or null>",
  "paginate": <true or false>,
  "max_pages": <integer or null>
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

    Tries LiteLLM proxy first, then Ollama, then falls back to heuristic parsing.
    """
    if litellm_client.is_available():
        try:
            return _parse_with_litellm(query)
        except Exception as exc:
            logger.warning("LiteLLM intent parsing failed (%s) — trying Ollama", exc)

    if ollama_available():
        try:
            return _parse_with_ollama(query)
        except Exception as exc:
            logger.warning("Ollama intent parsing failed (%s) — using heuristic fallback", exc)
    else:
        logger.warning("No LLM available — using heuristic intent parser")

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


# ── LiteLLM parsing ───────────────────────────────────────────────────────────

def _parse_with_litellm(query: str) -> Intent:
    log_path = cfg.log_dir / "llm_calls.log"
    prompt = _INTENT_PROMPT.format(query=query)
    raw = litellm_client.call(prompt, max_tokens=256, temperature=0.0, log_path=log_path)
    data = litellm_client.parse_json_response(raw)

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
        output_format=_validated_format(data.get("output_format")),
        output_file=data.get("output_file") or None,
        paginate=bool(data.get("paginate", False)),
        max_pages=int(data["max_pages"]) if data.get("max_pages") else None,
    )


# ── Ollama parsing ────────────────────────────────────────────────────────────

def _parse_with_ollama(query: str) -> Intent:
    model = cfg.llm.get("local_model", "qwen2.5:1.5b")
    log_path = cfg.log_dir / "llm_calls.log"

    prompt = _INTENT_PROMPT.format(query=query)
    raw = ollama_generate(prompt, model=model, log_path=log_path)
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
        output_format=_validated_format(data.get("output_format")),
        output_file=data.get("output_file") or None,
        paginate=bool(data.get("paginate", False)),
        max_pages=int(data["max_pages"]) if data.get("max_pages") else None,
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

    content_target = _extract_target_from_query(query, url)
    out_fmt, out_file = _heuristic_output(query, url)
    pag, max_pg = _heuristic_paginate(query)

    return Intent(
        url=_normalise_url(url),
        intent_type=intent_type,
        content_target=content_target,
        max_urls=cfg.scraping.get("max_urls", 25),
        depth=depth,
        output_format=out_fmt,
        output_file=out_file,
        paginate=pag,
        max_pages=max_pg,
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


def _validated_format(value: str | None) -> str | None:
    valid = {"json", "csv", "md", "text"}
    if not value:
        return None
    v = str(value).lower().strip()
    return v if v in valid else None


def _heuristic_paginate(query: str) -> tuple[bool, int | None]:
    """Detect paginate=True and optional max_pages from query text."""
    q = query.lower()
    paginate_hints = [
        "paginate", "paginated", "all pages", "multiple pages",
        "next page", "click through", "page through",
    ]
    wants_paginate = any(h in q for h in paginate_hints)

    # Detect "N pages" / "first N pages" / "paginate N pages"
    m = re.search(r'\b(\d+)\s+pages?\b', q)
    if m:
        return True, int(m.group(1))

    return wants_paginate, None


def _heuristic_output(query: str, url: str) -> tuple[str | None, str | None]:
    """Detect output format and file from query text when no LLM is available."""
    q = query.lower()

    if "csv" in q:
        fmt, ext = "csv", ".csv"
    elif "markdown" in q or " md " in q:
        fmt, ext = "md", ".md"
    elif " text" in q or " txt" in q:
        fmt, ext = "text", ".txt"
    elif "json" in q:
        fmt, ext = "json", ".json"
    else:
        fmt, ext = None, ".json"

    # Explicit filename in query
    m = re.search(r'\b([\w.-]+\.(json|csv|txt|md))\b', query, re.IGNORECASE)
    if m:
        return fmt, m.group(1)

    file_phrases = [
        "in json file", "in csv file", "in a file", "to a file", "to file",
        "save to", "save as", "output to", "write to", "json file", "csv file",
    ]
    if any(p in q for p in file_phrases):
        domain = urlparse(url).netloc.replace(".", "_") if url else "output"
        return fmt, f"{domain}{ext}"

    return fmt, None
