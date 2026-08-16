"""Худалдан авалт / авлага / өглөг — төлбөрийн мөчлөг, тулгалт, хяналтууд."""

from datetime import date

import pytest
from bayan import ledger, partners, reports
from bayan.models import EntryStatus
from bayan.partners import InvoiceKind


def _cp(session, company, name="Харилцагч"):
    cp = partners.Counterparty(company_id=company.id, name=name)
    session.add(cp); session.flush()
    return cp


def _sales(session, company, cp, number="S-1", net=10_000_000_00, **kw):
    kw.setdefault("issue_date", date(2026, 3, 1))
    kw.setdefault("due_date", date(2026, 3, 31))
    return partners.post_invoice(session, company.id, cp.id, InvoiceKind.sales,
                                 number, net_minor=net, **kw)


# ------------------------------------------------------------ төлбөрийн мөчлөг

def test_pay_sales_invoice_closes_ar(session, company):
    """Авлагын төлбөр GL ба дэд дансыг ЗЭРЭГ хаана."""
    cp = _cp(session, company)
    inv = _sales(session, company, cp, net=10_000_000_00, with_vat=True)
    assert inv.outstanding_minor == 11_000_000_00

    res = partners.pay_invoice(session, company.id, inv, 4_000_000_00,
                               date(2026, 4, 5))
    assert res["settled"] is False
    assert res["outstanding_minor"] == 7_000_000_00
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1201"]["balance_minor"] == 7_000_000_00      # GL
    assert tb["1101"]["balance_minor"] == 4_000_000_00

    res = partners.pay_invoice(session, company.id, inv, 7_000_000_00,
                               date(2026, 4, 20))
    assert res["settled"] is True
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1201"]["balance_minor"] == 0
    assert inv.outstanding_minor == 0


def test_pay_purchase_invoice_closes_ap(session, company):
    cp = _cp(session, company)
    inv = partners.post_invoice(session, company.id, cp.id, InvoiceKind.purchase,
                                "P-1", date(2026, 3, 1), date(2026, 3, 31),
                                5_000_000_00, expense_account="7103")
    partners.pay_invoice(session, company.id, inv, 5_000_000_00, date(2026, 4, 1))
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["3101"]["balance_minor"] == 0
    assert tb["1101"]["balance_minor"] == -5_000_000_00
    assert inv.outstanding_minor == 0


def test_overpayment_rejected(session, company):
    cp = _cp(session, company)
    inv = _sales(session, company, cp, net=1_000_000_00)
    with pytest.raises(ledger.LedgerError, match="илүү төлөлт"):
        partners.pay_invoice(session, company.id, inv, 1_500_000_00,
                             date(2026, 4, 1))
    with pytest.raises(ledger.LedgerError):
        partners.pay_invoice(session, company.id, inv, 0, date(2026, 4, 1))
    assert inv.paid_minor == 0


# ------------------------------------------------------------ тулгалт

def test_subledger_reconciles_with_gl(session, company):
    """Дэд данс ↔ GL 1201/3101 — АУ-ийн үндсэн хяналт."""
    cp = _cp(session, company)
    s1 = _sales(session, company, cp, "S-1", 10_000_000_00, with_vat=True)
    _sales(session, company, cp, "S-2", 3_000_000_00)
    partners.post_invoice(session, company.id, cp.id, InvoiceKind.purchase,
                          "P-1", date(2026, 3, 2), date(2026, 4, 2),
                          6_000_000_00, expense_account="7103")

    rec = partners.subledger_reconciliation(session, company.id, date(2026, 3, 31))
    assert rec["receivable"]["matched"], rec["receivable"]
    assert rec["payable"]["matched"], rec["payable"]

    partners.pay_invoice(session, company.id, s1, 11_000_000_00, date(2026, 4, 10))
    rec = partners.subledger_reconciliation(session, company.id, date(2026, 4, 30))
    assert rec["receivable"]["matched"]
    assert rec["receivable"]["subledger_minor"] == 3_000_000_00

    # Төлбөрийн ӨМНӨХ өдрөөр харвал төлөгдөөгүй хэвээр харагдана
    rec = partners.subledger_reconciliation(session, company.id, date(2026, 4, 1))
    assert rec["receivable"]["subledger_minor"] == 14_000_000_00
    assert rec["receivable"]["matched"]


# ------------------------------------------------------------ насжилт

