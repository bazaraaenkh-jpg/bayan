"""ETAX — татварын тайлан бэлдэх, математик хаалт, илгээлтийн бүртгэл.

Гол шалгалт: маягтын тоо ерөнхий дэвтэртэй тэнцэхгүй бол ИЛГЭЭГДЭХГҮЙ.
"""

from datetime import date

import pytest
from sqlalchemy import select

from bayan import etax, reports
from bayan.ledger import LineInput, post_entry
from bayan.models import SourceType
from bayan.salary import Employee, PayrollLine

TODAY = date(2027, 3, 1)     # 2026 оны тайлант үе аль хэдийн дууссан


# ------------------------------------------------------------------ өгөгдөл

def _payroll(session, company, year=2026, month=7, gross=2_000_000_00,
             ndsh_emp=230_000_00, ndsh_er=250_000_00, hhoat=157_000_00):
    emp = session.scalar(select(Employee).where(Employee.company_id == company.id))
    if emp is None:
        emp = Employee(company_id=company.id, code="E01", last_name="Дорж",
                       first_name="Сүх", position=None, base_salary_minor=gross)
        session.add(emp)
        session.flush()
    session.add(PayrollLine(
        company_id=company.id, employee_id=emp.id, year=year, month=month,
        gross_minor=gross, ndsh_employee_minor=ndsh_emp,
        ndsh_employer_minor=ndsh_er, hhoat_minor=hhoat,
        net_minor=gross - ndsh_emp - hhoat))
    session.flush()
    return emp


def _payroll_gl(session, company, gross=2_000_000_00, ndsh_er=250_000_00,
                ndsh_emp=230_000_00, hhoat=157_000_00, day=25):
    net = gross - ndsh_emp - hhoat
    post_entry(session, company.id, date(2026, 7, day), [
        LineInput("7101", debit_minor=gross, description="Цалин"),
        LineInput("7102", debit_minor=ndsh_er, description="АО НДШ"),
        LineInput("3102", credit_minor=net, description="Гарт олгох"),
        LineInput("3103", credit_minor=ndsh_emp + ndsh_er, description="НДШ өглөг"),
        LineInput("3104", credit_minor=hhoat, description="ХХОАТ өглөг"),
    ], source_type=SourceType.salary, memo="Цалин")
    session.flush()


@pytest.fixture
def registered(session, company):
    """ТТД-тэй компани — ТТД байхгүй бол ямар ч тайлан илгээгдэхгүй."""
    company.reg_no = "1234567"
    session.flush()
    return company


@pytest.fixture
def tt11_ready(session, registered):
    """Цалин бодогдож, журналд ч бичигдсэн — хаалт бүрэн давах ёстой."""
    _payroll(session, registered)
    _payroll_gl(session, registered)
    return registered


# ------------------------------------------------------------- хуулийн хувь

def test_corporate_tax_uses_low_band_under_threshold():
    r = etax.TaxRates()
    assert r.corporate_tax(1_000_000_000_00) == 100_000_000_00      # 10%


def test_corporate_tax_uses_two_bands_above_threshold():
    r = etax.TaxRates()
    taxable = 10_000_000_000_00                                      # 10 тэрбум
    expected = 600_000_000_00 + int(4_000_000_000_00 * 0.25)
    assert r.corporate_tax(taxable) == expected


def test_small_taxpayer_election_taxes_revenue_at_one_percent():
    r = etax.TaxRates()
    tax = r.corporate_tax(50_000_000_00, revenue_minor=200_000_000_00,
                          small_election=True)
    assert tax == 2_000_000_00                                       # 1% × орлого


def test_small_election_ignored_when_revenue_over_cap():
    r = etax.TaxRates()
    tax = r.corporate_tax(50_000_000_00, revenue_minor=900_000_000_00,
                          small_election=True)
    assert tax == 5_000_000_00                                       # 10% × ногдох


def test_loss_pays_no_tax():
    assert etax.TaxRates().corporate_tax(-5_000_000_00) == 0


# --------------------------------------------------------------- ТТ-11 маягт

def test_tt11_reads_actual_payroll_not_an_estimate(session, tt11_ready):
    pkg = etax.build_tt11(session, tt11_ready.id, 2026, 7, today=TODAY)

    assert pkg.rows["gross_salary_minor"] == 2_000_000_00
    assert pkg.rows["haoat_tax_minor"] == 157_000_00     # шаталсан бодит дүн
    assert pkg.rows["ndsh_deduction_minor"] == 230_000_00
    assert pkg.rows["employee_count"] == 1


def test_tt11_passes_the_gate_when_ledger_agrees(session, tt11_ready):
    pkg = etax.build_tt11(session, tt11_ready.id, 2026, 7, today=TODAY)
    assert not pkg.blocked()
    assert all(c.ok for c in pkg.checks if c.blocking)


