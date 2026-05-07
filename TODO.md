# DataPulse — Agent Context & TODO

> **Purpose of this file:** Hand-off document for Claude Code sessions.
> Paste this file's contents at the start of a new session to give the agent
> full context on what exists, what decisions were made, and what to build next.

---

## Project Identity

| Field | Value |
|---|---|
| Project | DataPulse — AI-Driven Intent-Based Web Extraction Agent |
| Package | `datapulse-agent` v0.1.0 |
| CLI command | `datapulse` (after `source .env/bin/activate`) |
| Config file | `datapulse.config.yaml` |
| Venv | `.env/` (Python 3.14) — **this directory is the venv, not a dotenv file** |
| API keys file | `secrets.env` (gitignored) |
| Primary LLM | `mueen-80b` via LiteLLM proxy at `http://10.8.124.144:4000` |
| Ollama model | `qwen2.5:1.5b` — fallback only; needs `brew services start ollama` |
| Job store | `.datapulse/jobs/` (project-local, gitignored) |
| LLM logs | `.datapulse/logs/llm_calls.log` |
| Output files | `output/` (gitignored) |
| Debug HTML | `output/debug/` (only with `--debug`) |
| Test runner | `.env/bin/pytest tests/ -v` |
| Install | `.env/bin/pip install -e .` |

---

## Architecture Summary

DataPulse is a **5-module linear pipeline**. State is passed via a central `Job` dataclass checkpointed to disk after each URL.

```
Natural language query  OR  --url flag
          │
   ┌──────▼────────────────┐
   │ 1. Intent Parser        │  datapulse/modules/intent_parser.py
   │    ✅ Done              │  LiteLLM mueen-80b → structured Intent
   │                         │  Ollama fallback → heuristic fallback
   └──────┬────────────────┘
          │  Intent { url, intent_type, content_target, max_urls, depth }
   ┌──────▼────────────────┐
   │ 2. Scope Guard          │  datapulse/modules/scope_guard.py
   │    ✅ Done              │  URL dedup, domain filter, max_urls cap
   │                         │  Depth-aware URL queue for deep crawl
   └──────┬────────────────┘
          │  filtered URL list
   ┌──────▼────────────────┐
   │ 3. Scraper              │  datapulse/modules/scraper.py
   │    ✅ Done              │  Layer 1: httpx (fast, static)
   │                         │  Layer 2: Playwright headless (JS/scroll/paginate)
   │                         │  Layer 3: ScraperAPI / Zyte (anti-bot, optional)
   └──────┬────────────────┘
          │  raw HTML string
   ┌──────▼────────────────┐
   │ 4. Extractor            │  datapulse/modules/extractor.py
   │    ✅ Done              │  Two modes:
   │                         │  ┌─ Listing (skip_selector=False) ─────────────┐
   │                         │  │ BS4 clean → recursive semantic chunks        │
   │                         │  │ → LLM selector discovery → validation gate  │
   │                         │  │ → BeautifulSoup programmatic extraction      │
   │                         │  │ → LLM schema inference (field names)         │
   │                         │  └──────────────────────────────────────────────┘
   │                         │  ┌─ Detail (skip_selector=True) ───────────────┐
   │                         │  │ Extract <aside>/contact section from HTML    │
   │                         │  │ → single LLM call → structured JSON fields  │
   │                         │  │ method="llm_text"                            │
   │                         │  └──────────────────────────────────────────────┘
   └──────┬────────────────┘
          │  ExtractionResult { items[], selector_used, schema_fields, method }
   ┌──────▼────────────────┐
   │ 5. Formatter            │  datapulse/modules/formatter.py
   │    ✅ Done              │  JSON | CSV | Markdown table | plain text
   │                         │  + metadata: url, timestamp, item_count, method
   └─────────────────────────┘
```

