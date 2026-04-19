"""Export rendered HTML pages to PNG and PDF via Playwright (Chromium)."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

VIEWPORT = {"width": 1440, "height": 900}
PNG_WAIT_MS = 800   # extra grace for Plotly to paint after networkidle


def _target_name(html_path: Path, output_root: Path) -> str:
    """index.html → 'overview'; countries/usa.html → 'usa'."""
    if html_path.name == "index.html":
        return "overview"
    return html_path.stem


def export_pages(html_paths: list[Path], output_root: Path, formats: list[str]) -> list[Path]:
    """Open each HTML in Chromium and write PNG/PDF to output_root/exports/.
    formats: subset of {'png', 'pdf'}. Returns list of written artifact paths."""
    formats = [f.lower() for f in formats if f.lower() in ("png", "pdf")]
    if not formats:
        return []

    exports_dir = output_root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
            page = context.new_page()
            for html in html_paths:
                url = html.resolve().as_uri()
                log.info("exporting %s", html)
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(PNG_WAIT_MS)

                name = _target_name(html, output_root)
                if "png" in formats:
                    out = exports_dir / f"{name}.png"
                    page.screenshot(path=str(out), full_page=True)
                    written.append(out)
                if "pdf" in formats:
                    out = exports_dir / f"{name}.pdf"
                    page.pdf(
                        path=str(out),
                        format="A4",
                        print_background=True,
                        margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
                    )
                    written.append(out)
        finally:
            browser.close()

    return written
