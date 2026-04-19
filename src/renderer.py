"""Plotly charts + Jinja2 templates → HTML strings / files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .analyzer import CountrySummary, GlobalSummary
from .periods import pop_label

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"
ASSET_DIR = ROOT / "assets"
THEME_PATH = ROOT / "config" / "theme.json"

_theme = json.loads(THEME_PATH.read_text())
PALETTE = _theme["colors"]["palette"]
PRIMARY = _theme["colors"]["primary"]
ACCENT = _theme["colors"]["accent"]
TEXT = _theme["colors"]["text"]
MUTED = _theme["colors"]["text_muted"]
FONT_FAMILY = _theme["font"]["family"]

_BASE_LAYOUT = dict(
    font=dict(family=FONT_FAMILY, size=12, color=TEXT),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=40, r=20, t=10, b=40),
    hoverlabel=dict(font_family=FONT_FAMILY),
    xaxis=dict(gridcolor="#f1f5f9", linecolor=MUTED, ticks="outside", tickcolor=MUTED),
    yaxis=dict(gridcolor="#f1f5f9", linecolor=MUTED, ticks="outside", tickcolor=MUTED),
)

_PLOTLY_KW = dict(
    include_plotlyjs=False,
    full_html=False,
    config={"displayModeBar": False, "responsive": True},
)


def _fmt_money(v: float) -> str:
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


def _fmt_int(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def _fmt_range(start, end) -> str:
    if start is None or end is None:
        return "—"
    return f"{pd.Timestamp(start):%b %Y} – {pd.Timestamp(end):%b %Y}"


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _sparkline_html(trend: pd.DataFrame, color: str = PRIMARY) -> str:
    if trend.empty:
        return ""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trend["period_start"], y=trend["sales"],
        mode="lines", line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=_rgba(color, 0.12),
        hoverinfo="skip",
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        height=48, showlegend=False,
    )
    return fig.to_html(**_PLOTLY_KW)


def _comparison_chart(by_country: pd.DataFrame) -> str:
    if by_country.empty:
        return "<div class='empty'>No data</div>"
    top = by_country.head(15)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=top["sales"], y=top["display_name"],
        orientation="h",
        marker=dict(color=PRIMARY),
        hovertemplate="<b>%{y}</b><br>Sales: $%{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        **_BASE_LAYOUT,
        height=max(320, 28 * len(top) + 40),
        showlegend=False,
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat="$,.0f")
    return fig.to_html(**_PLOTLY_KW)


def _trend_chart(trend: pd.DataFrame) -> str:
    if trend.empty:
        return "<div class='empty'>No data for selected range</div>"
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trend["period_start"], y=trend["sales"],
        name="Sales", mode="lines+markers",
        line=dict(color=PRIMARY, width=2.5), marker=dict(size=6),
        yaxis="y",
        hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra>Sales</extra>",
    ))
    fig.add_trace(go.Bar(
        x=trend["period_start"], y=trend["quantity"],
        name="Units", marker=dict(color=_rgba(ACCENT, 0.35)),
        yaxis="y2",
        hovertemplate="%{x|%b %Y}<br>%{y:,} units<extra>Units</extra>",
    ))
    layout = {**_BASE_LAYOUT}
    layout["yaxis"] = {**_BASE_LAYOUT["yaxis"], "title": "Sales", "tickformat": "$,.0f"}
    fig.update_layout(
        **layout,
        height=340,
        yaxis2=dict(title="Units", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.12, x=0),
        hovermode="x unified",
    )
    return fig.to_html(**_PLOTLY_KW)


def _top_models_chart(top: pd.DataFrame) -> str:
    if top.empty:
        return "<div class='empty'>No model data</div>"
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=top["sales"], y=top["model"],
        orientation="h", marker=dict(color=PRIMARY),
        hovertemplate="<b>%{y}</b><br>$%{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        **_BASE_LAYOUT,
        height=max(300, 32 * len(top) + 40),
        showlegend=False,
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat="$,.0f")
    return fig.to_html(**_PLOTLY_KW)


def _model_share_chart(share: pd.DataFrame) -> str:
    if share.empty:
        return "<div class='empty'>No model data</div>"
    fig = go.Figure()
    fig.add_trace(go.Pie(
        labels=share["model"], values=share["sales"],
        hole=0.55, marker=dict(colors=PALETTE),
        textinfo="percent",
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        font=dict(family=FONT_FAMILY, size=12, color=TEXT),
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10),
        height=340,
        legend=dict(orientation="v", y=0.5, x=1.05),
    )
    return fig.to_html(**_PLOTLY_KW)


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["fmt_money"] = _fmt_money
    env.globals["fmt_int"] = _fmt_int
    env.globals["fmt_pct"] = _fmt_pct
    env.globals["pop_label"] = pop_label
    env.globals["fmt_range"] = _fmt_range
    return env


def _country_cards(summary: GlobalSummary) -> list[dict]:
    cards = []
    for i, cs in enumerate(summary.countries):
        cards.append({
            "country": cs.country,
            "display_name": cs.display_name,
            "currency": cs.currency,
            "slug": cs.slug,
            "total_sales": cs.total_sales,
            "total_quantity": cs.total_quantity,
            "order_count": cs.order_count,
            "model_count": cs.model_count,
            "spark_html": _sparkline_html(cs.trend, color=PALETTE[i % len(PALETTE)]),
        })
    return cards


def render_overview(
    summary: GlobalSummary,
    filter_state=None,
    query_string: str = "",
    asset_prefix: str = "",
    title: str = "Auto Sales Dashboard",
    show_upload: bool = False,
    empty: bool = False,
    available_countries: list[dict] | None = None,
    body_only: bool = False,
    data_stats: dict | None = None,
    admin_username: str | None = None,
) -> str:
    """Render the overview HTML as a string (used by FastAPI and the CLI)."""
    env = build_env()
    template = env.get_template("overview.html.j2")
    return template.render(
        title=title,
        asset_prefix=asset_prefix,
        summary=summary,
        cards=_country_cards(summary),
        comparison_chart=_comparison_chart(summary.by_country),
        trend_chart=_trend_chart(summary.trend),
        date_range=_fmt_range(*summary.date_range),
        filter_state=filter_state,
        query_string=query_string,
        show_upload=show_upload,
        empty=empty,
        available_countries=available_countries or [],
        body_only=body_only,
        data_stats=data_stats or {},
        admin_username=admin_username,
    )


def render_country(
    country: CountrySummary,
    filter_state=None,
    query_string: str = "",
    asset_prefix: str = "",
    show_upload: bool = False,
    available_countries: list[dict] | None = None,
    body_only: bool = False,
    data_stats: dict | None = None,
    admin_username: str | None = None,
) -> str:
    env = build_env()
    template = env.get_template("country.html.j2")
    return template.render(
        title=f"{country.display_name} · Auto Sales",
        asset_prefix=asset_prefix,
        country=country,
        trend_chart=_trend_chart(country.trend),
        top_models_chart=_top_models_chart(country.top_models),
        model_share_chart=_model_share_chart(country.model_share),
        date_range=_fmt_range(*country.date_range),
        filter_state=filter_state,
        query_string=query_string,
        show_upload=show_upload,
        available_countries=available_countries or [],
        body_only=body_only,
        data_stats=data_stats or {},
        admin_username=admin_username,
    )


def render_site(summary: GlobalSummary, output_dir: Path) -> list[Path]:
    """Render a static index.html and per-country detail pages. Used by the CLI."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "countries").mkdir(exist_ok=True)
    shutil.copy(ASSET_DIR / "style.css", output_dir / "style.css")

    written: list[Path] = []

    idx = output_dir / "index.html"
    idx.write_text(
        render_overview(summary, asset_prefix=""),
        encoding="utf-8",
    )
    written.append(idx)

    for cs in summary.countries:
        path = output_dir / "countries" / f"{cs.slug}.html"
        path.write_text(
            render_country(cs, asset_prefix="../"),
            encoding="utf-8",
        )
        written.append(path)

    return written
