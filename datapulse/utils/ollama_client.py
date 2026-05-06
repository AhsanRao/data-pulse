"""Ollama client — synchronous wrapper around the Ollama HTTP API.

Used by both intent_parser (query → structured intent) and extractor
(selector discovery + validation + schema inference when no Anthropic key).

Ollama must be running: `brew services start ollama`
Default base URL: http://localhost:11434
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_OLLAMA_BASE = "http://localhost:11434"
_TIMEOUT = 120  # seconds — 1.5B model is fast but JSON mode can be slow


def is_available(base_url: str = _OLLAMA_BASE) -> bool:
    """Return True if Ollama server is reachable."""
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_models(base_url: str = _OLLAMA_BASE) -> list[str]:
    """Return names of locally available models."""
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def generate(
    prompt: str,
    model: str = "qwen2.5:1.5b",
    base_url: str = _OLLAMA_BASE,
    temperature: float = 0.0,
    log_path: Path | None = None,
) -> str:
    """Send a prompt to Ollama and return the full response text.

    Uses non-streaming generate endpoint with format="json" for reliable
    structured output.  Temperature 0 for deterministic JSON extraction.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
        "format": "json",
    }

    logger.debug("Ollama generate: model=%s prompt_len=%d", model, len(prompt))

    try:
        resp = httpx.post(
            f"{base_url}/api/generate",
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        if log_path:
            _write_log(log_path, model, prompt, text)
        return text
    except httpx.ConnectError:
        raise RuntimeError(
            "Ollama is not running. Start it with: brew services start ollama"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise RuntimeError(
                f"Model '{model}' not found. Pull it with: ollama pull {model}"
            )
        raise


def generate_text(
    prompt: str,
    model: str = "qwen2.5:1.5b",
    base_url: str = _OLLAMA_BASE,
    temperature: float = 0.1,
    log_path: Path | None = None,
) -> str:
    """Like generate() but without enforcing JSON format — for free-form responses."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        resp = httpx.post(f"{base_url}/api/generate", json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        text = resp.json().get("response", "")
        if log_path:
            _write_log(log_path, model, prompt, text)
        return text
    except httpx.ConnectError:
        raise RuntimeError("Ollama is not running. Start it with: brew services start ollama")


def parse_json_response(text: str) -> dict:
    """Parse JSON from an Ollama response, stripping markdown fences if present."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    # Ollama with format=json sometimes wraps in extra whitespace
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def _write_log(path: Path, model: str, prompt: str, response: str) -> None:
    try:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"\n--- {ts} model={model} ---\nPROMPT:\n{prompt}\nRESPONSE:\n{response}\n"
        with path.open("a") as f:
            f.write(entry)
    except Exception:
        pass
