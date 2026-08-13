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
from itertools import combinations

# --------------------------------------------------------------- тохиргоо

AMOUNT_TOLERANCE_MINOR = 2_00      # ±2₮ хүртэлх зөрүүг зөвшөөрнө
DATE_TOLERANCE_DAYS = 7            # ±7 хоног
AUTO_MATCH_THRESHOLD = 0.85        # үүнээс дээш бол автоматаар тулгана
MIN_CANDIDATE_SCORE = 0.55         # үүнээс доош бол огт нэр дэвшүүлэхгүй

# Нэг гүйлгээнд хэдэн падаан нийлж болох (нэг шилжүүлгээр олон баримт төлөх)
GROUP_MAX_ITEMS = 4
GROUP_CANDIDATE_LIMIT = 14         # хослолын тооны тэсрэлтээс хамгаална

# ПОС/бэлэн борлуулалт банкинд ӨДРИЙН нэгдсэн орлого болж нэг дүнгээр ордог.
# Тэр өдрийн бүх баримтын нийлбэрийг нэг гүйлгээтэй тулгана.
DAILY_MIN_ITEMS = 2                # хоёроос доош бол ердийн бүлэглэлт хангалттай
DAILY_SCORE_PENALTY = 0.98         # нэг бүрчлэн тулгасантай адилтгахгүй

# Оноонд эзлэх жин (нийлбэр нь 1.0)
W_AMOUNT, W_DATE, W_PARTY = 0.60, 0.25, 0.15


@dataclass
class MatchResult:
    ebarimt_index: int
    txn_id: str | None
    confidence: float
    reasons: list[str] = field(default_factory=list)
    group_id: str | None = None      # олон падаан → нэг гүйлгээ болсон бол
    group_size: int = 1

    @property
    def auto(self) -> bool:
        return self.txn_id is not None and self.confidence >= AUTO_MATCH_THRESHOLD


def txn_direction(txn) -> str | None:
    """Банкны гүйлгээний чиглэл: credit = мөнгө ОРСОН, debit = ГАРСАН.

    (pipeline.post_entries-ийн конвенцтой ижил — тэнд credit үед
     Дт банк / Кт орлого бичилт үүсдэг.)
    """
    d = getattr(txn, "direction", None)
    if d is None:
        return None
    val = getattr(d, "value", None) or str(d)
    val = val.split(".")[-1].lower()
    if val == "credit":
        return "in"
    if val == "debit":
        return "out"
    return None


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


def direction_ok(item: dict, txn) -> bool:
    """Орлогын падааныг зарлагын гүйлгээтэй тулгахаас сэргийлнэ.

    eBarimt-ын 4 тайлангийн 2 нь орлого (мөнгө орно), 2 нь зарлага (мөнгө
    гарна). Чиглэл мэдэгдэж байхад эсрэг чиглэлийн гүйлгээтэй тулгах нь
    үргэлж алдаа — дүн, огноо нь тохиосон ч болохгүй.
    """
    want = item.get("direction")
    have = txn_direction(txn)
    if want is None or have is None:
        return True
    return want == have


def score_pair(item: dict, txn) -> tuple[float, list[str]]:
    """Нэг падаан ↔ нэг гүйлгээний нийт итгэлийн оноо ба шалтгаан."""
    if not direction_ok(item, txn):
        return 0.0, []

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


def _group_score(items: list[dict], txn) -> tuple[float, list[str]]:
    """Нийлмэл төлбөрийн итгэлийн оноо — дүн нийлбэрээр, огноо дунджаар."""
    total = sum(int(i["total_minor"]) for i in items)
    a_s = amount_score(total, int(txn.amount_minor))
    if a_s == 0.0:
        return 0.0, []

    t_day = parse_day(getattr(txn, "posted_at", None))
    d_scores = [date_score(parse_day(i.get("date")), t_day) for i in items]
    if any(s == 0.0 for s in d_scores):
        return 0.0, []
    d_s = sum(d_scores) / len(d_scores)

    texts = [getattr(txn, "counterparty_name", None),
             getattr(txn, "description_raw", None)]
    p_s = max(party_score(i.get("party"), texts) for i in items)

    total_score = (W_AMOUNT * a_s + W_DATE * d_s + W_PARTY * p_s) * 0.95
    diff = abs(total - int(txn.amount_minor))
    reasons = [f"{len(items)} падаан нэг гүйлгээнд нийлсэн"]
    reasons.append("нийлбэр яг тэнцүү" if diff == 0
                   else f"нийлбэрийн зөрүү {diff / 100:.2f}₮")
    return total_score, reasons


