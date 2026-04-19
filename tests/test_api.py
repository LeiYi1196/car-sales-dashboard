"""End-to-end tests for the FastAPI routes.

Each test runs against a throwaway SQLite DB (tmp_path) so nothing touches
the real data/app.db.  We stand up a TestClient per test via the `client`
fixture below and log in once to obtain the admin session cookie.
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


ADMIN_USERNAME = "Kirby"
ADMIN_PASSWORD = "test-password"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up the FastAPI app with a temp DB and known admin credentials."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    # Force reload so module-level singletons pick up the new env.
    import src.db as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None
    importlib.reload(db_mod)

    import src.app as app_mod
    importlib.reload(app_mod)

    with TestClient(app_mod.app) as c:
        yield c


def _login(client) -> None:
    """Authenticate via the login form so subsequent requests carry the cookie."""
    r = client.post(
        "/admin/login",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "next": "/upload",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert "admin_session" in client.cookies


def _sample_csv() -> bytes:
    """Produce a minimal CSV matching the default column_map in config/countries.json."""
    df = pd.DataFrame({
        "Date": [
            "2024-01-15", "2024-02-10", "2024-03-05",
            "2025-01-20", "2025-02-14",
        ],
        "Country": ["USA"] * 5,
        "Model": ["Model A", "Model B", "Model A", "Model A", "Model B"],
        "Sales": [1000.0, 1500.0, 1200.0, 1800.0, 1700.0],
        "Quantity": [1, 2, 1, 2, 2],
    })
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


# ──────────────────────────────────────────────────────────────────────
# Basic routes
# ──────────────────────────────────────────────────────────────────────

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_empty_overview_renders_welcome(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text.lower()
    assert "upload" in body or "welcome" in body or "no data" in body


# ──────────────────────────────────────────────────────────────────────
# Authentication (username + password, HMAC-signed session cookie)
# ──────────────────────────────────────────────────────────────────────

def test_admin_login_with_correct_credentials_sets_cookie(client):
    r = client.post(
        "/admin/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "next": "/upload"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/upload"
    assert "admin_session" in client.cookies
    # Cookie format is "<username>.<hex-signature>".
    assert client.cookies["admin_session"].startswith(ADMIN_USERNAME + ".")


def test_admin_login_with_wrong_password_redirects_with_error(client):
    r = client.post(
        "/admin/login",
        data={"username": ADMIN_USERNAME, "password": "wrong", "next": "/upload"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/admin/login" in r.headers["location"]
    assert "error=" in r.headers["location"]
    assert "admin_session" not in client.cookies


def test_admin_login_with_wrong_username_redirects_with_error(client):
    r = client.post(
        "/admin/login",
        data={"username": "mallory", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "admin_session" not in client.cookies


def test_x_admin_token_header_no_longer_works(client):
    """Legacy X-Admin-Token path is removed; it must not grant access."""
    r = client.post(
        "/upload/preview",
        files={"file": ("x.csv", b"a,b\n1,2", "text/csv")},
        headers={"X-Admin-Token": ADMIN_PASSWORD},
    )
    assert r.status_code in (401, 403)


def test_upload_requires_admin(client):
    # No session → browser-friendly redirect to login.
    r = client.get("/upload", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/admin/login" in r.headers["location"]

    # The POST endpoints still return 401 JSON (they're API-shaped).
    r2 = client.post(
        "/upload/preview",
        files={"file": ("x.csv", b"a,b\n1,2", "text/csv")},
    )
    assert r2.status_code in (401, 403)


def test_upload_page_after_login(client):
    _login(client)
    r = client.get("/upload")
    assert r.status_code == 200
    assert "upload" in r.text.lower()


def test_admin_logout_clears_cookie(client):
    _login(client)
    r = client.post("/admin/logout", follow_redirects=False)
    assert r.status_code == 303
    # After logout, /upload should redirect again.
    r2 = client.get("/upload", follow_redirects=False)
    assert r2.status_code in (302, 303, 307)


# ──────────────────────────────────────────────────────────────────────
# Upload flow (preview + commit)
# ──────────────────────────────────────────────────────────────────────

def test_upload_preview_returns_mapping(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r = client.post("/upload/preview", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "sample.csv"
    assert body["row_count"] == 5
    assert "Date" in body["columns"]
    assert "Sales" in body["columns"]
    assert "USA" in body["countries_found"]
    per_country = body["per_country"]
    usa = per_country["USA"]
    for field in ("date", "sales"):
        assert field in usa, f"missing suggestion for {field}"


def test_upload_commit_inserts_rows(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r = client.post("/upload/commit", files=files, data={"mapping": "{}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 5
    assert body["skipped_duplicates"] == 0

    # Overview should render a real page with data.
    r2 = client.get("/")
    assert r2.status_code == 200
    assert "USA" in r2.text


def test_duplicate_upload_is_deduplicated(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r1 = client.post("/upload/commit", files=files, data={"mapping": "{}"})
    assert r1.status_code == 200
    assert r1.json()["inserted"] == 5

    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r2 = client.post("/upload/commit", files=files, data={"mapping": "{}"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["inserted"] == 0
    assert body["skipped_duplicates"] == 5


# ──────────────────────────────────────────────────────────────────────
# Filtering + HTMX partials
# ──────────────────────────────────────────────────────────────────────

def test_time_filter_narrows_results(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    client.post("/upload/commit", files=files, data={"mapping": "{}"})

    r = client.get("/", params={"start": "2025-01-01", "end": "2025-01-31", "granularity": "M"})
    assert r.status_code == 200
    assert "USA" in r.text


def test_htmx_partial_omits_full_page(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    client.post("/upload/commit", files=files, data={"mapping": "{}"})

    r = client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text.lower()
    assert "<head" not in r.text.lower()


def test_country_page_routes(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    client.post("/upload/commit", files=files, data={"mapping": "{}"})

    r = client.get("/country/usa")
    assert r.status_code == 200
    assert "USA" in r.text


def test_country_page_404_for_unknown(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    client.post("/upload/commit", files=files, data={"mapping": "{}"})

    r = client.get("/country/atlantis")
    assert r.status_code == 404


def test_overview_includes_stats_chip_and_toolbar(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    client.post("/upload/commit", files=files, data={"mapping": "{}"})

    r = client.get("/")
    html = r.text
    assert 'class="toolbar"' in html
    assert 'class="stats-chip"' in html
    assert "rows" in html
    # Export buttons exist in the toolbar.
    assert 'onclick="exportPng' in html
    assert 'onclick="exportPdf' in html
    assert 'onclick="copyShareLink' in html


# ──────────────────────────────────────────────────────────────────────
# Admin actions (batch delete)
# ──────────────────────────────────────────────────────────────────────

def test_admin_delete_batch(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r = client.post("/upload/commit", files=files, data={"mapping": "{}"})
    batch_id = r.json()["batch_id"]

    r2 = client.post(f"/admin/batches/{batch_id}/delete")
    assert r2.status_code == 200
    assert r2.json()["rows_removed"] == 5

    r3 = client.get("/")
    assert r3.status_code == 200


def test_admin_delete_requires_login(client):
    _login(client)
    files = {"file": ("sample.csv", _sample_csv(), "text/csv")}
    r = client.post("/upload/commit", files=files, data={"mapping": "{}"})
    batch_id = r.json()["batch_id"]

    # Drop the session cookie and try again.
    client.cookies.clear()
    r2 = client.post(f"/admin/batches/{batch_id}/delete")
    assert r2.status_code in (401, 403)
