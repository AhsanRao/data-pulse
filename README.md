# DataPulse

**AI-Driven Intent-Based Web Extraction Agent**

DataPulse turns a plain-English sentence into structured data. You say what you want — DataPulse figures out how to get it.

```bash
datapulse run "get book titles and prices from https://books.toscrape.com"
datapulse run "give me all the links on https://news.ycombinator.com"
datapulse run "title and summary of each article on https://blog.example.com"
```

Runs locally. No cloud account required. Uses a local Ollama model for intent parsing and falls back to Ollama for extraction when no Anthropic key is configured.

---

## Quick Start

```bash
# 1. Clone and create venv
git clone <repo>
cd data-pulse
python3.14 -m venv .env      # or: python3 -m venv .env
source .env/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -e .
playwright install chromium

# 3. Install and start Ollama (local AI model)
brew install ollama
brew services start ollama
ollama pull qwen2.5:1.5b     # one-time, ~986 MB

# 4. (Optional) Add Anthropic key for higher-quality extraction
cp .env.example secrets.env
# edit secrets.env → ANTHROPIC_API_KEY=sk-ant-...

# 5. Run
datapulse run "get book titles and prices from https://books.toscrape.com"
```

---

## How It Works

DataPulse runs a 5-module pipeline on every query:

```
Your query
    │
    ▼
1. Intent Parser    — Ollama qwen2.5:1.5b parses: URL, what to extract, how deep to crawl
    │
    ▼
2. Scope Guard      — Enforces max URLs, domain filter, deduplication
    │
    ▼
3. Scraper          — Layer 1: httpx (fast, static HTML)
                      Layer 2: Playwright (JS pages, infinite scroll)
                      Layer 3: ScraperAPI / Zyte (bot-protected sites)
    │
    ▼
4. Extractor        — Trafilatura cleans HTML → LLM finds CSS selector →
                      Validation gate → BeautifulSoup extracts all items
    │
    ▼
5. Formatter        — Outputs JSON / CSV / Markdown / plain text + metadata
```

**LLM calls per job: 1–3 total.** Extraction itself runs programmatically (BeautifulSoup) — zero per-item LLM calls.

### LLM backends (auto-selected)

| Priority | Backend | Used for |
|---|---|---|
| 1st | Anthropic Claude Haiku | Selector discovery, validation, schema (if `ANTHROPIC_API_KEY` set) |
| 2nd | Ollama qwen2.5:1.5b | Intent parsing always; extraction when no Anthropic key |
| Fallback | Text-only | Full-page cleaned text (Trafilatura) |

---

## Usage

### Natural language queries (Phase 3)

```bash
# Structured data extraction
datapulse run "get book title and price from https://books.toscrape.com"

# URL listing
datapulse run "give me all the links on https://news.ycombinator.com"

# Deep crawl — follow links and extract from each page
datapulse run "title and summary of each article on https://blog.example.com"
```

### Explicit flags (faster, no Ollama needed for intent)

```bash
datapulse run --url https://books.toscrape.com \
  --target "book title and price" \
  --format json

datapulse run --url https://quotes.toscrape.com \
  --target "quote text and author" \
  --format csv \
  --output quotes.csv

# Full-page text — no LLM required at all
datapulse run --url https://example.com/article --format text
```

### Force Playwright (JS-rendered pages)

```bash
datapulse run "job listings from https://example-jobs.io/listings" --playwright
```

### Dry run (parse intent only)

```bash
datapulse run "get prices from https://example.com" --dry-run
```

---

## CLI Reference

| Command | Description |
|---|---|
| `datapulse run "<query>"` | Extract data from a natural language query |
| `datapulse run --url <URL>` | Extract data from a URL directly |
| `datapulse jobs` | List all recent jobs |
| `datapulse inspect <job_id>` | Show job details and selector cache |
| `datapulse resume <job_id>` | Resume a partial/interrupted job |
| `datapulse config` | Open config file in default editor |

### `run` flags

