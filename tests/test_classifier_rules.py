"""Үндсэн ангиллын дүрмүүд — дараалал ба чиглэлийн зөв ажиллагаа.

Дүрмүүд жагсаалтын дарааллаар priority авдаг тул НАРИЙН түлхүүр үг
ЕРӨНХИЙгөөс өмнө шалгагдах ёстой. Энэ дараалал эвдэрвэл "нийгмийн даатгал"
нь "даатгал" болж, "зээлийн хүү" нь "зээл" болж буруу ангилагдана.
"""

from datetime import datetime

import pytest
from sqlalchemy import func, select

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


# ------------------------------------------------- одоо байгаа компанид нөхөх

def test_sync_adds_missing_rules_to_existing_company(session, company):
    """Хуучин компанид цөөн дүрэм байхад дутууг нь нөхнө."""
    from sqlalchemy import delete
    from bayan.coa_seed import DEFAULT_RULE_PRIORITY_BASE, sync_default_rules

    keep = ["цалин", "түрээс"]
    session.execute(delete(ClassifierRule).where(
        ClassifierRule.company_id == company.id,
        ClassifierRule.keyword.notin_(keep)))
    session.flush()
    assert session.scalar(select(func.count()).select_from(ClassifierRule)
                          .where(ClassifierRule.company_id == company.id)) == 2

    res = sync_default_rules(session, company.id)
    assert res["added"] == len(DEFAULT_RULES) - 2
    total = session.scalar(select(func.count()).select_from(ClassifierRule)
                           .where(ClassifierRule.company_id == company.id))
    assert total == len(DEFAULT_RULES)
    # Дараалал зөв болсон эсэх — нөхсний дараа ч нарийн үг түрүүлнэ
    assert _classify(session, company, "НИЙГМИЙН ДААТГАЛЫН ШИМТГЭЛ") == "3103"
    assert _classify(session, company, "ЦАХИЛГААНЫ ТӨЛБӨР") == "7122"
    assert all(r.priority >= DEFAULT_RULE_PRIORITY_BASE for r in session.scalars(
        select(ClassifierRule).where(ClassifierRule.company_id == company.id)))


def test_sync_is_idempotent_and_keeps_user_rules(session, company):
    """Дахин ажиллуулахад давхардуулахгүй, хэрэглэгчийн дүрмийг хөндөхгүй."""
    from bayan.coa_seed import sync_default_rules

    session.add(ClassifierRule(company_id=company.id, keyword="миний нийлүүлэгч",
                               account_code="3101", priority=10))
    session.flush()

    first = sync_default_rules(session, company.id)
    second = sync_default_rules(session, company.id)
    assert second["added"] == 0 and second["reordered"] == 0

    mine = session.scalar(select(ClassifierRule).where(
        ClassifierRule.company_id == company.id,
        ClassifierRule.keyword == "миний нийлүүлэгч"))
    assert mine is not None and mine.priority == 10 and mine.account_code == "3101"
    # Хэрэглэгчийн дүрэм үндсэн дүрмээс ӨМНӨ шалгагдана
    assert _classify(session, company, "МИНИЙ НИЙЛҮҮЛЭГЧ ТӨЛБӨР") == "3101"


# ------------------------------------------------------- чиглэлийн утга

def test_credit_means_money_in_when_posted(session, company):
    """credit = банк руу мөнгө ОРСОН → Дт банк / Кт харьцах данс.

    Энэ бол чиглэлийн утгын эх сурвалж. UI-ийн шошго үүнээс урвуу байвал
    хэрэглэгч дүрмээ эсрэгээр нь тохируулна."""
    from bayan import ledger
    from bayan.models import Direction as D
    e = ledger.post_entry(session, company.id, __import__("datetime").date(2026, 7, 1), [
        ledger.LineInput("1101", debit_minor=50_000_00, description="орлого"),
        ledger.LineInput("5101", credit_minor=50_000_00, description="орлого"),
    ])
    from bayan.models import Account
    bank_id = session.scalar(select(Account.id).where(
        Account.company_id == company.id, Account.code == "1101"))
    bank = next(l for l in e.lines if l.account_id == bank_id)
    assert bank.debit_minor == 50_000_00, "мөнгө орох үед банкны данс дебетлэгдэнэ"
    assert D.credit.value == "credit"


def test_ui_labels_credit_as_income_not_expense():
    """Дэлгэц дээрх шошго урвуу байсныг барих регресс тест."""
    from pathlib import Path
    html = Path(__file__).resolve().parents[1] / "web" / "index.html"
    text = html.read_text(encoding="utf-8")
    assert "Зарлага (Credit)" not in text, "credit нь зарлага БИШ — мөнгө орж байна"
    assert "Орлого (Debit)" not in text, "debit нь орлого БИШ — мөнгө гарч байна"
    assert "Орлого (Credit)" in text
    assert "Зарлага — данснаас мөнгө гарсан (Debit)" in text


def test_expense_rules_only_fire_on_outgoing_money(session, company):
    """Зардлын дүрэм зөвхөн ЗАРЛАГАД ажиллана.

    "Даатгал" гэсэн үгтэй ОРЛОГО нь даатгалын нөхөн төлбөр — түүнийг
    даатгалын зардал руу хийвэл зардлыг хасах буруу бичилт үүснэ."""
    assert _classify(session, company, "МИГ ДААТГАЛ НӨХӨН ТӨЛБӨР",
                     Direction.debit) == "7116"
    assert _classify(session, company, "МИГ ДААТГАЛ НӨХӨН ТӨЛБӨР",
                     Direction.credit) is None      # AI/хүний шалгалтад үлдэнэ


def test_liability_rules_still_work_both_ways(session, company):
    """Зээл авах нь орлого, төлөх нь зарлага — хоёулаа 3201 руу орно."""
    assert _classify(session, company, "БОГИНО ХУГАЦААТ ЗЭЭЛ", Direction.credit) == "3201"
    assert _classify(session, company, "БОГИНО ХУГАЦААТ ЗЭЭЛ", Direction.debit) == "3201"


def test_every_expense_rule_carries_debit_direction(session, company):
    from bayan.models import Account
    expense_codes = {a.code for a in session.scalars(
        select(Account).where(Account.company_id == company.id))
        if a.code[0] in "67"}
    rules = session.scalars(select(ClassifierRule).where(
        ClassifierRule.company_id == company.id)).all()
    for r in rules:
        if r.account_code in expense_codes:
            assert r.direction == Direction.debit, f"'{r.keyword}' чиглэлгүй байна"
