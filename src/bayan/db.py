"""DB холболт. Хөгжүүлэлтэд SQLite, продакшнд PostgreSQL (RLS + trigger).

Продакшны тэмдэглэл: G1-G3 инвариантыг энд сервисийн давхаргад (ledger.py)
албаддаг бөгөөд PostgreSQL дээр нэмж DB trigger-ээр давхарлана
(migrations/001_pg_triggers.sql — Үе 1-ийн deploy даалгавар).
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from . import assets, assistant, audit, auth, etax, inventory, partners, salary, wip, models  # noqa: F401
from .models import Base

_DEFAULT_URL = "sqlite:///bayan.db"


def _run_migrations(engine):
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE employee ADD COLUMN work_condition VARCHAR(30) DEFAULT 'normal'"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE payroll_line ADD COLUMN is_manual BOOLEAN DEFAULT 0"))
            conn.commit()
        except Exception:
            pass
        try:
            # TimeSheet-д нэмэгдсэн чөлөөний хоног. Энэ мөргүй бол одоо
            # байгаа сан дээр цалингийн урьдчилсан тооцоо 500 өгнө.
            conn.execute(text(
                "ALTER TABLE time_sheet ADD COLUMN unpaid_leave_days FLOAT DEFAULT 0"))
            conn.commit()
        except Exception:
            pass


def make_engine(url: str = _DEFAULT_URL):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, future=True, **kwargs)
    Base.metadata.create_all(engine)
    _run_migrations(engine)
    return engine


def make_session(url: str = _DEFAULT_URL) -> Session:
    return sessionmaker(bind=make_engine(url), future=True)()
