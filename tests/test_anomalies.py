"""§5.4 Гажилт илрүүлэлт — давхар төлөлт, дансны буруу хослол, цалингийн
гажилт, НӨАТ-ын зөрүү, ирээдүйн огноо.

Гол шалгалт: цэвэр дэвтэр дээр ХУДАЛ дохио өгөхгүй, бодит алдааг барина.
"""

from datetime import date, timedelta

import pytest

from bayan import anomalies
from bayan.ledger import LineInput, post_entry
from bayan.models import ClassifierRule, SourceType
from bayan.salary import Employee, PayrollLine

AS_OF = date(2026, 8, 31)
D_FROM, D_TO = date(2026, 7, 1), date(2026, 7, 31)


def _pay(session, company, day, amount_minor, memo="Төлбөр", account="7103",
         source=SourceType.bank_txn):
    """Банкны хуулгаас үүссэн төлбөрийн бичилт (дүрмийн шалгалт үүнд л хамаарна)."""
    return post_entry(session, company.id, date(2026, 7, day), [
        LineInput(account, debit_minor=amount_minor, description=memo),
        LineInput("1101", credit_minor=amount_minor, description=memo),
    ], source_type=source, memo=memo)


# ------------------------------------------------------------ давхар төлөлт

def test_finds_two_identical_payments_days_apart(session, company):
    _pay(session, company, 10, 5_000_000_00, "Түрээсийн төлбөр", account="7103")
    _pay(session, company, 12, 5_000_000_00, "Түрээсийн төлбөр", account="7103")
    session.flush()

    found = anomalies.duplicate_payments(session, company.id, D_FROM, D_TO)

    assert len(found) == 1
    assert found[0].code == "DUPLICATE_PAYMENT"
    assert found[0].severity == "high"
    assert found[0].amount_minor == 5_000_000_00
    assert len(found[0].refs) == 2


def test_ignores_identical_payments_far_apart(session, company):
    _pay(session, company, 1, 5_000_000_00, "Түрээс")
    _pay(session, company, 28, 5_000_000_00, "Түрээс")
    session.flush()

    assert anomalies.duplicate_payments(session, company.id, D_FROM, D_TO) == []


def test_ignores_different_amounts(session, company):
    _pay(session, company, 10, 5_000_000_00)
    _pay(session, company, 11, 5_000_001_00)
    session.flush()

    assert anomalies.duplicate_payments(session, company.id, D_FROM, D_TO) == []


def test_each_pair_is_reported_once(session, company):
    for day in (10, 11, 12):
        _pay(session, company, day, 1_000_000_00, "Гурав дахин")
    session.flush()

    found = anomalies.duplicate_payments(session, company.id, D_FROM, D_TO)
    assert len(found) == 3          # 3 бичилтээс C(3,2) = 3 хос


# ------------------------------------------------------- дансны буруу хослол

def _rule(session, company, keyword, code):
    session.add(ClassifierRule(company_id=company.id, keyword=keyword,
                               account_code=code, priority=10, active=True))
    session.flush()


def test_flags_a_posting_that_contradicts_the_accountants_own_rule(session, company):
    _rule(session, company, "шатахуун", "7110")
    _pay(session, company, 5, 800_000_00, "Шатахуун авав", account="2101")
    session.flush()

    found = anomalies.misposted_accounts(session, company.id, D_FROM, D_TO)

    assert len(found) == 1
    assert found[0].code == "MISPOSTED_ACCOUNT"
    assert "7110" in found[0].detail and "2101" in found[0].detail


def test_correct_posting_raises_no_flag(session, company):
    _rule(session, company, "шатахуун", "7110")
    _pay(session, company, 5, 800_000_00, "Шатахуун авав", account="7110")
    session.flush()

    assert anomalies.misposted_accounts(session, company.id, D_FROM, D_TO) == []


def test_no_rules_means_no_opinion(session, company):
    """Дүрэмгүй компанид дансны талаар санал бодол илэрхийлэхгүй."""
    from sqlalchemy import select

    for r in session.scalars(select(ClassifierRule).where(
            ClassifierRule.company_id == company.id)):
        r.active = False
    _pay(session, company, 5, 800_000_00, "Шатахуун авав", account="2101")
    session.flush()

    assert anomalies.misposted_accounts(session, company.id, D_FROM, D_TO) == []


def test_the_highest_priority_rule_decides(session, company):
    """Өгөгдмөл дүрэм шатахуун→7104 гэдэг ч нябо-гийн 7110 дүрэм давуу."""
    _rule(session, company, "шатахуун", "7110")          # priority 10
    _pay(session, company, 5, 800_000_00, "Шатахуун авав", account="7104")
    session.flush()

    found = anomalies.misposted_accounts(session, company.id, D_FROM, D_TO)
    assert len(found) == 1 and "7110" in found[0].detail


