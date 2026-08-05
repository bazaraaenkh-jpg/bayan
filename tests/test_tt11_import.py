"""ТТ-11 (ХХОАТ-ын тайлан) маягтаас цалингийн бүртгэл сэргээх."""

import io

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from bayan import tt11_import
from bayan.salary import Employee, PayrollLine


def _xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


HEADER = ["Овог", "Нэр", "Регистрийн дугаар", "Нийт орлого", "НДШ",
          "Татвар ногдох орлого", "Ногдуулсан татвар", "Гарт олгосон"]


def _row(last, first, reg, gross, ndsh, hhoat):
    taxable = gross - ndsh
    return [last, first, reg, gross, ndsh, taxable, hhoat, gross - ndsh - hhoat]


# --------------------------------------------------------------- толгой таних

def test_reads_standard_tt11_header():
    res = tt11_import.parse(_xlsx([
        ["ХХОАТ-ын тайлан, Маягт ТТ-11"], [],
        HEADER,
        _row("Бат-Эрдэнэ", "Болд", "УА90010111", 2_500_000, 287_500, 201_250),
    ]), "tt11.xlsx")

    assert res.header_row == 2
    assert res.columns["last_name"] == 0 and res.columns["first_name"] == 1
    assert res.columns["register"] == 2 and res.columns["gross"] == 3
    assert len(res.rows) == 1
    r = res.rows[0]
    assert r["name"] == "Бат-Эрдэнэ Болд"
    assert r["register"] == "УА90010111"
    assert r["gross_minor"] == 2_500_000_00


