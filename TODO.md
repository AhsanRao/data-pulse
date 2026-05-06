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
| API keys file | `secrets.env` (gitignored) — copy from `.env.example` |
| Ollama model | `qwen2.5:1.5b` — must be running: `brew services start ollama` |
| Job store | `~/.datapulse/jobs/` |
| LLM logs | `~/.datapulse/logs/llm_calls.log` |
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
   │    ✅ Phase 3 Done      │  Ollama qwen2.5:1.5b → structured Intent
   │                         │  Heuristic fallback when Ollama is down
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
   │                         │  Layer 2: Playwright headless (JS/scroll)
   │                         │  Layer 3: ScraperAPI / Zyte (anti-bot, optional)
   └──────┬────────────────┘
          │  raw HTML string
   ┌──────▼────────────────┐
   │ 4. Extractor            │  datapulse/modules/extractor.py
   │    ✅ Done              │  Trafilatura clean → semantic chunks
   │                         │  → LLM selector discovery (Anthropic or Ollama)
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

**LLM backends (auto-selected):**
1. Anthropic Claude Haiku — if `ANTHROPIC_API_KEY` in `secrets.env`
2. Ollama qwen2.5:1.5b — if Ollama is running (fallback and intent parsing)
3. Text-only mode — if neither is available

---

## File Map

```
datapulse/
├── main.py                  CLI — Typer, all 5 commands, full NL + deep crawl loop
├── job.py                   Job dataclass, save/load/checkpoint, list_all
├── config.py                Config loader: datapulse.config.yaml + secrets.env
│
├── modules/
│   ├── intent_parser.py     Module 1 — Ollama NL → Intent + heuristic fallback
│   ├── scope_guard.py       Module 2 — URL dedup, domain filter, cap, deep queue
│   ├── scraper.py           Module 3 — httpx / Playwright / ScraperAPI / Zyte
│   ├── extractor.py         Module 4 — clean/chunk/LLM/validate/extract (Anthropic OR Ollama)
│   └── formatter.py         Module 5 — JSON/CSV/MD/text renderer + metadata
│
└── utils/
    ├── ollama_client.py     Ollama HTTP wrapper — generate(), parse_json_response(), is_available()
    ├── html_cleaner.py      Trafilatura wrapper + BS4 fallback
    ├── chunker.py           Semantic block splitter (6000 char chunks)
    ├── validator.py         CSS selector application via BS4
    └── retry.py             @async_retry exponential backoff decorator

tests/
├── test_scraper.py          Phase 1: scraper, links, cleaner, chunker, job, retry (12 tests)
├── test_intent_parser.py    Phase 1+3: stub + Ollama path + heuristic (19 tests)
├── test_extractor.py        Phase 2: selector discovery, validation, full pipeline (11 tests)
└── test_formatter.py        Phase 2: JSON/CSV/MD/text output (10 tests)

datapulse.config.yaml        User config (all settings + defaults)
secrets.env                  API keys (gitignored — copy from .env.example)
.env/                        Python 3.14 venv
requirements.txt             Pinned deps (.env/bin/pip freeze)
pyproject.toml               Package config, pytest settings
README.md                    User-facing documentation
TODO.md                      This file
```

---

## Key Design Decisions

1. **`.env` is the venv directory, not a dotenv file.**
   API keys: `secrets.env` (project root) or `~/.datapulse/.env`. `config.py` skips
   paths that are directories automatically via `p.is_file()` check.

2. **Always use `.env/bin/*` for all commands.**
   System Python is Homebrew 3.14 — packages are not there.

3. **LLM routing in `extractor._llm_call()`:**
   Checks `cfg.anthropic_api_key` first → falls back to Ollama → raises if neither.
   Same routing used for selector discovery, validation gate, and schema inference.

4. **Intent parser has two modes:**
   - Ollama available → `_parse_with_ollama()` → structured JSON from qwen2.5:1.5b
   - Ollama down → `_parse_heuristic()` → regex URL extraction + keyword intent detection
   Both return the same `Intent` dataclass — the rest of the pipeline is unaware.

