"""Module 4 — Extraction Engine.

Pipeline:
  raw HTML
    → Trafilatura clean (strip nav/ads/scripts)
    → Semantic chunking (block-level HTML elements)
    → LLM selector discovery  (Anthropic Haiku if key available, else Ollama)
    → Validation gate          (selector applied to 5 random samples, LLM verifies)
    → Programmatic extraction  (BeautifulSoup, no further LLM calls)
    → Schema inference         (LLM infers field names)

On selector failure: retry with next chunk, up to max_retries.
If neither Anthropic nor Ollama is available: return cleaned text.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datapulse.config import cfg
from datapulse.utils.html_cleaner import clean_html, clean_html_keep_tags
from datapulse.utils.chunker import chunk_html
from datapulse.utils.validator import apply_selector, sample_elements

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_SELECTOR_PROMPT = """\
You are a CSS selector expert. Given the HTML snippet below, identify the CSS selector \
that best targets the following data: {content_target}

Rules:
- Prefer CSS selectors over XPath.
- Target the CONTAINER element that holds each individual item (not the whole list).
- Avoid brittle class names that look auto-generated (e.g. x7f2_item). \
  Prefer semantic tags, data- attributes, or structural selectors.
- If the page has no matching elements, return null for selector.

Respond ONLY with valid JSON in this exact schema (no markdown, no extra text):
{{
  "selector": "<css selector or null>",
  "xpath": "<xpath as fallback, or null>",
  "reasoning": "<one sentence>"
}}

HTML:
{html_chunk}"""

_VALIDATION_PROMPT = """\
You extracted these {count} text samples using the CSS selector `{selector}`.
The user wants: "{content_target}"

Samples:
{samples}

Do these samples match what the user wants? Answer with valid JSON only:
{{
  "valid": true or false,
  "reason": "<one sentence>"
}}"""

_SCHEMA_PROMPT = """\
The user wants to extract: "{content_target}"
Here are up to 10 sample extracted texts:
{samples}