**LLM backends (auto-selected in priority order):**
1. **LiteLLM proxy → mueen-80b** — if `LLM_BASE_URL` + `LLM_API_KEY` set in `secrets.env` (primary)
2. **Anthropic Claude Haiku** — if `ANTHROPIC_API_KEY` set and LiteLLM unreachable
3. **Ollama qwen2.5:1.5b** — if Ollama running and both above unavailable
4. **Text-only mode** — if no LLM is available (returns cleaned text, no structured data)

---

## File Map

```
datapulse/
├── main.py                  CLI — Typer, all 5 commands, full NL + deep crawl loop
│                            ASCII art banner, --paginate flag, inferred_schema passthrough
├── job.py                   Job dataclass, save/load/checkpoint, list_all
├── config.py                Config loader: datapulse.config.yaml + secrets.env
│                            LLM_* env vars (primary), LITELLM_* (backward compat fallback)
│
├── modules/
│   ├── intent_parser.py     Module 1 — LiteLLM/Ollama NL → Intent + heuristic fallback
│   │                        Extracts output_format + output_file from NL query via LLM prompt
│   ├── scope_guard.py       Module 2 — URL dedup, domain filter, cap, deep queue
│   ├── scraper.py           Module 3 — httpx / Playwright / ScraperAPI / Zyte
│   │                        _is_js_required(): auto-upgrades httpx→Playwright for JS-required pages
│   │                        _paginate_playwright(): click Next, dismiss consent, combine pages
│   │                        _dismiss_consent(): handles OneTrust and common cookie popups
│   ├── extractor.py         Module 4 — clean/chunk/LLM/validate/extract
│   │                        Listing mode: up to max_chunk_attempts (default 50) chunks, exits early on first valid selector
│   │                        Detail mode: skip_selector=True → _focused_text_for_extraction() → single LLM text→JSON call
│   │                        _focused_text_for_extraction(): pulls <aside>/contact section HTML, prepends href links
│   │                        _extract_from_text(): sends focused text to LLM, parses JSON, returns ExtractionResult(method="llm_text")
│   └── formatter.py         Module 5 — JSON/CSV/MD/text renderer + metadata
│                            _normalise_items(): key-value prefix parsing (name:/url: lines)
│                            deduplicate_items(): dedup by URL or content, returns (list, removed_count)
│                            llm_text method treated same as llm_selector in _to_text()
│
└── utils/
    ├── litellm_client.py    OpenAI-compatible LLM client — call(), is_available()
    ├── ollama_client.py     Ollama HTTP wrapper (fallback) — generate(), is_available()
    ├── html_cleaner.py      BS4 HTML cleaning — strips scripts/nav/ads/onetrust
    │                        Two-phase decompose to avoid NoneType crash on orphaned children
    ├── chunker.py           Recursive semantic block splitter (MAX_CHUNK_CHARS=6000)
    │                        No depth limit — drills until children fit, handles 22+ deep SPAs
    ├── validator.py         CSS selector application — anchors emit "name:\nurl:" format
    └── retry.py             @async_retry exponential backoff decorator

tests/
├── test_scraper.py          Phase 1: scraper, links, cleaner, chunker, job, retry
├── test_intent_parser.py    Phase 1+3: stub + LiteLLM path + heuristic
├── test_extractor.py        Phase 2: selector discovery, validation, pipeline (mocked)
├── test_formatter.py        Phase 2: JSON/CSV/MD/text output
└── test_integration.py      Phase 4: full pipeline integration tests (12 tests)

datapulse.config.yaml        User config (all settings + defaults, incl. playwright.max_pages)
secrets.env                  API keys (gitignored)
.env/                        Python 3.14 venv
output/                      All output files (gitignored)
output/debug/                Raw + clean HTML snapshots (--debug only, gitignored)
.datapulse/                  Jobs + logs (gitignored)
```

---

## Key Design Decisions

1. **`.env` is the venv directory, not a dotenv file.**
   API keys: `secrets.env` (project root). `config.py` skips paths that are directories
   automatically via `p.is_file()` check.

