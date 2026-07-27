"""G1-G4 инвариантуудын эвдэх оролдлогын тестүүд (шалгуур A4)."""

from datetime import date

import pytest
from bayan import ledger
from bayan.ledger import (
    LineInput, NotPostableError, PeriodLockedError, UnbalancedEntryError,
)
from bayan.models import EntryStatus, SourceType


def _simple_lines(amount=100_000_00):
    return [
        LineInput("1001", debit_minor=amount, description="Касст орлого"),
        LineInput("5101", credit_minor=amount, description="Борлуулалт"),
    ]


def test_balanced_entry_posts(session, company):
    e = ledger.post_entry(session, company.id, date(2026, 3, 1), _simple_lines())
    assert e.status == EntryStatus.posted
    assert e.entry_no == 1


def test_g1_unbalanced_rejected(session, company):
    lines = [
        LineInput("1001", debit_minor=100),
        LineInput("5101", credit_minor=99),
    ]
    with pytest.raises(UnbalancedEntryError):
        ledger.post_entry(session, company.id, date(2026, 3, 1), lines)


def test_g1_zero_entry_rejected(session, company):
    with pytest.raises(UnbalancedEntryError):
        ledger.post_entry(session, company.id, date(2026, 3, 1), [
            LineInput("1001", debit_minor=0),
            LineInput("5101", credit_minor=0),
        ])


def test_g3_locked_period_rejected(session, company):
    ledger.lock_period(session, company.id, 2026, 2)
    with pytest.raises(PeriodLockedError):
        ledger.post_entry(session, company.id, date(2026, 2, 15), _simple_lines())
    # дараагийн сар нээлттэй
    ledger.post_entry(session, company.id, date(2026, 3, 1), _simple_lines())


def test_g4_group_account_rejected(session, company):
    lines = [
        LineInput("11", debit_minor=100),   # бүлэг данс!
        LineInput("5101", credit_minor=100),
    ]
    with pytest.raises(NotPostableError):
        ledger.post_entry(session, company.id, date(2026, 3, 1), lines)


def test_g2_reversal_flow(session, company):
    e = ledger.post_entry(session, company.id, date(2026, 3, 1), _simple_lines())
    rev = ledger.reverse_entry(session, e.id)
    assert rev.source_type == SourceType.reversal
    assert rev.reversal_of == e.id
    assert e.status == EntryStatus.reversed
    # хоёр дахь удаа reverse хийхгүй
    with pytest.raises(ledger.ImmutableEntryError):
        ledger.reverse_entry(session, e.id)
    # trial balance-д reversed бичилт орохгүй тул үлдэгдэл 0
    tb = ledger.trial_balance(session, company.id)
    assert all(r["balance_minor"] == 0 for r in tb if r["code"] in ("1001", "5101"))


def test_trial_balance_math(session, company):
    ledger.post_entry(session, company.id, date(2026, 3, 1), _simple_lines(50_000_00))
    ledger.post_entry(session, company.id, date(2026, 3, 5), [
        LineInput("7103", debit_minor=20_000_00, description="Түрээс"),
        LineInput("1001", credit_minor=20_000_00),
    ])
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["1001"]["balance_minor"] == 30_000_00
    assert tb["5101"]["balance_minor"] == 50_000_00
    assert tb["7103"]["balance_minor"] == 20_000_00
    # Σдебит = Σкредит бүх дансаар
    assert sum(r["debit_minor"] for r in tb.values()) == \
           sum(r["credit_minor"] for r in tb.values())
