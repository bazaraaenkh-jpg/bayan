"""Модулиудын нэгдсэн тестүүд: БМ, ҮХ, цалин, нэхэмжлэх, СТ-1/СТ-2 баланс."""

from datetime import date

import pytest
from bayan import assets, inventory, ledger, partners, reports, salary
from bayan.partners import InvoiceKind


# ------------------------------------------------------------ бараа материал

def test_inventory_average_cost(session, company):
    item = inventory.Item(company_id=company.id, code="M01", name="Материал А")
    session.add(item); session.flush()

    inventory.receive(session, company.id, item, date(2026, 3, 1), 10, 100_000)   # @10к
    inventory.receive(session, company.id, item, date(2026, 3, 5), 10, 200_000)   # @20к
    assert item.qty == 20 and item.avg_cost_minor == 15_000

    mv = inventory.issue(session, company.id, item, date(2026, 3, 10), 5)
    assert mv.cost_minor == 75_000                    # 5 × дундаж 15к
    assert item.qty == 15 and item.total_cost_minor == 225_000

    # бүх үлдэгдлийг гаргахад өртөг бүрэн шавхагдана (бутархай үлдэхгүй)
    mv2 = inventory.issue(session, company.id, item, date(2026, 3, 11), 15)
    assert mv2.cost_minor == 225_000 and item.total_cost_minor == 0


def test_inventory_insufficient_stock(session, company):
    item = inventory.Item(company_id=company.id, code="M02", name="Б")
    session.add(item); session.flush()
    with pytest.raises(inventory.InventoryError):
        inventory.issue(session, company.id, item, date(2026, 3, 1), 1)


# ------------------------------------------------------------ үндсэн хөрөнгө

def test_depreciation_straight_line(session, company):
    a = assets.register_asset(session, company.id, "FA01", "Сервер",
                              cost_minor=3_600_000, life_months=36,
                              in_service_from=date(2026, 1, 15))
    assert a.monthly_depreciation_minor == 100_000

    for m in (1, 2, 3):
        assets.run_monthly_depreciation(session, company.id, date(2026, m, 28))
    assert a.accumulated_minor == 300_000
    assert a.book_value_minor == 3_300_000

    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2509"]["balance_minor"] == 300_000     # contra-asset кредит үлдэгдэл
    assert tb["7107"]["balance_minor"] == 300_000


def test_depreciation_stops_at_life_end(session, company):
    a = assets.register_asset(session, company.id, "FA02", "Богино",
                              cost_minor=100, life_months=3,
                              in_service_from=date(2026, 1, 1))
    for m in range(1, 7):   # 6 сар гүйлгэхэд 3-аас цааш бичихгүй
        assets.run_monthly_depreciation(session, company.id, date(2026, m, 28))
    assert a.months_depreciated == 3
    assert a.accumulated_minor == 100   # сүүлийн сар үлдэгдлээ бүрэн авсан


# ------------------------------------------------------------ цалин

def test_payroll_math_and_journal(session, company):
    e1 = salary.Employee(company_id=company.id, code="E1", last_name="Бат",
                         first_name="Дорж", base_salary_minor=2_000_000_00)
    e2 = salary.Employee(company_id=company.id, code="E2", last_name="Сүх",
                         first_name="Цэцэг", base_salary_minor=1_500_000_00)
    session.add_all([e1, e2])
    session.flush()
    
    session.add_all([
        salary.TimeSheet(company_id=company.id, employee_id=e1.id, year=2026, month=3, worked_days=22.0),
        salary.TimeSheet(company_id=company.id, employee_id=e2.id, year=2026, month=3, worked_days=22.0),
    ])
    session.flush()
    
    cfg = salary.SalaryConfig()   # 11.5 / 12.5 / 10
    res = salary.run_payroll(session, company.id, 2026, 3, cfg)

    assert res["count"] == 2
    gross = 3_500_000_00
    ndsh_emp = int(gross * 0.115)
    assert res["gross"] == gross and res["ndsh_emp"] == ndsh_emp
    # журналын тэнцвэрийг G1 аль хэдийн албадсан; дансдын үлдэгдэл шалгана
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["7101"]["balance_minor"] == gross
    assert tb["3102"]["balance_minor"] == res["net"]
    assert tb["3103"]["balance_minor"] == res["ndsh_emp"] + res["ndsh_er"]