def test_the_bank_side_of_the_entry_is_not_flagged(session, company):
    """1101-ийн мөр нь дүрмийн зорилтот данс биш — түүнийг зэмлэх ёсгүй."""
    _rule(session, company, "шатахуун", "7110")
    _pay(session, company, 5, 800_000_00, "Шатахуун авав", account="7110")
    session.flush()

    found = anomalies.misposted_accounts(session, company.id, D_FROM, D_TO)
    assert not any("1101" in f.detail for f in found)


# ---------------------------------------------------------- цалингийн гажилт

def _employee(session, company, code="E01", last="Дорж", first="Сүх"):
    e = Employee(company_id=company.id, code=code, last_name=last,
                 first_name=first, position=None, base_salary_minor=2_000_000_00)
    session.add(e)
    session.flush()
    return e


def _payroll(session, company, emp, month, gross):
    session.add(PayrollLine(
        company_id=company.id, employee_id=emp.id, year=2026, month=month,
        gross_minor=gross, ndsh_employee_minor=int(gross * 0.115),
        ndsh_employer_minor=int(gross * 0.125), hhoat_minor=int(gross * 0.08),
        net_minor=int(gross * 0.805)))
    session.flush()


def test_flags_a_salary_that_jumps(session, company):
    e = _employee(session, company)
    for m in (4, 5, 6):
        _payroll(session, company, e, m, 2_000_000_00)
    _payroll(session, company, e, 7, 4_000_000_00)          # +100%

    found = anomalies.payroll_anomalies(session, company.id, 2026, 7)

    jump = next(f for f in found if f.code == "PAYROLL_JUMP")
    assert jump.severity == "high"
    assert "+100%" in jump.detail


def test_a_modest_change_is_not_flagged(session, company):
    e = _employee(session, company)
    for m in (4, 5, 6):
        _payroll(session, company, e, m, 2_000_000_00)
    _payroll(session, company, e, 7, 2_200_000_00)          # +10%

    found = anomalies.payroll_anomalies(session, company.id, 2026, 7)
    assert not any(f.code == "PAYROLL_JUMP" for f in found)


def test_flags_a_double_payroll_line(session, company):
    e = _employee(session, company)
    _payroll(session, company, e, 7, 2_000_000_00)
    _payroll(session, company, e, 7, 2_000_000_00)

    found = anomalies.payroll_anomalies(session, company.id, 2026, 7)
    dup = next(f for f in found if f.code == "PAYROLL_DUPLICATE")
    assert dup.severity == "high"


def test_a_first_time_employee_is_noted_but_low_severity(session, company):
    e = _employee(session, company)
    _payroll(session, company, e, 7, 2_000_000_00)

    found = anomalies.payroll_anomalies(session, company.id, 2026, 7)
    new = next(f for f in found if f.code == "PAYROLL_NEW")
    assert new.severity == "low"


def test_no_payroll_means_no_findings(session, company):
    assert anomalies.payroll_anomalies(session, company.id, 2026, 7) == []


# ------------------------------------------------------------- НӨАТ-ын зөрүү

def _invoice(session, company, net, vat, number="INV-1"):
    from bayan.partners import Counterparty, Invoice, InvoiceKind

    cp = session.scalar(__import__("sqlalchemy").select(Counterparty).where(
        Counterparty.company_id == company.id))
    if cp is None:
        cp = Counterparty(company_id=company.id, name="Харилцагч", reg_no="111")
        session.add(cp)
        session.flush()
    session.add(Invoice(
        company_id=company.id, counterparty_id=cp.id, kind=InvoiceKind.sales,
        number=number, issue_date=date(2026, 7, 10), due_date=date(2026, 8, 10),
        net_minor=net, vat_minor=vat, paid_minor=0))
    session.flush()


def test_flags_vat_that_is_not_ten_percent(session, company):
    _invoice(session, company, 10_000_000_00, 500_000_00)     # 5% байна

    found = anomalies.vat_anomalies(session, company.id, D_FROM, D_TO)
    assert len(found) == 1
    assert found[0].code == "VAT_MISMATCH"
    assert "1,000,000" in found[0].detail


def test_correct_vat_is_not_flagged(session, company):
    _invoice(session, company, 10_000_000_00, 1_000_000_00)
    assert anomalies.vat_anomalies(session, company.id, D_FROM, D_TO) == []


def test_vat_free_invoice_is_not_flagged(session, company):
    _invoice(session, company, 10_000_000_00, 0)
    assert anomalies.vat_anomalies(session, company.id, D_FROM, D_TO) == []


# --------------------------------------------------------- ирээдүйн бичилт

