"""eBarimt падаан ↔ банкны гүйлгээний тулгалтын цөм логик.

Өмнө нь дүн ЯГ тэнцүү үед л тулгадаг байсан тул бодит амьдралд бараг
ажиллахгүй байв: банк шимтгэлээ суутгах, төгрөгийн бутархай, падаан хожим
бүртгэгдэх зэргээс болж 1-2₮, 1-7 хоногийн зөрүү байнга гардаг.

Энд гурван шалгуурыг оноогоор жинлэж, хамгийн сайн хослолыг шуналтай
(greedy) аргаар сонгоно. Оноо нь итгэлийн түвшин болж, доогуур оноотойг
автоматаар батлахгүй — хүний нүдэнд үлдээнэ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher

# --------------------------------------------------------------- тохиргоо

AMOUNT_TOLERANCE_MINOR = 2_00      # ±2₮ хүртэлх зөрүүг зөвшөөрнө
DATE_TOLERANCE_DAYS = 7            # ±7 хоног
AUTO_MATCH_THRESHOLD = 0.85        # үүнээс дээш бол автоматаар тулгана
MIN_CANDIDATE_SCORE = 0.55         # үүнээс доош бол огт нэр дэвшүүлэхгүй

# Оноонд эзлэх жин (нийлбэр нь 1.0)
W_AMOUNT, W_DATE, W_PARTY = 0.60, 0.25, 0.15


@dataclass
class MatchResult:
    ebarimt_index: int
    txn_id: str | None
    confidence: float
    reasons: list[str] = field(default_factory=list)

    @property
    def auto(self) -> bool:
        return self.txn_id is not None and self.confidence >= AUTO_MATCH_THRESHOLD


# --------------------------------------------------------------- туслахууд

_NOISE = re.compile(r"\b(ххк|ххн|ххкийн|ооо|llc|co|ltd|компани)\b|[^\w\s]", re.I)


def normalize_party(text: str | None) -> str:
    """Байгууллагын нэрийг тулгахад бэлдэнэ — дагавар, цэг таслалыг хасна."""
    if not text:
        return ""
    return " ".join(_NOISE.sub(" ", text.lower()).split())


def parse_day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def amount_score(a_minor: int, b_minor: int) -> float:
    """Яг таарвал 1.0, зөвшөөрөгдөх зөрүүн дотор бага зэрэг буурна.

    ±2₮-ийн дотор байх нь өөрөө хүчтэй дохио (банкны шимтгэл, бутархай)
    тул торгуулийг бага байлгана — эс тэгвэл огноо, нэр бүрэн таарсан
    илэрхий хослол автомат босгоос унана."""
    diff = abs(a_minor - b_minor)
    if diff == 0:
        return 1.0
    if diff > AMOUNT_TOLERANCE_MINOR:
        return 0.0
    return 1.0 - (diff / AMOUNT_TOLERANCE_MINOR) * 0.2   # хамгийн муудаа 0.8


def date_score(a: date | None, b: date | None) -> float:
    """Огноо мэдэгдэхгүй бол саармаг оноо — шийдэх эрхийг дүн, нэрэнд өгнө."""
    if a is None or b is None:
        return 0.5
    days = abs((a - b).days)
    if days > DATE_TOLERANCE_DAYS:
        return 0.0
    return 1.0 - (days / DATE_TOLERANCE_DAYS) * 0.6      # хамгийн муудаа 0.4


def party_score(ebarimt_party: str | None, txn_texts: list[str | None]) -> float:
    """Харилцагчийн нэрийг гүйлгээний утга/нэртэй ойролцоо тулгана."""
    needle = normalize_party(ebarimt_party)
    if len(needle) < 3:
        return 0.5                                        # мэдээлэлгүй → саармаг
    best = 0.0
    for text in txn_texts:
        hay = normalize_party(text)
        if not hay:
            continue
        if needle in hay or hay in needle:
            return 1.0
        best = max(best, SequenceMatcher(None, needle, hay).ratio())
    return best


def score_pair(item: dict, txn) -> tuple[float, list[str]]:
    """Нэг падаан ↔ нэг гүйлгээний нийт итгэлийн оноо ба шалтгаан."""
    a_s = amount_score(int(item["total_minor"]), int(txn.amount_minor))
    if a_s == 0.0:
        return 0.0, []                                    # дүн таарахгүй бол шууд хаана

    d_s = date_score(parse_day(item.get("date")), parse_day(getattr(txn, "posted_at", None)))
    if d_s == 0.0:
        return 0.0, []                                    # огноо хэт хол бол хаана

    p_s = party_score(item.get("party"), [
        getattr(txn, "counterparty_name", None),
        getattr(txn, "description_raw", None),
    ])

    total = W_AMOUNT * a_s + W_DATE * d_s + W_PARTY * p_s

    reasons: list[str] = []
    diff = abs(int(item["total_minor"]) - int(txn.amount_minor))
    reasons.append("дүн яг тэнцүү" if diff == 0 else f"дүнгийн зөрүү {diff / 100:.2f}₮")
    d1, d2 = parse_day(item.get("date")), parse_day(getattr(txn, "posted_at", None))
    if d1 and d2:
        days = abs((d1 - d2).days)
        reasons.append("огноо ижил" if days == 0 else f"огноо {days} хоногийн зөрүүтэй")
    if p_s >= 0.9:
        reasons.append("нэр таарсан")
    elif p_s >= 0.6:
        reasons.append("нэр ойролцоо")

    return total, reasons


def match(ebarimt_items: list[dict], bank_txns: list) -> list[MatchResult]:
    """Падаан бүрд хамгийн тохирох гүйлгээг онооны дарааллаар оноино.

    Нэг гүйлгээ зөвхөн нэг падаанд оногдоно. Хамгийн итгэлтэй хослолыг
    түрүүлж баталгаажуулснаар ойролцоо дүнтэй хэд хэдэн падаан хоорондоо
    солигдох эрсдэлийг багасгана.
    """
    candidates: list[tuple[float, int, str, list[str]]] = []
    by_id = {}
    for txn in bank_txns:
        by_id[txn.id] = txn

    for idx, item in enumerate(ebarimt_items):
        for txn in bank_txns:
            score, reasons = score_pair(item, txn)
            if score >= MIN_CANDIDATE_SCORE:
                candidates.append((score, idx, txn.id, reasons))

    # Хамгийн өндөр оноотойгоос нь эхлэн шуналтайгаар оноох
    candidates.sort(key=lambda c: -c[0])
    taken_items: dict[int, MatchResult] = {}
    taken_txns: set[str] = set()

    for score, idx, txn_id, reasons in candidates:
        if idx in taken_items or txn_id in taken_txns:
            continue
        taken_items[idx] = MatchResult(idx, txn_id, round(score, 4), reasons)
        taken_txns.add(txn_id)

    return [taken_items.get(i, MatchResult(i, None, 0.0, []))
            for i in range(len(ebarimt_items))]
