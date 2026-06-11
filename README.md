# Multi-Country Car Sales Dashboard

A config-driven Python tool with two usage modes that share the same code:

- **Web App** (recommended for sharing): FastAPI + SQLite with a browser UI for uploading data, time filtering, MoM/YoY comparisons, and shareable public URLs. See [Section 2](#2-web-app).
- **CLI** (original mode): One-shot generation of static HTML/PNG/PDF to `output/`, suited for offline archiving or manual distribution. See [Section 3](#3-cli-usage).

Both modes read the same `config/countries.json` and the same normalisation logic — column mappings are written once and work everywhere.

---

## Quick demo

**Web dashboard** — upload a CSV, get interactive charts, MoM/YoY KPIs, per-country drill-down, PNG/PDF export:

```bash
pip install -r requirements.txt
uvicorn src.app:app --reload
# open http://localhost:8000
```

**Natural-language query** (`POST /chat`) — LLM dispatches Python tools, never generates SQL:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the highest sales growth in Q2?"}' | python3 -m json.tool
```

```json
{
  "answer": "Germany recorded the strongest Q2 growth at +18.4% QoQ, driven by Model Y
             which accounted for 62% of units sold. China followed at +11.2% QoQ."
}
```

The LLM picks one of 5 typed Python tools (`get_global_summary`, `get_country_detail`,
`compare_countries`, `get_top_models`, `get_trend`), calls it, and interprets structured
data — no raw SQL, no injection risk. 34/34 tests pass; chat tests run fully offline.

---

## Contents

1. [Installation](#1-installation)
2. [Web App](#2-web-app)
3. [CLI Usage](#3-cli-usage)
4. [Input Data Requirements](#4-input-data-requirements)
5. [Column Mapping on Upload](#5-column-mapping-on-upload)
6. [CLI Output](#6-cli-output)
7. [Sharing](#7-sharing)
8. [Adding / Editing a Country](#8-adding--editing-a-country)
9. [CLI Reference](#9-cli-reference)
10. [FAQ](#10-faq)
11. [Project Structure](#11-project-structure)
12. [Natural-Language Chat (`/chat`)](#12-natural-language-chat-chat)

---

## 1. Installation

```bash
cd car-sales-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Only needed for CLI PNG/PDF export:
playwright install chromium       # ~90 MB
```

Core dependencies: `pandas`, `openpyxl` (xlsx), `plotly`, `jinja2`, `fastapi`, `sqlalchemy`, `uvicorn`. `playwright` is only used for CLI PNG/PDF export — the Web App does not need it.

Natural-language chat (`/chat`) requires an Anthropic API key (see [Section 12](#12-natural-language-chat-chat)).

Run tests:

```bash
pytest tests/                     # period calculations + FastAPI routes + chat tool dispatch (offline mock)
```

---

## 2. Web App

Web App mode stores data in SQLite (supports incremental uploads), provides a browser UI (time filtering / MoM+YoY / multi-country select / PNG screenshot / PDF print / copy-link), and generates **publicly shareable URLs** (reads are public; writes require admin credentials).

### 2.1 Run locally

```bash
source .venv/bin/activate
uvicorn src.app:app --reload
# Open http://localhost:8000
```

On first visit the database is empty — the page will prompt you to go to `/upload`. The admin login is at `/admin/login`. **Default credentials**:

| Field | Default | Override |
|---|---|---|
| Username | `Kirby` | env var `ADMIN_USERNAME` |
| Password | `Kirby123` | env var `ADMIN_PASSWORD` |
| Session signing secret | value of `ADMIN_PASSWORD` | env var `SESSION_SECRET` (optional) |

- Data is stored at `car-sales-dashboard/data/app.db` by default — it does not touch `output/`
- Custom path: `export DB_PATH=/somewhere/app.db`
- **Always change the default password before deploying publicly** (see next section).

### 2.2 Deploy to Render (free tier)

The repo already includes a [Dockerfile](Dockerfile) and [render.yaml](render.yaml).

1. Push `car-sales-dashboard/` to a GitHub repository
2. Go to https://render.com/ → **New** → **Blueprint** → select the repo
3. Render reads `render.yaml` and will:
   - Build the image from the Dockerfile
   - Mount a 1 GB persistent disk at `/var/data` (SQLite lives here — survives restarts)
4. After the build you get a `https://<your-app>.onrender.com` URL
5. **Render Dashboard → Environment** — add two variables to override defaults:
   - `ADMIN_USERNAME` = your username
   - `ADMIN_PASSWORD` = a strong password (16+ random characters recommended)
6. Open `/admin/login`, log in → redirected to `/upload` → upload the first file → done

Share the root URL (or a URL with query params) with anyone. Visitors **do not see the Upload button** (they're not logged in) but can still use **Export PNG / Export PDF / Copy link**.

### 2.3 Route reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Overview page — supports `?start=...&end=...&granularity=M&countries=USA&countries=France` |
| `GET` | `/country/{slug}` | Country detail page — same filter params |
| `GET` | `/upload` | Upload page (admin only) |
| `POST` | `/upload/preview` | Parse file + return column-mapping suggestion (JSON) |
| `POST` | `/upload/commit` | Persist rows to DB |
| `POST` | `/admin/batches/{id}/delete` | Roll back an upload batch |
| `POST` | `/admin/login` | Set session cookie |
| `GET` | `/healthz` | Liveness probe |
| `POST` | `/chat` | Natural-language query (requires `ANTHROPIC_API_KEY`) |

### 2.4 MoM / YoY comparisons

KPI cards show two badges based on the current granularity:

| Granularity | Badge A | Badge B |
|---|---|---|
| Month (M) | **MoM** (vs previous month) | **YoY** (vs same month last year) |
| Quarter (Q) | **QoQ** (vs previous quarter) | **YoY** (vs same quarter last year) |
| Year (Y) | **YoY** (vs previous year) | — |

Calculations are done on pandas `Period` objects — **gaps in the data are handled correctly** (uses `period_range` to fill, then shifts, and outputs NaN for missing periods rather than misaligning values).

---

## 3. CLI Usage

**Most common** (generate HTML + PNG + PDF):

```bash
source .venv/bin/activate
python -m src.cli --input "../HTML/Auto Sales data.csv"
open output/index.html
```

**HTML only, no image export** (fast, a few seconds):

```bash
python -m src.cli --input data/*.xlsx --formats html
```

**Specific countries only**:

```bash
python -m src.cli --input data/q1.xlsx --countries USA France China
```

**Multiple files** (merged automatically):

```bash
python -m src.cli --input data/q1.xlsx data/q2.xlsx data/q3.csv
```

---

## 4. Input Data Requirements

The tool is **flexible** about input Excel/CSV files:

- Supported extensions: `.xlsx` / `.xls` / `.csv`
- Header row: auto-detected (scans the first 10 rows for the best column-name match)
- A file can contain one country or multiple countries mixed together
- Column names do not need to be consistent (a candidate list is defined in config); matching is case-insensitive

**Required fields** (after normalisation, at minimum date and sales must be present; others are optional):

| Canonical field | Meaning | Example column names |
|---|---|---|
| `date` | Order / sale date | ORDERDATE, Order Date, 日期, 销售日期 |
| `country` | Country | COUNTRY, Country, 国家 |
| `sales` | Sales amount | SALES, Amount, Revenue, 销售额 |
| `quantity` | Units sold | QUANTITYORDERED, Qty, Quantity, 数量 |
| `model` | Vehicle model | PRODUCTLINE, Model, 车型 |

Unrecognised fields fall back to the `default` config in `config/countries.json`. Rows missing required fields are skipped with a log warning.

---

## 5. Column Mapping on Upload

Uploaded files **do not need to match a template exactly**. Two ways to adapt the mapping:

### A. Interactive UI (recommended — Web App)

1. Open `/upload`, select a file, click **Preview**
2. The system will:
   - Auto-detect the header row
   - Read all countries present in the data
   - Guess the most likely raw column name for each canonical field (date / sales / quantity / model) per country, with other raw columns listed as dropdown candidates
   - Show a 5-row data preview for reference
3. Use the dropdowns to **correct any mismatches**, then click **Commit**
4. Insertion uses SQLite `ON CONFLICT DO NOTHING` — the same `(date, country, model, sales, quantity, source_file)` combination is deduplicated automatically, so uploading the same file twice does not double-count

Canonical fields:

| Field | Required | Notes |
|---|---|---|
| `date` | ✅ | Supports `%Y-%m-%d` / `%d.%m.%Y` / `%m-%d-%Y` and common formats; also handles Excel date cells |
| `sales` | ✅ | Numeric sales amount |
| `quantity` | | Units sold; defaults to 0 if missing |
| `model` | | Vehicle model name; defaults to `Unknown` if missing |

### B. Edit `config/countries.json` (advanced / CLI batch)

If you regularly process the same file format, hard-code the mapping in the config — both CLI and Web App read the same file. See [Section 8](#8-adding--editing-a-country) for examples. Mappings in `column_map` act as a **candidate list** during auto-matching; the UI preview will prefer any candidate that exists in the actual file.

### Mapping flow

```
Upload → auto-detect header → read raw columns → for each country + field:
                                                  ├─ explicit user override? → use it
                                                  ├─ match in config column_map? → use it
                                                  └─ neither → fuzzy match on field name
```

---

## 6. CLI Output

After a run, `output/` looks like:

```
output/
├── index.html                    # Overview: all country cards + comparison chart
├── style.css
├── countries/
│   ├── usa.html                  # Country detail page
│   ├── france.html
│   └── ...
└── exports/
    ├── overview.png              # Screenshot of the overview page
    ├── overview.pdf              # PDF of the overview page
    ├── usa.png
    ├── usa.pdf
    └── ...                       # PNG + PDF for each country
```

- **HTML** is fully interactive: hover for values, double-click to zoom, click cards to drill down
- **PNG** is a full-page screenshot at 2880 px wide (retina), ready to paste into PowerPoint or docs
- **PDF** is A4 layout, suitable for printing or archiving

---

## 7. Sharing

Choose based on what the recipient needs:

### A. Just an image or report → send PNG or PDF

Send the relevant file from `output/exports/` by email or message. Each file is fully self-contained and viewable offline.

- Global snapshot: `overview.png` or `overview.pdf`
- Country detail: `{country}.png` or `{country}.pdf`

### B. Interactive page (hover, drill-down) → zip the whole output folder

```bash
cd car-sales-dashboard
zip -r dashboard.zip output
```

Send `dashboard.zip`; the recipient unzips and opens `output/index.html`.

**⚠️ Note**: the HTML loads Plotly from a CDN (`cdn.plot.ly`), so the recipient needs internet access. For fully offline HTML, edit [templates/base.html.j2](templates/base.html.j2) to load a local copy of `plotly.min.js`, or change `include_plotlyjs=False` to `include_plotlyjs='inline'` in `_PLOTLY_KW` in [src/renderer.py](src/renderer.py) (each HTML file grows by ~3 MB but works completely offline).

### C. Shareable URL (multi-person, mobile-friendly) → static hosting

`output/` is a standard static site that any static host can serve:

| Service | Steps | Time |
|---|---|---|
| **Netlify Drop** | Open https://app.netlify.com/drop, drag the `output/` folder in | 30 s |
| **Cloudflare Pages** | `npx wrangler pages deploy output` | 1 min |
| **GitHub Pages** | Push to repo → Settings → Pages → source: `/output` | 2 min |
| **Vercel** | `vercel deploy output` | 1 min |

Netlify Drop is the fastest and requires no account — good for one-off sharing. For permanent hosting with a custom domain, use Pages or Vercel.

### D. Regular updates for the same audience → Option C + re-run

If the same dashboard needs to be updated weekly or monthly for the same group, use option C: share a fixed URL, and re-upload `output/` after each `python -m src.cli ...` run.

---

## 8. Adding / Editing a Country

Open [config/countries.json](config/countries.json) and add an entry under `countries`. **Only specify what differs from `default`**.

### Most common: localised date format + currency

```json
"Germany": {
  "display_name": "Deutschland",
  "currency": "EUR",
  "date_format": "%d.%m.%Y"
}
```

### Different column names (e.g. Chinese headers)

```json
"China": {
  "display_name": "中国",
  "currency": "CNY",
  "column_map": {
    "date":     ["销售日期", "日期"],
    "sales":    ["销售金额", "金额"],
    "quantity": ["销量", "数量"],
    "model":    ["车型名称", "车型"]
  }
}
```

Keys you specify override the defaults; unspecified keys inherit from `default`. No restart needed — re-running the CLI picks up changes immediately.

`date_format` uses Python `strptime` syntax: `%Y-%m-%d` / `%d/%m/%Y` / `%m-%d-%Y`, etc.

---

## 9. CLI Reference

```
python -m src.cli --input FILE [FILE ...] [options]
```

| Argument | Short | Default | Description |
|---|---|---|---|
| `--input` | `-i` | (required) | One or more input file paths |
| `--output-dir` | `-o` | `./output` | Output directory |
| `--formats` | `-f` | `html png pdf` | Output types: `html` / `png` / `pdf`, space-separated |
| `--countries` | | (all) | Render specific countries only, e.g. `--countries USA France` |
| `--config` | `-c` | `config/countries.json` | Custom config file |
| `--top-n` | | `10` | Top models per country |
| `--log-level` | | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## 10. FAQ

**Q: I ran the CLI but `output/exports/` is empty.**
You forgot `playwright install chromium`. Run it once and retry. If you only need HTML, pass `--formats html` to skip exports.

**Q: A country's data isn't showing up.**
Check the logs for `Skipping N rows ... missing required columns`. The date or sales column wasn't recognised. Add the actual column name to `column_map.date` / `column_map.sales` for that country in `config/countries.json`.

**Q: Dates all fail to parse.**
The log will say `date_format %s matched <50% of rows; falling back to infer`. Fix `date_format` for that country in the config, or remove it entirely to let pandas infer (handles most common formats automatically).

**Q: How do I change colours or fonts?**
Edit `colors.primary` / `colors.palette` / `font.family` in [config/theme.json](config/theme.json). Chart-level layout is in `_BASE_LAYOUT` in [src/renderer.py](src/renderer.py).

**Q: The exported PNG is too tall / too large.**
Edit `VIEWPORT` in [src/exporter.py](src/exporter.py) (default `1440×900`, `device_scale_factor=2`). Dropping to `{"width": 1200, "height": 800}` with `device_scale_factor=1` significantly reduces file size.

**Q: How do I register this as a Claude Code skill?**
The repo root already has [SKILL.md](SKILL.md). Copy the entire `car-sales-dashboard/` directory to `~/.claude/skills/` and Claude Code will discover it automatically.

---

## 11. Project Structure

```
car-sales-dashboard/
├── README.md
├── SKILL.md                # Claude Code skill metadata
├── requirements.txt
├── Dockerfile              # Web App deployment
├── render.yaml             # Render Blueprint config
├── config/
│   ├── countries.json      # Field mappings + per-country overrides
│   └── theme.json          # Colours, fonts
├── src/
│   ├── loader.py           # Read xlsx/csv, auto-detect header row
│   ├── normalizer.py       # Apply config and normalise; detect_columns / normalize_with_mapping
│   ├── analyzer.py         # pandas aggregation + date filtering + granularity
│   ├── periods.py          # MoM / QoQ / YoY calculations
│   ├── filters.py          # Web query-param parsing
│   ├── renderer.py         # Plotly charts + Jinja2 rendering
│   ├── exporter.py         # Playwright → PNG + PDF (CLI only)
│   ├── db.py               # SQLAlchemy models + session (Web App)
│   ├── chat.py             # Claude tool-use dispatch (POST /chat)
│   ├── app.py              # FastAPI entry point (Web App)
│   └── cli.py              # Entry point (CLI mode)
├── templates/
│   ├── base.html.j2
│   ├── overview.html.j2 / _overview_body.html.j2
│   ├── country.html.j2  / _country_body.html.j2
│   ├── upload.html.j2 / login.html.j2 / empty.html.j2
│   └── partials/
│       ├── filter_bar.html.j2
│       ├── kpi_cards.html.j2
│       └── trend_table.html.j2
├── assets/
│   └── style.css
├── tests/
│   ├── test_periods.py     # MoM / QoQ / YoY calculations
│   ├── test_api.py         # FastAPI routes / upload / auth
│   └── test_chat.py        # POST /chat tool dispatch (offline mock)
├── data/                   # Web App SQLite database (gitignored)
└── output/                 # CLI-generated output
```

Data flow:

```
CLI mode:   input files → loader → normalizer → analyzer → renderer → exporter

Web mode:   browser → app.py → loader → normalize_with_mapping → db.py (SQLite)
                ↑                                                  ↓
         filter_bar + HTMX  ←  renderer  ←  analyzer  ←  load_sales_df

Chat mode:  POST /chat → chat.py → Claude (tool-use) → analyzer.py tools → structured result → text answer
```

---

## 12. Natural-Language Chat (`/chat`)

`POST /chat` accepts a natural-language question (English or Chinese) and answers it using **Claude tool-use** to dispatch local analysis functions.

### Setup

Create a `.env` file in the project root (see `.env.example`):

```bash
ANTHROPIC_API_KEY=your_key_here
# Optional: override the default model (default: claude-sonnet-4-6)
# CHAT_MODEL=claude-haiku-4-5-20251001
```

### Examples

```bash
# Which country has the highest total sales?
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country has the highest total sales?"}' | python3 -m json.tool

# Which country dropped the most in Q3 2019?
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the largest MoM decline in Q3 2019?"}'
```

### Available tools

The LLM **does not generate SQL**. It calls one of five Python tools, each wrapping an existing `analyzer.py` function:

| Tool | Function |
|---|---|
| `get_global_summary` | Global KPIs + country rankings |
| `get_country_detail` | Single-country monthly/quarterly/annual trend + model rankings |
| `compare_countries` | Side-by-side comparison + market share |
| `get_top_models` | Best-selling models globally or per country |
| `get_trend` | Time-series trend at a specified granularity |
