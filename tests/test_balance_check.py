"""Дебит/кредитийн тэнцлийн шалгуур — «тэнцлээ / тэнцэхгүй».

Инвариант байгаа гэж ИТГЭХ биш, ХЭМЖИХ нь энэ шалгуурын гол зорилго:
хөндлөнгийн бичилт, миграц, өгөгдлийн эвдрэл гарвал барих ёстой.
"""

from datetime import date

from sqlalchemy import select

from bayan.ledger import LineInput, check_balance, post_entry
from bayan.models import JournalLine, SourceType


def _sale(session, company, day=1, amount=5_000_000_00):
    return post_entry(session, company.id, date(2026, 7, day), [
        LineInput("1101", debit_minor=amount, description="Борлуулалт"),
        LineInput("5101", credit_minor=amount, description="Борлуулалт"),
    ], source_type=SourceType.manual, memo="Борлуулалт")


def _break_entry(session, entry, delta=100_00):
    """Дэвтрийг гаднаас эвдэнэ — post_entry-г тойрч шууд мөр өөрчилнө."""
    line = session.scalar(select(JournalLine).where(
        JournalLine.entry_id == entry.id, JournalLine.debit_minor > 0))
    line.debit_minor += delta
    session.flush()


# ------------------------------------------------------------ тэнцсэн үе

def test_clean_books_report_balanced(session, company):
    _sale(session, company)
    session.flush()

    res = check_balance(session, company.id)

    assert res["balanced"] is True
    assert res["difference_minor"] == 0
    assert res["unbalanced_count"] == 0
    assert all(c["ok"] for c in res["checks"])


def test_empty_books_are_balanced(session, company):
    res = check_balance(session, company.id)
    assert res["balanced"] is True and res["entry_count"] == 0


def test_totals_add_up_across_many_entries(session, company):
    for d in range(1, 6):
        _sale(session, company, day=d, amount=d * 1_000_000_00)
    session.flush()

    res = check_balance(session, company.id)

    assert res["entry_count"] == 5
    assert res["total_debit_minor"] == res["total_credit_minor"] == 15_000_000_00
    assert res["balanced"] is True


# ---------------------------------------------------------- тэнцэхгүй үе

def test_a_corrupted_entry_is_caught(session, company):
    entry = _sale(session, company)
    session.flush()
    _break_entry(session, entry)

    res = check_balance(session, company.id)

    assert res["balanced"] is False
    assert res["unbalanced_count"] == 1
    assert res["difference_minor"] == 100_00


def test_the_broken_entry_is_named_so_it_can_be_found(session, company):
    entry = _sale(session, company, day=9)
    session.flush()
    _break_entry(session, entry, delta=250_00)

    res = check_balance(session, company.id)
    bad = res["unbalanced_entries"][0]

    assert bad["entry_id"] == entry.id
    assert bad["entry_date"] == "2026-07-09"
    assert bad["difference_minor"] == 250_00
    assert bad["memo"] == "Борлуулалт"


def test_only_the_broken_entry_is_listed(session, company):
    for d in (1, 2, 3):
        _sale(session, company, day=d)
    bad = _sale(session, company, day=4)
    session.flush()
    _break_entry(session, bad)

    res = check_balance(session, company.id)

    assert res["entry_count"] == 4
    assert res["unbalanced_count"] == 1
    assert res["unbalanced_entries"][0]["entry_id"] == bad.id


def test_the_failing_check_explains_the_gap(session, company):
    entry = _sale(session, company)
    session.flush()
    _break_entry(session, entry, delta=333_00)

    res = check_balance(session, company.id)
    sums = next(c for c in res["checks"] if c["code"] == "SUM_TOTALS")

    assert sums["ok"] is False
    assert "+333.00" in sums["detail"]


def test_entry_level_and_total_checks_fail_together(session, company):
    entry = _sale(session, company)
    session.flush()
    _break_entry(session, entry)

    failed = {c["code"] for c in check_balance(session, company.id)["checks"]
              if not c["ok"]}
    assert {"G1_ENTRIES", "SUM_TOTALS"} <= failed


def test_a_single_tugrik_is_not_tolerated(session, company):
    """Давхар бичилтэд бөөрөнхийлөлт байхгүй — 1 мөнгө ч зөрж болохгүй."""
    entry = _sale(session, company)
    session.flush()
    _break_entry(session, entry, delta=1)

    assert check_balance(session, company.id)["balanced"] is False


# ------------------------------------------------------------ хугацааны муж

def test_period_filter_narrows_the_check(session, company):
    _sale(session, company, day=5)
    post_entry(session, company.id, date(2026, 8, 20), [
        LineInput("1101", debit_minor=2_000_000_00, description="8-р сар"),
        LineInput("5101", credit_minor=2_000_000_00, description="8-р сар"),
    ], source_type=SourceType.manual, memo="8-р сар")
    session.flush()

    july = check_balance(session, company.id, date(2026, 7, 1), date(2026, 7, 31))
    assert july["entry_count"] == 1
    assert july["total_debit_minor"] == 5_000_000_00


def test_balance_sheet_equation_is_part_of_the_verdict(session, company):
    _sale(session, company)
    session.flush()

    codes = [c["code"] for c in check_balance(session, company.id)["checks"]]
    assert "BS_EQUATION" in codes


# ------------------------------------------- дэлгэц шалгах явцад илэрсэн алдаа

def test_timesheet_has_the_unpaid_leave_column(session):
    """Загварт нэмэгдсэн багана одоо байгаа санд ч үүссэн байх ёстой.

    Миграц дутуу байсан тул цалингийн урьдчилсан тооцоо 500 өгч байв."""
    from sqlalchemy import text

    cols = {r[1] for r in session.execute(text("PRAGMA table_info(time_sheet)"))}
    assert "unpaid_leave_days" in cols


def test_audit_log_timestamp_column_is_named_at():
    """api.py нь AuditLog.created_at гэж эрэмбэлж AttributeError өгч байв."""
    from bayan.models import AuditLog

    assert hasattr(AuditLog, "at")
    assert not hasattr(AuditLog, "created_at")