| Flag | Default | Description |
|---|---|---|
| `--url` | — | Target URL (skips Ollama intent parsing) |
| `--target` | — | What to extract — activates LLM selector mode |
| `--format` | `json` | `json` \| `csv` \| `md` \| `text` |
| `--output` | stdout | Write output to file path |
| `--max-urls` | 25 | URL cap per job |
| `--depth` | from intent | Crawl depth (0=seed only, 2=deep) |
| `--playwright` | off | Force Playwright layer |
| `--dry-run` | off | Show parsed intent, skip fetch |
| `--debug` | off | Verbose logging to stderr + log file |

---

## Configuration

`datapulse.config.yaml` — all settings have sensible defaults:

```yaml
scraping:
  max_urls: 25            # global URL cap per job
  depth: 1                # 0=seed only, 1=follow links, 2=deep
  concurrency: 4          # parallel workers
  timeout_seconds: 30
  same_domain_only: true

playwright:
  scroll_increment_px: 500
  scroll_pause_ms: 800
  headless: true
  max_scroll_attempts: 30

llm:
  local_model: qwen2.5:1.5b          # Ollama model for intent parsing
  selector_model: claude-haiku-4-5-20251001  # used if ANTHROPIC_API_KEY set
  output_model: claude-haiku-4-5-20251001
  max_retries: 3

output:
  default_format: json
  include_metadata: true
```

API keys go in `secrets.env` (never in the config file):

```
ANTHROPIC_API_KEY=sk-ant-...   # optional
SCRAPERAPI_KEY=                # optional — for bot-protected sites
ZYTE_API_KEY=                  # optional — alternative anti-bot proxy
```

> **Note:** The virtual environment lives in `.env/`. Because this conflicts with the
> usual `.env` dotenv filename, DataPulse reads API keys from `secrets.env` instead.

---

## Job Persistence

Every job is saved to `~/.datapulse/jobs/<job_id>.json`. This enables:

- `datapulse inspect <job_id>` — see what was extracted and which selector was used
- `datapulse resume <job_id>` — continue from the last checkpoint
- Selector caching — pages with the same template reuse the discovered selector

LLM call logs: `~/.datapulse/logs/llm_calls.log`

---

## Scraping Layers

| Layer | When used | Requirement |
|---|---|---|
| httpx | Always tried first (fast, static HTML) | None |
| Playwright | httpx returns bot block / `--playwright` flag | `playwright install chromium` |
| ScraperAPI | Cloudflare detected, `SCRAPERAPI_KEY` set | `SCRAPERAPI_KEY` in `secrets.env` |
| Zyte | ScraperAPI unavailable, `ZYTE_API_KEY` set | `ZYTE_API_KEY` in `secrets.env` |

---

## Development

### Run tests

```bash
.env/bin/pytest tests/ -v
# 53 tests, all passing
```

### Project structure

```
datapulse/
├── main.py              CLI (Typer) — all commands + crawl loop
├── job.py               Job dataclass + JSON persistence
├── config.py            Config loader (YAML + secrets.env)
├── modules/
│   ├── intent_parser.py Module 1 — Ollama NL→Intent + heuristic fallback
│   ├── scope_guard.py   Module 2 — URL dedup, domain filter, depth queue
│   ├── scraper.py       Module 3 — httpx / Playwright / ScraperAPI / Zyte
│   ├── extractor.py     Module 4 — clean + chunk + LLM + BS4 extraction
│   └── formatter.py     Module 5 — JSON / CSV / MD / text output
└── utils/
    ├── ollama_client.py Ollama HTTP wrapper (shared by all LLM callers)
    ├── html_cleaner.py  Trafilatura + BS4 fallback
    ├── chunker.py       Semantic block splitter
    ├── validator.py     CSS selector application
    └── retry.py         @async_retry decorator
```

### Build phases

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ Done | Scraper pipeline, job persistence, CLI skeleton |
| 2 | ✅ Done | LLM extraction (Anthropic Haiku), all output formats |
| 3 | ✅ Done | Ollama intent parser, NL queries, Ollama extraction fallback |
| 4 | 🔜 Next | ScraperAPI/Zyte integration, resume command, error messages |
| 5 | 🔜 | FastAPI REST server |

---

## License

© 2025 DataPulse. All rights reserved.
