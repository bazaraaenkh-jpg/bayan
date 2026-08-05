"""Нягтлангийн өөрийн Excel дансны төлөвлөгөөг оруулах.

Шинэ хэрэглэгч бүр олон жил хөтөлсөн төлөвлөгөөтэй ирдэг. Түүнийг гараар
дахин бичүүлэх нь шилжилтийн хамгийн том саад тул багануудыг автоматаар
таних ёстой — тогтмол загвар шаардахгүй.
"""

import io

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from bayan import coa_import
from bayan.models import Account, NormalSide


def _xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(text) -> bytes:
    return text.encode("utf-8")


# ------------------------------------------------------------ багана таних

def test_reads_standard_header():
    data = _xlsx([
        ["Дансны төлөвлөгөө"], [],
        ["Код", "Дансны нэр", "Ердийн тал"],
        ["1101", "Харилцах данс", "Дебит"],
        ["3101", "Дансны өглөг", "Кредит"],
    ])
    res = coa_import.parse(data, "coa.xlsx")
    assert res.header_row == 2
    assert res.columns["code"] == 0 and res.columns["name"] == 1
    assert [a["code"] for a in res.accounts] == ["1101", "3101"]
    assert res.accounts[0]["name"] == "Харилцах данс"


def test_reads_english_header():
    res = coa_import.parse(_xlsx([
        ["Code", "Name"], ["1101", "Bank"], ["5101", "Sales"]]), "coa.xlsx")
    assert len(res.accounts) == 2


def test_columns_in_unusual_order():
    """Нэр эхэнд, код хойно байсан ч таних ёстой."""
    res = coa_import.parse(_xlsx([
        ["Дансны нэр", "Код"], ["Харилцах", "1101"], ["Касс", "1001"]]), "c.xlsx")
    assert res.columns["code"] == 1 and res.columns["name"] == 0
    assert {a["code"] for a in res.accounts} == {"1101", "1001"}


def test_guesses_columns_without_header():
    """Толгойгүй файлыг агуулгаар нь таамаглана."""
    res = coa_import.parse(_xlsx([
        ["1101", "Харилцах данс"], ["1001", "Касс"], ["3101", "Өглөг"]]), "c.xlsx")
    assert res.header_row is None
    assert len(res.accounts) == 3


def test_csv_is_supported():
    res = coa_import.parse(_csv("Код,Нэр\n1101,Харилцах\n5101,Борлуулалт\n"), "coa.csv")
    assert len(res.accounts) == 2


# --------------------------------------------------------------- цэвэрлэгээ

def test_skips_junk_rows_and_reports_them():
    res = coa_import.parse(_xlsx([
        ["Код", "Нэр"],
        ["1101", "Харилцах"],
        ["", ""],
        ["НИЙТ ДҮН", "1,500,000"],
        ["1101", "Давхардсан"],
        ["9999999999999", "Хэт урт код"],
        ["1201", ""],
    ]), "c.xlsx")
    assert [a["code"] for a in res.accounts] == ["1101"]
    joined = " ".join(res.skipped)
    assert "давхардсан" in joined and "нэргүй" in joined


def test_strips_excel_float_codes():
    """Excel код багануудыг 1101.0 гэж уншдаг."""
    res = coa_import.parse(_xlsx([["Код", "Нэр"], ["1101.0", "Харилцах"]]), "c.xlsx")
    assert res.accounts[0]["code"] == "1101"


# ------------------------------------------------------------- ердийн тал

@pytest.mark.parametrize("code, expected", [
    ("1101", "debit"), ("2501", "debit"),
    ("3101", "credit"), ("4101", "credit"), ("5101", "credit"),
    ("6101", "debit"), ("7103", "debit"),
])
def test_normal_side_derived_from_code(code, expected):
    assert coa_import.normal_side_for(code) == expected


def test_explicit_side_column_wins_over_code():
    assert coa_import.normal_side_for("1101", "Кредит") == "credit"
    assert coa_import.normal_side_for("5101", "Дебит") == "debit"