5. **The crawl loop in `main._run_pipeline()` handles all intent types:**
   - `url_list`: collect links only, no extraction
   - `page_content` / `structured_data`: extract + format each URL
   - `deep_content`: extract + enqueue new links via `scope_guard.enqueue_discovered_links()`
   Concurrency controlled by `asyncio.Semaphore(concurrency)` (default 4).

6. **Selector cache in Job.selector_cache:**
   Pattern: `{url: selector}`. On resume or deep crawl, `_find_pattern_selector()` in
   `main.py` matches by domain so pages with the same template reuse the selector.

7. **Ollama format=json mode:**
   `ollama_client.generate()` always sends `"format": "json"` for deterministic output.
   For free-form responses use `generate_text()`.

8. **Playwright scroll** stops when `document.body.scrollHeight` stops growing across
   two consecutive increments. Cap: `playwright.max_scroll_attempts` (default 30).

---

## Build Status

### ✅ Phase 1 — Core Pipeline (Complete)

- [x] Project structure, pyproject.toml, requirements.txt, .gitignore
- [x] `Job` dataclass — JSON persistence, checkpoint, list/load, mark_url_done
- [x] `Config` loader — YAML + multi-location dotenv (skips `.env` directory)
- [x] `Scraper` — httpx fast path, Playwright scroll, ScraperAPI/Zyte stubs
- [x] `html_cleaner.py` — Trafilatura + BS4 fallback
- [x] `chunker.py` — semantic block splitter
- [x] `retry.py` — `@async_retry` decorator
- [x] `validator.py` — CSS selector application
- [x] CLI: `run`, `jobs`, `inspect`, `resume`, `config` commands

### ✅ Phase 2 — LLM Extraction (Complete)

- [x] `extractor.py` — full pipeline: clean → chunk → selector discovery →
  validation gate → programmatic extraction → schema inference
- [x] `formatter.py` — JSON, CSV, Markdown table, plain text + metadata
- [x] `main.py` — `--target` flag wires into extractor; selector cached in job
- [x] **35 tests passing**

### ✅ Phase 3 — Intent Parser + Local Model (Complete)

- [x] Ollama installed via Homebrew (`brew services start ollama`)
- [x] `qwen2.5:1.5b` model pulled (986 MB — download started, completes automatically)
- [x] `utils/ollama_client.py` — HTTP wrapper: `generate()`, `generate_text()`,
  `is_available()`, `list_models()`, `parse_json_response()`
- [x] `intent_parser.py` replaced stub — Ollama primary + heuristic fallback
- [x] `extractor.py` LLM routing — Anthropic → Ollama → text-only
- [x] `scope_guard.py` — depth-aware URL queue + `enqueue_discovered_links()`
- [x] `main.py` — full NL query path + concurrent deep crawl loop
- [x] **53/53 tests passing** (`.env/bin/pytest tests/ -v`)

> **To test end-to-end with Ollama:**
> ```bash
> source .env/bin/activate
> # Wait for model: ollama list  (should show qwen2.5:1.5b)
> datapulse run "get book titles and prices from https://books.toscrape.com"
> datapulse run "give me all the links on https://news.ycombinator.com"
> ```

---

## 🔜 Phase 4 — Anti-bot + Resume + Polish

### Tasks

- [ ] **ScraperAPI integration** — `scraper.py` Layer 3 exists but is minimal
  - Real call: `http://api.scraperapi.com?api_key={key}&url={url}&render=true`
  - Cloudflare detection: check `cf-ray` response header + challenge text patterns
  - Auto-trigger: if httpx returns 403/429/503 AND Playwright also blocked

- [ ] **Zyte integration** — POST to `https://api.zyte.com/v1/extract`