2. **Always use `.env/bin/*` for all commands.**
   System Python is Homebrew 3.14 — packages are not installed there.

3. **LLM env vars use `LLM_*` prefix (not `LITELLM_*`).**
   `config.py` reads `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` first, then falls back
   to `LITELLM_BASE_URL` etc. for backward compat. `secrets.env` uses `LLM_*`.

4. **Chunker has NO fixed depth limit.**
   Old `_MAX_RECURSE_DEPTH=15` was removed. SPAs nest up to 22+ levels deep
   (cyberab.org Angular app = depth 22, MWC Algolia = depth ~12). Recursion terminates
   naturally because HTML trees are finite. Chunks are ≤6,000 chars each.

5. **Extractor caps chunk attempts at `max_chunk_attempts` (default 50), exits early on success.**
   Old hard cap of 10 was replaced with a configurable `cfg.llm.get("max_chunk_attempts", 50)`.
   Loop returns immediately when a valid selector is found — so chunk 10 success skips chunks 11–50.
   This fixed MWC (exhibitors at chunk 12) and cyberab (cards at chunk 28+).

6. **Schema fields flow from extractor → formatter via `inferred_schema`.**
   `main.py` collects `extraction.schema_fields` into `inferred_schema[]` and passes
   it to the combined `ExtractionResult`. Previously hardcoded `["value"]`.

7. **`apply_selector` emits structured text for anchor elements.**
   When the matched element is an `<a>` tag with `href`, output is:
   `"name: {h3_text}\nurl: {href}"` — not a flat text blob.
   `_normalise_items()` in formatter parses `key: value` prefix lines to map fields.

8. **`--paginate` uses Playwright to click through Next Page buttons.**
   Calls `_dismiss_consent()` first (handles OneTrust), then clicks
   `a[aria-label="Next Page"]` etc., waits for networkidle, accumulates all page HTMLs,
   combines body contents into one document. Controlled by `playwright.max_pages` (default 20).
   Used for client-side paginated sites like MWC Barcelona (1000 items from 20 pages).

9. **URLs with `!` must use single quotes in zsh.**
   `datapulse run --url 'https://site.com/Catalog#!/...'` — double quotes don't protect `!`.

10. **Selector cache in `Job.selector_cache`:**
    Pattern: `{url: selector}`. On resume or deep crawl, `_find_pattern_selector()` in
    `main.py` matches by domain so pages with the same template reuse the selector.

11. **Playwright scroll** stops when `document.body.scrollHeight` stops growing for 3
    consecutive checks. Cap: `playwright.max_scroll_attempts` (default 60).

12. **Detail pages use direct text extraction, not CSS selectors.**
    Contact info / social media links are non-repeating fields scattered across a page —
    CSS selector discovery (designed for lists of repeated items) always fails on them.
    `extract(skip_selector=True)` skips selector discovery entirely: it extracts the
    `<aside>` or contact section HTML and makes one LLM call to parse it as JSON.
    Cost: 1 LLM call per detail page. Old cost: 7–18 failed attempts + 1 text call.

13. **Detail schema is a union, not a per-page overwrite.**
    Detail pages run concurrently — the last one to finish would overwrite `detail_schema`
    with its own (possibly smaller) field set. Now `detail_schema` accumulates all unique
    field names seen across all pages, so LinkedIn found on page 5 still becomes a CSV column.

---

## Build Status

### ✅ Phase 1 — Core Pipeline (Complete)
- [x] Project structure, pyproject.toml, requirements.txt, .gitignore
- [x] `Job` dataclass — JSON persistence, checkpoint, list/load, mark_url_done
- [x] `Config` loader — YAML + multi-location dotenv (skips `.env` directory)
- [x] `Scraper` — httpx fast path, Playwright scroll, ScraperAPI/Zyte stubs
- [x] `html_cleaner.py` — BS4 HTML cleaning
- [x] `chunker.py` — semantic block splitter
- [x] `retry.py` — `@async_retry` decorator
- [x] `validator.py` — CSS selector application
- [x] CLI: `run`, `jobs`, `inspect`, `resume`, `config` commands