def test_tt11_is_blocked_when_payroll_does_not_match_the_ledger(session, registered):
    """Цалингийн бүртгэл 2 сая, журналд 3 сая — илгээхийг хориглоно."""
    _payroll(session, registered)
    _payroll_gl(session, registered, gross=3_000_000_00)

    pkg = etax.build_tt11(session, registered.id, 2026, 7, today=TODAY)

    assert pkg.blocked()
    failed = [c.code for c in pkg.checks if c.blocking and not c.ok]
    assert "TT11_GROSS" in failed


def test_tt11_is_blocked_when_nothing_was_posted_to_the_ledger(session, registered):
    _payroll(session, registered)
    pkg = etax.build_tt11(session, registered.id, 2026, 7, today=TODAY)
    assert pkg.blocked()


def test_tt11_reports_the_gap_in_tugriks(session, registered):
    _payroll(session, registered)
    _payroll_gl(session, registered, gross=3_000_000_00)
    pkg = etax.build_tt11(session, registered.id, 2026, 7, today=TODAY)

    gross_check = next(c for c in pkg.checks if c.code == "TT11_GROSS")
    assert "-1,000,000" in gross_check.detail


def test_tt11_year_form_sums_every_month(session, registered):
    _payroll(session, registered, month=7)
    _payroll(session, registered, month=8, gross=1_000_000_00,
             ndsh_emp=115_000_00, ndsh_er=125_000_00, hhoat=68_500_00)

    pkg = etax.build_tt11(session, registered.id, 2026, None, today=TODAY)
    assert pkg.rows["gross_salary_minor"] == 3_000_000_00
    assert pkg.period == "2026"


# ------------------------------------------------------------ нийтлэг хаалт

def test_missing_tin_blocks_submission(session, tt11_ready):
    tt11_ready.reg_no = ""
    session.flush()

    pkg = etax.build_tt11(session, tt11_ready.id, 2026, 7, today=TODAY)
    assert pkg.blocked()
    assert not next(c for c in pkg.checks if c.code == "TIN").ok


def test_period_that_has_not_ended_blocks_submission(session, tt11_ready):
    pkg = etax.build_tt11(session, tt11_ready.id, 2026, 7, today=date(2026, 7, 15))
    assert pkg.blocked()
    assert not next(c for c in pkg.checks if c.code == "PERIOD").ok


# --------------------------------------------------------------- ТТ-02 маягт

def test_tt02_matches_the_income_statement(session, registered):
    post_entry(session, registered.id, date(2026, 5, 1), [
        LineInput("1101", debit_minor=50_000_000_00, description="Борлуулалт"),
        LineInput("5101", credit_minor=50_000_000_00, description="Борлуулалт"),
    ], source_type=SourceType.manual, memo="Борлуулалт")
    post_entry(session, registered.id, date(2026, 5, 2), [
        LineInput("7103", debit_minor=20_000_000_00, description="Түрээс"),
        LineInput("1101", credit_minor=20_000_000_00, description="Түрээс"),
    ], source_type=SourceType.manual, memo="Түрээс")
    session.flush()

    pkg = etax.build_tt02(session, registered.id, 2026, today=TODAY)
    inc = reports.income_statement(session, registered.id,
                                   date(2026, 1, 1), date(2026, 12, 31))

    assert pkg.rows["gross_revenue_minor"] == inc["revenue_minor"] + inc["other_income_minor"]
    assert pkg.rows["taxable_income_minor"] == 30_000_000_00
    assert pkg.rows["calculated_tax_minor"] == 3_000_000_00       # 10%


def test_tt02_period_is_the_year(session, registered):
    pkg = etax.build_tt02(session, registered.id, 2026, today=TODAY)
    assert pkg.period == "2026" and pkg.form_code == "ТТ-02"


# -------------------------------------------------------------- ТТ-03а маягт

def test_tt03a_is_blocked_when_vat_does_not_tie_to_the_ledger(session, registered):
    from bayan.partners import Counterparty, Invoice, InvoiceKind

    p = Counterparty(company_id=registered.id, name="Харилцагч", reg_no="1234567")
    session.add(p)
    session.flush()
    session.add(Invoice(
        company_id=registered.id, counterparty_id=p.id, kind=InvoiceKind.sales,
        number="INV-1", issue_date=date(2026, 7, 5), due_date=date(2026, 8, 5),
        net_minor=10_000_000_00, vat_minor=1_000_000_00, paid_minor=0))
    session.flush()

    pkg = etax.build_tt03a(session, registered.id, 2026, 7, today=TODAY)

    # Нэхэмжлэхэд НӨАТ бий ч журналд 3105 бичигдээгүй → хаалт барина
    assert pkg.blocked()
    assert "VAT_OUTPUT" in [c.code for c in pkg.checks if c.blocking and not c.ok]


