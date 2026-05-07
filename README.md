# DataPulse

**AI-Driven Intent-Based Web Extraction Agent**

DataPulse turns a plain-English sentence into structured data. You say what you want — DataPulse figures out how to get it.

```bash
datapulse run "get book titles and prices from https://books.toscrape.com"
datapulse run "give me all the links on https://news.ycombinator.com"
datapulse run --url https://path.rsaconference.com/... --target "exhibitor name and type" --playwright
```

---

## Quick Start

```bash
# 1. Clone and create venv
git clone <repo>
cd data-pulse
python3.14 -m venv .env
source .env/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -e .
playwright install chromium

# 3. Configure your LLM (see secrets.env for all options)
cp secrets.env.example secrets.env   # or edit secrets.env directly
# → set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

# 4. (Optional) Start Ollama for a free local fallback
brew install ollama && brew services start ollama
ollama pull qwen2.5:1.5b

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
1. Intent Parser    — LLM parses: URL, what to extract, how deep to crawl
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
4. Extractor        — Cleans HTML (strips nav/scripts/ads) → recursive chunker →
                      LLM finds CSS selector → validation gate →
                      BeautifulSoup extracts all items
    │
    ▼
5. Formatter        — Outputs JSON / CSV / Markdown / plain text + metadata
```

**LLM calls per job: 2–3 total** for selector discovery + validation + schema inference. Extraction itself runs programmatically — zero per-item LLM calls.

The job header shown at startup reflects everything the intent parser detected:

```
 Parsing intent…
 Job     job_20260507_133234_62f2a6
 URL     https://cyberab.org/Catalog#!/...
 Target  exhibitors name and addresses
 Layer   auto  ·  Format json  ·  Max URLs 25
 Output  output/cyberab_org.json
```

---

## LLM Backends

DataPulse uses any **OpenAI-compatible** provider — set three env vars in `secrets.env`:

```bash
LLM_BASE_URL=<endpoint>    # your provider's base URL
LLM_API_KEY=<key>          # your API key
LLM_MODEL=<model-name>     # model to use
```

### Provider examples

| Provider | `LLM_BASE_URL` | `LLM_MODEL` example |
|---|---|---|
| **LiteLLM proxy** | `http://your-server:4000` | `mueen-80b` |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini` |
| **Anthropic** | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` |
| **Ollama** (local) | auto-detected | `qwen2.5:1.5b` |

### Auto-selection priority

| Priority | Backend | Condition |
|---|---|---|
| 1st | OpenAI-compatible provider | `LLM_BASE_URL` + `LLM_API_KEY` set |
| 2nd | Anthropic direct SDK | `ANTHROPIC_API_KEY` set |
| 3rd | Ollama (local) | Ollama running, model pulled |
| Fallback | Text-only | Trafilatura full-page text |

---

## Usage

### Natural language queries

The intent parser extracts URL, target, format, and output file directly from your sentence — no flags needed:

```bash
datapulse run "get book title and price from https://books.toscrape.com"
datapulse run "give me all the links on https://news.ycombinator.com"
datapulse run "get exhibitor names and addresses in json file from https://cyberab.org/..."
datapulse run "save product names and prices as csv from https://books.toscrape.com"
```

JS-heavy pages (Angular, React, hashbang `#!/` URLs) are detected automatically and upgraded to Playwright — no `--playwright` flag needed.

### Explicit flags (no NL parsing needed)

```bash
datapulse run --url https://books.toscrape.com \
  --target "book title and price" \
  --format json \
  --output books.json          # → written to output/books.json

datapulse run --url https://quotes.toscrape.com \
  --target "quote text and author" \
  --format csv \
  --output quotes.csv          # → output/quotes.csv
```

### JS-rendered pages (SPAs)

```bash
datapulse run --url https://example-spa.com/catalog \
  --target "product name and price" \
  --playwright \
  --output products.json
```

### Client-side pagination (Algolia, React, Vue)