### ✅ Phase 2 — LLM Extraction (Complete)
- [x] `extractor.py` — full pipeline: clean → chunk → selector discovery →
  validation gate → programmatic extraction → schema inference
- [x] `formatter.py` — JSON, CSV, Markdown table, plain text + metadata
- [x] `main.py` — `--target` flag wires into extractor; selector cached in job

### ✅ Phase 3 — Intent Parser + Local Model (Complete)
- [x] `utils/ollama_client.py` — HTTP wrapper for Ollama
- [x] `intent_parser.py` — LiteLLM primary + Ollama fallback + heuristic
- [x] `scope_guard.py` — depth-aware URL queue + `enqueue_discovered_links()`
- [x] `main.py` — full NL query path + concurrent deep crawl loop

### ✅ Phase 3.5 — LiteLLM Proxy Integration (Complete)
- [x] `utils/litellm_client.py` — OpenAI-compatible `httpx` wrapper
- [x] `config.py` — `LLM_*` env var properties (primary) + `LITELLM_*` fallback
- [x] `extractor.py` — LiteLLM first in `_llm_call()` routing
- [x] `secrets.env` — `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`

### ✅ Phase 3.6 — Chunker + Cleaner Fixes (Complete)
- [x] `html_cleaner.py` — two-phase decompose to fix NoneType crash on orphaned BS4 children
- [x] `html_cleaner.py` — strips OneTrust/GDPR consent SDK divs via `_STRIP_ID_PATTERNS`
- [x] `chunker.py` — recursive chunker with NO fixed depth limit (unlimited)
- [x] `extractor.py` — validation prompt made more permissive for container elements
- [x] `validator.py` — `apply_selector` uses `separator="\n"` for clean multi-line text

### ✅ Phase 3.7 — Logging + Output Polish (Complete)
- [x] `main.py` — suppress trafilatura/httpx/playwright noise from console
- [x] `main.py` — `_resolve_output()` auto-places files in `output/` folder
- [x] `main.py` — `_save_debug_html()` saves raw + clean snapshots to `output/debug/`
- [x] `output/` and `.datapulse/` added to `.gitignore`
- [x] `secrets.env` — updated to use generic `LLM_*` env vars with provider examples

### ✅ Phase 3.9 — Intent-Driven Output + Pagination + Auto Playwright (Complete)
- [x] `intent_parser.py` — prompt extracts `output_format`, `output_file`, `paginate`, `max_pages` from NL query
- [x] `intent_parser.py` — `_validated_format()`, `_heuristic_output()`, `_heuristic_paginate()` helpers for fallback path
- [x] `job.py` — `Intent` gets `output_format`, `output_file`, `paginate`, `max_pages` fields (backward-compat)
- [x] `main.py` — applies intent output/paginate/max_pages hints when no CLI flags override them
- [x] `main.py` — added `--max-pages` CLI flag; CLI value wins over intent-detected value
- [x] `main.py` — job header shows all fields on one Layer line: `playwright +paginate · Max Pages N · Format csv · Max URLs 25`
- [x] `main.py` — job header shows `Output` line (file path or `stdout`)
- [x] `scraper.py` — `_is_js_required()` auto-upgrades httpx→Playwright for JS-required pages
- [x] `scraper.py` — `max_pages` threaded through `scrape()` → `_fetch_playwright()` → `_paginate_playwright()`
- [x] `scraper.py` — httpx `ConnectError`/SSL errors now fall through to Playwright instead of crashing

**Verified:**
- `datapulse run 'get exhibitor names and urls from https://www.mwcbarcelona.com/exhibitors/ paginate 2 pages in csv file'`
  → intent detects paginate=True, max_pages=2, format=csv, writes to `output/mwcbarcelona_com.csv`

