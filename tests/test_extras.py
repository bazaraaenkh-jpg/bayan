"""Дотоод шилжүүлэг, НӨАТ ТТ-03а, нэвтрэлт, PDF задлалтын цөм логик."""

from datetime import date, datetime

import pytest
from bayan import auth, vat
from bayan.extract_pdf import rows_to_statement
from bayan.internal_transfers import detect
from bayan.models import BankTxn, Direction, ExtractionPath
from bayan.partners import Counterparty, InvoiceKind, post_invoice
from bayan.registry import load_registry


def _txn(acct, direction, amount, cp, day=15):
    return BankTxn(
        statement_id="s", company_id="c", bank_account_key=acct, seq_no=1,
        posted_at=datetime(2026, 6, day, 10, 0),
        direction=Direction(direction), amount_minor=amount,
        counterparty_account=cp, description_raw="x", description_norm="x",
        canonical_hash=f"{acct}{direction}{amount}{day}",
        extraction_path=ExtractionPath.excel)


# ------------------------------------------------------- дотоод шилжүүлэг

def test_internal_mirror_dedupe():
    own = {"A1": "110101", "A2": "110102"}
    txns = [
        _txn("A1", "debit", 100, "A2"),    # эх бичилт
        _txn("A2", "credit", 100, "A1"),   # толин тусгал → алгасна
        _txn("A2", "credit", 999, "A1"),   # mirror-гүй → бүртгэнэ
        _txn("A1", "debit", 50, "A1"),     # cp == өөрөө → ГАДААД (ХХБ-ны онцлог!)
        _txn("A1", "credit", 70, "B9"),    # энгийн гадаад
    ]
    res = detect(txns, own)
    assert len(res.mirrors) == 1 and res.mirrors[0].amount_minor == 100
    assert len(res.internal) == 2          # эх дебит + mirror-гүй кредит
    assert len(res.external) == 2


# ------------------------------------------------------- НӨАТ ТТ-03а

def test_tt03a(session, company):
    cp = Counterparty(company_id=company.id, name="Х")
    session.add(cp); session.flush()
    post_invoice(session, company.id, cp.id, InvoiceKind.sales, "S1",
                 date(2026, 6, 5), date(2026, 6, 20), 5_000_000_00, with_vat=True)
    post_invoice(session, company.id, cp.id, InvoiceKind.purchase, "P1",
                 date(2026, 6, 8), date(2026, 6, 25), 1_200_000_00,
                 with_vat=True, expense_account="7103")
    r = vat.tt03a(session, company.id, 2026, 6)["rows"]
    assert r["26_nogduulsan_tatvar"] == 500_000_00
    assert r["49_hasagdah_noat"] == 120_000_00
    assert r["64_etssiin_tolboh"] == 380_000_00
    assert r["65_etssiin_butsaan_avah"] == 0
    # өөр сард юу ч байхгүй
    assert vat.tt03a(session, company.id, 2026, 7)["rows"]["31_nogduulsan_niit"] == 0


def test_ebarimt_reconcile(session, company):
    cp = Counterparty(company_id=company.id, name="Х")
    session.add(cp); session.flush()
    inv = post_invoice(session, company.id, cp.id, InvoiceKind.sales, "S9",
                       date(2026, 6, 5), date(2026, 6, 20),
                       2_000_000_00, with_vat=True)
    res = vat.reconcile_ebarimt(session, company.id, [
        {"date": "2026-06-05", "total_minor": inv.total_minor, "receipt_id": "EB1"},
        {"date": "2026-06-09", "total_minor": 777, "receipt_id": "EB2"},
    ], 2026, 6)
    assert len(res["matched"]) == 1
    assert res["unmatched_receipts"][0]["receipt_id"] == "EB2"


# ------------------------------------------------------- нэвтрэлт, эрх

def test_auth_roundtrip(session, company):
    user = auth.create_user(session, "a@b.mn", "Бат", "нууц12345")
    auth.add_membership(session, user.id, company.id, "accountant")
    token = auth.login(session, "A@B.MN", "нууц12345")
    payload = auth.parse_token(token)
    role = auth.get_role(session, payload["uid"], company.id)
    assert role == "accountant"
    auth.require(role, "post")                          # зөвшөөрөгдөнө
    with pytest.raises(auth.AuthError):
        auth.require(role, "admin")                     # owner биш
    with pytest.raises(auth.AuthError):
        auth.require(None, "read")                      # гишүүн биш
    with pytest.raises(auth.AuthError):
        auth.login(session, "a@b.mn", "буруу1234")
    with pytest.raises(auth.AuthError):
        auth.parse_token(token[:-4] + "0000")           # эвдэрсэн гарын үсэг
    # нууц үг солих + reset токен
    auth.change_password(session, user.id, "нууц12345", "шинэ12345")
    auth.login(session, "a@b.mn", "шинэ12345")
    rt = auth.make_reset_token(user.id)
    auth.apply_reset_token(session, rt, "дахин1234")
    auth.login(session, "a@b.mn", "дахин1234")


