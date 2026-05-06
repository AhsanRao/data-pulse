"""CLI entry point for DataPulse."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
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

app = typer.Typer(
    name="datapulse",
    help="[bold cyan]DataPulse[/] — AI-Driven Intent-Based Web Extraction Agent",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(debug: bool = False) -> None:
    from datapulse.config import cfg

    level = logging.DEBUG if debug else logging.WARNING
    log_file = cfg.log_dir / "datapulse.log"
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[
            RichHandler(console=Console(stderr=True), show_path=False, markup=True),
            logging.FileHandler(log_file),
        ],
    )


# ── run ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    query: Optional[str] = typer.Argument(None,
        help='Natural language query, e.g. "get book prices from https://books.toscrape.com"'),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Target URL (overrides URL in query)"),
    target: Optional[str] = typer.Option(None, "--target", "-t",
        help='What to extract, e.g. "product name and price"'),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write output to file"),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format: text|json|csv|md"),
    max_urls: Optional[int] = typer.Option(None, "--max-urls", help="Max URLs to crawl"),
    depth: Optional[int] = typer.Option(None, "--depth", help="Crawl depth (0=seed only)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse intent only, skip fetch"),
    force_playwright: bool = typer.Option(False, "--playwright", help="Force Playwright layer"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """Run a full extraction from a natural language query or --url flag."""
    _setup_logging(debug)

    if not query and not url:
        console.print("[red]Error:[/] Provide a query or --url <URL>.\n")
        console.print("Examples:")
        console.print('  datapulse run "get book titles and prices from https://books.toscrape.com"')
        console.print("  datapulse run --url https://books.toscrape.com --target 'book title and price'")
        raise typer.Exit(1)

    asyncio.run(_run_pipeline(
        query=query,
        url_override=url,
        target_override=target,
        fmt=fmt,
        output=output,
        max_urls_override=max_urls,
        depth_override=depth,
        dry_run=dry_run,
        force_playwright=force_playwright,
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
) -> None:
    from datapulse.config import cfg
    from datapulse.job import Job
    from datapulse.modules.intent_parser import parse_query, parse_url_intent
    from datapulse.modules.scope_guard import init_job_scope, enqueue_discovered_links, should_follow_links
    from datapulse.modules.scraper import scrape, extract_links
    from datapulse.modules.extractor import extract
    from datapulse.modules.formatter import format_result

    # ── Build intent ──────────────────────────────────────────────────────────
    if url_override:
        # Explicit --url path: no LLM parsing needed
        intent = parse_url_intent(
            url=url_override,
            intent_type="structured_data" if target_override else "page_content",
            content_target=target_override or "",
            max_urls=max_urls_override or cfg.scraping.get("max_urls", 25),
            depth=depth_override if depth_override is not None else cfg.scraping.get("depth", 1),
        )
        raw_query = f"--url {url_override}" + (f" --target '{target_override}'" if target_override else "")
    else:
        # Natural language path: Ollama intent parser
        console.print("[dim]Parsing intent…[/]")
        try:
            intent = parse_query(query)  # type: ignore[arg-type]
        except ValueError as exc:
            console.print(f"[red]Could not parse query:[/] {exc}")
            raise typer.Exit(1)

        # CLI overrides take precedence over parsed values
        if max_urls_override:
            intent.max_urls = max_urls_override
        if depth_override is not None:
            intent.depth = depth_override
        if target_override:
            intent.content_target = target_override
        raw_query = query or ""

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

    mode_tag = f"[green]{intent.content_target}[/]" if intent.content_target else "[dim]full text[/]"
    console.print(Panel(
        f"[bold cyan]DataPulse[/] · Job [dim]{job.job_id}[/]\n"
        f"URL: [link={intent.url}]{intent.url}[/link]\n"
        f"Intent: [cyan]{intent.intent_type}[/]  |  Target: {mode_tag}\n"
        f"Format: [green]{fmt}[/]  |  Max URLs: [green]{intent.max_urls}[/]  |  Depth: [green]{intent.depth}[/]",
        border_style="cyan",
    ))

    if dry_run:
        console.print("[yellow]Dry run — skipping fetch.[/]")
        console.print_json(json.dumps({"job_id": job.job_id, "intent": {
            "url": intent.url, "intent_type": intent.intent_type,
            "content_target": intent.content_target,
            "max_urls": intent.max_urls, "depth": intent.depth,
        }}))
        return

    # ── Crawl loop ────────────────────────────────────────────────────────────
    all_results: list = []
    concurrency = cfg.scraping.get("concurrency", 4)
    sem = asyncio.Semaphore(concurrency)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        total_task = progress.add_task(
            f"[cyan]Crawling {intent.url}[/]",
            total=min(intent.max_urls, len(job.urls_pending) or 1),
        )

        while job.urls_pending:
            # Take a batch up to concurrency limit
            batch = job.urls_pending[:concurrency]

            async def process_url(url: str) -> None:
                async with sem:
                    progress.update(total_task, description=f"Fetching [cyan]{url[:60]}[/]…")
                    scrape_result = await scrape(url, force_playwright=force_playwright)

                    if scrape_result.error or not scrape_result.html:
                        console.print(f"[red]✗[/] {url} — {scrape_result.error or 'empty response'}")
                        job.mark_url_failed(url)
                        return

                    layer_colors = {"httpx": "green", "playwright": "yellow",
                                    "scraperapi": "magenta", "zyte": "blue"}
                    badge = layer_colors.get(scrape_result.layer_used, "white")

                    # For url_list intent: just collect links, skip extraction
                    if intent.intent_type == "url_list":
                        links = extract_links(scrape_result.html, url)
                        result_data = {"url": url, "links": links}
                        all_results.extend(links)
                        job.mark_url_done(url, result=result_data)
                        console.print(
                            f"[{badge}]●[/] {url[:60]} → [bold]{len(links)}[/] links"
                        )
                    else:
                        # Extract content
                        selector_hint = job.selector_cache.get(url) or \
                                        _find_pattern_selector(url, job.selector_cache)
                        extraction = extract(
                            scrape_result.html,
                            url=scrape_result.url,
                            content_target=intent.content_target,
                            selector_hint=selector_hint,
                        )
                        if extraction.selector_used:
                            job.selector_cache[url] = extraction.selector_used

                        result_data = {
                            "url": scrape_result.url,
                            "items": extraction.items,
                            "selector": extraction.selector_used,
                            "method": extraction.method,
                        }
                        all_results.extend(extraction.items)
                        job.mark_url_done(url, result=result_data)
                        console.print(
                            f"[{badge}]●[/] {url[:60]} → "
                            f"[bold]{len(extraction.items)}[/] items "
                            f"[dim]({extraction.method})[/]"
                        )

                    # Deep crawl: discover and enqueue new URLs from this page
                    if should_follow_links(job):
                        links = extract_links(scrape_result.html, url)
                        added = enqueue_discovered_links(links, job)
                        if added:
                            progress.update(
                                total_task,
                                total=min(intent.max_urls, len(job.urls_processed) + len(job.urls_pending)),
                            )

                    progress.advance(total_task)

            await asyncio.gather(*[process_url(u) for u in batch])

    # ── Format and output ─────────────────────────────────────────────────────
    from datapulse.modules.extractor import ExtractionResult

    # Build a combined ExtractionResult from all collected items
    # For url_list: items are link dicts; we materialise them as text
    if intent.intent_type == "url_list":
        display_items = [f"{lnk.get('text', '')} → {lnk.get('href', '')}" for lnk in all_results]
        schema_fields = ["text", "href"]
    else:
        display_items = [str(i) for i in all_results if i]
        schema_fields = job.results[0].get("items", [{}]) if job.results else []
        # Try to get schema from first successful extraction
        schema_fields = ["value"]

    combined = ExtractionResult(
        items=display_items or ["No data extracted."],
        selector_used=next(iter(job.selector_cache.values()), None),
        schema_fields=schema_fields,
        cleaned_text="\n".join(display_items[:5]),
        method="llm_selector" if job.selector_cache else "text_only",
    )

    include_meta = cfg.output.get("include_metadata", True)
    formatted = format_result(combined, url=intent.url, fmt=fmt, include_metadata=include_meta)

    console.rule("[dim]Results[/]")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(formatted)
        console.print(f"[green]✓[/] Written to [bold]{output}[/]  ({len(display_items)} items)")
    else:
        if fmt == "json":
            console.print_json(formatted)
        else:
            console.print(formatted)
    console.rule()

    job.status = "complete" if not job.urls_failed else "partial"
    job.save()

    status_color = "green" if job.status == "complete" else "yellow"
    console.print(
        f"[{status_color}]{job.status.upper()}[/] · "
        f"[bold]{len(job.urls_processed)}[/] URLs · "
        f"[bold]{len(display_items)}[/] items · "
        f"[dim]{job.job_id}[/]"
    )


def _find_pattern_selector(url: str, cache: dict[str, str]) -> str | None:
    """Return a cached selector if URL matches a known domain pattern."""
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
    from datapulse.job import Job

    try:
        job = Job.load(job_id)
    except FileNotFoundError:
        console.print(f"[red]Job not found:[/] {job_id}")
        raise typer.Exit(1)

    if job.status == "complete":
        console.print(f"[green]Job {job_id} is already complete.[/]")
        raise typer.Exit(0)

    console.print(f"Resuming [bold]{job_id}[/] — {len(job.urls_pending)} URLs remaining")
    # Re-run the pipeline with the existing job's intent
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
    ))


# ── jobs ──────────────────────────────────────────────────────────────────────

@app.command()
def jobs(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of jobs to show"),
):
    """List recent jobs with status."""
    from datapulse.job import Job

    all_jobs = Job.list_all()[:limit]
    if not all_jobs:
        console.print("[dim]No jobs found.[/]")
        return

    table = Table(title="DataPulse Jobs", border_style="cyan")
    table.add_column("Job ID", style="dim")
    table.add_column("Status")
    table.add_column("Done", justify="right")
    table.add_column("Pending", justify="right")
    table.add_column("Query", max_width=55, no_wrap=True)
    table.add_column("Created")

    colors = {"complete": "green", "running": "cyan", "failed": "red",
              "partial": "yellow", "pending": "dim"}
    for j in all_jobs:
        c = colors.get(j["status"], "white")
        table.add_row(
            j["job_id"], f"[{c}]{j['status']}[/]",
            str(j["urls_processed"]), str(j["urls_pending"]),
            j["query"], j["created_at"][:19].replace("T", " "),
        )
    console.print(table)


# ── inspect ───────────────────────────────────────────────────────────────────

@app.command()
def inspect(
    job_id: str = typer.Argument(..., help="Job ID to inspect"),
):
    """Show detailed info about a specific job."""
    from datapulse.job import Job

    try:
        job = Job.load(job_id)
    except FileNotFoundError:
        console.print(f"[red]Job not found:[/] {job_id}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]Job:[/] {job.job_id}\n"
        f"[bold]Status:[/] {job.status}\n"
        f"[bold]Query:[/] {job.query}\n"
        f"[bold]URL:[/] {job.intent.url}\n"
        f"[bold]Intent:[/] {job.intent.intent_type}  |  Target: {job.intent.content_target or '—'}\n"
        f"[bold]Processed:[/] {len(job.urls_processed)}  |  "
        f"Pending: {len(job.urls_pending)}  |  Failed: {len(job.urls_failed)}\n"
        f"[bold]Results:[/] {len(job.results)} items\n"
        f"[bold]Output:[/] {job.output_path or 'stdout'}  ({job.output_format})\n"
        f"[bold]Created:[/] {job.created_at}\n"
        f"[bold]Updated:[/] {job.updated_at}",
        title="Job Details", border_style="cyan",
    ))
    if job.selector_cache:
        console.print("[bold]Selector cache:[/]")
        for pattern, sel in job.selector_cache.items():
            console.print(f"  [dim]{pattern}[/] → [green]{sel}[/]")


# ── config ────────────────────────────────────────────────────────────────────

@app.command(name="config")
def config_cmd():
    """Open datapulse.config.yaml in the default editor."""
    import os, subprocess
    p = Path.cwd() / "datapulse.config.yaml"
    if not p.exists():
        console.print(f"[yellow]Config not found: {p}[/]")
        return
    subprocess.run([os.environ.get("EDITOR", "vi"), str(p)])


if __name__ == "__main__":
    app()