def test_taxable_column_does_not_steal_the_tax_column():
    """«Татвар ногдох орлого» нь «татвар» гэсэн үг агуулдаг — ХХОАТ-ыг булаах ёсгүй."""
    res = tt11_import.parse(_xlsx([
        HEADER, _row("Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 68_500),
    ]), "t.xlsx")

    assert res.columns["taxable"] == 5
    assert res.columns["hhoat"] == 6
    assert res.rows[0]["hhoat_minor"] == 68_500_00


def test_reads_combined_name_column():
    res = tt11_import.parse(_xlsx([
        ["Ажилтны нэр", "Нийт орлого", "НДШ", "ХХОАТ"],
        ["Бат-Эрдэнэ Болд", 2_000_000, 230_000, 157_000],
    ]), "t.xlsx")

    r = res.rows[0]
    assert r["last_name"] == "Бат-Эрдэнэ" and r["first_name"] == "Болд"


def test_csv_and_formatted_amounts():
    res = tt11_import.parse(_csv(
        "Овог нэр,Нийт орлого,НДШ,ХХОАТ,Гарт олгох\n"
        "Дорж Сүх,\"2 500 000.00₮\",\"287,500\",201250,\"2,011,250\"\n"), "t.csv")

    r = res.rows[0]
    assert r["gross_minor"] == 2_500_000_00
    assert r["ndsh_employee_minor"] == 287_500_00
    assert r["net_minor"] == 2_011_250_00


def test_returns_nothing_when_header_unrecognised():
    res = tt11_import.parse(_xlsx([["a", "b"], ["1", "2"]]), "t.xlsx")
    assert res.rows == [] and res.columns == {}


# --------------------------------------------------------------- цэвэрлэгээ

def test_skips_total_and_empty_rows():
    res = tt11_import.parse(_xlsx([
        HEADER,
        _row("Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 68_500),
        ["", "", "", "", "", "", "", ""],
        ["НИЙТ ДҮН", "", "", 1_000_000, 115_000, 885_000, 68_500, 816_500],
    ]), "t.xlsx")

    assert len(res.rows) == 1
    assert "нийлбэрийн мөр" in " ".join(res.skipped)


def test_skips_duplicate_register():
    res = tt11_import.parse(_xlsx([
        HEADER,
        _row("Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 68_500),
        _row("Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 68_500),
    ]), "t.xlsx")

    assert len(res.rows) == 1
    assert "давхардсан" in " ".join(res.skipped)


def test_skips_rows_without_amounts():
    res = tt11_import.parse(_xlsx([
        HEADER, ["Дорж", "Сүх", "УБ85010203", "", "", "", "", ""],
    ]), "t.xlsx")

    assert res.rows == []
    assert "дүнгүй" in " ".join(res.skipped)


def test_derives_gross_when_only_net_given():
    res = tt11_import.parse(_xlsx([
        ["Овог нэр", "НДШ", "ХХОАТ", "Гарт олгосон"],
        ["Дорж Сүх", 115_000, 68_500, 816_500],
    ]), "t.xlsx")

    assert res.rows[0]["gross_minor"] == 1_000_000_00


# ------------------------------------------------------------- тэнцлийн шалгалт

def test_flags_net_that_does_not_reconcile():
    """Файлын «гарт олгох» тэнцэхгүй бол мэдээлээд тэнцсэн дүнг хэрэглэнэ."""
    res = tt11_import.parse(_xlsx([
        HEADER,
        ["Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 885_000, 68_500, 900_000],
    ]), "t.xlsx")

    r = res.rows[0]
    assert r["net_minor"] == 816_500_00           # бохир − НДШ − ХХОАТ
    assert r["net_in_file_minor"] == 900_000_00
    assert r["mismatch_minor"] == 83_500_00
    assert res.warnings and "тэнцэхгүй" in res.warnings[0]


def test_rounding_tolerance_does_not_warn():
    res = tt11_import.parse(_xlsx([
        HEADER,
        ["Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 885_000, 68_500, 816_500.5],
    ]), "t.xlsx")
    assert res.warnings == []


# ------------------------------------------------------------ санд бичих

def _parsed(rows=None):
    return tt11_import.parse(_xlsx([HEADER] + (rows or [
        _row("Бат-Эрдэнэ", "Болд", "УА90010111", 2_500_000, 287_500, 201_250),
        _row("Дорж", "Сүх", "УБ85010203", 1_000_000, 115_000, 68_500),
    ])), "t.xlsx").rows


def test_apply_creates_employees_and_payroll(session, company):
    out = tt11_import.apply(session, company.id, 2026, 6, _parsed())
    session.flush()

    assert out["created_employees"] == 2
    assert out["created_lines"] == 2
    assert out["entry_id"] is None            # өгөгдмөл нь журналд бичихгүй

    emps = session.scalars(select(Employee).where(
        Employee.company_id == company.id)).all()
    assert {e.code for e in emps} == {"УА90010111", "УБ85010203"}

    lines = session.scalars(select(PayrollLine).where(
        PayrollLine.company_id == company.id)).all()
    assert len(lines) == 2
    assert all(p.year == 2026 and p.month == 6 for p in lines)
    assert all(p.journal_entry_id is None for p in lines)


def test_payroll_line_balances_gross_net_and_deductions(session, company):
    tt11_import.apply(session, company.id, 2026, 6, _parsed())
    session.flush()

    for p in session.scalars(select(PayrollLine)).all():
        assert p.gross_minor - p.ndsh_employee_minor - p.hhoat_minor == p.net_minor
        assert p.ndsh_employer_minor > 0      # АО-ын НДШ тохиргооноос бодогдоно


def test_apply_matches_existing_employee_by_register(session, company):
    session.add(Employee(
        company_id=company.id, code="УА90010111", last_name="Бат-Эрдэнэ",
        first_name="Болд", position="Нягтлан", base_salary_minor=2_000_000_00))
    session.flush()

    out = tt11_import.apply(session, company.id, 2026, 6, _parsed())
    session.flush()

    assert out["created_employees"] == 1      # зөвхөн хоёр дахь нь шинэ
    assert out["created_lines"] == 2
    assert len(session.scalars(select(Employee)).all()) == 2


def test_apply_matches_existing_employee_by_name_when_no_register(session, company):
    session.add(Employee(
        company_id=company.id, code="E01", last_name="Дорж", first_name="Сүх",
        position=None, base_salary_minor=1_000_000_00))
    session.flush()

    rows = tt11_import.parse(_xlsx([
        ["Овог нэр", "Нийт орлого", "НДШ", "ХХОАТ"],
        ["Дорж Сүх", 1_000_000, 115_000, 68_500]]), "t.xlsx").rows
    out = tt11_import.apply(session, company.id, 2026, 6, rows)

    assert out["created_employees"] == 0
    assert out["created_lines"] == 1


def test_apply_is_idempotent_for_the_same_month(session, company):
    tt11_import.apply(session, company.id, 2026, 6, _parsed())
    session.flush()
    again = tt11_import.apply(session, company.id, 2026, 6, _parsed())
    session.flush()

    assert again["created_lines"] == 0
    assert again["skipped_existing"] == 2
    assert len(session.scalars(select(PayrollLine)).all()) == 2


def test_apply_can_post_a_balanced_journal_entry(session, company):
    from bayan.ledger import trial_balance
    from datetime import date

    out = tt11_import.apply(session, company.id, 2026, 6, _parsed(),
                            post_entry=True)
    session.flush()

    assert out["entry_id"]
    lines = session.scalars(select(PayrollLine)).all()
    assert all(p.journal_entry_id == out["entry_id"] for p in lines)

    tb = trial_balance(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    assert sum(r["debit_minor"] for r in tb) == sum(r["credit_minor"] for r in tb)

    by_code = {r["code"]: r for r in tb}
    assert by_code["7101"]["debit_minor"] == out["gross"]
    assert by_code["3102"]["credit_minor"] == out["net"]
    assert by_code["3104"]["credit_minor"] == out["hhoat"]


def test_apply_without_rows_posts_nothing(session, company):
    out = tt11_import.apply(session, company.id, 2026, 6, [], post_entry=True)
    assert out["entry_id"] is None and out["created_lines"] == 0
