"""Config loader — reads datapulse.config.yaml and .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Load API keys — tries multiple locations in priority order.
# Note: if .env is a directory (e.g. the venv), it is skipped automatically.
def _load_env_files() -> None:
    candidates = [
        Path.cwd() / "secrets.env",          # preferred when .env is the venv dir
        Path.cwd() / ".env",                  # standard location (if it's a file)
        Path.home() / ".datapulse" / ".env",  # user-global fallback
    ]
    for p in candidates:
        if p.is_file():
            load_dotenv(p, override=False)

_load_env_files()

_DEFAULTS: dict[str, Any] = {
    "scraping": {
        "max_urls": 25,
        "depth": 1,
        "concurrency": 4,
        "timeout_seconds": 30,
        "same_domain_only": True,
    },
    "playwright": {
        "scroll_increment_px": 500,
        "scroll_pause_ms": 800,
        "headless": True,
        "max_scroll_attempts": 30,
    },
    "llm": {
        "local_model": "qwen2.5:1.5b",
        "selector_model": "claude-haiku-4-5-20251001",
        "output_model": "claude-haiku-4-5-20251001",
        "max_retries": 3,
    },
    "output": {
        "default_format": "json",
        "include_metadata": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with path.open() as f:
            return yaml.safe_load(f) or {}
    return {}


class Config:
    def __init__(self, config_path: Path | None = None):
        path = config_path or Path.cwd() / "datapulse.config.yaml"
        self._project_root = path.parent
        file_cfg = _load_yaml(path)
        self._cfg = _deep_merge(_DEFAULTS, file_cfg)

    # ── accessors ─────────────────────────────────────────────────────────────

    @property
    def scraping(self) -> dict:
        return self._cfg["scraping"]

    @property
    def playwright(self) -> dict:
        return self._cfg["playwright"]

    @property
    def llm(self) -> dict:
        return self._cfg["llm"]

    @property
    def output(self) -> dict:
        return self._cfg["output"]

    def get(self, *keys: str, default: Any = None) -> Any:
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
        return node

    # ── API keys ──────────────────────────────────────────────────────────────

    @property
    def anthropic_api_key(self) -> str | None:
        return os.getenv("ANTHROPIC_API_KEY")

    @property
    def llm_base_url(self) -> str | None:
        return os.getenv("LLM_BASE_URL")

    @property
    def llm_api_key(self) -> str | None:
        return os.getenv("LLM_API_KEY")

    @property
    def llm_model(self) -> str | None:
        return os.getenv("LLM_MODEL")

    # ── Model selection (env overrides config.yaml defaults) ──────────────────

    @property
    def anthropic_model(self) -> str:
        return os.getenv("ANTHROPIC_MODEL") or self.llm.get("selector_model", "claude-haiku-4-5-20251001")

    @property
    def ollama_model(self) -> str:
        return os.getenv("OLLAMA_MODEL") or self.llm.get("local_model", "qwen2.5:1.5b")

    @property
    def scraperapi_key(self) -> str | None:
        return os.getenv("SCRAPERAPI_KEY")

    @property
    def zyte_api_key(self) -> str | None:
        return os.getenv("ZYTE_API_KEY")

    # ── paths ─────────────────────────────────────────────────────────────────

    @property
    def log_dir(self) -> Path:
        d = self._project_root / ".datapulse" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def jobs_dir(self) -> Path:
        d = self._project_root / ".datapulse" / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d


# Singleton used across the app
cfg = Config()
