"""Хуулга ба компани устгах — эргэшгүй үйлдлийн хамгаалалт.

Устгах нь буцаах боломжгүй тул хоёр зүйлийг тусгайлан шалгана:
дэвтэрт бичигдсэн гүйлгээтэй хуулга устахгүй байх, компанийн нэрээр
баталгаажуулаагүй бол компани устахгүй байх.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import bayan.api as apimod
from bayan.db import make_engine
from bayan.models import (BankTxn, ClassificationSuggestion, Company, Direction,
                          ExtractionPath, Statement, StatementStatus)


@pytest.fixture
def client():
    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    return TestClient(apimod.app)


def _register(client, superadmin=False):
    uid = uuid4().hex[:6]
    email = f"del_{uid}@bayan.mn"
    r = client.post("/api/register", json={
        "email": email, "name": "Нягтлан",
        "password": "pass12345password", "company_name": f"Устгах ХХК {uid}"})
    assert r.status_code == 200, r.text
    j = r.json()
    if superadmin:
        from sqlalchemy import select as sa_select
        s = apimod.SessionLocal()
        # ЯГ энэ бүртгэлийг эрхжүүлнэ — эхний хэрэглэгчийг биш
        u = s.scalar(sa_select(apimod.auth.User).where(apimod.auth.User.email == email))
        u.is_superadmin = True
        s.commit(); s.close()
    return j["company_id"], {"Authorization": f"Bearer {j['token']}"}


def _add_statement(company_id, *, reconciled=False, txns=2):
    s = apimod.SessionLocal()
    stmt = Statement(company_id=company_id, file_name="ST_TEST.xls",
                     file_sha256=uuid4().hex, descriptor_id="tdb.internet_bank.xls.v1",
                     status=StatementStatus.parsed_verified)
    s.add(stmt); s.flush()
    for i in range(txns):
        t = BankTxn(
            statement_id=stmt.id, company_id=company_id, bank_account_key="k",
            seq_no=i + 1, posted_at=datetime(2026, 7, 1), direction=Direction.credit,
            amount_minor=10_000_00, balance_after_minor=None,
            counterparty_account=None, counterparty_name=None,
            description_raw="ГҮЙЛГЭЭ", description_norm="гүйлгээ",
            channel_ref=None, canonical_hash=uuid4().hex,
            extraction_path=ExtractionPath.excel,
            reconciled_line_id=("line-1" if reconciled and i == 0 else None))
        s.add(t); s.flush()
        s.add(ClassificationSuggestion(
            bank_txn_id=t.id, company_id=company_id, account_code="7106",
            confidence=0.9, rationale="тест", source="rule"))
    s.commit()
    sid = stmt.id
    s.close()
    return sid


def _counts():
    s = apimod.SessionLocal()
    from sqlalchemy import func, select
    out = {
        "statement": s.scalar(select(func.count()).select_from(Statement)),
        "bank_txn": s.scalar(select(func.count()).select_from(BankTxn)),
        "suggestion": s.scalar(select(func.count()).select_from(ClassificationSuggestion)),
        "company": s.scalar(select(func.count()).select_from(Company)),
    }
    s.close()
    return out


# ------------------------------------------------------------ хуулга устгах

def test_delete_statement_removes_txns_and_suggestions(client):
    company_id, headers = _register(client)
    sid = _add_statement(company_id, txns=3)
    assert _counts()["bank_txn"] == 3

    r = client.delete(f"/api/companies/{company_id}/statements/{sid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["deleted_txns"] == 3

    c = _counts()
    assert c["statement"] == 0 and c["bank_txn"] == 0 and c["suggestion"] == 0
    assert c["company"] == 1                      # компани хэвээр


def test_posted_statement_is_protected(client):
    """Дэвтэрт бичигдсэн гүйлгээтэй хуулга устахгүй."""
    company_id, headers = _register(client)
    sid = _add_statement(company_id, reconciled=True, txns=2)

    r = client.delete(f"/api/companies/{company_id}/statements/{sid}", headers=headers)
    assert r.status_code == 409, r.text
    assert "буцаана" in r.json()["detail"]
    assert _counts()["bank_txn"] == 2             # юу ч устаагүй


def test_cannot_delete_statement_of_another_company(client):
    company_id, headers = _register(client)
    other_id, _ = _register(client)
    sid = _add_statement(other_id)

    r = client.delete(f"/api/companies/{company_id}/statements/{sid}", headers=headers)
    assert r.status_code == 404
    assert _counts()["statement"] == 1


def test_missing_statement_returns_404(client):
    company_id, headers = _register(client)
    r = client.delete(f"/api/companies/{company_id}/statements/does-not-exist",
                      headers=headers)
    assert r.status_code == 404


# ----------------------------------------------------------- компани устгах

def test_company_delete_requires_superadmin(client):
    company_id, headers = _register(client)
    s = apimod.SessionLocal()
    name = s.get(Company, company_id).name
    s.close()

    r = client.delete(f"/api/admin/companies/{company_id}",
                      params={"confirm_name": name}, headers=headers)
    assert r.status_code == 403
    assert _counts()["company"] == 1


def test_company_delete_requires_exact_name(client):
    company_id, headers = _register(client, superadmin=True)
    r = client.delete(f"/api/admin/companies/{company_id}",
                      params={"confirm_name": "буруу нэр"}, headers=headers)
    assert r.status_code == 400
    assert _counts()["company"] == 1


def test_superadmin_purges_company_and_all_children(client):
    company_id, headers = _register(client, superadmin=True)
    _add_statement(company_id, txns=4)
    s = apimod.SessionLocal()
    name = s.get(Company, company_id).name
    s.close()

    r = client.delete(f"/api/admin/companies/{company_id}",
                      params={"confirm_name": name}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["statements"] == 1
    assert body["counts"]["bank_txns"] == 4

    c = _counts()
    assert c == {"statement": 0, "bank_txn": 0, "suggestion": 0, "company": 0}


def test_purge_leaves_other_companies_untouched(client):
    keep_id, _ = _register(client)
    _add_statement(keep_id, txns=2)

    drop_id, headers = _register(client, superadmin=True)
    _add_statement(drop_id, txns=3)

    s = apimod.SessionLocal()
    name = s.get(Company, drop_id).name
    s.close()

    r = client.delete(f"/api/admin/companies/{drop_id}",
                      params={"confirm_name": name}, headers=headers)
    assert r.status_code == 200, r.text

    c = _counts()
    assert c["company"] == 1 and c["statement"] == 1 and c["bank_txn"] == 2
