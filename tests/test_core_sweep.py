"""Цөм модулиудын инвариант — буцаалт, СТ-4, ханш, эрхийн түлхүүр."""

from datetime import date

import pytest
from sqlalchemy import select

from bayan import fx, inventory, ledger, partners, reports
from bayan.models import EntryStatus, FxRate, JournalEntry
from bayan.partners import InvoiceKind


def _tb(session, company, **kw):
    return {r["code"]: r for r in ledger.trial_balance(session, company.id, **kw)}


# ------------------------------------------------------------ буцаалт

def test_reversal_preserves_line_metadata(session, company):
    """Буцаалт нь харилцагч/валют/зардлын төвийг хадгална."""
    cp = partners.Counterparty(company_id=company.id, name="Харилцагч")
    session.add(cp); session.flush()
    inv = partners.post_invoice(session, company.id, cp.id, InvoiceKind.sales,
                                "S-1", date(2026, 3, 1), date(2026, 3, 31),
                                10_000_000_00)

    ledger.reverse_entry(session, inv.journal_entry_id,
                         reversal_date=date(2026, 3, 5))

    gl = _tb(session, company).get("1201", {}).get("balance_minor", 0)
    sub = partners.get_counterparty_ar_balance(session, company.id, cp.id)
    assert gl == 0
    assert sub == gl                                   # өмнө нь 10 сая үлддэг байв

    rev = session.scalar(select(JournalEntry).where(
        JournalEntry.reversal_of == inv.journal_entry_id))
    # Буцаалтад 1201 нь кредит тал болно — тэр мөрөнд харилцагч үлдсэн байх ёстой
    assert any(l.counterparty_id == cp.id for l in rev.lines)


def test_reversal_preserves_currency_for_fx(session, company):
    from bayan.models import Account

    acc = session.scalar(select(Account).where(
        Account.company_id == company.id, Account.code == "1103"))
    acc.currency = "USD"
    session.flush()
    e = ledger.post_entry(session, company.id, date(2026, 1, 5), [
        ledger.LineInput("1103", debit_minor=34_000_000_00, currency="USD",
                         amount_currency=10_000.0),
        ledger.LineInput("4101", credit_minor=34_000_000_00)])

    fx.run_revaluation(session, company.id, date(2026, 1, 31))
    ledger.reverse_entry(session, e.id, reversal_date=date(2026, 2, 10))

    # Буцаалт валютын дүнг ч эсрэгээр хуулсан тул үлдэгдэл 0 болно. Дараагийн
    # үнэлгээ өмнө хүлээн зөвшөөрсөн олзыг буцаан 1103-ыг тэглэнэ.
    # (Өмнө нь буцаалтын amount_currency алдагдаж, данс 34 сая болж хөөрдөг байв.)
    fx.run_revaluation(session, company.id, date(2026, 2, 28))
    assert _tb(session, company).get("1103", {}).get("balance_minor", 0) == 0


def test_cash_flow_matches_actual_cash_after_reversal(session, company):
    """СТ-4-ийн net_change нь мөнгөний бодит өөрчлөлттэй үргэлж тэнцэнэ."""
    ledger.post_entry(session, company.id, date(2026, 1, 5), [
        ledger.LineInput("1101", debit_minor=50_000_000_00),
        ledger.LineInput("4101", credit_minor=50_000_000_00)])
    e = ledger.post_entry(session, company.id, date(2026, 1, 10), [
        ledger.LineInput("7103", debit_minor=3_000_000_00),
        ledger.LineInput("1101", credit_minor=3_000_000_00)])

    def check():
        cf = reports.cash_flow_statement(session, company.id,
                                         date(2026, 1, 1), date(2026, 1, 31))
        cash = _tb(session, company, date_to=date(2026, 1, 31))["1101"]["balance_minor"]
        assert cf["current"]["net_change"] == cash, (cf["current"], cash)

    check()
    ledger.reverse_entry(session, e.id, reversal_date=date(2026, 1, 20))
    check()                                            # өмнө нь 3 сая зөрдөг байв


# ------------------------------------------------------------ ханш

def test_stored_rate_wins_over_fallback(session, company):
    session.add(FxRate(company_id=company.id, currency="USD",
                       rate_date=date(2026, 1, 31), rate=3600.0))
    session.flush()
    rate, src = fx.resolve_rate("USD", date(2026, 1, 31), session, company.id)
    assert (rate, src) == (3600.0, "stored")
    assert fx.resolve_rate("MNT", date(2026, 1, 31))[0] == 1.0


def test_strict_mode_refuses_unverified_rate():
    """strict үед баталгаажаагүй нөөц ханшаар бичилт хийхгүй."""
    with pytest.raises(fx.FxRateError, match="гараар оруулна уу"):
        fx.resolve_rate("USD", date(2026, 1, 31), strict=True)
    # Анхдагчаар нөөц ханш ажиллана, харин эх сурвалж нь тэмдэглэгдэнэ
    assert fx.resolve_rate("USD", date(2026, 1, 31))[1] == "mock"


