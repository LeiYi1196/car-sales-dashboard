"""Parse filter state from HTTP query params."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import pandas as pd

Granularity = Literal["M", "Q", "Y"]

_GRAN_ALIASES = {
    "m": "M", "month": "M", "monthly": "M",
    "q": "Q", "quarter": "Q", "quarterly": "Q",
    "y": "Y", "year": "Y", "yearly": "Y", "annual": "Y",
}


@dataclass
class FilterState:
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    granularity: Granularity
    countries: list[str]

    def query_string(self) -> str:
        parts = [f"granularity={self.granularity}"]
        if self.start is not None:
            parts.append(f"start={self.start.date().isoformat()}")
        if self.end is not None:
            parts.append(f"end={self.end.date().isoformat()}")
        for c in self.countries:
            parts.append(f"countries={c}")
        return "&".join(parts)


def _parse_date(s: str | None) -> pd.Timestamp | None:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return pd.Timestamp(s)
    except Exception:
        return None


def parse_filters(
    start: str | None = None,
    end: str | None = None,
    granularity: str | None = None,
    countries: list[str] | None = None,
) -> FilterState:
    g = (granularity or "M").strip().lower()
    g_norm: Granularity = _GRAN_ALIASES.get(g, "M")  # type: ignore[assignment]

    cleaned_countries = [c.strip() for c in (countries or []) if c and c.strip()]

    return FilterState(
        start=_parse_date(start),
        end=_parse_date(end),
        granularity=g_norm,
        countries=cleaned_countries,
    )
