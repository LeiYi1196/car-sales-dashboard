---
name: car-sales-dashboard
description: Generate a multi-country car sales dashboard (HTML + PNG + PDF) from one or more Excel/CSV files. Use when the user asks to visualize car sales data, build a sales dashboard, or compare sales across countries. Handles heterogeneous column names and header rows via config-driven field mapping.
---

# Car Sales Multi-Country Dashboard

Run from this skill's directory:

```bash
python -m src.cli --input <file1.xlsx> [<file2.xlsx> ...] --output-dir output --formats html png pdf
```

The tool:
1. Loads .xlsx/.csv with auto-detected header rows
2. Normalizes columns per `config/countries.json` (candidate column-name lists + per-country overrides)
3. Aggregates KPIs, monthly trends, TOP product lines
4. Renders an overview HTML + per-country drill-down HTML
5. Exports each view as PNG and PDF via Playwright

To support a new country, add an entry under `countries.{CODE}` in `config/countries.json` with any of: `display_name`, `currency`, `date_format`, `column_map` (overrides default candidates).

First-time setup: `pip install -r requirements.txt && playwright install chromium`