def _match_groups(ebarimt_items: list[dict], results: list[MatchResult],
                  bank_txns: list, taken_txns: set[str]) -> None:
    """Үлдсэн падаануудыг нэг гүйлгээнд нийлүүлж тулгахыг оролдоно.

    Бодит амьдралд нэг шилжүүлгээр хэд хэдэн баримтын төлбөрийг нэг дор
    төлдөг. 1:1 тулгалт үүнийг барьж чаддаггүй тул падаанууд «банкны мөнгө
    ирээгүй» болж хуурамчаар харагддаг байв.
    """
    free = [i for i, r in enumerate(results) if r.txn_id is None]
    if not free:
        return

    for txn in bank_txns:
        if txn.id in taken_txns or not free:
            continue
        t_day = parse_day(getattr(txn, "posted_at", None))
        t_amt = int(txn.amount_minor)

        cands = [
            i for i in free
            if direction_ok(ebarimt_items[i], txn)
            and int(ebarimt_items[i]["total_minor"]) < t_amt
            and date_score(parse_day(ebarimt_items[i].get("date")), t_day) > 0.0
        ]
        if len(cands) < 2:
            continue
        # Огноогоор ойрхныг эхэнд нь — хослолын хайлт хязгаартай
        cands.sort(key=lambda i: -date_score(
            parse_day(ebarimt_items[i].get("date")), t_day))
        cands = cands[:GROUP_CANDIDATE_LIMIT]

        found = None
        for size in range(2, min(GROUP_MAX_ITEMS, len(cands)) + 1):
            for combo in combinations(cands, size):
                score, reasons = _group_score(
                    [ebarimt_items[i] for i in combo], txn)
                if score >= MIN_CANDIDATE_SCORE:
                    found = (combo, score, reasons)
                    break
            if found:
                break

        if not found:
            continue

        combo, score, reasons = found
        gid = f"G-{txn.id}"
        for i in combo:
            results[i] = MatchResult(i, txn.id, round(score, 4), list(reasons),
                                     group_id=gid, group_size=len(combo))
            free.remove(i)
        taken_txns.add(txn.id)