For sites where page navigation happens in JavaScript (URL doesn't change), use `--paginate`:

```bash
# Get all exhibitors from MWC Barcelona (20 pages × 50 items)
datapulse run --url https://www.mwcbarcelona.com/exhibitors/ \
  --target "exhibitor name and URL" \
  --paginate \
  --output mwc_exhibitors.json
```

`--paginate` automatically clicks through "Next Page" buttons, dismisses cookie consent popups, and combines all pages into a single extraction pass. Configure `playwright.max_pages` in `datapulse.config.yaml` (default: 20).

### Debug mode

```bash
datapulse run --url https://example.com --target "..." --debug
# Saves to:
#   output/<job_id>_<slug>_raw.html    — full Playwright HTML
#   output/debug/<job_id>_<slug>_clean.html  — after stripping scripts/nav/ads
#   .datapulse/logs/llm_calls.log      — every LLM prompt + response
#   .datapulse/logs/datapulse.log      — structured debug log
```

---

## CLI Reference

| Command | Description |
|---|---|
| `datapulse run "<query>"` | Extract from a natural language query |
| `datapulse run --url <URL>` | Extract from a URL directly |
| `datapulse jobs` | List recent jobs |
| `datapulse inspect <job_id>` | Show job details and selector cache |
| `datapulse resume <job_id>` | Resume a partial/interrupted job |
| `datapulse config` | Open config file in default editor |

### `run` flags

| Flag | Default | Description |
|---|---|---|
| `--url` | — | Target URL (skips LLM intent parsing) |
| `--target` | — | What to extract — activates LLM selector mode |
| `--format` | `json` | `json` \| `csv` \| `md` \| `text` |
| `--output` | stdout | Output file — auto-placed in `output/` if no directory given |
| `--max-urls` | 25 | URL cap per job |
| `--depth` | from config | Crawl depth (0=seed only) |
| `--playwright` | off | Force Playwright layer (required for SPAs) |
| `--paginate` | off | Click through Next Page buttons (JS-paginated sites, implies Playwright) |
| `--dry-run` | off | Show parsed intent, skip fetch |
| `--debug` | off | Verbose logs + HTML snapshots to `output/debug/` |

---

## Configuration

`datapulse.config.yaml` — all settings have sensible defaults:

```yaml
scraping:
  max_urls: 25
  depth: 1
  concurrency: 4
  timeout_seconds: 60
  same_domain_only: true

playwright:
  scroll_increment_px: 800
  scroll_pause_ms: 1500
  headless: true
  max_scroll_attempts: 60
  max_pages: 20            # max Next Page clicks with --paginate

llm:
  local_model: qwen2.5:1.5b
  selector_model: claude-haiku-4-5-20251001   # Anthropic direct SDK only
  max_chunk_attempts: 50  # exits early the moment a valid selector is found

output:
  default_format: json
  include_metadata: true
```

API keys and LLM config go in `secrets.env` (never commit this file):

```bash
LLM_BASE_URL=http://your-server:4000
LLM_API_KEY=your-key
LLM_MODEL=your-model

ANTHROPIC_API_KEY=sk-ant-...    # optional direct SDK fallback
SCRAPERAPI_KEY=                  # optional anti-bot proxy
ZYTE_API_KEY=                    # optional anti-bot proxy
```

> **Note:** The virtual environment lives in `.env/`. Because this conflicts with the usual `.env` dotenv filename, keys go in `secrets.env` instead.

---

## Output Files

All output files are written to the `output/` folder (git-ignored):

```
output/
├── books.json              ← --output books.json
├── quotes.csv
└── debug/                  ← only created with --debug
    ├── job_..._raw.html    ← full Playwright HTML
    └── job_..._clean.html  ← after stripping scripts/nav/ads
```

---

## Job Persistence

Every job is saved to `.datapulse/jobs/<job_id>.json`:

- `datapulse inspect <job_id>` — see what was extracted and which selector was used
- `datapulse resume <job_id>` — continue from the last checkpoint
- Selector caching — pages with the same domain template reuse the discovered selector

Logs:
- `.datapulse/logs/datapulse.log` — structured run log
- `.datapulse/logs/llm_calls.log` — every LLM prompt + response (always written)

---

## Scraping Layers

| Layer | When used | Requirement |
|---|---|---|
| httpx | Always tried first (fast, static HTML) | None |
| Playwright | httpx returns bot block or `--playwright` flag | `playwright install chromium` |
| ScraperAPI | Cloudflare detected, `SCRAPERAPI_KEY` set | Key in `secrets.env` |
| Zyte | ScraperAPI unavailable, `ZYTE_API_KEY` set | Key in `secrets.env` |

---

## Development

```bash
# Run tests
.env/bin/pytest tests/ -v        # 54 tests, all passing

# Run a single scrape
source .env/bin/activate
datapulse run --url https://books.toscrape.com --target "title and price" --debug
```

### Project structure

```
datapulse/
├── main.py              CLI (Typer) — all commands, crawl loop, UI
├── job.py               Job dataclass + JSON persistence
├── config.py            Config loader (YAML + secrets.env)
├── modules/
│   ├── intent_parser.py Module 1 — LLM NL→Intent + heuristic fallback
│   ├── scope_guard.py   Module 2 — URL dedup, domain filter, depth queue
│   ├── scraper.py       Module 3 — httpx / Playwright / ScraperAPI / Zyte
│   ├── extractor.py     Module 4 — clean + recursive chunk + LLM + BS4
│   └── formatter.py     Module 5 — JSON / CSV / MD / text output
└── utils/
    ├── litellm_client.py OpenAI-compatible LLM client (primary backend)
    ├── ollama_client.py  Ollama HTTP wrapper (local fallback)
    ├── html_cleaner.py   BS4 + Trafilatura HTML cleaning
    ├── chunker.py        Recursive semantic block splitter
    ├── validator.py      CSS selector application + sampling
    └── retry.py          @async_retry decorator
```

### Build phases

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ Done | Scraper pipeline, job persistence, CLI skeleton |
| 2 | ✅ Done | LLM extraction (selector discovery + validation), all output formats |
| 3 | ✅ Done | LLM intent parser, natural language queries |
| 3.5 | ✅ Done | LiteLLM / OpenAI-compatible provider support, project-local storage |
| 3.6 | ✅ Done | Recursive HTML chunker, html_cleaner crash fix, validation prompt fix |
| 3.7 | ✅ Done | Generic LLM env vars, output/ folder, debug HTML, clean logs + UI |
| 3.8 | ✅ Done | ASCII art banner, structured field extraction (name/url), `--paginate` for JS-paginated sites |
| 4 | 🔜 Next | ScraperAPI/Zyte real integration, `resume` polish, error messages |
| 5 | 🔜 | FastAPI REST server |

---

## License

© 2025 DataPulse. All rights reserved.
