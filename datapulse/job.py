"""Job dataclass and state management."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["pending", "running", "complete", "failed", "partial"]
IntentType = Literal["url_list", "page_content", "deep_content", "structured_data"]


@dataclass
class Intent:
    url: str
    intent_type: IntentType = "page_content"
    content_target: str = ""
    max_urls: int = 25
    depth: int = 1


@dataclass
class Job:
    query: str
    intent: Intent
    job_id: str = field(default_factory=lambda: f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    status: JobStatus = "pending"
    urls_pending: list[str] = field(default_factory=list)
    urls_processed: list[str] = field(default_factory=list)
    urls_failed: list[str] = field(default_factory=list)
    selector_cache: dict[str, str] = field(default_factory=dict)
    results: list[Any] = field(default_factory=list)
    output_format: str = "json"
    output_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── persistence ──────────────────────────────────────────────────────────

    @property
    def store_path(self) -> Path:
        from datapulse.config import cfg
        return cfg.jobs_dir / f"{self.job_id}.json"

    def save(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = asdict(self)
        data["intent"] = asdict(self.intent)
        self.store_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, job_id: str) -> "Job":
        from datapulse.config import cfg
        path = cfg.jobs_dir / f"{job_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No job found: {job_id}")
        data = json.loads(path.read_text())
        intent_data = data.pop("intent")
        intent = Intent(**intent_data)
        return cls(intent=intent, **data)

    @classmethod
    def list_all(cls) -> list[dict]:
        from datapulse.config import cfg
        store_dir = cfg.jobs_dir
        jobs = []
        for p in sorted(store_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(p.read_text())
                jobs.append({
                    "job_id": data["job_id"],
                    "status": data["status"],
                    "query": data["query"],
                    "created_at": data["created_at"],
                    "urls_processed": len(data.get("urls_processed", [])),
                    "urls_pending": len(data.get("urls_pending", [])),
                })
            except Exception:
                continue
        return jobs

    def mark_url_done(self, url: str, result: Any | None = None) -> None:
        if url in self.urls_pending:
            self.urls_pending.remove(url)
        if url not in self.urls_processed:
            self.urls_processed.append(url)
        if result is not None:
            self.results.append(result)
        self.save()

    def mark_url_failed(self, url: str) -> None:
        if url in self.urls_pending:
            self.urls_pending.remove(url)
        if url not in self.urls_failed:
            self.urls_failed.append(url)
        self.save()
