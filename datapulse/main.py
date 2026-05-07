"""CLI entry point for DataPulse."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TaskProgressColumn, TextColumn, TimeElapsedColumn,
)
from rich.table import Table
from rich import box

app = typer.Typer(
    name="datapulse",
    help="[bold cyan]DataPulse[/] — AI-Driven Intent-Based Web Extraction",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging(debug: bool = False) -> None:
    from datapulse.config import cfg

    level = logging.DEBUG if debug else logging.INFO
    log_file = cfg.log_dir / "datapulse.log"

    fmt = "%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s"
    datefmt = "%H:%M:%S"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            RichHandler(
                console=err_console,
                show_path=False,
                markup=True,
                log_time_format="%H:%M:%S",
                rich_tracebacks=False,
                # In non-debug mode only show WARNING+ on console
                level=logging.DEBUG if debug else logging.WARNING,
            ),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )

    # Suppress noisy third-party loggers — always, even in debug mode
    _QUIET = [
        "trafilatura", "trafilatura.core", "trafilatura.htmlprocessing",
        "trafilatura.utils", "trafilatura.filters",
        "httpx", "httpcore", "urllib3",
        "playwright", "asyncio",
        "charset_normalizer",
    ]
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.ERROR)


# ── Output path helper ────────────────────────────────────────────────────────

def _resolve_output(output: Path | None) -> Path | None:
    """If output is a bare filename, route it into the output/ folder."""
    if output is None:
        return None
    if output.parent == Path("."):
        out_dir = Path("output")
        out_dir.mkdir(exist_ok=True)
        return out_dir / output
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _save_debug_html(job_id: str, url: str, raw_html: str, clean_html: str) -> None:
    """Save raw and cleaned HTML snapshots to output/debug/ for inspection."""
    slug = re.sub(r"[^\w]", "_", url)[:50].strip("_")
    debug_dir = Path("output") / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{job_id}_{slug}"
    (debug_dir / f"{prefix}_raw.html").write_text(raw_html, encoding="utf-8")
    (debug_dir / f"{prefix}_clean.html").write_text(clean_html, encoding="utf-8")
    logging.getLogger(__name__).debug(
        "Debug HTML saved → output/debug/%s_{raw,clean}.html", prefix
    )


# ── Banner ────────────────────────────────────────────────────────────────────

def _print_banner() -> None:
    from datapulse import __version__
    from rich.text import Text
    from rich.align import Align

    logo_lines = [
        r"  ____        _        ____        _          ",
        r" |  _ \  __ _| |_ __ _|  _ \ _   _| |___  ___ ",
        r" | | | |/ _` | __/ _` | |_) | | | | / __|/ _ \\",
        r" | |_| | (_| | || (_| |  __/| |_| | \__ \  __/",
        r" |____/ \__,_|\__\__,_|_|    \__,_|_|___/\___|",
    ]

    console.print()
    for line in logo_lines:
        console.print(Align.center(f"[bold cyan]{line}[/]"))

    tagline = Text.assemble(
        ("  AI-Driven Web Extraction  ", "bold white on #1a1a2e"),
        ("  v" + __version__ + "  ", "dim on #1a1a2e"),
    )
    console.print(Align.center(tagline))
    console.print()


# ── run ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    query: Optional[str] = typer.Argument(None,
        help='Natural language query, e.g. "get book prices from https://books.toscrape.com"'),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Target URL"),
    target: Optional[str] = typer.Option(None, "--target", "-t",
        help='What to extract, e.g. "product name and price"'),
    output: Optional[Path] = typer.Option(None, "--output", "-o",
        help="Output file (auto-placed in output/ if no directory given)"),
    fmt: str = typer.Option("json", "--format", "-f", help="json | csv | md | text"),
    max_urls: Optional[int] = typer.Option(None, "--max-urls", help="Max URLs to crawl"),
    depth: Optional[int] = typer.Option(None, "--depth", help="Crawl depth (0=seed only)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse intent only, skip fetch"),
    force_playwright: bool = typer.Option(False, "--playwright", help="Force Playwright layer"),
    paginate: bool = typer.Option(False, "--paginate",
        help="Click through Next/pagination buttons (Playwright only, for client-side paginated sites)"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages",
        help="Max pages to click through with --paginate (overrides playwright.max_pages in config)"),
    follow: bool = typer.Option(False, "--follow",
        help="After listing crawl, visit each extracted URL and scrape detail pages"),
    detail_target: Optional[str] = typer.Option(None, "--detail-target",
        help='What to extract from each detail page, e.g. "email and social media links"'),
    max_follow: Optional[int] = typer.Option(None, "--max-follow",
        help="Max detail URLs to follow (default: 50)"),
    debug: bool = typer.Option(False, "--debug",
        help="Verbose logging + save raw/clean HTML snapshots to output/debug/"),
):
    """Run a full extraction from a natural language query or --url flag."""
    _setup_logging(debug)
    _print_banner()

    if not query and not url:
        console.print("[red]Error:[/] Provide a query or --url <URL>.\n")
        console.print("  datapulse run [cyan]\"get book titles from https://books.toscrape.com\"[/]")
        console.print("  datapulse run [cyan]--url https://books.toscrape.com --target 'title and price'[/]")
        raise typer.Exit(1)

    resolved_output = _resolve_output(output)

    asyncio.run(_run_pipeline(
        query=query,
        url_override=url,
        target_override=target,
        fmt=fmt,
        output=resolved_output,
        max_urls_override=max_urls,
        depth_override=depth,
        dry_run=dry_run,
        force_playwright=force_playwright,
        paginate=paginate,
        max_pages_override=max_pages,
        follow=follow,
        detail_target=detail_target,
        max_follow=max_follow,
        debug=debug,
    ))


async def _run_pipeline(
    query: Optional[str],
    url_override: Optional[str],
    target_override: Optional[str],
    fmt: str,
    output: Optional[Path],
    max_urls_override: Optional[int],
    depth_override: Optional[int],
    dry_run: bool,
    force_playwright: bool,
    paginate: bool = False,
    max_pages_override: Optional[int] = None,
    follow: bool = False,
    detail_target: Optional[str] = None,
    max_follow: Optional[int] = None,
    debug: bool = False,
) -> None:
    from datapulse.config import cfg
    from datapulse.job import Job
    from datapulse.modules.intent_parser import parse_query, parse_url_intent
    from datapulse.modules.scope_guard import init_job_scope, enqueue_discovered_links, should_follow_links
    from datapulse.modules.scraper import scrape, extract_links
    from datapulse.modules.extractor import extract
    from datapulse.modules.formatter import format_result, deduplicate_items
    from datapulse.utils.html_cleaner import clean_html_keep_tags

    t_start = time.monotonic()

    # ── Build intent ──────────────────────────────────────────────────────────
    if url_override:
        intent = parse_url_intent(
            url=url_override,
            intent_type="structured_data" if target_override else "page_content",
            content_target=target_override or "",
            max_urls=max_urls_override or cfg.scraping.get("max_urls", 25),
            depth=depth_override if depth_override is not None else cfg.scraping.get("depth", 1),
        )
        raw_query = f"--url {url_override}" + (f" --target '{target_override}'" if target_override else "")
    else:
        console.print(" [dim]Parsing intent…[/]")
        try:
            intent = parse_query(query)  # type: ignore[arg-type]
        except ValueError as exc:
            console.print(f" [red]✗ Could not parse query:[/] {exc}")
            raise typer.Exit(1)

        if max_urls_override:
            intent.max_urls = max_urls_override
        if depth_override is not None:
            intent.depth = depth_override
        if target_override:
            intent.content_target = target_override
        raw_query = query or ""

        # Apply output hints parsed by the intent layer (only when not set via CLI flags)
        if output is None and intent.output_file:
            if intent.output_format:
                fmt = intent.output_format
            output = _resolve_output(Path(intent.output_file))
        elif intent.output_format and fmt == "json":
            fmt = intent.output_format

        # Apply pagination hints from intent (only when --paginate not already set)
        if not paginate and intent.paginate:
            paginate = True

    # ── Create job ────────────────────────────────────────────────────────────
    job = Job(
        query=raw_query,
        intent=intent,
        output_format=fmt,
        output_path=str(output) if output else None,
    )
    init_job_scope(job)
    job.status = "running"
    job.save()

    # CLI --max-pages wins over intent-detected value; both fall back to config default
    effective_max_pages = max_pages_override or intent.max_pages
    if paginate:
        layer_label = "[yellow]playwright[/] [dim]+paginate[/]"
    elif force_playwright:
        layer_label = "[yellow]playwright[/]"
    else:
        layer_label = "[green]auto[/]"

    target_label = f"[cyan]{intent.content_target}[/]" if intent.content_target else "[dim]full text[/]"
    output_label = f"[green]{output}[/]" if output else "[dim]stdout[/]"

    layer_line = (
        f"{layer_label}"
        + (f"  [dim]·[/]  [bold]Max Pages[/] {effective_max_pages}" if paginate and effective_max_pages else "")
        + f"  [dim]·[/]  [bold]Format[/] [green]{fmt}[/]"
        + f"  [dim]·[/]  [bold]Max URLs[/] {intent.max_urls}"
    )

    console.print(
        f" [bold]Job[/]     [dim]{job.job_id}[/]\n"
        f" [bold]URL[/]     {intent.url}\n"
        f" [bold]Target[/]  {target_label}\n"
        f" [bold]Layer[/]   {layer_line}\n"
        f" [bold]Output[/]  {output_label}\n"
    )

    if dry_run:
        console.print(" [yellow]Dry run — skipping fetch.[/]")
        console.print_json(json.dumps({"job_id": job.job_id, "intent": {
            "url": intent.url, "intent_type": intent.intent_type,
            "content_target": intent.content_target,
            "max_urls": intent.max_urls, "depth": intent.depth,
        }}))
        return

    # ── Crawl loop ────────────────────────────────────────────────────────────
    all_results: list = []
    inferred_schema: list[str] = []
    concurrency = cfg.scraping.get("concurrency", 4)
    sem = asyncio.Semaphore(concurrency)

    with Progress(
        SpinnerColumn(),
        TextColumn(" [progress.description]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        total_task = progress.add_task(
            f"[cyan]Crawling[/]",
            total=min(intent.max_urls, len(job.urls_pending) or 1),
        )

        while job.urls_pending:
            batch = job.urls_pending[:concurrency]

            async def process_url(url: str) -> None:
                async with sem:
                    short_url = url[:70] + "…" if len(url) > 70 else url
                    progress.update(total_task,
                        description=f"[cyan]Fetching[/] [dim]{short_url}[/]")

                    scrape_result = await scrape(url, force_playwright=force_playwright or paginate, paginate=paginate, max_pages=effective_max_pages)

                    if scrape_result.error or not scrape_result.html:
                        err_msg = scrape_result.error or "empty response"
                        hint = _scrape_error_hint(scrape_result.status_code, err_msg, force_playwright or paginate)
                        progress.print(
                            f" [red]✗[/] {short_url}\n"
                            f"   [dim]{err_msg}[/]"
                            + (f"\n   [yellow]→[/] [dim]{hint}[/]" if hint else "")
                        )
                        job.mark_url_failed(url)
                        return

                    layer_badges = {
                        "httpx": "[green]httpx[/]",
                        "playwright": "[yellow]playwright[/]",
                        "scraperapi": "[magenta]scraperapi[/]",
                        "zyte": "[blue]zyte[/]",
                    }
                    layer_tag = layer_badges.get(scrape_result.layer_used, scrape_result.layer_used)

                    # Save debug HTML snapshots before extraction
                    if debug:
                        clean_snap = clean_html_keep_tags(scrape_result.html)
                        _save_debug_html(job.job_id, url, scrape_result.html, clean_snap)

                    if intent.intent_type == "url_list":
                        links = extract_links(scrape_result.html, url)
                        all_results.extend(links)
                        job.mark_url_done(url, result={"url": url, "links": links})
                        progress.print(
                            f" [green]✓[/] {layer_tag}  [dim]{short_url}[/]"
                            f"  →  [bold]{len(links)}[/] links"
                        )
                    else:
                        selector_hint = job.selector_cache.get(url) or \
                                        _find_pattern_selector(url, job.selector_cache)
                        progress.update(total_task,
                            description=f"[cyan]Extracting[/] [dim]{short_url}[/]")
                        extraction = extract(
                            scrape_result.html,
                            url=scrape_result.url,
                            content_target=intent.content_target,
                            selector_hint=selector_hint,
                        )
                        if extraction.selector_used:
                            job.selector_cache[url] = extraction.selector_used
                        if extraction.schema_fields and extraction.schema_fields != ["value"]:
                            inferred_schema[:] = extraction.schema_fields

                        all_results.extend(extraction.items)
                        job.mark_url_done(url, result={
                            "url": scrape_result.url,
                            "items": extraction.items,
                            "selector": extraction.selector_used,
                            "method": extraction.method,
                        })

                        method_tag = (
                            "[green]selector[/]" if extraction.method == "llm_selector"
                            else "[dim]text[/]"
                        )
                        selector_hint_str = (
                            f"  [dim]{extraction.selector_used}[/]"
                            if extraction.selector_used else ""
                        )
                        progress.print(
                            f" [green]✓[/] {layer_tag}  [dim]{short_url}[/]\n"
                            f"   {method_tag}  [bold]{len(extraction.items)}[/] items"
                            f"{selector_hint_str}"
                        )
                        if extraction.method == "text_only" and intent.content_target and len(extraction.items) <= 1:
                            progress.print(
                                "   [yellow]⚠[/] [dim]No structured items found — "
                                "try a more specific --target or --debug to inspect HTML[/]"
                            )

                    if should_follow_links(job):
                        links = extract_links(scrape_result.html, url)
                        added = enqueue_discovered_links(links, job)
                        if added:
                            progress.update(
                                total_task,
                                total=min(intent.max_urls,
                                          len(job.urls_processed) + len(job.urls_pending)),
                            )

                    progress.advance(total_task)

            await asyncio.gather(*[process_url(u) for u in batch])

    # ── Deduplication (paginate mode) ─────────────────────────────────────────
    from datapulse.modules.extractor import ExtractionResult

    if paginate and all_results:
        all_results, removed = deduplicate_items(all_results)
        if removed:
            console.print(f" [dim]Deduplication: removed {removed} repeated items[/]")

    # ── Follow phase (detail crawl) ───────────────────────────────────────────
    detail_by_url: dict[str, list] = {}
    detail_schema: list[str] = []

    if follow and detail_target and all_results:
        follow_urls = _extract_follow_urls(all_results, intent.url)
        cap = max_follow or 50
        follow_urls = follow_urls[:cap]

        if follow_urls:
            console.print(
                f"\n [bold cyan]Follow phase[/]  [dim]·[/]  "
                f"[bold]{len(follow_urls)}[/] detail URLs  [dim]·[/]  "
                f"target: [cyan]{detail_target}[/]\n"
            )
            follow_selector_cache: dict[str, str] = {}

            with Progress(
                SpinnerColumn(),
                TextColumn(" [progress.description]{task.description}"),
                BarColumn(bar_width=28),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=False,
            ) as fprogress:
                ftask = fprogress.add_task("[cyan]Following[/]", total=len(follow_urls))

                for fu_batch in [follow_urls[i:i+concurrency] for i in range(0, len(follow_urls), concurrency)]:
                    async def follow_url(furl: str) -> None:
                        async with sem:
                            fshort = furl[:70] + "…" if len(furl) > 70 else furl
                            fprogress.update(ftask, description=f"[cyan]Detail[/] [dim]{fshort}[/]")
                            fscrape = await scrape(furl, force_playwright=force_playwright)
                            if fscrape.error or not fscrape.html:
                                fprogress.advance(ftask)
                                return

                            if debug:
                                clean_snap = clean_html_keep_tags(fscrape.html)
                                _save_debug_html(job.job_id, furl, fscrape.html, clean_snap)

                            fextraction = extract(
                                fscrape.html,
                                url=fscrape.url,
                                content_target=detail_target,
                                skip_selector=True,
                            )
                            for f in fextraction.schema_fields:
                                if f not in detail_schema and f not in ("content", "value"):
                                    detail_schema.append(f)

                            detail_by_url[furl] = fextraction.items
                            layer_tag = {"httpx": "[green]httpx[/]", "playwright": "[yellow]playwright[/]"}.get(fscrape.layer_used, fscrape.layer_used)
                            method_tag = (
                                "[green]selector[/]" if fextraction.method == "llm_selector"
                                else "[cyan]text-llm[/]" if fextraction.method == "llm_text"
                                else "[dim]text[/]"
                            )
                            fprogress.print(f" [green]✓[/] {layer_tag}  [dim]{fshort}[/]  {method_tag}  [bold]{len(fextraction.items)}[/] items")
                            fprogress.advance(ftask)

                    await asyncio.gather(*[follow_url(u) for u in fu_batch])

    # ── Format and output ─────────────────────────────────────────────────────

    if intent.intent_type == "url_list":
        display_items = [f"{lnk.get('text', '')} → {lnk.get('href', '')}" for lnk in all_results]
        schema_fields = ["text", "href"]
    else:
        listing_items = [str(i) for i in all_results if i]
        if detail_by_url:
            display_items = _merge_with_details(listing_items, detail_by_url, intent.url)
            schema_fields = (inferred_schema or ["value"]) + [f for f in detail_schema if f not in (inferred_schema or [])]
        else:
            display_items = listing_items
            schema_fields = inferred_schema or ["value"]

    if not display_items and intent.content_target:
        console.print(
            " [yellow]⚠ Tip:[/] [dim]No structured data found. "
            "Try --debug to inspect the HTML, or rephrase --target.[/]"
        )

    combined = ExtractionResult(
        items=display_items or ["No data extracted."],
        selector_used=next(iter(job.selector_cache.values()), None),
        schema_fields=schema_fields,
        cleaned_text="\n".join(display_items[:5]),
        method="llm_selector" if job.selector_cache else "text_only",
    )

    include_meta = cfg.output.get("include_metadata", True)
    formatted = format_result(combined, url=intent.url, fmt=fmt, include_metadata=include_meta)

    elapsed = time.monotonic() - t_start
    console.rule("[dim]Results[/]")

    if output:
        output.write_text(formatted, encoding="utf-8")
        console.print(f"\n [green]✓[/] [bold]{len(display_items)}[/] items  →  [bold]{output}[/]")
        if debug:
            console.print(f"   [dim]Debug HTML snapshots → output/debug/[/]")
    else:
        if fmt == "json":
            console.print_json(formatted)
        else:
            console.print(formatted)
        console.print(f"\n [green]✓[/] [bold]{len(display_items)}[/] items extracted")

    job.status = "complete" if not job.urls_failed else "partial"
    job.save()

    status_color = "green" if job.status == "complete" else "yellow"
    selector_str = (
        f"  [dim]selector: {combined.selector_used}[/]"
        if combined.selector_used else ""
    )
    console.rule()
    console.print(
        f" [{status_color}]{job.status.upper()}[/]"
        f"  [bold]{len(job.urls_processed)}[/] URL{'s' if len(job.urls_processed) != 1 else ''}"
        f"  [dim]·[/]  [bold]{len(display_items)}[/] items"
        f"  [dim]·[/]  {elapsed:.1f}s"
        f"{selector_str}"
        f"\n [dim]{job.job_id}[/]\n"
    )


def _scrape_error_hint(status_code: int, error: str, playwright_active: bool) -> str | None:
    if status_code in (403, 429, 503):
        if not playwright_active:
            return "blocked — try adding --playwright flag"
        return "blocked even with Playwright — add SCRAPERAPI_KEY to secrets.env"
    if "ssl" in error.lower() or "certificate" in error.lower():
        return "SSL error — Playwright should resolve this automatically; check network if it persists"
    if "timeout" in error.lower():
        return "timeout — try increasing timeout_seconds in datapulse.config.yaml"
    return None


def _extract_follow_urls(items: list, base_url: str) -> list[str]:
    """Parse 'url: <href>' lines from extracted items and resolve to absolute URLs."""
    from urllib.parse import urljoin
    seen: set[str] = set()
    urls: list[str] = []
    for item in items:
        m = re.search(r'url:\s*(\S+)', str(item), re.IGNORECASE)
        if m:
            href = m.group(1).strip()
            abs_url = urljoin(base_url, href)
            if abs_url not in seen and abs_url != base_url:
                seen.add(abs_url)
                urls.append(abs_url)
    return urls


def _find_domain_selector(url: str, cache: dict[str, str]) -> str | None:
    """Find a cached selector by matching netloc — scoped to a given cache dict."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    for cached_url, selector in cache.items():
        if urlparse(cached_url).netloc == domain:
            return selector
    return None