# ------------------------------------------------------------ нэхэмжлэх, насжилт

def test_invoice_and_aging(session, company):
    cp = partners.Counterparty(company_id=company.id, name="Мөнхжин ХХК")
    session.add(cp); session.flush()

    inv = partners.post_invoice(
        session, company.id, cp.id, InvoiceKind.sales, "INV-001",
        issue_date=date(2026, 1, 10), due_date=date(2026, 1, 25),
        net_minor=1_000_000_00, with_vat=True)
    assert inv.vat_minor == 100_000_00
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1201"]["balance_minor"] == 1_100_000_00
    assert tb["3105"]["balance_minor"] == 100_000_00

    aging = partners.aging_report(session, company.id, InvoiceKind.sales,
                                  as_of=date(2026, 3, 15))
    assert len(aging) == 1
    assert aging[0]["overdue_days"] == 49
    # buckets[0] = хугацаа болоогүй, дараа нь 0-30 / 31-60 / …
    assert aging[0]["buckets"][0] == 0
    assert aging[0]["buckets"][2] == 1_100_000_00    # 31-60 хоногийн сагс


# ------------------------------------------------------------ СТ-1 / СТ-2

def test_financial_statements_balance(session, company):
    """Хольсон үйл ажиллагааны дараа СТ-1 заавал тэнцэнэ."""
    cp = partners.Counterparty(company_id=company.id, name="Харилцагч")
    session.add(cp); session.flush()
    partners.post_invoice(session, company.id, cp.id, InvoiceKind.sales,
                          "S1", date(2026, 2, 1), date(2026, 2, 15),
                          net_minor=5_000_000_00, with_vat=True)
    partners.post_invoice(session, company.id, cp.id, InvoiceKind.purchase,
                          "P1", date(2026, 2, 3), date(2026, 2, 20),
                          net_minor=1_200_000_00, with_vat=True,
                          expense_account="7103")
    assets.register_asset(session, company.id, "FA1", "Машин",
                          cost_minor=24_000_000_00, life_months=48,
                          in_service_from=date(2026, 2, 1))
    assets.run_monthly_depreciation(session, company.id, date(2026, 2, 28))

    inc = reports.income_statement(session, company.id)
    assert inc["revenue_minor"] == 5_000_000_00
    assert inc["expenses_minor"] == 1_200_000_00 + 50_000_000  # түрээс + элэгдэл

    bs = reports.balance_sheet(session, company.id)
    assert bs["balanced"], bs
    assert bs["total_assets_minor"] == bs["total_liab_equity_minor"]


