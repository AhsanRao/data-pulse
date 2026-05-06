"""Module 2 — Scope Guard.

Enforces crawl limits before any network call:
  - max_urls cap
  - depth limit (0 = seed only, 1 = follow links once, 2 = two hops)
  - same-domain filter
  - URL deduplication

Also provides the depth-aware URL queue used by the deep_content pipeline.
"""

from __future__ import annotations

import logging

from datapulse.job import Job

logger = logging.getLogger(__name__)


def init_job_scope(job: Job) -> None:
    """Populate urls_pending from the intent seed URL."""
    seed = job.intent.url
    if seed not in job.urls_pending and seed not in job.urls_processed:
        job.urls_pending = [seed]
    job.save()


def filter_urls(urls: list[str], job: Job) -> list[str]:
    """Return only URLs that pass all scope rules.

    Rules (in order):
      1. Skip already-processed or already-pending URLs (dedup)
      2. Same-domain filter (configurable)
      3. max_urls cap (counts pending + allowed so far)
    """
    from datapulse.config import cfg

    processed_set = set(job.urls_processed)
    pending_set = set(job.urls_pending)
    allowed: list[str] = []

    for url in urls:
        if url in processed_set or url in pending_set or url in allowed:
            continue

        total_queued = len(job.urls_pending) + len(allowed)
        if job.intent.max_urls and total_queued >= job.intent.max_urls:
            logger.debug("Scope guard: max_urls=%d reached", job.intent.max_urls)
            break

        if cfg.scraping.get("same_domain_only", True):
            from datapulse.modules.scraper import same_domain
            if not same_domain(url, job.intent.url):
                logger.debug("Scope guard: off-domain skipped: %s", url)
                continue

        allowed.append(url)

    return allowed


def enqueue_discovered_links(links: list[dict], job: Job) -> int:
    """Add new URLs from link discovery into job.urls_pending.

    Returns how many URLs were added.
    """
    hrefs = [lnk["href"] for lnk in links if lnk.get("href")]
    filtered = filter_urls(hrefs, job)
    job.urls_pending.extend(filtered)
    job.save()
    return len(filtered)


def should_follow_links(job: Job) -> bool:
    """True if the intent depth calls for following links beyond the seed."""
    return job.intent.depth > 0 and job.intent.intent_type in ("deep_content", "url_list")
