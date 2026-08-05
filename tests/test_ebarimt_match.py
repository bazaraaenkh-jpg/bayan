"""eBarimt ↔ банк тулгалтын оноололт.

Өмнө нь дүн ЯГ тэнцүү үед л тулгадаг байсан тул банкны шимтгэл, бутархай,
падаан хожим бүртгэгдэх зэргээс болж бодит хуулга дээр бараг тулгагддаггүй
байв. Энд зөвшөөрөгдөх зөрүү ба итгэлийн түвшнийг шалгана.
"""

from datetime import datetime

from bayan import ebarimt_match as em


class Txn:
    """BankTxn-ийн тулгалтад хэрэглэгддэг талбаруудын хөнгөн орлуулагч."""

    def __init__(self, id, amount_minor, day, counterparty_name=None, desc=""):
        self.id = id
        self.amount_minor = amount_minor
        self.posted_at = datetime(2026, 7, day, 10, 0)
        self.counterparty_name = counterparty_name
        self.description_raw = desc


def _item(total_minor, day, party="НОМИН ГЭГЭЭ ХХК", rid="EB-1"):
    return {"total_minor": total_minor, "date": f"2026-07-{day:02d}",
            "party": party, "receipt_id": rid}


# ------------------------------------------------------------ үндсэн урсгал

def test_exact_match_is_fully_confident():
    res = em.match([_item(120_000_00, 15)],
                   [Txn("t1", 120_000_00, 15, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id == "t1"
    assert res[0].confidence == 1.0
    assert res[0].auto


def test_small_amount_difference_still_matches():
    """Банкны шимтгэлээс болж 1.50₮ зөрүүтэй байсан ч тулгана."""
    res = em.match([_item(120_000_00, 15)],
                   [Txn("t1", 119_998_50, 15, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id == "t1"
    assert res[0].auto
    assert "дүнгийн зөрүү" in ", ".join(res[0].reasons)


def test_date_lag_within_window_matches():
    """Падаан 3 хоногийн дараа бүртгэгдсэн ч тулгана."""
    res = em.match([_item(120_000_00, 15)],
                   [Txn("t1", 120_000_00, 18, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id == "t1"
    assert "огноо 3 хоногийн зөрүүтэй" in res[0].reasons


def test_beyond_amount_tolerance_does_not_match():
    res = em.match([_item(120_000_00, 15)], [Txn("t1", 119_000_00, 15)])
    assert res[0].txn_id is None


def test_beyond_date_tolerance_does_not_match():
    res = em.match([_item(120_000_00, 1)], [Txn("t1", 120_000_00, 20)])
    assert res[0].txn_id is None


# ------------------------------------------------------------ нэрийн нөлөө

def test_party_name_breaks_the_tie():
    """Ижил дүн, ижил огноотой хоёр гүйлгээнээс нэр таарсныг сонгоно."""
    res = em.match(
        [_item(50_000_00, 10, party="НОМИН ГЭГЭЭ ХХК")],
        [Txn("wrong", 50_000_00, 10, "ӨӨР КОМПАНИ ХХК"),
         Txn("right", 50_000_00, 10, "НОМИН ГЭГЭЭ ХХК")],
    )
    assert res[0].txn_id == "right"


def test_party_found_inside_description():
    res = em.match(
        [_item(50_000_00, 10, party="НОМИН ГЭГЭЭ")],
        [Txn("t1", 50_000_00, 10, None, "ШИЛЖҮҮЛЭГ НОМИН ГЭГЭЭ ХХК-Д")],
    )
    assert res[0].txn_id == "t1"
    assert "нэр таарсан" in res[0].reasons


def test_mismatched_name_lowers_confidence_below_auto():
    """Дүн, огноо таарсан ч нэр огт өөр бол автоматаар батлахгүй."""
    res = em.match([_item(50_000_00, 10, party="НОМИН ГЭГЭЭ ХХК")],
                   [Txn("t1", 49_999_00, 13, "ХОЛБООГҮЙ БАЙГУУЛЛАГА")])
    assert res[0].txn_id == "t1"
    assert not res[0].auto, res[0].confidence


# ------------------------------------------------------------ оноолт

def test_one_txn_is_never_used_twice():
    res = em.match([_item(50_000_00, 10, rid="EB-1"), _item(50_000_00, 10, rid="EB-2")],
                   [Txn("t1", 50_000_00, 10)])
    assigned = [r.txn_id for r in res]
    assert assigned.count("t1") == 1
    assert None in assigned


def test_best_pair_wins_over_weaker_one():
    """Ойролцоо дүнтэй падаанууд хоорондоо солигдох ёсгүй."""
    res = em.match(
        [_item(50_000_00, 10, party="А КОМПАНИ", rid="EB-A"),
         _item(50_000_50, 10, party="Б КОМПАНИ", rid="EB-B")],
        [Txn("tA", 50_000_00, 10, "А КОМПАНИ"),
         Txn("tB", 50_000_50, 10, "Б КОМПАНИ")],
    )
    assert res[0].txn_id == "tA"
    assert res[1].txn_id == "tB"


def test_missing_date_falls_back_to_amount_and_name():
    item = _item(50_000_00, 10)
    item["date"] = ""
    res = em.match([item], [Txn("t1", 50_000_00, 10, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id == "t1"


def test_no_bank_txns_returns_empty_matches():
    res = em.match([_item(50_000_00, 10)], [])
    assert len(res) == 1 and res[0].txn_id is None


# ------------------------------------------------------------ нэр цэвэрлэх

def test_legal_suffix_is_ignored_when_comparing_names():
    assert em.normalize_party("Номин Гэгээ ХХК") == em.normalize_party("НОМИН ГЭГЭЭ")
