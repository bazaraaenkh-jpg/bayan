"""Үндсэн ангиллын дүрмүүд — дараалал ба чиглэлийн зөв ажиллагаа.

Дүрмүүд жагсаалтын дарааллаар priority авдаг тул НАРИЙН түлхүүр үг
ЕРӨНХИЙгөөс өмнө шалгагдах ёстой. Энэ дараалал эвдэрвэл "нийгмийн даатгал"
нь "даатгал" болж, "зээлийн хүү" нь "зээл" болж буруу ангилагдана.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from bayan import classify
from bayan.coa_seed import DEFAULT_RULES, SEED
from bayan.models import BankTxn, ClassifierRule, Direction, ExtractionPath


def _txn(company_id, description, direction=Direction.debit, amount_minor=10_000_00):
    return BankTxn(
        statement_id=None, company_id=company_id, bank_account_key="k",
        seq_no=1, posted_at=datetime(2026, 7, 1), direction=direction,
        amount_minor=amount_minor, balance_after_minor=None,
        counterparty_account=None, counterparty_name=None,
        description_raw=description, description_norm=description.lower(),
        channel_ref=None, canonical_hash="h", extraction_path=ExtractionPath.excel,
    )


def _classify(session, company, text, direction=Direction.debit):
    s = classify.apply_rules(session, company.id, _txn(company.id, text, direction))
    return s.account_code if s else None


# ------------------------------------------------------------------ бүрдэл

def test_rule_library_is_substantially_larger():
    assert len(DEFAULT_RULES) >= 48, f"одоо {len(DEFAULT_RULES)} дүрэм"


def test_every_rule_points_at_a_real_account():
    codes = {row[0] for row in SEED}
    missing = sorted({r[1] for r in DEFAULT_RULES} - codes)
    assert not missing, f"дансны төлөвлөгөөнд байхгүй код: {missing}"


def test_no_duplicate_keywords():
    kws = [r[0] for r in DEFAULT_RULES]
    assert len(kws) == len(set(kws))


def test_rules_are_seeded_with_ascending_priority(session, company):
    rules = session.scalars(
        select(ClassifierRule).where(ClassifierRule.company_id == company.id)
        .order_by(ClassifierRule.priority)).all()
    assert len(rules) == len(DEFAULT_RULES)
    prios = [r.priority for r in rules]
    assert prios == sorted(prios) and len(set(prios)) == len(prios)


# ------------------------------------------------- дарааллаас хамаарах кейс

@pytest.mark.parametrize("text, expected", [
    ("НИЙГМИЙН ДААТГАЛЫН ШИМТГЭЛ ТӨЛӨВ", "3103"),   # "даатгал"/"шимтгэл"-ээс өмнө
    ("АВТО ДААТГАЛЫН ТӨЛБӨР", "7116"),
    ("ЗЭЭЛИЙН ХҮҮ ТӨЛӨЛТ", "7119"),                 # "зээл"-ээс өмнө
    ("БОГИНО ХУГАЦААТ ЗЭЭЛ ОЛГОВ", "3201"),
    ("НӨАТ ТӨЛӨЛТ", "3105"),                        # "татвар"-аас тусдаа
    ("ХХОАТ ШИЛЖҮҮЛЭВ", "3106"),
    ("ОРЛОГЫН АЛБАН ТАТВАР", "3104"),
    ("ДАНС ХӨТӨЛСНИЙ ШИМТГЭЛ", "7106"),
])
def test_specific_keyword_wins_over_general(session, company, text, expected):
    assert _classify(session, company, text) == expected


# ------------------------------------------------------ шинэ хамрах хүрээ

@pytest.mark.parametrize("text, expected", [
    ("ЦАХИЛГААНЫ ТӨЛБӨР УБЦТС", "7122"),
    ("ДУЛААНЫ ТӨЛБӨР", "7122"),
    ("ПЕТРОВИС ШАТАХУУН", "7104"),
    ("ТАКСИНЫ ЗАРДАЛ", "7104"),
    ("ЮНИТЕЛ ДАТА ТӨЛБӨР", "7105"),
    ("БИЧИГ ХЭРГИЙН ХУДАЛДАН АВАЛТ", "7109"),
    ("ЗАР СУРТАЛЧИЛГААНЫ ЗАРДАЛ", "7111"),
    ("ТОМИЛОЛТЫН ЗАРДАЛ", "7112"),
    ("АВТОМАШИНЫ ЗАСВАР", "7113"),
    ("ЦЭВЭРЛЭГЭЭНИЙ ҮЙЛЧИЛГЭЭ", "7114"),
    ("КАРГО ХҮРГЭЛТ", "7115"),
    ("АУДИТЫН ҮЙЛЧИЛГЭЭ", "7117"),
])
def test_common_narrations_are_classified(session, company, text, expected):
    assert _classify(session, company, text) == expected


# ------------------------------------------------------------------ чиглэл

def test_revenue_rule_only_applies_to_credit(session, company):
    assert _classify(session, company, "БОРЛУУЛАЛТЫН ОРЛОГО", Direction.credit) == "5101"
    # Ижил утгатай ЗАРЛАГА нь орлогын данс руу ангилагдах ёсгүй
    assert _classify(session, company, "БОРЛУУЛАЛТЫН БУЦААЛТ", Direction.debit) != "5101"


def test_interest_income_vs_interest_expense(session, company):
    assert _classify(session, company, "ХАДГАЛАМЖИЙН ХҮҮ", Direction.credit) == "5202"
    assert _classify(session, company, "ЗЭЭЛИЙН ХҮҮ", Direction.debit) == "7119"


def test_unknown_narration_returns_nothing(session, company):
    """Дүрэм таарахгүй бол AI давхаргад шилжинэ — таамаглаж болохгүй."""
    assert _classify(session, company, "ТОДОРХОЙГҮЙ ГҮЙЛГЭЭ XYZ") is None
