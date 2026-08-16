"""Үндсэн хөрөнгө / цалин — хасалт, давхардлын хамгаалалт, баталгаажуулалт."""

from datetime import date

import pytest
from sqlalchemy import select

from bayan import assets, ledger, reports, salary


def _asset(session, company, code="FA1", cost=24_000_000_00, life=48, **kw):
    kw.setdefault("in_service_from", date(2026, 1, 1))
    return assets.register_asset(session, company.id, code, "Машин",
                                 cost_minor=cost, life_months=life, **kw)


def _employee(session, company, code="E1", base=3_000_000_00, year=2026, month=3):
    e = salary.Employee(company_id=company.id, code=code, last_name="Тест",
                        first_name="Ажилтан", base_salary_minor=base)
    session.add(e); session.flush()
    session.add(salary.TimeSheet(company_id=company.id, employee_id=e.id,
                                 year=year, month=month, worked_days=22.0))
    session.flush()
    return e


def _tb(session, company):
    return {r["code"]: r for r in ledger.trial_balance(session, company.id)}


# ------------------------------------------------------------ ҮХ хасалт

def test_retire_asset_derecognises_accumulated_depreciation(session, company):
    """Хасалтад 2509 цэвэрлэгдэж, гарз нь ҮЛДЭХ өртгөөр бичигдэнэ."""
    a = _asset(session, company)
    assets.run_monthly_depreciation(session, company.id, date(2026, 3, 31))
    assert a.accumulated_minor == 1_500_000_00      # 3 сар × 500к
    assert a.book_value_minor == 22_500_000_00

    res = assets.retire_asset(session, company.id, a.id, date(2026, 4, 1))
    assert res["loss_minor"] == 22_500_000_00       # бүтэн 24 сая БИШ
    tb = _tb(session, company)
    assert tb["2509"]["balance_minor"] == 0         # өлгөөтэй үлдэхгүй
    assert tb["2501"]["balance_minor"] == 0
    assert tb["7199"]["balance_minor"] == 22_500_000_00
    assert a.active is False

    with pytest.raises(assets.AssetError):
        assets.retire_asset(session, company.id, a.id, date(2026, 4, 2))


def test_retire_asset_with_proceeds_books_gain_or_loss(session, company):
    a = _asset(session, company, "FA-SELL")
    assets.run_monthly_depreciation(session, company.id, date(2026, 3, 31))

    # Үлдэх өртөг 22.5 сая, 25 саяар зарав → 2.5 сая олз
    res = assets.retire_asset(session, company.id, a.id, date(2026, 4, 1),
                              proceeds_minor=25_000_000_00)
    assert res["gain_minor"] == 2_500_000_00 and res["loss_minor"] == 0
    tb = _tb(session, company)
    assert tb["5201"]["balance_minor"] == 2_500_000_00
    assert tb["1101"]["balance_minor"] == 25_000_000_00
    assert tb["2509"]["balance_minor"] == 0

    b = _asset(session, company, "FA-LOSS")
    assets.run_monthly_depreciation(session, company.id, date(2026, 3, 31))
    res = assets.retire_asset(session, company.id, b.id, date(2026, 4, 1),
                              proceeds_minor=20_000_000_00)
    assert res["loss_minor"] == 2_500_000_00 and res["gain_minor"] == 0


# ------------------------------------------------------------ давхардал

def test_depreciation_is_idempotent_per_period(session, company):
    """Нэг үеийг давтан гүйлгэхэд давхар бичилт үүсэхгүй."""
    a = _asset(session, company)
    for _ in range(3):
        assets.run_monthly_depreciation(session, company.id, date(2026, 1, 31))
    assert a.months_depreciated == 1
    assert _tb(session, company)["2509"]["balance_minor"] == 500_000_00

    assets.run_monthly_depreciation(session, company.id, date(2026, 2, 28))
    assert a.months_depreciated == 2
    assert _tb(session, company)["2509"]["balance_minor"] == 1_000_000_00


def test_depreciation_catches_up_skipped_months(session, company):
    """Алгассан сарууд гүйцээгдэнэ (өмнө нь 1 сар л бичигддэг байв)."""
    a = _asset(session, company)
    res = assets.run_monthly_depreciation(session, company.id, date(2026, 6, 30))
    assert a.months_depreciated == 6
    assert res[0]["months"] == 6
    assert _tb(session, company)["2509"]["balance_minor"] == 3_000_000_00