- [ ] **`datapulse resume`** — currently re-runs `_run_pipeline()` from scratch
  - Should continue from `job.urls_pending` without re-creating the Job
  - Load existing `job.selector_cache` to skip rediscovery
  - Append new results to existing `job.output_path` file

- [ ] **Actionable error messages** for common failures:
  - 403/429: "Try `--playwright` or add SCRAPERAPI_KEY to secrets.env"
  - No items extracted: "Try a more specific `--target` description"
  - Ollama 404 (model missing): "Run: ollama pull qwen2.5:1.5b"
  - Ollama connection refused: "Run: brew services start ollama"

- [ ] **Integration tests** matching spec Section 6:
  - UC-01: link extraction from `https://news.ycombinator.com`
  - UC-02: structured data from `https://books.toscrape.com`
  - UC-03: deep article extraction (needs a mock server to avoid network calls in CI)
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

## Test Coverage

```
tests/test_scraper.py       12 tests  — scraper, links, cleaner, chunker, job, retry
tests/test_intent_parser.py 19 tests  — URL extraction, heuristics, Ollama path (mocked)
tests/test_extractor.py     11 tests  — selector discovery, validation, pipeline (mocked)
tests/test_formatter.py     11 tests  — JSON, CSV, MD, text output

Total: 53/53 passing  (.env/bin/pytest tests/ -v)
```

---

## Quick Commands Reference

```bash
# Activate venv
source .env/bin/activate

# Start Ollama (required for NL queries and extraction without Anthropic key)
brew services start ollama

# Check model is ready
ollama list   # should show: qwen2.5:1.5b

# Run tests
.env/bin/pytest tests/ -v

# ── NL query (Phase 3) ──
datapulse run "get book titles and prices from https://books.toscrape.com"
datapulse run "give me all the links on https://news.ycombinator.com"
datapulse run "title and summary of each article on https://blog.example.com"

# ── --url flag (Phase 1/2) ──
datapulse run --url https://books.toscrape.com --target "book title and price" --format json
datapulse run --url https://books.toscrape.com --format csv --output books.csv
datapulse run --url https://books.toscrape.com --format text  # no LLM needed

# ── Job management ──
datapulse jobs
datapulse inspect job_20260506_143201_abc123
datapulse resume job_20260506_143201_abc123

# ── Debug ──
datapulse run --url https://example.com --target "headlines" --debug
datapulse run --url https://example.com --dry-run
```

---

## Ollama Setup (one-time)

```bash
# Install
brew install ollama

# Start as background service (auto-restarts on login)
brew services start ollama

# Pull the model (986 MB — one-time download)
ollama pull qwen2.5:1.5b

# Verify
ollama list
curl http://localhost:11434/api/tags
```

---

## LLM Backend Priority

| Condition | Backend Used |
|---|---|
| `ANTHROPIC_API_KEY` set in `secrets.env` | Claude Haiku (selector + schema + validation) |
| Ollama running + `qwen2.5:1.5b` available | Ollama (selector + schema + validation + intent) |
| Neither | Text-only mode (Trafilatura clean text, no structured extraction) |

Intent parsing **always** uses Ollama (or heuristic fallback) — never Anthropic.

---

## Dependencies (key versions, from `.env/bin/pip freeze`)

| Package | Version | Purpose |
|---|---|---|
| anthropic | 0.99.0 | Claude Haiku API (optional — Ollama fallback available) |
| httpx | 0.28.1 | Fast static scraping + Ollama HTTP client |
| playwright | 1.59.0 | JS/dynamic page scraping |
| trafilatura | 2.0.0 | Main content extraction |
| beautifulsoup4 | 4.14.3 | HTML parsing + selector application |
| typer | 0.25.1 | CLI framework |
| rich | 15.0.0 | Terminal output, progress bars |
| PyYAML | 6.0.3 | Config file parsing |
| python-dotenv | 1.2.2 | `secrets.env` loading |
| fastapi | 0.136.1 | REST API (Phase 5) |
| pytest | 9.0.3 | Test runner |

Full pinned list in `requirements.txt`.
