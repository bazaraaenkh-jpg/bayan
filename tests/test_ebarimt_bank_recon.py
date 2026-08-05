"""eBarimt ↔ банкны хуулгын тулгалтын endpoint.

Тулгаагүй банкны гүйлгээг буцаах салаа нь BankTxn-д байхгүй талбарт хандаж
500 (JSON бус) буцаадаг байсан — хэрэглэгчийн талд "is not valid JSON"
алдаа болж харагддаг байв.
"""

import io
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import bayan.api as apimod
from bayan.db import make_engine
from bayan.models import BankTxn, Direction, ExtractionPath, Statement, StatementStatus


@pytest.fixture
def client():
    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    return TestClient(apimod.app)


def _register(client):
    uid = uuid4().hex[:6]
    r = client.post("/api/register", json={
        "email": f"eb_{uid}@bayan.mn", "name": "Нягтлан",
        "password": "pass12345password", "company_name": f"eBarimt ХХК {uid}",
    })
    assert r.status_code == 200, r.text
    j = r.json()
    return j["company_id"], {"Authorization": f"Bearer {j['token']}"}


def _add_bank_txn(company_id, *, amount_minor, seq=1, counterparty_name=None):
    s = apimod.SessionLocal()
    stmt = Statement(company_id=company_id, file_name="test.xls",
                     file_sha256=uuid4().hex, descriptor_id="tdb.internet_bank.xls.v1",
                     status=StatementStatus.parsed_verified)
    s.add(stmt); s.flush()
    s.add(BankTxn(
        statement_id=stmt.id, company_id=company_id, bank_account_key="413108778",
        seq_no=seq, posted_at=datetime(2026, 7, 15, 10, 0),
        direction=Direction.credit, amount_minor=amount_minor,
        balance_after_minor=amount_minor,
        counterparty_account=None, counterparty_name=counterparty_name,
        description_raw="ГҮЙЛГЭЭНИЙ УТГА", description_norm="гүйлгээний утга",
        channel_ref=None, canonical_hash=uuid4().hex,
        extraction_path=ExtractionPath.excel,
    ))
    s.commit(); s.close()


def _ebarimt_csv(rows):
    out = io.StringIO()
    out.write("Огноо,Нийт дүн,ДДТД,Харилцагч\n")
    for date, amount, ddtd, party in rows:
        out.write(f"{date},{amount},{ddtd},{party}\n")
    return out.getvalue().encode("utf-8")


def _post(client, company_id, headers, csv_bytes):
    return client.post(
        f"/api/companies/{company_id}/ebarimt/reconcile-bank",
        headers=headers, data={"mode": "excel"},
        files=[("files", ("ebarimt.csv", csv_bytes, "text/csv"))],
    )


def test_unmatched_bank_txn_does_not_500(client):
    """Тулгаагүй банкны гүйлгээ байхад ч JSON хариу буцаана."""
    company_id, headers = _register(client)
    _add_bank_txn(company_id, amount_minor=50_000_00)

    # eBarimt-д огт таарахгүй дүн — банкны гүйлгээ NO_EBARIMT салаагаар явна
    r = _post(client, company_id, headers,
              _ebarimt_csv([("2026-07-10", "999.00", "EB-1", "Нийлүүлэгч")]))

    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    no_eb = [i for i in j["items"] if i["status"] == "NO_EBARIMT"]
    assert len(no_eb) == 1
    # Талбарын нэр буруу байсан газар — утга бөглөгдсөн байх ёстой
    assert no_eb[0]["party"]
    assert no_eb[0]["total_mnt"] == 50_000.00


def test_unmatched_bank_txn_uses_counterparty_name(client):
    company_id, headers = _register(client)
    _add_bank_txn(company_id, amount_minor=50_000_00, counterparty_name="НОМИН ГЭГЭЭ ХХК")

    r = _post(client, company_id, headers,
              _ebarimt_csv([("2026-07-10", "999.00", "EB-1", "Нийлүүлэгч")]))
    assert r.status_code == 200, r.text
    no_eb = [i for i in r.json()["items"] if i["status"] == "NO_EBARIMT"]
    assert no_eb[0]["party"] == "НОМИН ГЭГЭЭ ХХК"


def test_matching_ebarimt_and_bank_amounts(client):
    company_id, headers = _register(client)
    _add_bank_txn(company_id, amount_minor=120_000_00)

    r = _post(client, company_id, headers,
              _ebarimt_csv([("2026-07-15", "120000.00", "EB-9", "Харилцагч")]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["matched_count"] == 1
    assert j["matched_amount_mnt"] == 120_000.00
    assert j["unmatched_bank_count"] == 0
    assert [i["status"] for i in j["items"]] == ["MATCHED"]