def test_flags_an_entry_dated_in_the_future(session, company):
    post_entry(session, company.id, AS_OF + timedelta(days=30), [
        LineInput("7103", debit_minor=100_000_00, description="Ирээдүй"),
        LineInput("1101", credit_minor=100_000_00, description="Ирээдүй"),
    ], source_type=SourceType.manual, memo="Ирээдүйн бичилт")
    session.flush()

    found = anomalies.future_entries(session, company.id, AS_OF)
    assert len(found) == 1 and found[0].code == "FUTURE_ENTRY"


def test_todays_entry_is_not_in_the_future(session, company):
    post_entry(session, company.id, AS_OF, [
        LineInput("7103", debit_minor=100_000_00, description="Өнөөдөр"),
        LineInput("1101", credit_minor=100_000_00, description="Өнөөдөр"),
    ], source_type=SourceType.manual, memo="Өнөөдөр")
    session.flush()

    assert anomalies.future_entries(session, company.id, AS_OF) == []


# ------------------------------------------------------------------ багц

def test_clean_books_produce_no_noise(session, company):
    """Дүрмийн дагуу бичигдсэн дэвтэр дээр НЭГ Ч дохио гарах ёсгүй.

    Өгөгдмөл 70 дүрэм байхад худал дохио амархан үүсдэг — нябо-г
    дүжирүүлэхгүйн тулд энэ шалгалт чухал."""
    _pay(session, company, 5, 1_000_000_00, "Түрээс", account="7103")
    _pay(session, company, 15, 2_500_000_00, "Цахилгаан", account="7122")
    session.flush()

    result = anomalies.scan(session, company.id, 2026, 7, AS_OF)
    assert result["total"] == 0
    assert result["by_severity"] == {"high": 0, "medium": 0, "low": 0}


def test_scan_gathers_every_detector(session, company):
    _rule(session, company, "шатахуун", "7110")
    _pay(session, company, 10, 5_000_000_00, "Түрээс", account="7103")
    _pay(session, company, 12, 5_000_000_00, "Түрээс", account="7103")  # давхар
    _pay(session, company, 5, 800_000_00, "Шатахуун", account="2101")   # буруу данс
    _invoice(session, company, 10_000_000_00, 500_000_00)             # НӨАТ
    e = _employee(session, company)
    _payroll(session, company, e, 7, 2_000_000_00)                    # шинэ ажилтан
    session.flush()

    result = anomalies.scan(session, company.id, 2026, 7, AS_OF)
    codes = {a["code"] for a in result["anomalies"]}

    assert {"DUPLICATE_PAYMENT", "MISPOSTED_ACCOUNT", "VAT_MISMATCH",
            "PAYROLL_NEW"} <= codes
    assert result["total"] == len(result["anomalies"])


def test_findings_are_sorted_by_severity(session, company):
    _rule(session, company, "шатахуун", "7110")
    _pay(session, company, 10, 5_000_000_00, "Түрээс", account="7103")
    _pay(session, company, 12, 5_000_000_00, "Түрээс", account="7103")
    _pay(session, company, 5, 800_000_00, "Шатахуун", account="2101")
    e = _employee(session, company)
    _payroll(session, company, e, 7, 2_000_000_00)
    session.flush()

    result = anomalies.scan(session, company.id, 2026, 7, AS_OF)
    order = [anomalies.SEVERITY_ORDER[a["severity"]]
             for a in result["anomalies"]]
    assert order == sorted(order)
    assert result["anomalies"][0]["severity"] == "high"


def test_every_finding_carries_evidence(session, company):
    _pay(session, company, 10, 5_000_000_00, "Түрээс", account="7103")
    _pay(session, company, 12, 5_000_000_00, "Түрээс", account="7103")
    session.flush()

    for a in anomalies.scan(session, company.id, 2026, 7, AS_OF)["anomalies"]:
        assert a["refs"], f"{a['code']} нотолгоогүй байна"
        assert a["detail"]


def test_payroll_journal_is_not_judged_by_bank_rules(session, company):
    """Цалингийн журнал 7101 дебит хийдэг нь ЗӨВ — «цалин→3102» дүрэм нь
    банкны гүйлгээнд зориулагдсан тул түүнийг зэмлэх ёсгүй."""
    post_entry(session, company.id, date(2026, 7, 25), [
        LineInput("7101", debit_minor=5_000_000_00, description="Цалин"),
        LineInput("7102", debit_minor=600_000_00, description="АО НДШ"),
        LineInput("3102", credit_minor=4_000_000_00, description="Гарт олгох"),
        LineInput("3103", credit_minor=1_200_000_00, description="НДШ өглөг"),
        LineInput("3104", credit_minor=400_000_00, description="ХХОАТ өглөг"),
    ], source_type=SourceType.salary, memo="Цалин 2026-07")
    session.flush()

    assert anomalies.misposted_accounts(session, company.id, D_FROM, D_TO) == []