# ------------------------------------------------------------ СТ-3 / СТ-4
def test_changes_in_equity_and_cash_flow(session, company):
    # 1. Өмч оруулах (Financing Inflow)
    ledger.post_entry(session, company.id, date(2026, 3, 1), [
        ledger.LineInput("1001", debit_minor=1_000_000_00, description="Хөрөнгө оруулалт"),
        ledger.LineInput("4101", credit_minor=1_000_000_00, description="Өмч"),
    ])

    # 2. Борлуулалт (Operating Inflow)
    ledger.post_entry(session, company.id, date(2026, 3, 5), [
        ledger.LineInput("1001", debit_minor=200_000_00, description="Борлуулалт"),
        ledger.LineInput("5101", credit_minor=200_000_00, description="Орлого"),
    ])

    # 3. Зардал (Operating Outflow)
    ledger.post_entry(session, company.id, date(2026, 3, 10), [
        ledger.LineInput("7103", debit_minor=50_000_00, description="Түрээс"),
        ledger.LineInput("1001", credit_minor=50_000_00, description="Түрээс төлөх"),
    ])

    # СТ-3 шалгах
    eq = reports.statement_of_changes_in_equity(session, company.id, date(2026, 3, 1), date(2026, 3, 31))
    curr = eq["current"]
    assert curr["start"]["capital"] == 0
    assert curr["start"]["retained"] == 0
    assert curr["net_income"]["retained"] == 150_000_00  # 200,000 - 50,000
    assert curr["end"]["capital"] == 1_000_000_00
    assert curr["end"]["retained"] == 150_000_00
    assert curr["end"]["total"] == 1_150_000_00

    # СТ-4 шалгах
    cf = reports.cash_flow_statement(session, company.id, date(2026, 3, 1), date(2026, 3, 31))
    cf_curr = cf["current"]
    assert cf_curr["operating_inflow"] == 200_000_00
    assert cf_curr["operating_outflow"] == 50_000_00
    assert cf_curr["operating_net"] == 150_000_00
    assert cf_curr["financing_inflow"] == 1_000_000_00
    assert cf_curr["financing_net"] == 1_000_000_00
    assert cf_curr["net_change"] == 1_150_000_00
    assert cf_curr["detail"]["op_out_materials"] == 0
    assert cf_curr["detail"]["op_out_other"] == 50_000_00  # 7103 is classified as other operating outflow

    # Өмнөх оны харьцуулалт (хоосон байх ёстой)
    assert "prior" in cf
    assert cf["prior"]["net_change"] == 0


def test_export_reports(session, company):
    from bayan import export_reports
    # Post some entries first
    ledger.post_entry(session, company.id, date(2026, 3, 1), [
        ledger.LineInput("1001", debit_minor=10000, description="In"),
        ledger.LineInput("5101", credit_minor=10000, description="Out"),
    ])

    # Test XML Export
    xml_data = export_reports.export_xml(session, company.id, 2026, 3)
    assert "<eBalance>" in xml_data
    assert "<BalanceSheet>" in xml_data
    assert "100.00" in xml_data  # 10000 minor = 100.00

    # Test Excel Export
    excel_path = export_reports.export_excel(session, company.id, 2026, 3)
    assert excel_path.exists()
    assert excel_path.suffix == ".xlsx"
    excel_path.unlink() # Cleanup


def test_payroll_with_timesheet_and_progressive_pit(session, company):
    from bayan.salary import Employee, TimeSheet, run_payroll, SalaryConfig
    from sqlalchemy import select

    # 1. Ажилтан үүсгэх (20 сая₮-ийн өндөр цалинтай)
    emp = Employee(company_id=company.id, code="E9", last_name="Захирал",
                   first_name="Мөнх", base_salary_minor=20_000_000_00)
    session.add(emp)
    session.flush()

    # 2. Цагийн бүртгэл үүсгэх (15 хоног ажилласан, 5 хоног амарсан, 2 хоног өвдсөн - 50%-иар)
    ts = TimeSheet(
        company_id=company.id,
        employee_id=emp.id,
        year=2026,
        month=3,
        worked_days=15.0,
        vacation_days=5.0,
        sick_days=2.0,
        sick_pay_pct=50.0
    )
    session.add(ts)
    session.flush()

    cfg = SalaryConfig()
    res = run_payroll(session, company.id, 2026, 3, cfg)
    
    assert res["count"] == 1
    
    # Бохир цалин бодолт:
    # Өдрийн тариф = 20,000,000 / 22 = 909090.909
    # Ажилласан = 909090.909 * 15 = 13,636,363.636
    # Амралт = 909090.909 * 5 = 4,545,454.545
    # Өвчтэй = 909090.909 * 2 * 50% = 909,090.909
    # Нийт бохир = 13636363.636 + 4545454.545 + 909090.909 = 19,090,909.09₮ (1,909,090,909 minor)
    assert abs(res["gross"] - 19_090_909_09) <= 10  # float rounding зөрүү бага
    
    # НДШ Ажилтан (11.5% ceiling = 7,830,000₮):
    # ndsh_base = 7,830,000₮
    # ndsh_emp = 7,830,000 * 11.5% = 900,450₮ (90,045,000 minor)
    assert res["ndsh_emp"] == 900_450_00
    
    # Татвар ногдох орлого = 19,090,909.09 - 900,450 = 18,190,459.09₮
    # Шаталсан ХХОАТ (2026):
    # 10 сая хүртэлх дүн: 10,000,000 * 10% = 1,000,000₮
    # 10-15 сая хүртэлх дүн: 5,000,000 * 15% = 750,000₮
    # 15 саяас давсан дүн: (18,190,459.09 - 15,000,000) * 20% = 3,190,459.09 * 20% = 638,091.81₮
    # Нийт ХХОАТ = 1,000,000 + 750,000 + 638,091.81 = 2,388,091.81₮ (хөнгөлөлт 20,000₮ хасахаас өмнө)
    # хөнгөлөлт 20,000₮ хасаад = 2,368,091.81₮ (236,809,181 minor)
    assert abs(res["hhoat"] - 236_809_181) <= 10