Infer a flat JSON schema for each item. Respond ONLY with valid JSON like:
{{
  "fields": ["field1", "field2"],
  "reasoning": "<one sentence>"
}}"""


# ── LLM backend selection ─────────────────────────────────────────────────────

def _llm_call(prompt: str, model: str | None = None, max_tokens: int = 512) -> str:
    """Route LLM calls: Anthropic if key available, else Ollama, else error."""
    log_path = cfg.log_dir / "llm_calls.log"

    # Prefer Anthropic when key is set
    if cfg.anthropic_api_key:
        return _call_anthropic(prompt, model, max_tokens, log_path)

    # Fall back to Ollama
    from datapulse.utils.ollama_client import is_available, generate, parse_json_response as _
    if is_available():
        ollama_model = cfg.llm.get("local_model", "qwen2.5:1.5b")
        return _call_ollama(prompt, ollama_model, log_path)

    raise RuntimeError(
        "No LLM available. Either:\n"
        "  1. Add ANTHROPIC_API_KEY to secrets.env\n"
        "  2. Start Ollama: brew services start ollama && ollama pull qwen2.5:1.5b"
    )


def _call_anthropic(prompt: str, model: str | None, max_tokens: int, log_path: Path) -> str:
    import anthropic

    model = model or cfg.llm.get("selector_model", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    logger.debug("Anthropic call: model=%s", model)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    response = msg.content[0].text
    _write_llm_log(log_path, model, prompt, response)
    return response


def _call_ollama(prompt: str, model: str, log_path: Path) -> str:
    from datapulse.utils.ollama_client import generate

    logger.debug("Ollama call: model=%s", model)
    return generate(prompt, model=model, log_path=log_path)


def _write_llm_log(path: Path, model: str, prompt: str, response: str) -> None:
    try:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"\n--- {ts} model={model} ---\nPROMPT:\n{prompt}\nRESPONSE:\n{response}\n"
        with path.open("a") as f:
            f.write(entry)
    except Exception:
        pass


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response, stripping any markdown fences."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    return json.loads(cleaned.strip())


# ── Selector discovery ────────────────────────────────────────────────────────

@dataclass
class SelectorResult:
    selector: str | None
    xpath: str | None
    reasoning: str
    validated: bool = False
    item_count: int = 0


def _discover_selector(html_chunk: str, content_target: str) -> SelectorResult:
    prompt = _SELECTOR_PROMPT.format(
        content_target=content_target,
        html_chunk=html_chunk[:4000],
    )
    raw = _llm_call(prompt)
    data = _parse_json_response(raw)
    return SelectorResult(
        selector=data.get("selector"),
        xpath=data.get("xpath"),
        reasoning=data.get("reasoning", ""),
    )


# ── Validation gate ───────────────────────────────────────────────────────────

def _validate_selector(
    html: str,
    selector: str,
    content_target: str,
    k: int = 5,
) -> tuple[bool, str]:
    """Apply selector, pull k samples, ask LLM if they match the target."""
    elements = apply_selector(html, selector)
    if not elements:
        return False, "Selector returned 0 elements."

    samples = sample_elements(elements, k=k)
    formatted = "\n".join(f"  {i+1}. {s[:200]}" for i, s in enumerate(samples))

    prompt = _VALIDATION_PROMPT.format(
        count=len(samples),
        selector=selector,
        content_target=content_target,
        samples=formatted,
    )
    raw = _llm_call(prompt)
    data = _parse_json_response(raw)
    return bool(data.get("valid", False)), data.get("reason", "")


# ── Schema inference ──────────────────────────────────────────────────────────

def _infer_schema(elements: list[str], content_target: str) -> list[str]:
    sample = sample_elements(elements, k=10)
    formatted = "\n".join(f"  - {s[:300]}" for s in sample)
    prompt = _SCHEMA_PROMPT.format(content_target=content_target, samples=formatted)
    try:
        raw = _llm_call(prompt)
        data = _parse_json_response(raw)
        fields = data.get("fields", [])
        if fields:
            return [str(f) for f in fields]
    except Exception as exc:
        logger.debug("Schema inference failed: %s", exc)
    return ["value"]


# ── Full extraction pipeline ──────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    items: list[Any]
    selector_used: str | None
    schema_fields: list[str]
    cleaned_text: str
    method: str  # "llm_selector" | "text_only"


def extract(
    html: str,
    url: str = "",
    content_target: str = "",
    selector_hint: str | None = None,
) -> ExtractionResult:
    """Run the full extraction pipeline for a single page."""
    cleaned_text = clean_html(html, url=url)

    if not content_target:
        return ExtractionResult(
            items=[cleaned_text],
            selector_used=None,
            schema_fields=["content"],
            cleaned_text=cleaned_text,
            method="text_only",
        )

    # Check at least one LLM backend is reachable
    has_anthropic = bool(cfg.anthropic_api_key)
    try:
        from datapulse.utils.ollama_client import is_available
        has_ollama = is_available()
    except Exception:
        has_ollama = False

    if not has_anthropic and not has_ollama:
        logger.warning("No LLM available — returning cleaned text.")
        return ExtractionResult(
            items=[cleaned_text],
            selector_used=None,
            schema_fields=["content"],
            cleaned_text=cleaned_text,
            method="text_only",
        )

    structural_html = clean_html_keep_tags(html)
    chunks = chunk_html(structural_html)
    max_retries: int = cfg.llm.get("max_retries", 3)

    # Try cached selector first
    if selector_hint:
        elements = apply_selector(structural_html, selector_hint)
        if elements:
            logger.debug("Cached selector '%s' → %d elements", selector_hint, len(elements))
            return _build_result(elements, selector_hint, content_target, cleaned_text)

    for attempt, chunk in enumerate(chunks[:max_retries], start=1):
        logger.debug("Selector discovery attempt %d/%d", attempt, max_retries)
        try:
            sel_result = _discover_selector(chunk, content_target)
        except Exception as exc:
            logger.debug("Selector discovery failed: %s", exc)
            continue

        if not sel_result.selector:
            logger.debug("LLM returned null selector — trying next chunk")
            continue

        try:
            valid, reason = _validate_selector(structural_html, sel_result.selector, content_target)
        except Exception as exc:
            valid, reason = False, str(exc)

        logger.debug("Validation: valid=%s reason=%s", valid, reason)

        if valid:
            elements = apply_selector(structural_html, sel_result.selector)
            return _build_result(elements, sel_result.selector, content_target, cleaned_text)

        logger.debug("Selector '%s' failed validation — retrying", sel_result.selector)

    logger.warning("All %d selector attempts failed — returning cleaned text.", max_retries)
    return ExtractionResult(
        items=[cleaned_text],
        selector_used=None,
        schema_fields=["content"],
        cleaned_text=cleaned_text,
        method="text_only",
    )


def _build_result(
    elements: list[str],
    selector: str,
    content_target: str,
    cleaned_text: str,
) -> ExtractionResult:
    schema_fields = _infer_schema(elements, content_target)
    return ExtractionResult(
        items=elements,
        selector_used=selector,
        schema_fields=schema_fields,
        cleaned_text=cleaned_text,
        method="llm_selector",
    )