### ✅ Phase 3.8 — Structured Extraction + Pagination (Complete)
- [x] `main.py` — ASCII art DataPulse banner (centered, Claude Code style)
- [x] `main.py` — `inferred_schema` collected from extraction, no longer hardcoded `["value"]`
- [x] `validator.py` — anchor elements emit `"name: {h3}\nurl: {href}"` structured text
- [x] `formatter.py` — `_normalise_items()` parses `key: value` prefix lines before positional mapping
- [x] `scraper.py` — `--paginate` flag: `_paginate_playwright()`, `_dismiss_consent()`
- [x] `main.py` — `--paginate` CLI flag, passed through `_run_pipeline()`
- [x] `extractor.py` — removed hard `[:10]` cap; now reads `max_chunk_attempts` from config (default 50), exits early on first valid selector
- [x] `extractor.py` — validation prompt requires ALL requested fields present
- [x] `extractor.py` — selector prompt clarified: container must hold ALL requested fields
- [x] `datapulse.config.yaml` — added `playwright.max_pages: 20`

**Verified working sites (Phase 3.6–3.8):**
- `https://books.toscrape.com` — static HTML, httpx, 20 items
- `https://path.rsaconference.com/...` — SPA, Playwright, 650+ exhibitors
- `https://www.mwcbarcelona.com/exhibitors/` — Algolia SPA, `--paginate`, 1000 items
- `https://cyberab.org/Catalog#!/...` — Angular/DNN, Playwright, 103 items (depth-22 nesting)

---

## ✅ Phase 4 — Polish + Follow + Integration Tests (Complete)

- [x] **Actionable error messages** — `_scrape_error_hint()` in `main.py`
  - 403/429/503: "try --playwright" or "add SCRAPERAPI_KEY"
  - SSL/certificate errors: note that Playwright auto-resolves
  - Timeout: suggest increasing `timeout_seconds` in config
  - text_only with content_target: "try a more specific --target or --debug"
  - No items at all: global tip printed after output
- [x] **Deduplication in `--paginate` mode** — `deduplicate_items()` in `formatter.py`
  - Deduplicates by `url:` value if present, otherwise by full content
  - Returns `(deduped_list, removed_count)`; count shown in console
- [x] **`--follow` + `--detail-target` + `--max-follow`** — deep detail crawl
  - After listing crawl, `_extract_follow_urls()` parses `url:` lines from extracted items
  - `_merge_with_details()` concatenates listing + detail text by URL match, joining ALL detail items (not just first)
  - Combined schema = listing fields + union of all detail page fields
- [x] **Lenient validation** — `strict=False` param on `_validate_selector()` + `extract()`
  - `_VALIDATION_PROMPT_LENIENT`: accepts partial field matches
  - Strict mode still used for listing crawls
- [x] **Integration tests** — `tests/test_integration.py` (12 tests, all passing)
  - UC-01: link extraction from mock HTML
  - UC-02: structured data extraction with mocked LLM
  - UC-03: deduplication by URL and by content
  - UC-04: JS-required auto-upgrade httpx→Playwright
  - UC-05: follow URL extraction and result merging
  - UC-06: strict vs lenient validation
- [x] **`datapulse jobs` polish** — Output column shows file path or `stdout (format)`
- [x] **`job.py` `list_all()`** — returns `output_path` and `output_format` fields

**Total tests: 66 passing (54 existing + 12 new integration tests)**

---

## ✅ Phase 4.1 — Detail Page Extraction Overhaul (Complete)

- [x] **`skip_selector=True` on `extract()`** — bypasses CSS selector discovery for detail/profile pages
  - Detail pages have non-repeating contact data (website, email, social links) — selector approach always failed
  - Previously: 7–18 LLM calls per page wasted on selector discovery before falling through to text
  - Now: 1 LLM call per detail page (focused text → JSON extraction)