def test_revaluation_marks_unverified_rate_in_journal(session, company):
    from bayan.models import Account

    acc = session.scalar(select(Account).where(
        Account.company_id == company.id, Account.code == "1103"))
    acc.currency = "USD"
    session.flush()
    ledger.post_entry(session, company.id, date(2026, 3, 10), [
        ledger.LineInput("1103", debit_minor=3_400_000_00, currency="USD",
                         amount_currency=1000.0),
        ledger.LineInput("4101", credit_minor=3_400_000_00)])

    entry = fx.run_revaluation(session, company.id, date(2026, 3, 31))
    assert entry is not None
    assert any("БАТАЛГААЖААГҮЙ ХАНШ" in (l.description or "") for l in entry.lines)

    # Гараар ханш оруулсан бол сануулга гарахгүй
    session.add(FxRate(company_id=company.id, currency="USD",
                       rate_date=date(2026, 4, 30), rate=3500.0))
    session.flush()
    entry = fx.run_revaluation(session, company.id, date(2026, 4, 30))
    assert entry is not None
    assert not any("БАТАЛГААЖААГҮЙ" in (l.description or "") for l in entry.lines)


# ------------------------------------------------------------ агуулах

def test_transfer_rejects_same_warehouse(session, company):
    w1 = inventory.Warehouse(company_id=company.id, code="W1", name="Төв")
    w2 = inventory.Warehouse(company_id=company.id, code="W2", name="Салбар")
    it = inventory.Item(company_id=company.id, code="I1", name="Бараа")
    session.add_all([w1, w2, it]); session.flush()
    inventory.receive(session, company.id, it, date(2026, 3, 1), 100,
                      10_000_000_00, warehouse_id=w1.id)

    with pytest.raises(inventory.InventoryError, match="ижил"):
        inventory.transfer(session, company.id, it, w1.id, w1.id, 10,
                           date(2026, 3, 5))

    before = _tb(session, company)["2101"]["balance_minor"]
    inventory.transfer(session, company.id, it, w1.id, w2.id, 40, date(2026, 3, 5))
    assert _tb(session, company)["2101"]["balance_minor"] == before
    assert it.qty == 100


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
        "email": "core@bayan.mn", "password": "Password123!",
        "name": "Нягтлан", "company_name": "Core Co"})
    c._h = {"Authorization": f"Bearer {r.json()['token']}"}
    c._cid = r.json()["company_id"]
    return c


def test_previously_dead_endpoints_are_reachable(client):
    """PERMISSIONS-д байхгүй эрхийн түлхүүрээс болж 403 өгдөг байсан замууд."""
    cid, h = client._cid, client._h
    assert client.get(f"/api/companies/{cid}/inventory/transfer/pending",
                      headers=h).status_code == 200
    r = client.get(f"/api/companies/{cid}/wip/orders/none/variance", headers=h)
    assert r.status_code != 403                        # 404 хүлээж байна
    r = client.delete(f"/api/companies/{cid}/classifier-rules/none", headers=h)
    assert r.status_code != 403


def test_confirmation_act_and_loyalty_cards_do_not_crash(client):
    cid, h = client._cid, client._h
    cp = client.post(f"/api/companies/{cid}/counterparties",
                     json={"name": "Харилцагч", "reg_no": "1234567"},
                     headers=h).json()
    r = client.get(f"/api/companies/{cid}/counterparties/{cp['id']}/confirmation-act",
                   headers=h)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/companies/{cid}/loyalty-cards",
                      headers=h).status_code == 200


def test_payslip_uses_real_payroll(client):
    """Цалингийн хуудас бодит бодогдсон дүнг гаргана (хатуу бичсэн mock биш)."""
    cid, h = client._cid, client._h
    emp = client.post(f"/api/companies/{cid}/employees", json={
        "code": "E1", "last_name": "Тэст", "first_name": "Ажилтан",
        "base_salary_minor": 3_000_000_00}, headers=h).json()

    r = client.get(f"/api/companies/{cid}/payroll/payslip-pdf",
                   params={"employee_id": emp["id"], "year": 2026, "month": 3},
                   headers=h)
    assert r.status_code == 404                        # цалин бодогдоогүй

    client.post(f"/api/companies/{cid}/timesheets", json={
        "employee_id": emp["id"], "year": 2026, "month": 3,
        "worked_days": 22.0}, headers=h)
    client.post(f"/api/companies/{cid}/payroll/run",
                json={"year": 2026, "month": 3}, headers=h)

    r = client.get(f"/api/companies/{cid}/payroll/payslip-pdf",
                   params={"employee_id": emp["id"], "year": 2026, "month": 3},
                   headers=h)
    assert r.status_code == 200, r.text
    body = r.content.decode("utf-8")
    assert "Тэст Ажилтан" in body
    assert "2,500,000.00" not in body                  # хуучин хуурамч дүн
    assert "3,000,000.00" in body                      # бодит бодогдсон цалин