def test_depreciation_stops_at_end_of_life(session, company):
    a = _asset(session, company, "FA-SHORT", cost=1_000_000_00, life=3)
    assets.run_monthly_depreciation(session, company.id, date(2026, 12, 31))
    assert a.months_depreciated == 3
    assert a.accumulated_minor == 1_000_000_00      # бүрэн элэгдсэн
    assert a.book_value_minor == 0
    assert assets.run_monthly_depreciation(session, company.id,
                                           date(2027, 1, 31)) == []


def test_payroll_is_idempotent_per_month(session, company):
    """Нэг сарын цалинг давтан бодоход давхар өглөг үүсэхгүй."""
    _employee(session, company)
    first = salary.run_payroll(session, company.id, 2026, 3,
                               entry_date=date(2026, 3, 31))
    assert first["count"] == 1
    tb = _tb(session, company)
    gross, net = tb["7101"]["balance_minor"], tb["3102"]["balance_minor"]

    for _ in range(2):
        again = salary.run_payroll(session, company.id, 2026, 3,
                                   entry_date=date(2026, 3, 31))
        assert again["count"] == 0 and again["skipped_existing"] == 1
    tb = _tb(session, company)
    assert tb["7101"]["balance_minor"] == gross
    assert tb["3102"]["balance_minor"] == net

    # Дараагийн сар хэвийн бодогдоно
    emp = session.scalars(select(salary.Employee).where(
        salary.Employee.company_id == company.id)).first()
    session.add(salary.TimeSheet(company_id=company.id, employee_id=emp.id,
                                 year=2026, month=4, worked_days=22.0))
    session.flush()
    assert salary.run_payroll(session, company.id, 2026, 4,
                              entry_date=date(2026, 4, 30))["count"] == 1


# ------------------------------------------------------------ баталгаажуулалт

def test_asset_registration_validation(session, company):
    with pytest.raises(assets.AssetError, match="ашиглалтын хугацаа"):
        _asset(session, company, "FA-A", life=0)
    with pytest.raises(assets.AssetError, match="өртөг 0-ээс их"):
        _asset(session, company, "FA-B", cost=0)
    # үлдэх өртөг ≥ өртөг бол элэгдэл СӨРӨГ болно
    with pytest.raises(assets.AssetError, match="үлдэх өртөг"):
        _asset(session, company, "FA-C", cost=10_000_000_00,
               salvage_minor=50_000_000_00)


def test_manual_payroll_override_is_validated(session, company):
    """Тэнцэхгүй гараар засалт нь ойлгомжтой алдаа өгнө (журнал унахгүй)."""
    e = _employee(session, company)
    with pytest.raises(ledger.LedgerError, match="тэнцэхгүй"):
        salary.run_payroll(session, company.id, 2026, 3,
                           entry_date=date(2026, 3, 31),
                           overrides={e.id: {"is_manual": True,
                                             "net": 9_999_999_00}})
    # Тэнцүү утга бол зөвшөөрөгдөнө
    res = salary.run_payroll(session, company.id, 2026, 3,
                             entry_date=date(2026, 3, 31),
                             overrides={e.id: {"is_manual": True,
                                               "gross": 2_000_000_00,
                                               "ndsh_emp": 200_000_00,
                                               "hhoat": 100_000_00,
                                               "net": 1_700_000_00}})
    assert res["count"] == 1
    tb = _tb(session, company)
    assert tb["3102"]["balance_minor"] == 1_700_000_00


# ------------------------------------------------------------ балансад тусах

def test_assets_and_payroll_reflect_in_balance_sheet(session, company):
    ledger.post_entry(session, company.id, date(2026, 1, 1), [
        ledger.LineInput("1101", debit_minor=50_000_000_00),
        ledger.LineInput("4101", credit_minor=50_000_000_00)])
    a = _asset(session, company)
    _employee(session, company, year=2026, month=1)
    salary.run_payroll(session, company.id, 2026, 1, entry_date=date(2026, 1, 31))
    assets.run_monthly_depreciation(session, company.id, date(2026, 1, 31))

    bs = reports.balance_sheet(session, company.id, date(2026, 1, 31))
    rows = {r["name"]: r["amount_minor"] for r in bs["assets"] + bs["liabilities"]}
    assert rows["Үндсэн хөрөнгө"] == 24_000_000_00
    assert rows["Хуримтлагдсан элэгдэл"] == -500_000_00
    # СТ-1-ийн цэвэр ҮХ ↔ дэвтрийн үлдэх өртөг
    assert (rows["Үндсэн хөрөнгө"] + rows["Хуримтлагдсан элэгдэл"]
            == a.book_value_minor)
    assert rows["Цалингийн өглөг"] > 0
    assert bs["balanced"]