def test_aging_respects_as_of(session, company):
    """as_of-оос хойших нэхэмжлэх ба төлбөр тухайн үеийн тайланд орохгүй."""
    cp = _cp(session, company)
    inv = _sales(session, company, cp, "S-1", 5_000_000_00,
                 issue_date=date(2026, 3, 1), due_date=date(2026, 3, 31))
    _sales(session, company, cp, "S-FUTURE", 9_000_000_00,
           issue_date=date(2026, 12, 1), due_date=date(2026, 12, 31))

    rows = partners.aging_report(session, company.id, InvoiceKind.sales,
                                 date(2026, 5, 10))
    assert [r["number"] for r in rows] == ["S-1"]          # ирээдүйнх орохгүй

    # 6-р сард төлөв — 5-р сарын насжилтад төлөгдөөгүй хэвээр
    partners.pay_invoice(session, company.id, inv, 5_000_000_00, date(2026, 6, 1))
    rows = partners.aging_report(session, company.id, InvoiceKind.sales,
                                 date(2026, 5, 10))
    assert rows[0]["outstanding_minor"] == 5_000_000_00
    rows = partners.aging_report(session, company.id, InvoiceKind.sales,
                                 date(2026, 6, 30))
    assert [r["number"] for r in rows] == []               # хаагдсан


def test_aging_separates_not_due_from_overdue(session, company):
    cp = _cp(session, company)
    _sales(session, company, cp, "S-NOTDUE", 1_000_000_00,
           issue_date=date(2026, 3, 1), due_date=date(2026, 4, 30))
    _sales(session, company, cp, "S-LATE", 2_000_000_00,
           issue_date=date(2026, 3, 1), due_date=date(2026, 3, 20))

    rows = {r["number"]: r for r in partners.aging_report(
        session, company.id, InvoiceKind.sales, date(2026, 4, 1))}
    assert rows["S-NOTDUE"]["buckets"][0] == 1_000_000_00   # хугацаа болоогүй
    assert rows["S-NOTDUE"]["overdue_days"] == 0
    assert rows["S-LATE"]["buckets"][1] == 2_000_000_00     # 0-30 хоцорсон


# ------------------------------------------------------------ хяналтууд

def test_duplicate_invoice_number_rejected(session, company):
    cp = _cp(session, company)
    _sales(session, company, cp, "S-1")
    with pytest.raises(ledger.LedgerError, match="аль хэдийн бүртгэгдсэн"):
        _sales(session, company, cp, "S-1")
    # өөр төрөлд ижил дугаар зөвшөөрөгдөнө
    partners.post_invoice(session, company.id, cp.id, InvoiceKind.purchase,
                          "S-1", date(2026, 3, 1), date(2026, 3, 31),
                          1_000_000_00, expense_account="7103")
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1201"]["balance_minor"] == 10_000_000_00     # давхардаагүй


def test_zero_or_negative_invoice_rejected(session, company):
    cp = _cp(session, company)
    for amount in (0, -3_000_000_00):
        with pytest.raises(ledger.LedgerError, match="0-ээс их байх ёстой"):
            _sales(session, company, cp, f"S-{amount}", amount)


def test_credit_limit_ignores_draft_entries(session, company):
    """Ерөнхий дэвтэрт ороогүй ноорог зээлийн хязгаарт нөлөөлөхгүй."""
    cp = _cp(session, company)
    cp.credit_limit_minor = 10_000_000_00
    session.flush()
    ledger.post_entry(session, company.id, date(2026, 3, 1), [
        ledger.LineInput("1201", debit_minor=9_000_000_00, counterparty_id=cp.id),
        ledger.LineInput("5101", credit_minor=9_000_000_00),
    ], status=EntryStatus.draft)

    assert partners.get_counterparty_ar_balance(session, company.id, cp.id) == 0
    _sales(session, company, cp, "S-1", 2_000_000_00)       # татгалзахгүй

    # Батлагдсан бичилт бол хязгаарт орно
    with pytest.raises(ledger.LedgerError, match="зээлийн хязгаар"):
        _sales(session, company, cp, "S-2", 9_000_000_00)


# ------------------------------------------------------------ API давхарга

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    import bayan.api as apimod
    from bayan.db import make_engine

    apimod.SessionLocal = sessionmaker(bind=make_engine("sqlite:///:memory:"),
                                       future=True)
    apimod._hits.clear()
    c = TestClient(apimod.app)
    r = c.post("/api/register", json={
        "email": "ap@bayan.mn", "password": "Password123!",
        "name": "Нягтлан", "company_name": "Тест ХХК"})
    c._h = {"Authorization": f"Bearer {r.json()['token']}"}
    c._cid = r.json()["company_id"]
    return c


def test_sod_does_not_block_single_user_company(client):
    """Ганц хэрэглэгчтэй компанид SoD-ийн улмаас нэхэмжлэх үүсгэж чадахгүй байв."""
    cid, h = client._cid, client._h
    cp = client.post(f"/api/companies/{cid}/counterparties",
                     json={"name": "Харилцагч", "kind": "customer"},
                     headers=h).json()
    for kind in ("sales", "purchase"):
        r = client.post(f"/api/companies/{cid}/invoices", json={
            "counterparty_id": cp["id"], "kind": kind, "number": f"{kind}-1",
            "issue_date": "2026-01-01", "due_date": "2026-01-31",
            "net_minor": 1_000_000_00, "with_vat": False}, headers=h)
        assert r.status_code == 200, r.text


