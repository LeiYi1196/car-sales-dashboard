"""Tests for MoM/QoQ/YoY computation."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.periods import aggregate, pop_label, with_deltas


def _df(rows):
    return pd.DataFrame(rows, columns=["date", "country", "sales", "quantity"]).assign(
        date=lambda d: pd.to_datetime(d["date"])
    )


def test_aggregate_monthly_sums():
    df = _df([
        ("2024-01-15", "USA", 100, 1),
        ("2024-01-20", "USA", 50,  2),
        ("2024-02-03", "USA", 200, 3),
    ])
    agg = aggregate(df, "M")
    assert list(agg["period_label"]) == ["2024-01", "2024-02"]
    assert list(agg["sales"]) == [150.0, 200.0]
    assert list(agg["quantity"]) == [3, 3]


def test_mom_pct_simple():
    df = _df([
        ("2024-01-15", "USA", 100, 1),
        ("2024-02-15", "USA", 150, 1),
        ("2024-03-15", "USA", 120, 1),
    ])
    out = with_deltas(aggregate(df, "M"), "M")
    pop = out["pop_pct"].tolist()
    assert math.isnan(pop[0])
    assert pop[1] == pytest.approx(50.0)
    assert pop[2] == pytest.approx(-20.0)


def test_yoy_monthly_fills_gaps():
    """YoY should compare Jan 2025 to Jan 2024 even when intermediate months missing."""
    df = _df([
        ("2024-01-15", "USA", 100, 1),
        ("2025-01-20", "USA", 150, 1),  # Jan 2025 → YoY vs Jan 2024
    ])
    out = with_deltas(aggregate(df, "M"), "M")
    # Only two actual periods present
    assert len(out) == 2
    jan2025 = out[out["period_label"] == "2025-01"].iloc[0]
    assert jan2025["yoy_pct"] == pytest.approx(50.0)


def test_quarterly_qoq_and_yoy():
    df = _df([
        ("2024-01-15", "USA", 100, 1),
        ("2024-04-15", "USA", 200, 1),  # Q2
        ("2024-07-15", "USA", 300, 1),  # Q3
        ("2025-01-10", "USA", 150, 1),  # Q1 2025 → YoY vs Q1 2024 = +50%
    ])
    out = with_deltas(aggregate(df, "Q"), "Q")
    q2 = out[out["period_label"] == "2024Q2"].iloc[0]
    q1_2025 = out[out["period_label"] == "2025Q1"].iloc[0]
    assert q2["pop_pct"] == pytest.approx(100.0)      # 200 vs 100
    assert q1_2025["yoy_pct"] == pytest.approx(50.0)  # 150 vs 100


def test_yearly_yoy_equals_pop():
    df = _df([
        ("2023-06-15", "USA", 1000, 1),
        ("2024-06-15", "USA", 1200, 1),
    ])
    out = with_deltas(aggregate(df, "Y"), "Y")
    y2024 = out[out["period_label"] == "2024"].iloc[0]
    assert y2024["pop_pct"] == pytest.approx(20.0)
    assert y2024["yoy_pct"] == pytest.approx(20.0)


def test_empty_input():
    empty = pd.DataFrame(columns=["date", "country", "sales", "quantity"])
    out = with_deltas(aggregate(empty, "M"), "M")
    assert out.empty
    assert {"pop_pct", "yoy_pct"}.issubset(out.columns)


def test_pop_label():
    assert pop_label("M") == "MoM"
    assert pop_label("Q") == "QoQ"
    assert pop_label("Y") == "YoY"
