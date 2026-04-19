"""SQLAlchemy models + session factory for persistent sales data."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import pandas as pd
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def _database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    path = Path(os.environ.get("DB_PATH", str(DEFAULT_DB_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


class Base(DeclarativeBase):
    pass


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    sales = relationship("Sale", back_populates="batch", cascade="all, delete-orphan")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    country = Column(String, nullable=False, index=True)
    display_name = Column(String)
    currency = Column(String)
    sales = Column(Float, nullable=False)
    quantity = Column(Integer, default=0)
    model = Column(String)
    source_file = Column(String)
    batch_id = Column(Integer, ForeignKey("upload_batches.id"), index=True)

    batch = relationship("UploadBatch", back_populates="sales")

    __table_args__ = (
        UniqueConstraint(
            "date", "country", "model", "sales", "quantity", "source_file",
            name="uq_sale_row",
        ),
        Index("ix_sales_country_date", "country", "date"),
    )


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = _database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def session_scope() -> Iterator[Session]:
    """FastAPI dependency: yields a Session, commits on success, rolls back on error."""
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def load_sales_df(session: Session) -> pd.DataFrame:
    """Read all sales rows into the standard normalized DataFrame shape."""
    rows = session.execute(
        Sale.__table__.select().order_by(Sale.date.asc())
    ).mappings().all()
    if not rows:
        return pd.DataFrame(
            columns=["date", "country", "display_name", "currency",
                     "sales", "quantity", "model", "source_file"]
        )
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["sales"] = df["sales"].astype(float)
    df["quantity"] = df["quantity"].fillna(0).astype(int)
    df["model"] = df["model"].fillna("Unknown").astype(str)
    return df


def insert_batch(
    session: Session,
    df: pd.DataFrame,
    filename: str,
) -> tuple[int, int, int]:
    """Insert rows from a normalized DataFrame as one batch.

    Returns (batch_id, inserted_count, skipped_count).
    Uses INSERT OR IGNORE so that rows already present (by unique constraint) are skipped.
    """
    if df.empty:
        raise ValueError("empty DataFrame — nothing to insert")

    batch = UploadBatch(filename=filename, row_count=len(df))
    session.add(batch)
    session.flush()  # assigns batch.id

    records = []
    for row in df.itertuples(index=False):
        d = row.date
        if isinstance(d, pd.Timestamp):
            d = d.date()
        elif isinstance(d, datetime):
            d = d.date()
        records.append(
            {
                "date": d,
                "country": str(row.country),
                "display_name": getattr(row, "display_name", None) or str(row.country),
                "currency": getattr(row, "currency", None) or "USD",
                "sales": float(row.sales),
                "quantity": int(getattr(row, "quantity", 0) or 0),
                "model": str(getattr(row, "model", "Unknown") or "Unknown"),
                "source_file": filename,
                "batch_id": batch.id,
            }
        )

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(Sale).values(records)
        stmt = stmt.on_conflict_do_nothing(index_elements=[
            "date", "country", "model", "sales", "quantity", "source_file"
        ])
        result = session.execute(stmt)
        inserted = result.rowcount if result.rowcount is not None else 0
    else:
        # Fallback: plain insert, let unique constraint fail one-by-one.
        inserted = 0
        for rec in records:
            try:
                session.execute(Sale.__table__.insert().values(**rec))
                inserted += 1
            except Exception:
                session.rollback()

    skipped = len(records) - inserted
    batch.row_count = inserted
    return batch.id, inserted, skipped


def delete_batch(session: Session, batch_id: int) -> int:
    batch = session.get(UploadBatch, batch_id)
    if not batch:
        return 0
    n = len(batch.sales)
    session.delete(batch)
    return n


def stats(session: Session) -> dict:
    total = session.query(func.count(Sale.id)).scalar() or 0
    min_d = session.query(func.min(Sale.date)).scalar()
    max_d = session.query(func.max(Sale.date)).scalar()
    batches = session.query(func.count(UploadBatch.id)).scalar() or 0
    country_count = session.query(func.count(func.distinct(Sale.country))).scalar() or 0
    latest = session.query(func.max(UploadBatch.created_at)).scalar()
    return {
        "rows": int(total),
        "min_date": min_d,
        "max_date": max_d,
        "batches": int(batches),
        "country_count": int(country_count),
        "latest_uploaded_at": latest,
    }