def test_fx_revaluation(session, company):
    from bayan import fx, ledger
    from bayan.models import Account, NormalSide, JournalEntry, JournalLine
    from sqlalchemy import select

    # 1. USD Данс үүсгэх
    usd_acc = session.scalar(select(Account).where(Account.company_id == company.id, Account.code == "1103"))
    if not usd_acc:
        usd_acc = Account(
            company_id=company.id,
            code="1103",
            name="USD Данс",
            normal_side=NormalSide.debit,
            currency="USD",
            is_postable=True
        )
        session.add(usd_acc)
    else:
        usd_acc.currency = "USD"
    
    # Ханшийн зөрүүний данснууд байгаа эсэхийг баталгаажуулна (үгүй бол нэмнэ)
    gain_acc = session.scalar(select(Account).where(Account.company_id == company.id, Account.code == "5204"))
    if not gain_acc:
        session.add(Account(
            company_id=company.id, code="5204", name="Ханшийн зөрүүний олз",
            normal_side=NormalSide.credit, currency="MNT", is_postable=True
        ))
    loss_acc = session.scalar(select(Account).where(Account.company_id == company.id, Account.code == "7118"))
    if not loss_acc:
        session.add(Account(
            company_id=company.id, code="7118", name="Ханшийн зөрүүний гарз",
            normal_side=NormalSide.debit, currency="MNT", is_postable=True
        ))
    session.flush()

    # 2. Эхний гүйлгээ бичих: 1,000 USD-ийг 3400.0 ханшаар авлаа = 3,400,000 MNT
    ledger.post_entry(session, company.id, date(2026, 3, 10), [
        ledger.LineInput("1103", debit_minor=3_400_000_00, currency="USD", amount_currency=1000.0),
        ledger.LineInput("4101", credit_minor=3_400_000_00)
    ])

    # 3. Сарын эцэст 3450.0 ханшаар дахин үнэлгээ хийх
    # Монголбанкны API-г дуурайх ханш:
    # 1000 USD * 3450 = 3,450,000 MNT. Ханшийн олз = +50,000 MNT (5,000,000 minor)
    entry = fx.run_revaluation(session, company.id, date(2026, 3, 31))
    
    assert entry is not None
    assert entry.memo == "Ханшийн тэгшитгэл дахин үнэлгээ 2026-03-31"
    assert len(entry.lines) == 2
    
    # Мөрүүдийн дүн
    lines = sorted(entry.lines, key=lambda l: l.line_no)
    assert lines[0].debit_minor == 50_000_00  # Дт 1103 (USD Данс)
    assert lines[1].credit_minor == 50_000_00 # Кт 5204 (Ханшийн олз)

    # 4. Баланс шалгах
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1103"]["balance_minor"] == 3_450_000_00  # Шинэ үлдэгдэл 3,450,000₮ болсон байна!
    assert tb["5204"]["balance_minor"] == 50_000_00
