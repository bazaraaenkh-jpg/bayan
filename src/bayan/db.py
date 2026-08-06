"""DB холболт. Хөгжүүлэлтэд SQLite, продакшнд PostgreSQL (RLS + trigger).

Продакшны тэмдэглэл: G1-G3 инвариантыг энд сервисийн давхаргад (ledger.py)
албаддаг бөгөөд PostgreSQL дээр нэмж DB trigger-ээр давхарлана
(migrations/001_pg_triggers.sql — Үе 1-ийн deploy даалгавар).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import assets, assistant, audit, auth, inventory, partners, salary, wip, models  # noqa: F401
from .models import Base

_DEFAULT_URL = "sqlite:///bayan.db"


def make_engine(url: str = _DEFAULT_URL):
    kwargs = {}
    if url.startswith("sqlite"):
        # Вэб сервер олон thread-ээс ханддаг; :memory: бол нэг connection хуваалцана
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, future=True, **kwargs)
    Base.metadata.create_all(engine)
    return engine


def make_session(url: str = _DEFAULT_URL) -> Session:
    return sessionmaker(bind=make_engine(url), future=True)()
