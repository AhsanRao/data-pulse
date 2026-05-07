"""OpenAI-compatible LLM client.

Works with any OpenAI-compatible provider:
  - Google Gemini  (LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai, LLM_CHAT_PATH=/chat/completions)
  - OpenAI         (LLM_BASE_URL=https://api.openai.com, LLM_MODEL=gpt-4o-mini)
  - Anthropic†     (LLM_BASE_URL=https://api.anthropic.com/v1, LLM_MODEL=claude-haiku-4-5-20251001)
  - LiteLLM proxy  (LLM_BASE_URL=http://your-proxy:4000)
  - Any compatible proxy or self-hosted model

Configure via secrets.env:
  LLM_BASE_URL=<endpoint>          # required
  LLM_API_KEY=<key>                # required
  LLM_MODEL=<model-name>           # required
  LLM_CHAT_PATH=<completions-path> # optional, default: /v1/chat/completions

† Anthropic's REST API is OpenAI-compatible at /v1/chat/completions.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds


def _get_config() -> tuple[str, str, str, str]:
    """Return (base_url, api_key, model, chat_path) from env via config.

    Priority: LLM_* env vars → LITELLM_* env vars (backward compat) → defaults.
    LLM_CHAT_PATH lets you override the completions endpoint path:
      - Standard / LiteLLM proxy: /v1/chat/completions  (default)
      - Google Gemini:            /chat/completions
    """
    from datapulse.config import cfg
    base_url = cfg.llm_base_url or ""
    api_key = cfg.llm_api_key or ""
    model = cfg.llm_model or ""
    chat_path = os.getenv("LLM_CHAT_PATH", "/v1/chat/completions")
    return base_url.rstrip("/"), api_key, model, chat_path


def is_available() -> bool:
    """Return True if the LLM provider is configured (base URL + API key set)."""
    try:
        base_url, api_key, _, _ = _get_config()
        return bool(base_url and api_key)
    except Exception:
        return False


def call(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    log_path: Path | None = None,
) -> str:
    """Send a prompt to the configured LLM and return the response text."""
    base_url, api_key, default_model, chat_path = _get_config()
    model = model or default_model

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    logger.debug("LLM call: model=%s prompt_len=%d", model, len(prompt))

    try:
        resp = httpx.post(
            f"{base_url}{chat_path}",
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
            f"LLM endpoint not reachable at {base_url}. "
            "Check that LLM_BASE_URL is correct in secrets.env."
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"LLM request failed: {exc.response.status_code} {exc.response.text}"
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
