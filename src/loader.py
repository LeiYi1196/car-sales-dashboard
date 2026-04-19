"""Read Excel/CSV files and return a raw DataFrame with auto-detected headers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)

MAX_HEADER_SCAN_ROWS = 10


def _collect_candidates(default_map: dict, country_maps: Iterable[dict]) -> set[str]:
    """All possible column names (lower-cased) across default + all countries."""
    cands: set[str] = set()
    for name_list in default_map.values():
        cands.update(n.lower() for n in name_list)
    for cmap in country_maps:
        for name_list in cmap.values():
            cands.update(n.lower() for n in name_list)
    return cands


def _detect_header_row(raw: pd.DataFrame, candidates: set[str]) -> int:
    """Return the row index whose values overlap most with the candidate column names."""
    best_row, best_score = 0, -1
    for i in range(min(MAX_HEADER_SCAN_ROWS, len(raw))):
        row_vals = {str(v).strip().lower() for v in raw.iloc[i].tolist() if pd.notna(v)}
        score = len(row_vals & candidates)
        if score > best_score:
            best_row, best_score = i, score
    return best_row


def _read_one(path: Path, config: dict) -> pd.DataFrame:
    default_map = config["default"]["column_map"]
    country_maps = [c.get("column_map", {}) for c in config.get("countries", {}).values()]
    candidates = _collect_candidates(default_map, country_maps)

    header_setting = config["default"].get("header_row", "auto")
    suffix = path.suffix.lower()

    if header_setting == "auto":
        if suffix in (".xlsx", ".xls"):
            raw = pd.read_excel(path, header=None, nrows=MAX_HEADER_SCAN_ROWS)
        else:
            raw = pd.read_csv(path, header=None, nrows=MAX_HEADER_SCAN_ROWS)
        header_row = _detect_header_row(raw, candidates)
        log.info("Detected header row %d for %s", header_row, path.name)
    else:
        header_row = int(header_setting)

    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path, header=header_row)
    else:
        df = pd.read_csv(path, header=header_row)

    df["__source_file"] = path.name
    return df


def load_files(paths: Iterable[str | Path], config: dict) -> pd.DataFrame:
    """Read and concatenate one or more xlsx/csv files."""
    frames = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise FileNotFoundError(p)
        frames.append(_read_one(p, config))
    if not frames:
        raise ValueError("no input files")
    return pd.concat(frames, ignore_index=True, sort=False)
