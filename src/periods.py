"""Period-over-period deltas (MoM / QoQ / YoY).

Pure pandas helpers; no I/O. Takes a DataFrame with a period column and value
columns, produces the same DataFrame with added percent-change columns.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

Granularity = Literal["M", "Q", "Y"]


def _period_key(date: pd.Series, granularity: Granularity) -> pd.Series:
    """Return a Period-typed series at the requested granularity."""
    freq = {"M": "M", "Q": "Q", "Y": "Y"}[granularity]
    return date.dt.to_period(freq)


def aggregate(df: pd.DataFrame, granularity: Granularity) -> pd.DataFrame:
    """Group the normalized sales df by period, summing sales and quantity.

    Returns a DataFrame indexed 0..N-1 with columns:
      - period (Period dtype)
      - period_start (Timestamp, first day of period)
      - period_label (str, human-readable: '2024-03', '2024Q1', '2024')
      - sales (float)
      - quantity (int)
    """
    if df.empty:
        return pd.DataFrame(columns=["period", "period_start", "period_label", "sales", "quantity"])

    g = df.copy()
    g["period"] = _period_key(g["date"], granularity)
    out = (
        g.groupby("period", as_index=False)
         .agg(sales=("sales", "sum"), quantity=("quantity", "sum"))
         .sort_values("period")
         .reset_index(drop=True)
    )
    out["period_start"] = out["period"].dt.to_timestamp()
    out["period_label"] = out["period"].astype(str)
    return out


def with_deltas(agg: pd.DataFrame, granularity: Granularity) -> pd.DataFrame:
    """Add previous-period and year-over-year deltas to an aggregated frame.

    Added columns:
      - prev_sales / prev_quantity  (value of the immediately previous period)
      - pop_pct                     (period-over-period %, i.e. MoM / QoQ / YoY depending on granularity)
      - yoy_pct                     (year-over-year %; identical to pop_pct when granularity == 'Y')
    """
    if agg.empty:
        for c in ("prev_sales", "prev_quantity", "pop_pct", "yoy_pct"):
            agg[c] = pd.Series(dtype="float64")
        return agg

    out = agg.copy()

    # Reindex to a continuous period range so shift() lines up even when months are missing.
    freq = {"M": "M", "Q": "Q", "Y": "Y"}[granularity]
    full_index = pd.period_range(out["period"].min(), out["period"].max(), freq=freq)
    tmp = out.set_index("period").reindex(full_index)

    tmp["prev_sales"] = tmp["sales"].shift(1)
    tmp["prev_quantity"] = tmp["quantity"].shift(1)
    tmp["pop_pct"] = (tmp["sales"] - tmp["prev_sales"]) / tmp["prev_sales"] * 100.0

    yoy_offset = {"M": 12, "Q": 4, "Y": 1}[granularity]
    yoy_prev = tmp["sales"].shift(yoy_offset)
    tmp["yoy_pct"] = (tmp["sales"] - yoy_prev) / yoy_prev * 100.0

    tmp = tmp.reset_index().rename(columns={"index": "period"})
    # Filter back to only the periods we originally had (keep gaps out of the UI).
    out = tmp[tmp["period"].isin(out["period"])].reset_index(drop=True)

    # Replace infinities (division by zero) with NaN.
    out[["pop_pct", "yoy_pct"]] = out[["pop_pct", "yoy_pct"]].where(
        out[["pop_pct", "yoy_pct"]].abs() != float("inf")
    )
    return out


def pop_label(granularity: Granularity) -> str:
    """Human-readable label for the period-over-period metric."""
    return {"M": "MoM", "Q": "QoQ", "Y": "YoY"}[granularity]