def test_ar_provisioning_is_idempotent(client):
    """Давтан ажиллуулахад нөөц хуримтлагдахгүй — зөвхөн зөрүү бичигдэнэ."""
    cid, h = client._cid, client._h
    cp = client.post(f"/api/companies/{cid}/counterparties",
                     json={"name": "Харилцагч", "kind": "customer"},
                     headers=h).json()
    client.post(f"/api/companies/{cid}/invoices", json={
        "counterparty_id": cp["id"], "kind": "sales", "number": "S-OLD",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "net_minor": 10_000_000_00, "with_vat": False}, headers=h)

    body = {"provision_date": "2026-08-16"}
    first = client.post(f"/api/companies/{cid}/ar-provisioning",
                        json=body, headers=h).json()
    assert first["provision_amount"] == 5_000_000.0      # 50% (>90 хоног)
    assert first["adjustment"] == 5_000_000.0
    for _ in range(2):
        again = client.post(f"/api/companies/{cid}/ar-provisioning",
                            json=body, headers=h).json()
        assert again["provision_amount"] == 5_000_000.0
        assert again["adjustment"] == 0.0
        assert again["gl_entry_id"] is None

    tb = {r["code"]: r for r in client.get(
        f"/api/companies/{cid}/trial-balance", headers=h).json()}
    assert tb["1209"]["balance_minor"] == 5_000_000_00    # 15 сая биш
    assert tb["7121"]["balance_minor"] == 5_000_000_00    # 7109 биш
    assert "7109" not in tb or tb["7109"]["balance_minor"] == 0


def test_ar_provisioning_uses_provision_date(client):
    """Хоцрогдол нь тооцооны огноогоор бодогдоно (date.today() биш)."""
    cid, h = client._cid, client._h
    cp = client.post(f"/api/companies/{cid}/counterparties",
                     json={"name": "Харилцагч", "kind": "customer"},
                     headers=h).json()
    client.post(f"/api/companies/{cid}/invoices", json={
        "counterparty_id": cp["id"], "kind": "sales", "number": "S-1",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "net_minor": 10_000_000_00, "with_vat": False}, headers=h)

    # 2026-02-05 — ердөө 5 хоног хоцорсон тул нөөц үүсэхгүй
    r = client.post(f"/api/companies/{cid}/ar-provisioning",
                    json={"provision_date": "2026-02-05"}, headers=h).json()
    assert r["provision_amount"] == 0.0


def test_pay_endpoint_and_reconciliation(client):
    cid, h = client._cid, client._h
    cp = client.post(f"/api/companies/{cid}/counterparties",
                     json={"name": "Харилцагч", "kind": "customer"},
                     headers=h).json()
    inv = client.post(f"/api/companies/{cid}/invoices", json={
        "counterparty_id": cp["id"], "kind": "sales", "number": "S-1",
        "issue_date": "2026-06-01", "due_date": "2026-06-30",
        "net_minor": 4_000_000_00, "with_vat": False}, headers=h).json()

    r = client.post(f"/api/companies/{cid}/invoices/{inv['id']}/pay",
                    json={"amount_minor": 4_000_000_00,
                          "pay_date": "2026-07-01"}, headers=h)
    assert r.status_code == 200 and r.json()["settled"] is True

    r = client.post(f"/api/companies/{cid}/invoices/{inv['id']}/pay",
                    json={"amount_minor": 1_00, "pay_date": "2026-07-02"},
                    headers=h)
    assert r.status_code == 422                          # илүү төлөлт

    rec = client.get(f"/api/companies/{cid}/subledger-reconciliation",
                     headers=h).json()
    assert rec["receivable"]["matched"] and rec["payable"]["matched"]
    assert rec["receivable"]["subledger_minor"] == 0


def test_vat_receivable_shown_under_tax_row(session, company):
    """1203 «НӨАТ-ын авлага» СТ-1-д худалдааны авлагад БИШ, татварт орно."""
    cp = _cp(session, company)
    partners.post_invoice(session, company.id, cp.id, InvoiceKind.purchase,
                          "P-1", date(2026, 3, 1), date(2026, 3, 31),
                          10_000_000_00, with_vat=True, expense_account="7103")
    bs = reports.balance_sheet(session, company.id, date(2026, 3, 31))
    rows = {r["name"]: r for r in bs["assets"]}
    assert rows["Дансны авлага"]["amount_minor"] == 0
    assert rows["Татвар, НДШ-ийн авлага"]["amount_minor"] == 1_000_000_00
    assert bs["balanced"]