# ------------------------------------------------------- PDF (Зам Б) цөм логик

def test_pdf_rows_to_statement():
    desc = next(d for d in load_registry() if d.bank == "khan")
    header = ["Гүйлгээний огноо", "Салбар", "Дебит гүйлгээ", "Кредит гүйлгээ",
              "Үлдэгдэл", "Харьцсан данс", "Гүйлгээний утга"]
    page1 = [header,
             ["2026.03.02 10:15", "УБ", None, "1,500,000.00", "5,500,000.00",
              "5099887766", "Борлуулалт эхлэл"],
             [None, None, None, None, None, None, "үргэлжлэл хоёр дахь мөр"]]
    page2 = [header,        # давтагдсан толгой — алгасагдана
             ["2026.03.05 14:00", "УБ", "400,000.00", None, "5,100,000.00",
              None, "Түрээс"],
             ["Нийт дүн", None, "400,000.00", "1,500,000.00", None, None, None]]
    meta_text = ("Дансны дугаар: 5041234567\nЭхний үлдэгдэл: 4,000,000.00\n"
                 "Эцсийн үлдэгдэл: 5,100,000.00\nХуулгын хугацаа: 2026.03.01 - 2026.03.31")
    stmt = rows_to_statement([page1, page2], meta_text, desc)

    assert stmt.metadata["opening_balance_minor"] == 400_000_000
    assert stmt.metadata["closing_balance_minor"] == 510_000_000
    assert len(stmt.rows) == 2                          # 3 биш: үргэлжлэл нийлсэн
    assert "үргэлжлэл" in stmt.rows[0].values["description"]
    assert stmt.rows[1].values["debit"] == "400,000.00"


# ------------------------------------------------------- Audit Logging
def test_audit_logging(session, company):
    from bayan.context import current_actor_id, current_company_id
    from bayan.models import AuditLog
    from bayan.partners import Counterparty
    from sqlalchemy import select

    # Set context variables
    current_actor_id.set("test_actor")
    current_company_id.set(company.id)

    # 1. Test Insert
    cp = Counterparty(company_id=company.id, name="Test Partner", reg_no="999999")
    session.add(cp)
    session.flush()

    # Verify insert log was created
    log1 = session.scalar(
        select(AuditLog).where(AuditLog.action == "insert", AuditLog.entity == "counterparty")
    )
    assert log1 is not None
    assert log1.actor_id == "test_actor"
    assert log1.company_id == company.id
    assert log1.detail["new"]["name"] == "Test Partner"

    # 2. Test Update
    cp.name = "Updated Partner"
    session.flush()

    # Verify update log was created
    log2 = session.scalars(
        select(AuditLog).where(AuditLog.action == "update", AuditLog.entity == "counterparty")
    ).first()
    assert log2 is not None
    assert log2.actor_id == "test_actor"
    assert log2.detail["old"]["name"] == "Test Partner"
    assert log2.detail["new"]["name"] == "Updated Partner"

    # 3. Test Delete
    session.delete(cp)
    session.flush()

    # Verify delete log was created
    log3 = session.scalars(
        select(AuditLog).where(AuditLog.action == "delete", AuditLog.entity == "counterparty")
    ).first()
    assert log3 is not None
    assert log3.actor_id == "test_actor"
    assert log3.detail["old"]["name"] == "Updated Partner"


# ------------------------------------------------------- Ebarimt API
def test_ebarimt_client(session, company):
    from bayan import ebarimt
    from bayan.partners import Invoice, InvoiceKind
    from sqlalchemy import select

    client = ebarimt.EbarimtClient()
    
    # 1. Test creation
    res = client.create_receipt(amount_minor=110000, vat_minor=10000)
    assert res["success"]
    assert "receipt_id" in res
    assert "qr_data" in res

    # 2. Test void
    void_res = client.void_receipt(res["receipt_id"])
    assert void_res["success"]

    # 3. Test purchase fetch
    invoices = client.fetch_purchase_invoices(2026, 3)
    assert len(invoices) == 1
    assert invoices[0]["supplier_tin"] == "5011223344"
