"""CLI entry: python -m src.cli --input FILE ... --output-dir DIR --formats html png pdf"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .analyzer import summarize_all
from .loader import load_files
from .normalizer import normalize
from .renderer import render_site

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "countries.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="car-sales-dashboard",
        description="Generate multi-country car sales dashboard (HTML/PNG/PDF).",
    )
    p.add_argument("--input", "-i", nargs="+", required=True, help="Input .xlsx / .csv files")
    p.add_argument("--config", "-c", default=str(DEFAULT_CONFIG), help="Path to countries.json")
    p.add_argument("--output-dir", "-o", default=str(ROOT / "output"), help="Output directory")
    p.add_argument(
        "--formats", "-f", nargs="+", default=["html", "png", "pdf"],
        choices=["html", "png", "pdf"],
        help="Artifacts to produce (default: all three)",
    )
    p.add_argument("--countries", nargs="+", default=None, help="Only render these countries (keys)")
    p.add_argument("--top-n", type=int, default=10, help="TOP N models per country (default 10)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("cli")

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("loading %d file(s)", len(args.input))
    raw = load_files(args.input, config)
    df = normalize(raw, config)

    if args.countries:
        keep = set(args.countries)
        df = df[df["country"].isin(keep)]
        if df.empty:
            log.error("no rows match --countries %s", args.countries)
            return 2

    summary = summarize_all(df, top_n=args.top_n)
    log.info(
        "aggregated: %d countries, $%.0f total sales, %d orders",
        summary.country_count, summary.total_sales, summary.order_count,
    )

    html_paths: list[Path] = []
    if "html" in args.formats:
        html_paths = render_site(summary, output_dir)
        log.info("wrote %d HTML pages → %s", len(html_paths), output_dir)
    else:
        # still need the pages on disk for export
        html_paths = render_site(summary, output_dir)

    export_formats = [f for f in args.formats if f in ("png", "pdf")]
    if export_formats:
        from .exporter import export_pages  # lazy: avoid playwright import when not needed
        artifacts = export_pages(html_paths, output_dir, export_formats)
        log.info("wrote %d export artifact(s) → %s/exports/", len(artifacts), output_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
