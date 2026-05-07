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
   │    ✅ Done              │  BS4 clean → recursive semantic chunks
   │                         │  → LLM selector discovery (all chunks tried)
   │                         │  → Validation gate (5-sample LLM verify)
   │                         │  → BeautifulSoup programmatic extraction
   │                         │  → LLM schema inference (field names)
   └──────┬────────────────┘
          │  ExtractionResult { items[], selector_used, schema_fields, method }
   ┌──────▼────────────────┐
   │ 5. Formatter            │  datapulse/modules/formatter.py
   │    ✅ Done              │  JSON | CSV | Markdown table | plain text
   │                         │  + metadata: url, timestamp, item_count, method
   └─────────────────────────┘
```

**LLM backends (auto-selected in priority order):**
1. **Any OpenAI-compatible provider** — if `LLM_BASE_URL` + `LLM_API_KEY` set in `secrets.env` (primary; currently Google Gemini)
2. **Anthropic Claude Haiku** — if `ANTHROPIC_API_KEY` set and primary LLM not configured
3. **Ollama (local)** — if Ollama running and both above unavailable
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
│   ├── intent_parser.py     Module 1 — LLM/Ollama NL → Intent + heuristic fallback
│   │                        Extracts output_format + output_file from NL query via LLM prompt
│   ├── scope_guard.py       Module 2 — URL dedup, domain filter, cap, deep queue
│   ├── scraper.py           Module 3 — httpx / Playwright / ScraperAPI / Zyte
│   │                        _is_js_required(): auto-upgrades httpx→Playwright for JS-required pages
│   │                        _paginate_playwright(): click Next, dismiss consent, combine pages
│   │                        _dismiss_consent(): handles OneTrust and common cookie popups
│   ├── extractor.py         Module 4 — clean/chunk/LLM/validate/extract
│   │                        Tries up to max_chunk_attempts (default 50) chunks; exits early on first valid selector
│   └── formatter.py         Module 5 — JSON/CSV/MD/text renderer + metadata
│                            _normalise_items(): key-value prefix parsing (name:/url: lines)
│
└── utils/
    ├── llm_client.py        OpenAI-compatible LLM client — call(), is_available(), LLM_CHAT_PATH support
    ├── ollama_client.py     Ollama HTTP wrapper (fallback) — generate(), is_available()
    ├── html_cleaner.py      BS4 HTML cleaning — strips scripts/nav/ads/onetrust
    │                        Two-phase decompose to avoid NoneType crash on orphaned children
    ├── chunker.py           Recursive semantic block splitter (MAX_CHUNK_CHARS=6000)
    │                        No depth limit — drills until children fit, handles 22+ deep SPAs
    ├── validator.py         CSS selector application — anchors emit "name:\nurl:" format
    └── retry.py             @async_retry exponential backoff decorator

tests/
├── test_scraper.py          Phase 1: scraper, links, cleaner, chunker, job, retry
├── test_intent_parser.py    Phase 1+3: stub + LLM path + heuristic
├── test_extractor.py        Phase 2: selector discovery, validation, pipeline (mocked)
└── test_formatter.py        Phase 2: JSON/CSV/MD/text output

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
   `LLM_CHAT_PATH` overrides the completions endpoint path — needed for Google Gemini
   (`/chat/completions`) vs standard providers (`/v1/chat/completions`, the default).

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

### ✅ Phase 3.5 — Generic LLM Client + Google Gemini (Complete)
- [x] `utils/llm_client.py` — OpenAI-compatible `httpx` wrapper (renamed from `litellm_client.py`)
- [x] `config.py` — `LLM_*` env var properties (primary) + `LITELLM_*` fallback; removed deprecated `litellm_*` aliases
- [x] `extractor.py` — `llm_client` first in `_llm_call()` routing; `has_llm` flag
- [x] `intent_parser.py` — `_parse_with_llm()` (renamed from `_parse_with_litellm()`)
- [x] `secrets.env` — switched to Google Gemini free tier (`gemini-3.1-flash-lite`), added `LLM_CHAT_PATH`
- [x] `llm_client.py` — `LLM_CHAT_PATH` env var for provider-specific completions path
- [x] `llm_client.py` — `is_available()` checks config vars only (no HTTP ping — works for cloud APIs)

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

## 🔜 Phase 4 — Anti-bot + Resume + Polish

### Tasks

- [ ] **ScraperAPI integration** — Layer 3 exists but minimal
  - Real call: `http://api.scraperapi.com?api_key={key}&url={url}&render=true`
  - Cloudflare detection: check `cf-ray` header + challenge text patterns
  - Auto-trigger: if httpx returns 403/429/503 AND Playwright also blocked

- [ ] **Zyte integration** — POST to `https://api.zyte.com/v1/extract`

- [ ] **`datapulse resume`** — currently re-runs from scratch
  - Should continue from `job.urls_pending` without re-creating the Job
  - Load existing `job.selector_cache` to skip selector rediscovery
  - Append new results to existing `job.output_path` file

- [ ] **Actionable error messages** for common failures:
  - 403/429: "Try `--playwright` or add SCRAPERAPI_KEY to secrets.env"
  - No items extracted: "Try a more specific `--target` description"
  - LLM unreachable: "Check LLM_BASE_URL / LLM_API_KEY in secrets.env"
  - Ollama 404 (model missing): "Run: ollama pull qwen2.5:1.5b"

- [ ] **Deduplication in `--paginate` mode** — featured items repeat on every page
  (e.g. MWC: 1000 total, 844 unique). Add dedup by name or URL in formatter.

- [ ] **Integration tests** matching spec Section 6:
  - UC-01: link extraction from `https://news.ycombinator.com`
  - UC-02: structured data from `https://books.toscrape.com`
  - UC-03: deep article extraction (mock server)
  - UC-04: JS infinite scroll (Playwright path)

- [ ] **`datapulse jobs` polish** — show selector used, output file path

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

# Verify LLM is configured
.env/bin/python -c "from datapulse.utils import llm_client; print(llm_client.is_available())"

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
| `--dry-run` | off | Show parsed intent, skip fetch |
| `--debug` | off | Verbose logs + HTML snapshots to `output/debug/` |

---

## LLM Backend Priority

| Condition | Backend Used |
|---|---|
| `LLM_BASE_URL` + `LLM_API_KEY` set in `secrets.env` | **Configured provider** (currently Gemini `gemini-3.1-flash-lite`) |
| `ANTHROPIC_API_KEY` set, primary LLM not configured | Claude Haiku — selector + schema + validation |
| Ollama running, both above unavailable | Local model (e.g. `gemma3:4b`, `qwen2.5-coder:7b`) |
| None available | Text-only mode — cleaned text, no structured extraction |

---

## LLM Config (`secrets.env`)

```bash
# Generic OpenAI-compatible (works with LiteLLM, OpenAI, Anthropic)
LLM_BASE_URL=http://10.8.124.144:4000
LLM_API_KEY=sk-my-litellm-key-2026
LLM_MODEL=mueen-80b
LLM_CHAT_PATH=v1/chat/completions

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