def _merge_with_details(listing_items: list[str], detail_by_url: dict[str, list], base_url: str) -> list[str]:
    """Merge listing items with their detail page results by matching on URL."""
    from urllib.parse import urljoin
    merged: list[str] = []
    for item in listing_items:
        m = re.search(r'url:\s*(\S+)', item, re.IGNORECASE)
        if m:
            href = m.group(1).strip()
            abs_url = urljoin(base_url, href)
            detail_items = detail_by_url.get(abs_url, [])
            detail_text = "\n".join(str(i) for i in detail_items) if detail_items else ""
            merged.append(item + ("\n" + detail_text if detail_text else ""))
        else:
            merged.append(item)
    return merged


def _find_pattern_selector(url: str, cache: dict[str, str]) -> str | None:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    for cached_url, selector in cache.items():
        if urlparse(cached_url).netloc == domain:
            return selector
    return None


# ── resume ────────────────────────────────────────────────────────────────────

@app.command()
def resume(
    job_id: str = typer.Argument(..., help="Job ID to resume"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Resume a previously interrupted job from its last checkpoint."""
    _setup_logging(debug)
    _print_banner()
    from datapulse.job import Job

    try:
        job = Job.load(job_id)
    except FileNotFoundError:
        console.print(f" [red]✗ Job not found:[/] {job_id}")
        raise typer.Exit(1)

    if job.status == "complete":
        console.print(f" [green]✓ Job {job_id} is already complete.[/]")
        raise typer.Exit(0)

    console.print(f" Resuming [bold]{job_id}[/]  —  {len(job.urls_pending)} URLs remaining\n")
    asyncio.run(_run_pipeline(
        query=None,
        url_override=job.intent.url,
        target_override=job.intent.content_target or None,
        fmt=job.output_format,
        output=Path(job.output_path) if job.output_path else None,
        max_urls_override=job.intent.max_urls,
        depth_override=job.intent.depth,
        dry_run=False,
        force_playwright=False,
        debug=debug,
    ))


# ── jobs ──────────────────────────────────────────────────────────────────────

@app.command()
def jobs(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of jobs to show"),
):
    """List recent jobs with status."""
    from datapulse.job import Job

    _print_banner()
    all_jobs = Job.list_all()[:limit]
    if not all_jobs:
        console.print(" [dim]No jobs found.[/]")
        return

    table = Table(
        title="Recent Jobs",
        box=box.SIMPLE_HEAD,
        border_style="dim",
        show_edge=False,
        pad_edge=True,
    )
    table.add_column("Job ID", style="dim", no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Items", justify="right")
    table.add_column("Pending", justify="right")
    table.add_column("Query / URL", max_width=45, no_wrap=True)
    table.add_column("Output", max_width=28, no_wrap=True, style="dim")
    table.add_column("Created", style="dim")

    colors = {
        "complete": "green", "running": "cyan", "failed": "red",
        "partial": "yellow", "pending": "dim",
    }
    for j in all_jobs:
        c = colors.get(j["status"], "white")
        out = j.get("output_path") or f"[dim]stdout ({j.get('output_format','json')})[/]"
        table.add_row(
            j["job_id"],
            f"[{c}]{j['status']}[/]",
            str(j["urls_processed"]),
            str(j["urls_pending"]),
            j["query"],
            out,
            j["created_at"][:19].replace("T", " "),
        )
    console.print(table)


# ── inspect ───────────────────────────────────────────────────────────────────

@app.command()
def inspect(
    job_id: str = typer.Argument(..., help="Job ID to inspect"),
):
    """Show detailed info about a specific job."""
    from datapulse.job import Job

    _print_banner()
    try:
        job = Job.load(job_id)
    except FileNotFoundError:
        console.print(f" [red]✗ Job not found:[/] {job_id}")
        raise typer.Exit(1)

    status_color = {
        "complete": "green", "running": "cyan", "failed": "red",
        "partial": "yellow",
    }.get(job.status, "white")

    console.print(Panel(
        f"[bold]Job ID[/]     {job.job_id}\n"
        f"[bold]Status[/]     [{status_color}]{job.status}[/]\n"
        f"[bold]Query[/]      {job.query}\n"
        f"[bold]URL[/]        {job.intent.url}\n"
        f"[bold]Target[/]     {job.intent.content_target or '[dim]—[/]'}\n"
        f"[bold]Processed[/]  {len(job.urls_processed)}  "
        f"[dim]·[/]  Pending {len(job.urls_pending)}  "
        f"[dim]·[/]  Failed {len(job.urls_failed)}\n"
        f"[bold]Results[/]    {len(job.results)} items\n"
        f"[bold]Output[/]     {job.output_path or '[dim]stdout[/]'}  [dim]({job.output_format})[/]\n"
        f"[bold]Created[/]    {job.created_at}\n"
        f"[bold]Updated[/]    {job.updated_at}",
        title="[bold cyan]Job Details[/]",
        border_style="cyan",
        padding=(1, 2),
    ))

    if job.selector_cache:
        console.print(" [bold]Selector cache:[/]")
        for pattern, sel in job.selector_cache.items():
            console.print(f"  [dim]{pattern}[/]  →  [green]{sel}[/]")
        console.print()


# ── config ────────────────────────────────────────────────────────────────────

@app.command(name="config")
def config_cmd():
    """Open datapulse.config.yaml in the default editor."""
    import os
    import subprocess
    p = Path.cwd() / "datapulse.config.yaml"
    if not p.exists():
        console.print(f" [yellow]Config not found: {p}[/]")
        return
    subprocess.run([os.environ.get("EDITOR", "vi"), str(p)])


if __name__ == "__main__":
    app()
