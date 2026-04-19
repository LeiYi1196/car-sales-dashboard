"""Apply config-driven column mapping to produce the standard schema."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

STANDARD_COLUMNS = ["date", "country", "sales", "quantity", "model"]
REQUIRED_COLUMNS = ("date", "sales")


def _merge_country_config(default: dict, country_cfg: dict | None) -> dict:
    """Shallow merge with column_map keys merged individually (country values win)."""
    merged: dict[str, Any] = {**default, **(country_cfg or {})}
    default_map = default.get("column_map", {})
    country_map = (country_cfg or {}).get("column_map", {})
    merged["column_map"] = {**default_map, **country_map}
    return merged


def _match_column(df_cols: list[str], candidates: list[str]) -> str | None:
    """Case-insensitive match — first hit wins."""
    lower_to_orig = {c.strip().lower(): c for c in df_cols if isinstance(c, str)}
    for cand in candidates:
        hit = lower_to_orig.get(cand.strip().lower())
        if hit:
            return hit
    return None


def _find_country_column(df: pd.DataFrame, config: dict) -> str:
    candidates = config["default"]["column_map"]["country"]
    col = _match_column(list(df.columns), candidates)
    if not col:
        raise ValueError(
            f"Could not find a country column. Tried: {candidates}. "
            f"Got columns: {list(df.columns)[:20]}"
        )
    return col


def _parse_dates(series: pd.Series, fmt: str | None) -> pd.Series:
    if fmt:
        parsed = pd.to_datetime(series, format=fmt, errors="coerce")
        if parsed.notna().sum() >= max(1, int(len(series) * 0.5)):
            return parsed
        log.warning("date_format %s matched <50%% of rows; falling back to infer", fmt)
    return pd.to_datetime(series, errors="coerce", dayfirst=True)


def detect_columns(raw: pd.DataFrame, config: dict) -> dict:
    """Inspect a raw uploaded DataFrame and return a mapping suggestion.

    Returns a dict shaped like:
      {
        "raw_columns": [...],
        "country_column": "COUNTRY" or None,
        "countries_found": ["USA", "France", ...],
        "per_country": {
           "USA": {
              "date":     {"matched": "ORDERDATE", "candidates": ["ORDERDATE","Date",...]},
              "sales":    {...},
              ...
           },
           ...
        }
      }

    Used by the upload preview endpoint so the UI can show the user what was
    auto-detected and let them override before committing.
    """
    raw_cols = [c for c in raw.columns if isinstance(c, str)]
    country_col = _match_column(raw_cols, config["default"]["column_map"]["country"])

    countries_found: list[str] = []
    if country_col:
        countries_found = sorted(
            str(v).strip()
            for v in raw[country_col].dropna().astype(str).str.strip().unique()
            if v and str(v).strip().lower() != "nan"
        )

    countries_cfg = config.get("countries", {})
    per_country: dict[str, dict] = {}

    if not countries_found:
        # No country column — report one "global" bucket using default config.
        cfg = _merge_country_config(config["default"], None)
        per_country["__all__"] = {
            std: {
                "matched": _match_column(raw_cols, cfg["column_map"].get(std, [])),
                "candidates": cfg["column_map"].get(std, []),
            }
            for std in STANDARD_COLUMNS if std != "country"
        }
    else:
        for c in countries_found:
            cfg = _merge_country_config(config["default"], countries_cfg.get(c))
            per_country[c] = {
                std: {
                    "matched": _match_column(raw_cols, cfg["column_map"].get(std, [])),
                    "candidates": cfg["column_map"].get(std, []),
                }
                for std in STANDARD_COLUMNS if std != "country"
            }

    return {
        "raw_columns": raw_cols,
        "country_column": country_col,
        "countries_found": countries_found,
        "per_country": per_country,
    }


def normalize_with_mapping(
    raw: pd.DataFrame,
    config: dict,
    mapping_override: dict | None = None,
) -> pd.DataFrame:
    """Normalize using config, but allow per-country column overrides from the UI.

    `mapping_override` shape (all keys optional):
      {
        "country_column": "some_col",                      # override country detection
        "per_country": {
           "USA":     {"date": "ORDERDATE", "sales": "SALES", "quantity": "QTY", "model": "MDL"},
           "__all__": {...}   # used when no country column is present
        }
      }

    Any column not specified falls back to config candidates.
    """
    override = mapping_override or {}
    per_country_override: dict[str, dict] = override.get("per_country") or {}
    country_column = override.get("country_column")

    if country_column and country_column in raw.columns:
        country_col = country_column
    else:
        try:
            country_col = _find_country_column(raw, config)
        except ValueError:
            if "__all__" in per_country_override:
                return _normalize_single_bucket(
                    raw, config, per_country_override["__all__"], country_key="__global__",
                )
            raise

    raw = raw.dropna(subset=[country_col]).copy()
    raw[country_col] = raw[country_col].astype(str).str.strip()

    pieces: list[pd.DataFrame] = []
    countries_cfg = config.get("countries", {})

    for country_key, group in raw.groupby(country_col, sort=False):
        cfg = _merge_country_config(config["default"], countries_cfg.get(country_key))
        ov = per_country_override.get(country_key, {})
        piece = _normalize_group(group, cfg, ov, country_key)
        if piece is not None:
            pieces.append(piece)

    if not pieces:
        raise ValueError("no rows survived normalization")

    result = pd.concat(pieces, ignore_index=True)
    result = result.dropna(subset=["date", "sales"])
    return result


def _normalize_group(
    group: pd.DataFrame,
    cfg: dict,
    override: dict,
    country_key: str,
) -> pd.DataFrame | None:
    """Normalize one country's rows. Returns None if required columns can't be resolved."""
    def resolve(std: str) -> str | None:
        forced = override.get(std)
        if forced and forced in group.columns:
            return forced
        return _match_column(list(group.columns), cfg["column_map"].get(std, []))

    col_by_std = {std: resolve(std) for std in STANDARD_COLUMNS}

    missing = [s for s in REQUIRED_COLUMNS if not col_by_std.get(s)]
    if missing:
        log.warning(
            "Skipping %d rows from country '%s': missing required columns %s",
            len(group), country_key, missing,
        )
        return None

    out = pd.DataFrame(index=group.index)
    out["date"] = _parse_dates(group[col_by_std["date"]], cfg.get("date_format"))
    out["country"] = country_key
    out["sales"] = pd.to_numeric(group[col_by_std["sales"]], errors="coerce")
    out["quantity"] = (
        pd.to_numeric(group[col_by_std["quantity"]], errors="coerce").fillna(0).astype(int)
        if col_by_std["quantity"] else 0
    )
    out["model"] = (
        group[col_by_std["model"]].astype(str) if col_by_std["model"] else "Unknown"
    )
    out["currency"] = cfg.get("currency") or "USD"
    out["display_name"] = cfg.get("display_name") or country_key
    return out


def _normalize_single_bucket(
    raw: pd.DataFrame,
    config: dict,
    override: dict,
    country_key: str,
) -> pd.DataFrame:
    cfg = _merge_country_config(config["default"], None)
    piece = _normalize_group(raw, cfg, override, country_key)
    if piece is None:
        raise ValueError("required columns (date, sales) could not be resolved")
    return piece.dropna(subset=["date", "sales"]).reset_index(drop=True)


def normalize(raw: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Backward-compatible entry point used by the CLI (no UI overrides)."""
    return normalize_with_mapping(raw, config, mapping_override=None)