def test_group_accounts_are_not_postable():
    """Өөр дансны угтвар болж буй данс руу бичилт хийж болохгүй (G4)."""
    res = coa_import.parse(_xlsx([
        ["Код", "Нэр"], ["11", "Банкин дахь мөнгө"],
        ["1101", "Харилцах"], ["1102", "Валют"]]), "c.xlsx")
    by = {a["code"]: a for a in res.accounts}
    assert by["11"]["is_postable"] is False
    assert by["1101"]["is_postable"] is True


# ------------------------------------------------------------ санд бичих

def test_apply_creates_accounts_with_parents(session, company):
    accounts = coa_import.parse(_xlsx([
        ["Код", "Нэр"], ["88", "Миний бүлэг"], ["8801", "Миний данс"]]), "c.xlsx").accounts
    out = coa_import.apply(session, company.id, accounts)
    session.flush()

    assert out["created"] == 2
    child = session.scalar(select(Account).where(
        Account.company_id == company.id, Account.code == "8801"))
    parent = session.scalar(select(Account).where(
        Account.company_id == company.id, Account.code == "88"))
    assert child.parent_id == parent.id
    assert child.normal_side == NormalSide.debit


def test_apply_never_touches_existing_accounts(session, company):
    before = session.scalar(select(Account).where(
        Account.company_id == company.id, Account.code == "1101"))
    original_name = before.name

    out = coa_import.apply(session, company.id, [
        {"code": "1101", "name": "ӨӨРЧЛӨХ ГЭСЭН", "normal_side": "credit"},
        {"code": "8802", "name": "Шинэ данс", "normal_side": "debit"},
    ])
    session.flush()

    assert out["created"] == 1 and out["skipped_existing"] == 1
    assert before.name == original_name
    assert before.normal_side == NormalSide.debit


def test_apply_is_idempotent(session, company):
    accounts = [{"code": "8803", "name": "Дахин", "normal_side": "debit"}]
    coa_import.apply(session, company.id, accounts)
    session.flush()
    second = coa_import.apply(session, company.id, accounts)
    assert second["created"] == 0


def test_unrecognisable_file_yields_nothing():
    res = coa_import.parse(_xlsx([["Огноо", "Тайлбар"], ["2026-07-01", "Гүйлгээ"]]), "x.xlsx")
    assert res.accounts == []


# ------------------------------------------------------------- API урсгал

@pytest.fixture
def client():
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    import bayan.api as apimod
    from bayan.db import make_engine

    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    c = TestClient(apimod.app)
    uid = uuid4().hex[:6]
    r = c.post("/api/register", json={
        "email": f"coa_{uid}@bayan.mn", "name": "Нягтлан",
        "password": "pass12345password", "company_name": f"КОА ХХК {uid}"})
    j = r.json()
    return c, j["company_id"], {"Authorization": f"Bearer {j['token']}"}


def _post_import(client, apply):
    c, cid, headers = client
    data = _xlsx([["Код", "Нэр"], ["8810", "Импортын данс"], ["8811", "Хоёр дахь"]])
    return c.post(f"/api/companies/{cid}/accounts/import", headers=headers,
                  data={"apply": "true" if apply else "false"},
                  files=[("file", ("coa.xlsx", data,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])


def test_preview_does_not_write_anything(client):
    c, cid, headers = client
    before = len(c.get(f"/api/companies/{cid}/accounts", headers=headers).json())

    r = _post_import(client, apply=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert body["parsed_count"] == 2
    assert len(body["preview"]) == 2

    after = len(c.get(f"/api/companies/{cid}/accounts", headers=headers).json())
    assert after == before, "урьдчилан харах үед санд бичигдэх ёсгүй"


def test_apply_creates_the_accounts(client):
    c, cid, headers = client
    r = _post_import(client, apply=True)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2

    codes = {a["code"] for a in c.get(f"/api/companies/{cid}/accounts", headers=headers).json()}
    assert {"8810", "8811"} <= codes


def test_unreadable_file_returns_helpful_error(client):
    c, cid, headers = client
    r = c.post(f"/api/companies/{cid}/accounts/import", headers=headers,
               data={"apply": "false"},
               files=[("file", ("junk.csv", b"a,b\n1,2\n", "text/csv"))])
    assert r.status_code == 422
    assert "багана" in r.json()["detail"]
