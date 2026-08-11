"""Гар журналын форм — ноорог, баримтын дугаар, харьцах дансны санал."""

from datetime import date

import pytest
from sqlalchemy import select

from bayan import ledger
from bayan.ledger import LineInput, post_entry
from bayan.models import ClassifierRule, EntryStatus, JournalEntry, SourceType


def _lines(amount=1_000_000_00):
    return [LineInput("7103", debit_minor=amount, description="Түрээс"),
            LineInput("1101", credit_minor=amount, description="Түрээс")]


# ------------------------------------------------------------ ноорог төлөв

def test_draft_entry_is_saved_but_stays_out_of_reports(session, company):
    post_entry(session, company.id, date(2026, 7, 1), _lines(),
               source_type=SourceType.manual, status=EntryStatus.draft)
    session.flush()

    saved = session.scalar(select(JournalEntry))
    assert saved.status == EntryStatus.draft

    # Ноорог нь гүйлгээний балансад ОРОХГҮЙ
    tb = ledger.trial_balance(session, company.id)
    assert tb == []


def test_posted_entry_reaches_the_reports(session, company):
    post_entry(session, company.id, date(2026, 7, 1), _lines(),
               source_type=SourceType.manual)
    session.flush()

    codes = {r["code"] for r in ledger.trial_balance(session, company.id)}
    assert {"7103", "1101"} <= codes


def test_draft_still_has_to_balance(session, company):
    """Ноорог гэдэг нь тэнцэхгүй байж болно гэсэн үг БИШ."""
    with pytest.raises(ledger.UnbalancedEntryError):
        post_entry(session, company.id, date(2026, 7, 1),
                   [LineInput("7103", debit_minor=500_00, description="х"),
                    LineInput("1101", credit_minor=400_00, description="х")],
                   source_type=SourceType.manual, status=EntryStatus.draft)


def test_draft_is_not_counted_by_the_balance_check(session, company):
    post_entry(session, company.id, date(2026, 7, 1), _lines(),
               source_type=SourceType.manual, status=EntryStatus.draft)
    session.flush()

    assert ledger.check_balance(session, company.id)["entry_count"] == 0


# ------------------------------------------------------ баримтын дугаар

def test_next_entry_number_starts_at_one(session, company):
    assert ledger.next_entry_no(session, company.id) == 1


def test_next_entry_number_follows_the_last_one(session, company):
    for d in (1, 2, 3):
        post_entry(session, company.id, date(2026, 7, d), _lines(),
                   source_type=SourceType.manual)
    session.flush()

    assert ledger.next_entry_no(session, company.id) == 4


def test_document_number_format():
    assert ledger.document_no("ЕЖ", 1, 2026) == "ЕЖ/26-001"
    assert ledger.document_no("КО", 42, 2026) == "КО/26-042"
    assert ledger.document_no("ЕЖ", 137, 2027) == "ЕЖ/27-137"


def test_document_number_is_per_company(session, company):
    from bayan.coa_seed import seed_company

    other = seed_company(session, "Хоёр дахь ХХК")
    session.flush()
    post_entry(session, company.id, date(2026, 7, 1), _lines(),
               source_type=SourceType.manual)
    session.flush()

    assert ledger.next_entry_no(session, company.id) == 2
    assert ledger.next_entry_no(session, other.id) == 1