def _match_daily(ebarimt_items: list[dict], results: list[MatchResult],
                 bank_txns: list, taken_txns: set[str]) -> None:
    """Нэг өдрийн бүх баримтын нийлбэрийг нэг гүйлгээтэй тулгана.

    ПОС болон бэлэн мөнгөний борлуулалт банкинд баримт бүрээр ордоггүй —
    өдрийн эцэст нэгдсэн дүнгээр (эсвэл маргааш нь) буудаг. Нэг бүрчлэн
    тулгах, 2–4-өөр бүлэглэх аль нь ч үүнийг барьж чадахгүй тул өдөрт 20–30
    баримт байхад бүгд «банкны мөнгө ирээгүй» болж харагддаг.

    Хоёр төрлийн багц оролдоно: тухайн өдрийн БҮХ баримт, мөн ПОС терминал
    тус бүрээр (олон терминалтай бол тус тусдаа суудаг).
    """
    free = [i for i, r in enumerate(results) if r.txn_id is None]
    if len(free) < DAILY_MIN_ITEMS:
        return

    # (чиглэл, өдөр, терминал) → баримтын индексүүд. терминал=None бол өдрийн бүх дүн.
    buckets: dict[tuple, list[int]] = {}
    for i in free:
        item = ebarimt_items[i]
        day = parse_day(item.get("date"))
        if day is None:
            continue
        direction = item.get("direction")
        buckets.setdefault((direction, day, None), []).append(i)
        # Тухайн өдөр ААН-ы шилжүүлгийн баримт хамт байвал өдрийн бүтэн
        # нийлбэр таарахгүй тул тайлан тус бүрээр ч тусад нь оролдоно
        ds = item.get("dataset")
        if ds:
            buckets.setdefault((direction, day, ("ds", ds)), []).append(i)
        pos = (item.get("pos_no") or "").strip()
        if pos:
            buckets.setdefault((direction, day, ("pos", pos)), []).append(i)

    # Том багцыг түрүүлж — өдрийн бүтэн нийлбэр нь дэд багцаас илүү хүчтэй дохио
    ordered = sorted((k for k, v in buckets.items() if len(v) >= DAILY_MIN_ITEMS),
                     key=lambda k: (-len(buckets[k]), k[1]))
    matched_idx: set[int] = set()

    for key in ordered:
        direction, day, pos = key
        members = [i for i in buckets[key] if i not in matched_idx]
        if len(members) < DAILY_MIN_ITEMS:
            continue
        total = sum(int(ebarimt_items[i]["total_minor"]) for i in members)

        best = None
        for txn in bank_txns:
            if txn.id in taken_txns:
                continue
            if direction is not None:
                have = txn_direction(txn)
                if have is not None and have != direction:
                    continue
            a_s = amount_score(total, int(txn.amount_minor))
            if a_s == 0.0:
                continue
            d_s = date_score(day, parse_day(getattr(txn, "posted_at", None)))
            if d_s == 0.0:
                continue
            p_s = max(party_score(ebarimt_items[i].get("party"),
                                  [getattr(txn, "counterparty_name", None),
                                   getattr(txn, "description_raw", None)])
                      for i in members)
            score = (W_AMOUNT * a_s + W_DATE * d_s + W_PARTY * p_s) * DAILY_SCORE_PENALTY
            if score >= MIN_CANDIDATE_SCORE and (best is None or score > best[0]):
                best = (score, txn, total)

        if best is None:
            continue

        score, txn, total = best
        diff = abs(total - int(txn.amount_minor))
        where = f" ({pos[1]} ПОС)" if pos and pos[0] == "pos" else ""
        reasons = [f"{day.isoformat()}-ний {len(members)} баримтын өдрийн "
                   f"нийлбэр{where}"]
        reasons.append("нийлбэр яг тэнцүү" if diff == 0
                       else f"нийлбэрийн зөрүү {diff / 100:.2f}₮")
        gid = f"D-{txn.id}"
        for i in members:
            results[i] = MatchResult(i, txn.id, round(score, 4), list(reasons),
                                     group_id=gid, group_size=len(members))
            matched_idx.add(i)
        taken_txns.add(txn.id)


def match(ebarimt_items: list[dict], bank_txns: list,
          allow_groups: bool = True, allow_daily: bool = True) -> list[MatchResult]:
    """Падаан бүрд хамгийн тохирох гүйлгээг онооны дарааллаар оноино.

    Нэг гүйлгээ зөвхөн нэг падаанд (эсвэл нэг бүлэг падаанд) оногдоно.
    Хамгийн итгэлтэй хослолыг түрүүлж баталгаажуулснаар ойролцоо дүнтэй хэд
    хэдэн падаан хоорондоо солигдох эрсдэлийг багасгана.
    """
    candidates: list[tuple[float, int, str, list[str]]] = []

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

    results = [taken_items.get(i, MatchResult(i, None, 0.0, []))
               for i in range(len(ebarimt_items))]

    # Өдрийн нийлбэр нь 2–4-ийн хослолоос хүчтэй дохио тул түрүүлж явна —
    # эс тэгвэл санамсаргүй гурвал тэр гүйлгээг эзэлж авна.
    if allow_daily:
        _match_daily(ebarimt_items, results, bank_txns, taken_txns)

    if allow_groups:
        _match_groups(ebarimt_items, results, bank_txns, taken_txns)

    return results