- [x] **`_focused_text_for_extraction()`** — pulls only the relevant section before sending to LLM
  - Finds the first `<aside>` that isn't a newsletter/footer aside
  - Prepends all `<a href>` links found in that section as `link: <url>` lines
  - Falls back to contact-section div (id containing "social"/"contact"/"links") then Trafilatura text
  - Avoids sending full page HTML (which causes LLM to pick up nav/footer URLs as the exhibitor's site)
- [x] **`_extract_from_text()`** — single LLM call for contact field extraction
  - Sends focused text with `_TEXT_EXTRACT_PROMPT`: extract specified fields, return flat JSON, null for missing
  - Returns `ExtractionResult(method="llm_text")` — handled correctly by formatter and progress display
- [x] **Schema union across detail pages** — replaces overwrite with accumulation
  - `detail_schema` now accumulates all unique fields seen across all concurrent detail page fetches
  - LinkedIn found on page 5 still becomes a column in the final CSV for all rows
- [x] **`aside` no longer stripped in `html_cleaner.py`**
  - Removed `"aside"` from `_STRIP_TAGS` — contact/social info on many sites lives in `<aside>` elements
  - Boilerplate sidebars are handled by the `_STRIP_ID_PATTERNS` pattern matching instead
- [x] **Debug HTML saved for detail pages** — `--debug` now captures follow phase HTML snapshots
- [x] **`method="llm_text"` displayed as `[cyan]text-llm[/]`** in follow phase progress output

**Verified on MWC Barcelona:**
```
datapulse run "get me exhibitors name and url from https://www.mwcbarcelona.com/exhibitors/ in csv file paginate 2 pages" \
  --follow --detail-target "social media links, email, and address" --max-follow 100
```
Output CSV: `name, url, website, stand, linkedin, twitter, facebook` — correct per-exhibitor values.

---

## 🔜 Phase 4.5 — External Anti-bot APIs (Optional)

- [ ] **ScraperAPI integration** — Layer 3 stubs exist, need real implementation
  - Real call: `http://api.scraperapi.com?api_key={key}&url={url}&render=true`
  - Auto-trigger: if httpx returns 403/429/503 AND Playwright also blocked
- [ ] **Zyte integration** — POST to `https://api.zyte.com/v1/extract`
  - Already stubbed in `scraper.py`

---

## 🔜 Phase 4.6 — Resume Polish

- [ ] **`datapulse resume`** — currently re-runs from scratch
  - Should continue from `job.urls_pending` without re-creating the Job
  - Load existing `job.selector_cache` to skip selector rediscovery
  - Append new results to existing `job.output_path` file

---

## 🔜 Phase 5 — REST API (Optional)

- [ ] **`datapulse/api.py`** — FastAPI app
  - `POST /run` — `{ url, target, format, max_urls, depth }` → starts async job
  - `GET /jobs` — list all jobs
  - `GET /jobs/{job_id}` — detail + results
  - `POST /resume/{job_id}` — resume partial job
- [ ] **`datapulse serve`** CLI command → Uvicorn on `localhost:8000`
- [ ] Background execution with SSE or polling for job status

---

## Quick Commands Reference

```bash
# Activate venv
source .env/bin/activate

# Run tests
.env/bin/pytest tests/ -v

# Verify LiteLLM proxy is up
python -c "from datapulse.utils import litellm_client; print(litellm_client.is_available())"

# ── Natural language query ──
datapulse run "get book titles and prices from https://books.toscrape.com"
datapulse run "give me all the links on https://news.ycombinator.com"

# ── Explicit flags ──
datapulse run --url https://books.toscrape.com --target "book title and price" --format json --output books.json
datapulse run --url https://books.toscrape.com --format csv --output books.csv

# ── JS/SPA pages (Playwright) ──
datapulse run --url "https://example.com/catalog" --target "name and price" --playwright --format json --output out.json

# ── Client-side pagination (Algolia, Angular, React) ──
datapulse run --url "https://www.mwcbarcelona.com/exhibitors/" --target "exhibitor name and URL" --paginate --format json --output mwc.json

# ── URLs with ! (zsh requires single quotes) ──
datapulse run --url 'https://cyberab.org/Catalog#!/c/s/Results/Format/list/Page/1/Size/200/Sort/NameAscending?typeId=7' --target "exhibitor name and address" --playwright --format json --output cyber.json

# ── Debug mode ──
datapulse run --url https://example.com --target "headlines" --debug
datapulse run --url https://example.com --dry-run

# ── Job management ──
datapulse jobs
datapulse inspect job_20260506_143201_abc123
datapulse resume job_20260506_143201_abc123
```

---

## All `run` Flags

| Flag | Default | Description |
|---|---|---|
| `--url` | — | Target URL (skips LLM intent parsing) |
| `--target` | — | What to extract — activates LLM selector mode |
| `--format` | `json` | `json` \| `csv` \| `md` \| `text` |
| `--output` | stdout | Output file — auto-placed in `output/` if no directory given |
| `--max-urls` | 25 | URL cap per job |
| `--depth` | from config | Crawl depth (0=seed only) |
| `--playwright` | off | Force Playwright layer (JS/SPA pages) |
| `--paginate` | off | Click through Next Page buttons — for JS-paginated sites (implies Playwright) |
| `--max-pages` | config | Max pages to click through with `--paginate` |
| `--follow` | off | Visit each extracted URL and scrape detail pages |
| `--detail-target` | — | What to extract from detail pages, e.g. `"email and social media"` |
| `--max-follow` | 50 | Max detail URLs to follow |
| `--dry-run` | off | Show parsed intent, skip fetch |
| `--debug` | off | Verbose logs + HTML snapshots to `output/debug/` |

---

## LLM Backend Priority

| Condition | Backend Used |
|---|---|
| `LLM_BASE_URL` + `LLM_API_KEY` set in `secrets.env` | **mueen-80b via LiteLLM** — all LLM tasks |
| `ANTHROPIC_API_KEY` set, LiteLLM unreachable | Claude Haiku — selector + schema + validation |
| Ollama running, both above unavailable | qwen2.5:1.5b — all LLM tasks |
| None available | Text-only mode — cleaned text, no structured extraction |

---

## LLM Config (`secrets.env`)

```bash
# Generic OpenAI-compatible (works with LiteLLM, OpenAI, Anthropic)
LLM_BASE_URL=http://10.8.124.144:4000
LLM_API_KEY=sk-my-litellm-key-2026
LLM_MODEL=mueen-80b

ANTHROPIC_API_KEY=          # optional direct Anthropic fallback
SCRAPERAPI_KEY=             # optional anti-bot proxy
ZYTE_API_KEY=               # optional anti-bot proxy
```

Test the raw API:
```bash
curl -s http://10.8.124.144:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-my-litellm-key-2026" \
  -H "Content-Type: application/json" \
  -d '{"model":"mueen-80b","messages":[{"role":"user","content":"say hi"}],"max_tokens":20}' \
  | python3 -m json.tool
```

---

## Dependencies (key versions)

| Package | Version | Purpose |
|---|---|---|
| anthropic | 0.99.0 | Claude Haiku API (optional fallback) |
| httpx | 0.28.1 | Fast static scraping + LiteLLM/Ollama HTTP client |
| playwright | 1.59.0 | JS/dynamic page scraping + pagination |
| trafilatura | 2.0.0 | Main content text extraction (text-only mode) |
| beautifulsoup4 | 4.14.3 | HTML parsing + selector application |
| typer | 0.25.1 | CLI framework |
| rich | 15.0.0 | Terminal output, progress bars, ASCII banner |
| PyYAML | 6.0.3 | Config file parsing |
| python-dotenv | 1.2.2 | `secrets.env` loading |
| fastapi | 0.136.1 | REST API (Phase 5, not yet active) |
| pytest | 9.0.3 | Test runner |
