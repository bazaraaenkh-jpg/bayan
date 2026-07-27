"""Validation gate V1-V6-ийн тестүүд — эвдрэл бүр баригдах ёстой."""

from datetime import datetime

from bayan.normalize import CanonTxn
from bayan.validate import run_gate


def _txn(seq, day, direction, amount, balance, desc="test", h=None):
    return CanonTxn(
        seq_no=seq, row_index=seq + 10,
        posted_at=datetime(2026, 3, day, 10, 0),
        direction=direction, amount_minor=amount,
        balance_after_minor=balance,
        counterparty_account=None, description_raw=desc,
        description_norm=desc, channel_ref=None,
        canonical_hash=h or f"hash{seq}",
    )


def _good_chain(opening=1_000_000):
    """Нээлт 10,000₮ → +5,000 → −2,000 → +1,000 = 14,000₮."""
    return [
        _txn(1, 1, "credit", 500_000, opening + 500_000),
        _txn(2, 2, "debit", 200_000, opening + 300_000),
        _txn(3, 3, "credit", 100_000, opening + 400_000),
    ]


def test_good_statement_passes():
    r = run_gate(_good_chain(), opening_minor=1_000_000, closing_minor=1_400_000)
    assert r.ok, r.to_dict()


def test_v1_broken_chain_located_at_row():
    txns = _good_chain()
    txns[1].balance_after_minor += 1  # 1 мөнгөөр эвдэв
    r = run_gate(txns, opening_minor=1_000_000, closing_minor=1_400_001)
    assert not r.ok
    v1 = [i for i in r.issues if i.check == "V1"]
    assert v1 and v1[0].row_index == txns[1].row_index  # яг тэр мөрийг заана


def test_v2_closing_mismatch():
    r = run_gate(_good_chain(), opening_minor=1_000_000, closing_minor=9_999_999)
    assert not r.ok
    assert any(i.check == "V2" for i in r.issues)


def test_v3_row_count():
    r = run_gate(_good_chain(), opening_minor=1_000_000, closing_minor=1_400_000,
                 declared_row_count=5)
    assert any(i.check == "V3" for i in r.issues)


def test_v4_date_out_of_period_and_order():
    txns = _good_chain()
    r = run_gate(txns, opening_minor=1_000_000, closing_minor=1_400_000,
                 period_from=datetime(2026, 3, 2))  # 1-ний гүйлгээ мужаас өмнө
    assert any(i.check == "V4" for i in r.issues)


def test_v5_continuity():
    r = run_gate(_good_chain(), opening_minor=1_000_000, closing_minor=1_400_000,
                 prev_closing_minor=999_999)
    assert any(i.check == "V5" for i in r.issues)


def test_v6_duplicate_hash():
    txns = _good_chain()
    txns[2].canonical_hash = txns[0].canonical_hash
    r = run_gate(txns, opening_minor=1_000_000, closing_minor=1_400_000)
    assert any(i.check == "V6" for i in r.issues)


def test_missing_metadata_reduced_assurance():
    txns = _good_chain()
    for t in txns:
        t.balance_after_minor = None
    r = run_gate(txns, opening_minor=None, closing_minor=None)
    assert not r.ok  # reduced assurance тэмдэглэгдэнэ
    assert any(i.check == "V2" for i in r.issues)
