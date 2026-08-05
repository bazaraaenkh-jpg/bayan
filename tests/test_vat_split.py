"""Батлах үед НӨАТ салгах.

vat_flag нь дүрэм, AI, eBarimt тулгалт бүгдэд тооцогдож хадгалагддаг байсан
ч журналын бичилт үүсгэхэд ОГТ ашиглагддаггүй байв. Үүнээс болж зардал
НӨАТ-ын дүнгээр хэтэрч, суутгах НӨАТ хуримтлагдахгүй тул ТТ-03А тайлан
дутуу гардаг.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from bayan import ledger, pipeline
from bayan.models import (Account, ClassificationSuggestion, Company, Direction,
                          ExtractionPath, BankTxn, Statement, StatementStatus)


def _txn(session, company, direction=Direction.debit, amount_minor=110_000_00):
    stmt = Statement(company_id=company.id, file_name="t.xls",
                     file_sha256=f"h{amount_minor}{direction.value}",
                     descriptor_id="d", status=StatementStatus.parsed_verified)
    session.add(stmt); session.flush()
    t = BankTxn(statement_id=stmt.id, company_id=company.id, bank_account_key="k",
                seq_no=1, posted_at=datetime(2026, 7, 1), direction=direction,
                amount_minor=amount_minor, balance_after_minor=None,
                counterparty_account=None, counterparty_name=None,
                description_raw="ТҮРЭЭСИЙН ТӨЛБӨР", description_norm="түрээсийн төлбөр",
                channel_ref=None, canonical_hash=f"c{amount_minor}{direction.value}",
                extraction_path=ExtractionPath.excel)
    session.add(t); session.flush()
    return t


def _suggest(session, company, txn, code, vat_flag):
    s = ClassificationSuggestion(
        bank_txn_id=txn.id, company_id=company.id, account_code=code,
        vat_flag=vat_flag, confidence=1.0, rationale="тест", source="rule",
        status="pending")
    session.add(s); session.flush()
    return s


def _approve(session, company, sug):
    ids = pipeline.approve_suggestions(session, company.id, [sug.id], "1101")
    return session.get(ledger.JournalEntry, ids[0]) if ids else None


def _by_code(session, company, entry):
    codes = {a.id: a.code for a in session.scalars(
        select(Account).where(Account.company_id == company.id))}
    return {codes[l.account_id]: (l.debit_minor, l.credit_minor) for l in entry.lines}


# ------------------------------------------------------------ худалдан авалт

def test_purchase_splits_input_vat(session, company):
    """110,000₮ зардал → 100,000 зардал + 10,000 НӨАТ авлага."""
    t = _txn(session, company, Direction.debit, 110_000_00)
    e = _approve(session, company, _suggest(session, company, t, "7103", True))
    lines = _by_code(session, company, e)

    assert lines["7103"] == (100_000_00, 0)
    assert lines[pipeline.VAT_INPUT_CODE] == (10_000_00, 0)
    assert lines["1101"] == (0, 110_000_00)


def test_sale_splits_output_vat(session, company):
    """Орлого 110,000₮ → 100,000 орлого + 10,000 НӨАТ өглөг."""
    t = _txn(session, company, Direction.credit, 110_000_00)
    e = _approve(session, company, _suggest(session, company, t, "5101", True))
    lines = _by_code(session, company, e)

    assert lines["1101"] == (110_000_00, 0)
    assert lines["5101"] == (0, 100_000_00)
    assert lines[pipeline.VAT_OUTPUT_CODE] == (0, 10_000_00)


# --------------------------------------------------------------- салгахгүй

def test_no_split_when_rule_says_no_vat(session, company):
    t = _txn(session, company, Direction.debit, 110_000_00)
    e = _approve(session, company, _suggest(session, company, t, "7103", False))
    lines = _by_code(session, company, e)

    assert lines["7103"] == (110_000_00, 0)
    assert pipeline.VAT_INPUT_CODE not in lines


def test_no_split_for_non_vat_payer(session, company):
    company.vat_payer = False
    session.flush()
    t = _txn(session, company, Direction.debit, 110_000_00)
    e = _approve(session, company, _suggest(session, company, t, "7103", True))
    lines = _by_code(session, company, e)

    assert lines["7103"] == (110_000_00, 0)
    assert pipeline.VAT_INPUT_CODE not in lines


# ----------------------------------------------------------------- тэнцэл

@pytest.mark.parametrize("amount", [1, 333_33, 100_000_00, 110_000_00, 999_999_99])
def test_entry_always_balances_despite_rounding(session, company, amount):
    """Бутархай тайрахад ч Дт = Кт байх ёстой (G1 инвариант)."""
    t = _txn(session, company, Direction.debit, amount)
    e = _approve(session, company, _suggest(session, company, t, "7103", True))
    assert sum(l.debit_minor for l in e.lines) == sum(l.credit_minor for l in e.lines)
    assert sum(l.debit_minor for l in e.lines) == amount


def test_split_uses_ten_percent_inclusive_formula(session, company):
    """Монголд үнэ дотор НӨАТ багтсан: цэвэр = нийт ÷ 1.1."""
    net, vat = pipeline._split_vat(
        session, company.id, 110_000_00,
        type("S", (), {"vat_flag": True})(), is_credit=False)
    assert (net, vat) == (100_000_00, 10_000_00)
