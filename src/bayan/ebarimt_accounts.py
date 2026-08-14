"""eBarimt-ын худалдан авалтыг ЗАРДЛЫН ДАНСААР автоматаар ангилах.

Баримт бүр 7199 «Бусад зардал»-д унавал тайлан утгагүй болно. Энд гурван
давхаргаар шийднэ:

  1. **Компанийн өөрийн дүрэм** (`ClassifierRule`) — нягтлан нэг удаа зааж
     өгөхөд ТТД/нэрээр нь тогтоно. Дараа сард автомат. Хамгийн дээгүүр.
  2. **Нийлүүлэгчийн суурь дүрэм** (доорх `SUPPLIER_RULES`) — Монголын
     тодорхой худалдагчид (ШТС, оператор, даатгал, банк…).
  3. **Утгын түлхүүр үг** (`KEYWORD_RULES`) — нэрэнд агуулагдах ерөнхий үг.

Аль нь ч таарахгүй бол 7199-д үлдэж, «ангилаагүй» гэж тэмдэглэгдэнэ —
нягтлан нэг удаа зааж өгвөл 1-р давхаргад суралцана.

⚠️ Эдгээр дүрэм нь **зөвхөн eBarimt-ын нийлүүлэгчийн НЭРэнд** ажиллана.
Банкны гүйлгээний утгад хэрэглэвэл алдаа гарна (жишээ нь «банк» гэсэн үг
шилжүүлгийн утгад байнга таарах ч тэр нь шимтгэл БИШ), тиймээс
`classify.apply_rules`-ийн жагсаалттай зориуд тусад нь байлгав.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ClassifierRule, Direction

FALLBACK_ACCOUNT = "7199"

#: Тодорхой нийлүүлэгчид — нэрний хэсэг → данс.
#: Дараалал ЧУХАЛ: тодорхойгоос ерөнхий рүү.
SUPPLIER_RULES: list[tuple[str, str]] = [
    # --- ШТС, шатахуун (7104) -------------------------------------------
    ("шунхлай", "7104"),
    ("петровис", "7104"),
    ("петростар", "7104"),
    ("петролиум", "7104"),
    ("петролеум", "7104"),
    ("нефть", "7104"),
    ("газ ойл", "7104"),
    ("газойл", "7104"),
    ("содмонгол", "7104"),
    ("сод монгол", "7104"),
    ("магнай трейд", "7104"),
    ("автобенз", "7104"),
    ("жаст ойл", "7104"),
    ("монгол ойл", "7104"),
    ("нік", "7104"),

    # --- Холбоо, интернэт, IT-үйлчилгээ (7105) --------------------------
    ("мобиком", "7105"),
    ("юнител", "7105"),
    ("унител", "7105"),
    ("скайтел", "7105"),
    ("skytel", "7105"),
    ("g-mobile", "7105"),
    ("жи мобайл", "7105"),
    ("ондо", "7105"),
    ("сислинк", "7105"),
    ("систелеком", "7105"),
    ("гэмнэт", "7105"),
    ("юнивишн", "7105"),

    # --- Даатгал (7116) --------------------------------------------------
    ("даатгал", "7116"),
    ("иншур", "7116"),
    ("insurance", "7116"),

    # --- Банк, санхүүгийн шимтгэл (7106) ---------------------------------
    # eBarimt-ын баримт банкнаас ирнэ гэдэг нь бараг үргэлж ҮЙЛЧИЛГЭЭНИЙ
    # ХУРААМЖ (зээл, хадгаламж баримт үүсгэдэггүй).
    ("банк", "7106"),
    ("хаан банк", "7106"),
    ("голомт", "7106"),
    ("төрийн банк", "7106"),
    ("капитрон", "7106"),
    ("ариг банк", "7106"),
    ("хас банк", "7106"),

    # --- Нийтийн үйлчилгээ, ашиглалт (7122) ------------------------------
    ("тохижилт", "7122"),
    ("ус суваг", "7122"),
    ("усуг", "7122"),
    ("убцтс", "7122"),
    ("дулааны сүлжээ", "7122"),
    ("дулаан шугам", "7122"),
    ("цахилгаан түгээх", "7122"),
    ("эрчим хүч", "7122"),
    ("хог", "7122"),

    # --- Тээвэр, хүргэлт (7115) ------------------------------------------
    ("карго", "7115"),
    ("экспресс", "7115"),
    ("шуудан", "7115"),
    ("дхл", "7115"),
    ("dhl", "7115"),

    # --- Мэргэжлийн үйлчилгээ (7117) -------------------------------------
    ("аудит", "7117"),
    ("нотариат", "7117"),
    ("хуулийн", "7117"),
    ("зөвлөх", "7117"),

    # --- Бичиг хэрэг, канц (7109) ----------------------------------------
    ("монгол шуудан", "7115"),
    ("канц", "7109"),
    ("офис", "7109"),
]

#: Нэрэнд агуулагдвал утгыг нь илтгэх ерөнхий үгс (нийлүүлэгч танигдаагүй үед)
KEYWORD_RULES: list[tuple[str, str]] = [
    ("шатахуун", "7104"),
    ("бензин", "7104"),
    ("дизель", "7104"),
    ("тээвэр", "7115"),
    ("хүргэлт", "7115"),
    ("түрээс", "7103"),
    ("зочид буудал", "7112"),
    ("зочид", "7112"),
    ("нисэх", "7112"),
    ("агаарын тээвэр", "7112"),
    ("сургалт", "7110"),
    ("академи", "7110"),
    ("сурталчилгаа", "7111"),
    ("реклам", "7111"),
    ("хэвлэл", "7111"),
    ("засвар", "7113"),
    ("сэлбэг", "7113"),
    ("автосервис", "7113"),
    ("цэвэрлэгээ", "7114"),
    ("ариутгал", "7114"),
    ("эмнэлэг", "7199"),
]

_NOISE = re.compile(r"\b(ххк|ххн|хк|llc|co|ltd|компани|corporation|корпораци)\b|[^\w\s]",
                    re.I)


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(_NOISE.sub(" ", text.lower()).split())


def is_individual(tin: str | None) -> bool:
    """ТТД-ээр иргэн эсэхийг ялгана.

    ААН-ы регистр 7 оронтой (2117932), иргэний ТТД 10–12 оронтой
    (111665652463). Иргэнээс худалдан авалт хийвэл ХХОАТ суутгах
    шаардлага гарч болзошгүй тул тусад нь тэмдэглэнэ.
    """
    t = (tin or "").strip()
    return t.isdigit() and len(t) >= 10


def _company_rules(session: Session, company_id: str) -> list[ClassifierRule]:
    return list(session.scalars(
        select(ClassifierRule)
        .where(ClassifierRule.company_id == company_id,
               ClassifierRule.active == True)          # noqa: E712
        .order_by(ClassifierRule.priority)))


def resolve(session: Session, company_id: str, party: str | None,
            tin: str | None = None, *, rules_cache: list | None = None,
            fallback: str = FALLBACK_ACCOUNT) -> dict:
    """Нийлүүлэгчийн нэрээр зардлын данс тодорхойлно.

    Буцаах: {"account_code", "source", "matched", "is_individual", "confident"}
    source: company_rule | supplier | keyword | fallback
    """
    name = normalize(party)
    info = {"account_code": fallback, "source": "fallback", "matched": None,
            "is_individual": is_individual(tin), "confident": False}
    if not name:
        return info

    # 1. Компанийн өөрийн (сурсан эсвэл гараар оруулсан) дүрэм
    rules = rules_cache if rules_cache is not None else _company_rules(session, company_id)
    for r in rules:
        if r.direction is not None and r.direction != Direction.debit:
            continue
        kw = normalize(r.keyword)
        if kw and kw in name:
            return {**info, "account_code": r.account_code, "source": "company_rule",
                    "matched": r.keyword, "confident": True}

    # 2. Тодорхой нийлүүлэгч
    for needle, code in SUPPLIER_RULES:
        if needle in name:
            return {**info, "account_code": code, "source": "supplier",
                    "matched": needle, "confident": True}

    # 3. Нэр дэх утгын түлхүүр үг
    for needle, code in KEYWORD_RULES:
        if needle in name:
            return {**info, "account_code": code, "source": "keyword",
                    "matched": needle, "confident": code != FALLBACK_ACCOUNT}

    return info


def learn(session: Session, company_id: str, keyword: str, account_code: str,
          priority: int = 100) -> ClassifierRule:
    """Нягтлангийн зааврыг дүрэм болгон хадгална (дараагийн сард автомат).

    Ижил түлхүүр үгтэй дүрэм байвал дансыг нь шинэчилнэ — давхардуулахгүй.
    """
    kw = (keyword or "").strip()
    if not kw:
        raise ValueError("Түлхүүр үг хоосон байна")

    existing = session.scalar(
        select(ClassifierRule).where(
            ClassifierRule.company_id == company_id,
            ClassifierRule.keyword == kw,
            ClassifierRule.direction == Direction.debit))
    if existing:
        existing.account_code = account_code
        existing.active = True
        existing.priority = min(existing.priority, priority)
        session.flush()
        return existing

    rule = ClassifierRule(company_id=company_id, keyword=kw,
                          direction=Direction.debit, account_code=account_code,
                          vat_flag=True, priority=priority, active=True)
    session.add(rule)
    session.flush()
    return rule