# ------------------------------------------------------------------ XML

def test_xml_carries_the_header_and_rows(session, tt11_ready):
    import xml.etree.ElementTree as ET

    pkg = etax.build_tt11(session, tt11_ready.id, 2026, 7, today=TODAY)
    root = ET.fromstring(etax.build_xml(pkg))

    assert root.findtext("Header/FormCode") == "ТТ-11"
    assert root.findtext("Header/Period") == "2026-07"
    assert root.findtext("Header/TIN") == tt11_ready.reg_no

    rows = {r.get("code"): r.text for r in root.findall("Rows/Row")}
    assert rows["gross_salary_minor"] == "2000000.00"
    assert rows["employee_count"] == "1"


def test_xml_records_the_gate_result(session, registered):
    import xml.etree.ElementTree as ET

    _payroll(session, registered)
    pkg = etax.build_tt11(session, registered.id, 2026, 7, today=TODAY)
    root = ET.fromstring(etax.build_xml(pkg))

    assert root.find("Validation").get("blocked") == "true"
    codes = {c.get("code"): c.get("ok") for c in root.findall("Validation/Check")}
    assert codes["TT11_GROSS"] == "false"


# ------------------------------------------------------------------ илгээх

def test_submit_refuses_when_the_gate_fails(session, registered):
    _payroll(session, registered)
    out = etax.submit(session, registered.id, "ТТ-11", 2026, 7, today=TODAY)

    assert out["status"] == "blocked"
    row = session.scalar(select(etax.EtaxSubmission))
    assert row.status == "blocked" and row.receipt_id is None
    assert row.payload_sha256


def test_submit_sends_in_mock_mode_when_the_gate_passes(session, tt11_ready):
    out = etax.submit(session, tt11_ready.id, "ТТ-11", 2026, 7,
                      actor_id="u1", today=TODAY)

    assert out["status"] == "mock"
    assert out["receipt_id"] == "MOCK-ТТ-11-2026-07"
    row = session.scalar(select(etax.EtaxSubmission))
    assert row.status == "mock" and row.actor_id == "u1"


def test_submission_stores_a_hash_of_what_was_sent(session, tt11_ready):
    import hashlib

    out = etax.submit(session, tt11_ready.id, "ТТ-11", 2026, 7, today=TODAY)
    row = session.scalar(select(etax.EtaxSubmission))
    assert row.payload_sha256 == hashlib.sha256(out["xml"].encode()).hexdigest()


def test_client_uses_the_real_endpoint_when_credentials_exist(monkeypatch):
    monkeypatch.setenv("ETAX_TIN", "1234567")
    monkeypatch.setenv("ETAX_TOKEN", "real-token")
    assert etax.EtaxClient(etax.EtaxConfig()).is_mock is False


def test_client_falls_back_to_mock_without_credentials():
    assert etax.EtaxClient(etax.EtaxConfig(tin="", token="")).is_mock is True


def test_history_lists_newest_first(session, tt11_ready):
    etax.submit(session, tt11_ready.id, "ТТ-11", 2026, 7, today=TODAY)
    etax.submit(session, tt11_ready.id, "ТТ-02", 2026, today=TODAY)

    hist = etax.history(session, tt11_ready.id)
    assert [h["form_code"] for h in hist][0] == "ТТ-02"
    assert len(hist) == 2


def test_unknown_form_is_rejected(session, company):
    with pytest.raises(ValueError):
        etax.build(session, company.id, "ТТ-99", 2026)


def test_every_form_has_a_latin1_safe_file_name():
    """HTTP толгой латин-1 тул кирилл маягтын кодыг файлын нэрэнд тавьж болохгүй."""
    for form in etax.FORMS:
        assert form in etax.ASCII_FORM
        etax.ASCII_FORM[form].encode("latin-1")      # алдаа гарвал тест унана


# --------------------------------------------- тайлан ба татвар нэг эх сурвалж

def test_report_module_and_etax_agree_on_tt11(session, tt11_ready):
    """Дэлгэц дээрх ТТ-11 ба татварт илгээх ТТ-11 нэг тоо байх ёстой."""
    screen = reports.haoat_tt11_report(session, tt11_ready.id, 2026)
    filed = etax.build_tt11(session, tt11_ready.id, 2026, today=TODAY)

    assert screen["gross_salary_minor"] == filed.rows["gross_salary_minor"]
    assert screen["haoat_tax_minor"] == filed.rows["haoat_tax_minor"]
    assert screen["form_code"] == "ТТ-11"
