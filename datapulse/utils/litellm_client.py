"""LiteLLM proxy client — OpenAI-compatible HTTP wrapper.

Points at a self-hosted LiteLLM gateway (http://10.8.124.144:4000/).
Model: mueen-80b  (Qwen 80B served via LiteLLM).

Used by both intent_parser and extractor as the primary LLM backend.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds


def _get_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, model) from config/env."""
    from datapulse.config import cfg
    base_url = cfg.litellm_base_url or "http://10.8.124.144:4000"
    api_key = cfg.litellm_api_key or "my-litellm-key-2026"
    model = cfg.litellm_model or "mueen-80b"
    return base_url.rstrip("/"), api_key, model


def is_available() -> bool:
    """Return True if the LiteLLM proxy is reachable."""
    try:
        base_url, api_key, _ = _get_config()
        r = httpx.get(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        return r.status_code in (200, 401)  # 401 means server is up, auth issue only
    except Exception:
        return False


def call(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    log_path: Path | None = None,
) -> str:
    """Send a prompt to the LiteLLM proxy and return the response text.

    Uses the /v1/chat/completions endpoint (OpenAI-compatible).
    """
    base_url, api_key, default_model = _get_config()
    model = model or default_model

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    logger.debug("LiteLLM call: model=%s prompt_len=%d", model, len(prompt))

    try:
        resp = httpx.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text: str = resp.json()["choices"][0]["message"]["content"]
        if log_path:
            _write_log(log_path, model, prompt, text)
        return text
    except httpx.ConnectError:
        raise RuntimeError(
            f"LiteLLM proxy not reachable at {base_url}. "
            "Check that the server is running and the URL is correct."
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"LiteLLM request failed: {exc.response.status_code} {exc.response.text}"
        ) from exc


def parse_json_response(text: str) -> dict:
    """Parse JSON from an LLM response, stripping markdown fences if present."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    return json.loads(cleaned.strip())


def _write_log(path: Path, model: str, prompt: str, response: str) -> None:
    try:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"\n--- {ts} model={model} ---\nPROMPT:\n{prompt}\nRESPONSE:\n{response}\n"
        with path.open("a") as f:
            f.write(entry)
    except Exception:
        pass
