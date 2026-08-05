"""Gate уналтын онош — тоон зөрүүний оронд ШАЛТГААНЫГ хэлэх ёстой."""

from datetime import datetime

from bayan import validate
from bayan.normalize import CanonTxn


def _txn(seq, amount_minor, balance_after_minor, *, direction="credit",
         day=1, desc="05-(5720669089-ХАРИЛЦАГЧ)-> 04-(413108778-ПИННАКЛ)"):
    return CanonTxn(
        seq_no=seq, row_index=seq,
        posted_at=datetime(2026, 7, day, 10, 0),
        direction=direction, amount_minor=amount_minor,
        balance_after_minor=balance_after_minor,
        counterparty_account=None,
        description_raw=desc, description_norm=desc,
        channel_ref=None, canonical_hash=f"h{seq}",
    )


def _chain(opening, specs, account="413108778", start_seq=1, start_day=1):
    """(дүн, чиглэл) жагсаалтаас тасралтгүй гинжтэй гүйлгээ үүсгэнэ.

    Бодит хуулгын адил харилцагчийн дугаар мөр бүрд өөр байна."""
    out, bal, seq, day = [], opening, start_seq, start_day
    for amount, direction in specs:
        bal += amount if direction == "credit" else -amount
        out.append(_txn(
            seq, amount, bal, direction=direction, day=day,
            desc=f"05-(57206690{seq:02d}-ХАРИЛЦАГЧ)-> 04-({account}-ПИННАКЛ)"))
        seq += 1
        day += 1
    return out


_FOUR = [(5_000_00, "credit")] * 4


def _mixed():
    """Хоёр өөр дансны хуулга нэг файлд нийлүүлэгдсэн байдал."""
    a = _chain(10_000_00, _FOUR, account="413108778")
    # Хоёр дахь данс — үлдэгдэл огт өөр цэгээс эхэлж, гинж тасарна
    b = _chain(50_000_00, _FOUR, account="411123311", start_seq=10, start_day=5)
    return a + b


def test_clean_statement_has_no_diagnosis():
    txns = _chain(10_000_00, [(5_000_00, "credit"), (3_000_00, "debit")])
    res = validate.run_gate(txns, opening_minor=10_000_00, closing_minor=12_000_00,
                            expected_account="413108778")
    assert res.ok, res.issues
    assert res.diagnosis == []


def test_multiple_accounts_in_one_file_is_named():
    """Хэд хэдэн дансны хуулга нийлүүлэгдсэнийг таньж, дансуудыг нэрлэнэ."""
    res = validate.run_gate(_mixed(), opening_minor=10_000_00,
                            closing_minor=17_000_00, expected_account="413108778")
    assert not res.ok
    msg = next(d for d in res.diagnosis if "холилдсон" in d)
    assert "413108778" in msg and "411123311" in msg
    assert "2 өөр дансны" in msg, msg
    assert "ТУСАД нь" in msg


def test_counterparty_registers_are_not_reported_as_accounts():
    """Харилцагчийн регистр/данс оношид дансаар тооцогдох ёсгүй."""
    res = validate.run_gate(_mixed(), opening_minor=10_000_00,
                            closing_minor=17_000_00, expected_account="413108778")
    msg = next(d for d in res.diagnosis if "холилдсон" in d)
    assert "5720669" not in msg, msg


def test_chain_break_without_foreign_account_gives_generic_advice():
    """Данс нэг боловч гинж тасарсан бол өөр зөвлөгөө өгнө."""
    # Хоёр блок хоёулаа ИЖИЛ дансных — данс нэрлэгдэх хангалттай мөртэй ч
    # ганцхан данс тул "холилдсон" гэж буруу дүгнэх ёсгүй
    a = _chain(10_000_00, _FOUR, account="413108778")
    b = _chain(90_000_00, _FOUR, account="413108778", start_seq=10, start_day=5)
    res = validate.run_gate(a + b, opening_minor=10_000_00,
                            closing_minor=16_000_00, expected_account="413108778")
    assert not res.ok
    assert validate._receiver_accounts(a + b) == ["413108778"]
    assert any("тасарсан" in d for d in res.diagnosis), res.diagnosis
    assert not any("холилдсон" in d for d in res.diagnosis)


def test_credits_only_export_is_flagged():
    """Зөвхөн орлогын гүйлгээтэй (шүүлттэй татсан) хуулгыг таньна."""
    txns = _chain(10_000_00, [(5_000_00, "credit"), (2_000_00, "credit")])
    # Хаалт таарахгүй — зарлага дутуу
    res = validate.run_gate(txns, opening_minor=10_000_00, closing_minor=11_000_00,
                            expected_account="413108778")
    assert not res.ok
    assert any("зөвхөн орлогын" in d for d in res.diagnosis), res.diagnosis
    assert any("ШҮҮЛТГҮЙ" in d for d in res.diagnosis)


def test_diagnosis_is_serialised():
    d = validate.run_gate(_mixed(), opening_minor=10_000_00, closing_minor=16_000_00,
                          expected_account="413108778").to_dict()
    assert isinstance(d["diagnosis"], list) and d["diagnosis"]
