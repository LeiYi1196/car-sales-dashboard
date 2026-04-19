"""Aggregation helpers for KPIs, time-series trends, top models, and period deltas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from .periods import Granularity, aggregate as _aggregate, with_deltas


def country_slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name.strip().lower()).strip("-")
    return s or "country"


@dataclass
class CountrySummary:
    country: str
    display_name: str
    currency: str
    slug: str
    total_sales: float
    total_quantity: int
    order_count: int
    model_count: int
    date_range: tuple[pd.Timestamp | None, pd.Timestamp | None]
    trend: pd.DataFrame                 # period, period_start, period_label, sales, quantity, pop_pct, yoy_pct
    top_models: pd.DataFrame            # model, sales, quantity
    model_share: pd.DataFrame           # model, sales
    granularity: Granularity = "M"

    # Backward-compat for the existing CLI templates that read `.monthly`.
    @property
    def monthly(self) -> pd.DataFrame:
        df = self.trend.copy()
        if "period_start" in df.columns:
            df = df.rename(columns={"period_start": "month"})
        return df


@dataclass
class GlobalSummary:
    total_sales: float
    total_quantity: int
    order_count: int
    country_count: int
    date_range: tuple[pd.Timestamp | None, pd.Timestamp | None]
    by_country: pd.DataFrame            # country, display_name, slug, currency, sales, quantity, orders
    trend: pd.DataFrame                 # aggregated across all countries
    countries: list[CountrySummary] = field(default_factory=list)
    granularity: Granularity = "M"
    pop_pct: float | None = None        # latest-period pop delta (for KPI badge)
    yoy_pct: float | None = None        # latest-period yoy delta


def _apply_date_filter(
    df: pd.DataFrame,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if start is not None:
        out = out[out["date"] >= pd.Timestamp(start)]
    if end is not None:
        out = out[out["date"] <= pd.Timestamp(end)]
    return out


def _trend_with_deltas(df: pd.DataFrame, granularity: Granularity) -> pd.DataFrame:
    return with_deltas(_aggregate(df, granularity), granularity)


def summarize_country(
    df: pd.DataFrame,
    country: str,
    top_n: int = 10,
    granularity: Granularity = "M",
) -> CountrySummary:
    sub = df[df["country"] == country]
    if sub.empty:
        return CountrySummary(
            country=country, display_name=country, currency="USD",
            slug=country_slug(country),
            total_sales=0.0, total_quantity=0, order_count=0, model_count=0,
            date_range=(None, None),
            trend=_trend_with_deltas(sub, granularity),
            top_models=pd.DataFrame(columns=["model", "sales", "quantity"]),
            model_share=pd.DataFrame(columns=["model", "sales"]),
            granularity=granularity,
        )

    by_model = (
        sub.groupby("model", as_index=False)
        .agg(sales=("sales", "sum"), quantity=("quantity", "sum"))
        .sort_values("sales", ascending=False)
        .reset_index(drop=True)
    )

    return CountrySummary(
        country=country,
        display_name=sub["display_name"].iloc[0],
        currency=sub["currency"].iloc[0],
        slug=country_slug(country),
        total_sales=float(sub["sales"].sum()),
        total_quantity=int(sub["quantity"].sum()),
        order_count=int(len(sub)),
        model_count=int(sub["model"].nunique()),
        date_range=(sub["date"].min(), sub["date"].max()),
        trend=_trend_with_deltas(sub, granularity),
        top_models=by_model.head(top_n).reset_index(drop=True),
        model_share=by_model[["model", "sales"]].copy(),
        granularity=granularity,
    )


def summarize_all(
    df: pd.DataFrame,
    top_n: int = 10,
    granularity: Granularity = "M",
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    countries: list[str] | None = None,
) -> GlobalSummary:
    """Compute the global summary, optionally filtered by date range + country list.

    `granularity` controls the time-series grain ('M', 'Q', 'Y') used for
    the trend chart and delta columns.
    """
    df = _apply_date_filter(df, start, end)
    if countries:
        keep = set(countries)
        df = df[df["country"].isin(keep)]

    if df.empty:
        return GlobalSummary(
            total_sales=0.0, total_quantity=0, order_count=0, country_count=0,
            date_range=(None, None),
            by_country=pd.DataFrame(
                columns=["country", "display_name", "currency", "sales",
                         "quantity", "orders", "slug"]
            ),
            trend=_trend_with_deltas(df, granularity),
            countries=[],
            granularity=granularity,
            pop_pct=None, yoy_pct=None,
        )

    by_country = (
        df.groupby(["country", "display_name", "currency"], as_index=False)
        .agg(sales=("sales", "sum"), quantity=("quantity", "sum"), orders=("sales", "size"))
        .sort_values("sales", ascending=False)
        .reset_index(drop=True)
    )
    by_country["slug"] = by_country["country"].apply(country_slug)

    countries_summaries = [
        summarize_country(df, c, top_n=top_n, granularity=granularity)
        for c in by_country["country"]
    ]

    trend = _trend_with_deltas(df, granularity)
    pop_pct = float(trend["pop_pct"].iloc[-1]) if not trend.empty and pd.notna(trend["pop_pct"].iloc[-1]) else None
    yoy_pct = float(trend["yoy_pct"].iloc[-1]) if not trend.empty and pd.notna(trend["yoy_pct"].iloc[-1]) else None

    return GlobalSummary(
        total_sales=float(df["sales"].sum()),
        total_quantity=int(df["quantity"].sum()),
        order_count=int(len(df)),
        country_count=int(df["country"].nunique()),
        date_range=(df["date"].min(), df["date"].max()),
        by_country=by_country,
        trend=trend,
        countries=countries_summaries,
        granularity=granularity,
        pop_pct=pop_pct,
        yoy_pct=yoy_pct,
    )
