"""FastAPI web app for the car-sales-dashboard.

Routes
------
GET  /                       overview (filtered by query params)
GET  /country/{slug}         country detail (filtered by query params)
GET  /upload                 upload form (admin-gated)
POST /upload/preview         parse file, return mapping suggestion
POST /upload/commit          persist rows to the DB
POST /admin/batches/{id}/delete
GET  /admin/login            render login form
POST /admin/login            username + password → session cookie
POST /admin/logout           clear session cookie
GET  /healthz                liveness probe
GET  /style.css              static stylesheet
GET  /print.css              print-only stylesheet
GET  /app.js                 client-side export + share helpers

Auth
----
- Reads are public.
- Writes require a valid `admin_session` cookie, which is issued by POST /admin/login
  on username + password match. Defaults: ADMIN_USERNAME=Kirby, ADMIN_PASSWORD=Kirby123
  (override via env vars; always override on production).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .analyzer import country_slug, summarize_all, summarize_country
from .db import (
    Sale,
    UploadBatch,
    delete_batch,
    get_engine,
    insert_batch,
    load_sales_df,
    session_scope,
    stats,
)
from .filters import FilterState, parse_filters
from .loader import _detect_header_row, _collect_candidates
from .normalizer import detect_columns, normalize_with_mapping
from .renderer import build_env, render_country, render_overview

log = logging.getLogger("app")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", ROOT / "config" / "countries.json"))
ASSET_DIR = ROOT / "assets"

app = FastAPI(title="Car Sales Dashboard")
app.mount("/assets", StaticFiles(directory=str(ASSET_DIR)), name="assets")

# Initialize DB at import time so the schema exists before first request.
get_engine()


# ──────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────────────
# Auth (username + password + HMAC-signed session cookie)
# ──────────────────────────────────────────────────────────────────────────

SESSION_COOKIE = "admin_session"
_DEFAULT_USERNAME = "Kirby"
_DEFAULT_PASSWORD = "Kirby123"


def _admin_credentials() -> tuple[str, str]:
    return (
        os.environ.get("ADMIN_USERNAME") or _DEFAULT_USERNAME,
        os.environ.get("ADMIN_PASSWORD") or _DEFAULT_PASSWORD,
    )


def _session_secret() -> bytes:
    explicit = os.environ.get("SESSION_SECRET")
    if explicit:
        return explicit.encode("utf-8")
    _, pwd = _admin_credentials()
    return pwd.encode("utf-8")


def _sign(username: str) -> str:
    return hmac.new(
        _session_secret(), username.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def make_session_cookie(username: str) -> str:
    return f"{username}.{_sign(username)}"


def verify_session_cookie(value: Optional[str]) -> Optional[str]:
    """Return the username if the cookie is valid for the configured admin, else None."""
    if not value or "." not in value:
        return None
    username, sig = value.rsplit(".", 1)
    expected = _sign(username)
    if not hmac.compare_digest(sig, expected):
        return None
    admin_user, _ = _admin_credentials()
    if username != admin_user:
        return None
    return username


def current_admin(admin_session: Optional[str] = Cookie(default=None)) -> Optional[str]:
    return verify_session_cookie(admin_session)


def require_admin(admin_session: Optional[str] = Cookie(default=None)) -> str:
    user = verify_session_cookie(admin_session)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin login required")
    return user


# ──────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ──────────────────────────────────────────────────────────────────────────

def _available_countries(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return (
        df[["country", "display_name"]]
        .drop_duplicates()
        .sort_values("display_name")
        .to_dict(orient="records")
    )


def _render_empty_state(show_upload: bool, filter_state: FilterState) -> str:
    env = build_env()
    return env.get_template("empty.html.j2").render(
        title="Auto Sales Dashboard",
        asset_prefix="/",
        show_upload=show_upload,
        filter_state=filter_state,
        query_string=filter_state.query_string(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Overview
# ──────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default="M"),
    countries: list[str] = Query(default_factory=list),
    body_only: bool = Query(default=False),
    hx_request: Optional[str] = Header(default=None, alias="HX-Request"),
    session: Session = Depends(session_scope),
    admin_session: Optional[str] = Cookie(default=None),
):
    body_only = body_only or bool(hx_request)
    fs = parse_filters(start, end, granularity, countries)
    df = load_sales_df(session)
    admin_username = verify_session_cookie(admin_session)
    show_upload = admin_username is not None

    if df.empty:
        return HTMLResponse(_render_empty_state(show_upload, fs))

    summary = summarize_all(
        df,
        top_n=10,
        granularity=fs.granularity,
        start=fs.start,
        end=fs.end,
        countries=fs.countries or None,
    )
    data_stats = stats(session)

    html = render_overview(
        summary,
        filter_state=fs,
        query_string=fs.query_string(),
        asset_prefix="/",
        show_upload=show_upload,
        empty=(summary.order_count == 0),
        available_countries=_available_countries(df),
        body_only=body_only,
        data_stats=data_stats,
        admin_username=admin_username,
    )
    return HTMLResponse(html)


# ──────────────────────────────────────────────────────────────────────────
# Country detail
# ──────────────────────────────────────────────────────────────────────────

@app.get("/country/{slug}", response_class=HTMLResponse)
def country_page(
    slug: str,
    request: Request,
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default="M"),
    body_only: bool = Query(default=False),
    hx_request: Optional[str] = Header(default=None, alias="HX-Request"),
    session: Session = Depends(session_scope),
    admin_session: Optional[str] = Cookie(default=None),
):
    body_only = body_only or bool(hx_request)
    fs = parse_filters(start, end, granularity, None)
    df = load_sales_df(session)
    admin_username = verify_session_cookie(admin_session)
    show_upload = admin_username is not None

    if df.empty:
        return HTMLResponse(_render_empty_state(show_upload, fs), status_code=404)

    # Find country by slug.
    df_countries = _available_countries(df)
    match = next((c for c in df_countries if country_slug(c["country"]) == slug), None)
    if not match:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "country not found")

    sub = df[df["country"] == match["country"]]
    if fs.start is not None:
        sub = sub[sub["date"] >= fs.start]
    if fs.end is not None:
        sub = sub[sub["date"] <= fs.end]

    cs = summarize_country(sub, match["country"], top_n=10, granularity=fs.granularity)
    data_stats = stats(session)

    html = render_country(
        cs,
        filter_state=fs,
        query_string=fs.query_string(),
        asset_prefix="/",
        show_upload=show_upload,
        available_countries=df_countries,
        body_only=body_only,
        data_stats=data_stats,
        admin_username=admin_username,
    )
    return HTMLResponse(html)


# ──────────────────────────────────────────────────────────────────────────
# Upload flow
# ──────────────────────────────────────────────────────────────────────────

@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    session: Session = Depends(session_scope),
    admin_session: Optional[str] = Cookie(default=None),
):
    # Browser navigations should hit the login form instead of a raw 401 JSON.
    if verify_session_cookie(admin_session) is None:
        return RedirectResponse(url="/admin/login?next=/upload", status_code=303)
    env = build_env()
    template = env.get_template("upload.html.j2")
    s = stats(session)
    batches = (
        session.query(UploadBatch)
        .order_by(UploadBatch.created_at.desc())
        .limit(10)
        .all()
    )
    html = template.render(
        title="Upload data",
        asset_prefix="/",
        show_upload=True,
        stats=s,
        batches=batches,
    )
    return HTMLResponse(html)


def _read_raw_upload(upload: UploadFile, config: dict) -> pd.DataFrame:
    """Save the uploaded bytes to a temp file, load via pandas with auto header detection."""
    suffix = Path(upload.filename or "upload.csv").suffix.lower() or ".csv"
    if suffix not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(400, f"unsupported file type: {suffix}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(upload.file.read())
        tmp_path = Path(tmp.name)

    try:
        default_map = config["default"]["column_map"]
        country_maps = [c.get("column_map", {}) for c in config.get("countries", {}).values()]
        candidates = _collect_candidates(default_map, country_maps)

        if suffix in (".xlsx", ".xls"):
            scan = pd.read_excel(tmp_path, header=None, nrows=10)
        else:
            scan = pd.read_csv(tmp_path, header=None, nrows=10)
        header_row = _detect_header_row(scan, candidates)

        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(tmp_path, header=header_row)
        else:
            df = pd.read_csv(tmp_path, header=header_row)
        df["__source_file"] = upload.filename or tmp_path.name
        return df
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


@app.post("/upload/preview")
async def upload_preview(
    file: UploadFile = File(...),
    _user: str = Depends(require_admin),
):
    config = load_config()
    raw = _read_raw_upload(file, config)

    detected = detect_columns(raw, config)
    preview_rows = raw.head(5).where(pd.notna(raw.head(5)), None).to_dict(orient="records")
    # Strip the injected __source_file from the user-facing preview.
    for row in preview_rows:
        row.pop("__source_file", None)

    return JSONResponse({
        "filename": file.filename,
        "row_count": int(len(raw)),
        "columns": detected["raw_columns"],
        "country_column": detected["country_column"],
        "countries_found": detected["countries_found"],
        "per_country": detected["per_country"],
        "preview": preview_rows,
    })


@app.post("/upload/commit")
async def upload_commit(
    file: UploadFile = File(...),
    mapping: str = Form(default="{}"),
    session: Session = Depends(session_scope),
    _user: str = Depends(require_admin),
):
    config = load_config()
    raw = _read_raw_upload(file, config)

    try:
        mapping_override = json.loads(mapping) if mapping else None
    except json.JSONDecodeError:
        raise HTTPException(400, "mapping field must be valid JSON")

    try:
        normalized = normalize_with_mapping(raw, config, mapping_override=mapping_override)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if normalized.empty:
        raise HTTPException(400, "no rows survived normalization — check your mapping")

    batch_id, inserted, skipped = insert_batch(
        session, normalized, filename=file.filename or "upload",
    )

    return JSONResponse({
        "batch_id": batch_id,
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "total_rows": int(len(normalized)),
    })


# ──────────────────────────────────────────────────────────────────────────
# Admin
# ──────────────────────────────────────────────────────────────────────────

@app.post("/admin/batches/{batch_id}/delete")
def admin_delete_batch(
    batch_id: int,
    session: Session = Depends(session_scope),
    _user: str = Depends(require_admin),
):
    removed = delete_batch(session, batch_id)
    return {"batch_id": batch_id, "rows_removed": removed}


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(next: str = "/upload", error: Optional[str] = None):
    env = build_env()
    return HTMLResponse(env.get_template("login.html.j2").render(
        title="Admin login",
        asset_prefix="/",
        show_upload=False,
        next_url=next,
        error=error,
    ))


@app.post("/admin/login")
def admin_login(
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/upload"),
):
    """Verify username/password, set an HMAC-signed session cookie."""
    expected_user, expected_pwd = _admin_credentials()
    user_ok = hmac.compare_digest(username, expected_user)
    pwd_ok = hmac.compare_digest(password, expected_pwd)
    if not (user_ok and pwd_ok):
        return RedirectResponse(
            url=f"/admin/login?error=bad+credentials&next={next}",
            status_code=303,
        )
    resp = RedirectResponse(url=next or "/upload", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(username),
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ──────────────────────────────────────────────────────────────────────────
# Static assets + health
# ──────────────────────────────────────────────────────────────────────────

@app.get("/style.css")
def style_css():
    return FileResponse(ASSET_DIR / "style.css", media_type="text/css")


@app.get("/print.css")
def print_css():
    return FileResponse(ASSET_DIR / "print.css", media_type="text/css")


@app.get("/app.js")
def app_js():
    return FileResponse(ASSET_DIR / "app.js", media_type="application/javascript")


@app.get("/healthz")
def healthz():
    return {"ok": True}
