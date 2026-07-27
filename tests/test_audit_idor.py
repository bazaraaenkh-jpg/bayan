"""АУДИТ: cross-tenant объектын хандалт (IDOR) байгаа эсэхийг батлах тест.

Хэрэглэгч Б өөрийн компанийн company_id-гаар guard-ыг давж, БУСДЫН
(компани А-гийн) entry_id дээр үйлдэл хийж чадаж байна уу?
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import bayan.api as apimod
from bayan import ledger
from bayan.db import make_engine
from bayan.models import EntryStatus, JournalEntry


@pytest.fixture
def client():
    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    return TestClient(apimod.app)


def _register(client, email, company):
    r = client.post("/api/register", json={
        "email": email, "name": "Т", "password": "нууц12345",
        "company_name": company})
    assert r.status_code == 200, r.text
    return r.json()


def test_cross_tenant_entry_approve_is_blocked(client):
    """Б компанийн хэрэглэгч А компанийн журналын бичилтийг батлаж БОЛОХГҮЙ."""
    ja = _register(client, "a@x.mn", "Альфа ХХК")
    jb = _register(client, "b@x.mn", "Бета ХХК")

    # А компанид бичилт үүсгэнэ
    db = apimod.SessionLocal()
    entry = ledger.post_entry(db, ja["company_id"], date(2026, 3, 1), [
        ledger.LineInput("1001", debit_minor=100_000, description="А-гийн мөнгө"),
        ledger.LineInput("5101", credit_minor=100_000),
    ])
    entry_id = entry.id
    db.commit(); db.close()

    # Б хэрэглэгч ӨӨРИЙН company_id-гаар guard давж, А-гийн entry_id-г дамжуулна
    hb = {"Authorization": f"Bearer {jb['token']}"}
    r = client.post(
        f"/api/companies/{jb['company_id']}/entries/{entry_id}/approve",
        headers=hb)

    assert r.status_code in (403, 404), (
        f"IDOR: Б компани А-гийн бичилтэд хандаж чадлаа ({r.status_code})")


def test_cross_tenant_invoice_counterparty_is_blocked(client):
    """Б компани А компанийн харилцагчийн ID-гаар нэхэмжлэх үүсгэж БОЛОХГҮЙ."""
    ja = _register(client, "a@x.mn", "Альфа ХХК")
    jb = _register(client, "b@x.mn", "Бета ХХК")
    ha = {"Authorization": f"Bearer {ja['token']}"}
    hb = {"Authorization": f"Bearer {jb['token']}"}

    cp = client.post(f"/api/companies/{ja['company_id']}/counterparties",
                     headers=ha, json={"name": "А-гийн харилцагч"}).json()

    r = client.post(f"/api/companies/{jb['company_id']}/invoices", headers=hb,
                    json={"counterparty_id": cp["id"], "kind": "sales",
                          "number": "X-1", "issue_date": "2026-03-01",
                          "due_date": "2026-03-15", "net_minor": 100_000})
    assert r.status_code in (403, 404, 422), (
        f"IDOR: Б компани А-гийн харилцагчаар нэхэмжлэх үүсгэлээ ({r.status_code})")
