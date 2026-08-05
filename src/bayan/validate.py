"""Шат 4 — Validation Gate (§4.4). Цэвэр детерминистик, LLM байхгүй.

V1  Үлдэгдлийн гинж — мөр бүр дээр
V2  Нээлт + Σкредит − Σдебит = Хаалт
V3  Мөрийн тоо (metadata байвал)
V4  Огноо мужид, үл буурах дараалалтай
V5  Өмнөх хуулгын хаалт = энэ хуулгын нээлт (өгөгдвөл)
V6  canonical_hash давхардалгүй
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .normalize import CanonTxn


@dataclass
class GateIssue:
    check: str          # V1..V6
    row_index: int | None
    detail: str


@dataclass
class GateResult:
    ok: bool
    issues: list[GateIssue] = field(default_factory=list)
    # Хүнд ойлгомжтой онош — тоон зөрүүг биш, ШАЛТГААНЫГ хэлнэ
    diagnosis: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "diagnosis": list(self.diagnosis),
            "issues": [
                {"check": i.check, "row": i.row_index, "detail": i.detail}
                for i in self.issues
            ],
        }


def run_gate(
    txns: list[CanonTxn],
    *,
    opening_minor: int | None,
    closing_minor: int | None,
    declared_row_count: int | None = None,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
    prev_closing_minor: int | None = None,
    expected_account: str | None = None,
) -> GateResult:
    issues: list[GateIssue] = []

    def signed(t: CanonTxn) -> int:
        return t.amount_minor if t.direction == "credit" else -t.amount_minor

    has_balance = all(t.balance_after_minor is not None for t in txns) and bool(txns)

    # V1 — үлдэгдлийн гинж
    if has_balance and opening_minor is not None:
        prev = opening_minor
        for t in txns:
            expected = prev + signed(t)
            if t.balance_after_minor != expected:
                issues.append(GateIssue(
                    "V1", t.row_index,
                    f"Гинж тасарлаа: хүлээгдсэн {expected}, файлд {t.balance_after_minor}",
                ))
                # гинжийг файлын утгаар үргэлжлүүлж дараагийн тасралтыг ч олно
            prev = t.balance_after_minor if t.balance_after_minor is not None else expected

    # V2 — нийлбэрийн тулгалт
    if opening_minor is not None and closing_minor is not None:
        total = sum(signed(t) for t in txns)
        if opening_minor + total != closing_minor:
            issues.append(GateIssue(
                "V2", None,
                f"Нээлт {opening_minor} + гүйлгээ {total} ≠ хаалт {closing_minor}",
            ))
    elif not has_balance:
        issues.append(GateIssue(
            "V2", None,
            "Нээлт/хаалтын үлдэгдэл мета-блокоос олдсонгүй — reduced assurance",
        ))

    # V3 — мөрийн тоо
    if declared_row_count is not None and declared_row_count != len(txns):
        issues.append(GateIssue(
            "V3", None,
            f"Хуулгад {declared_row_count} гүйлгээ гэж заасан, задлагдсан {len(txns)}",
        ))

    # V4 — огнооны муж, дараалал.
    # Дараалал ӨДРИЙН түвшинд шалгагдана: банкууд нэг өдрийн доторх гүйлгээг
    # цагийн бус боловсруулалтын дарааллаар бичдэг (ХХБ-ны бодит хуулгаар
    # баталгаажсан). Мөрийн дарааллын жинхэнэ бүрэн бүтэн байдлыг V1 хангана.
    prev_day = None
    for t in txns:
        if period_from and t.posted_at < period_from:
            issues.append(GateIssue("V4", t.row_index, f"Огноо мужаас өмнө: {t.posted_at}"))
        if period_to and t.posted_at > period_to:
            issues.append(GateIssue("V4", t.row_index, f"Огноо мужаас хойш: {t.posted_at}"))
        day = t.posted_at.date()
        if prev_day and day < prev_day:
            issues.append(GateIssue("V4", t.row_index, "Огноо буурсан дараалалтай (өдрөөр)"))
        prev_day = day

    # V5 — хуулга хоорондын залгамж
    if prev_closing_minor is not None and opening_minor is not None:
        if prev_closing_minor != opening_minor:
            issues.append(GateIssue(
                "V5", None,
                f"Өмнөх хуулгын хаалт {prev_closing_minor} ≠ энэ хуулгын нээлт {opening_minor}",
            ))

    # V6 — давхардал
    seen: set[str] = set()
    for t in txns:
        if t.canonical_hash in seen:
            issues.append(GateIssue("V6", t.row_index, "Давхардсан гүйлгээ (hash)"))
        seen.add(t.canonical_hash)

    return GateResult(
        ok=not issues, issues=issues,
        diagnosis=diagnose(txns, issues, opening_minor=opening_minor,
                           expected_account=expected_account),
    )


# ---------------------------------------------------------------- Онош

# Гүйлгээний утганд хүлээн авагч дансыг сумны ард бичдэг:
#   "05-(5720669089-ИЛГЭЭГЧ)-> 04-(413108778-ХҮЛЭЭН АВАГЧ)"
# Сумны ӨМНӨХ дугаар нь харилцагчийн данс/регистр тул хуулгын эзнийг заахгүй.
_RECEIVER_RE = re.compile(r"->\s*\d{2}-\((\d{8,12})-")


def _split_blocks(txns: list[CanonTxn], opening_minor: int | None) -> list[list[CanonTxn]]:
    """Үлдэгдлийн гинж тасарсан цэгээр хуулгыг блок болгон хуваана."""
    blocks: list[list[CanonTxn]] = []
    cur: list[CanonTxn] = []
    prev = opening_minor
    for t in txns:
        if t.balance_after_minor is None:
            cur.append(t)
            continue
        delta = t.amount_minor if t.direction == "credit" else -t.amount_minor
        if cur and prev is not None and t.balance_after_minor != prev + delta:
            blocks.append(cur)
            cur = []
        cur.append(t)
        prev = t.balance_after_minor
    if cur:
        blocks.append(cur)
    return blocks


def _receiver_accounts(txns: list[CanonTxn]) -> list[str]:
    """Хуулгад тохиолдсон хүлээн авагч дансууд, олонтой нь эхэнд.

    Ганц дансны хуулгад энэ жагсаалт нэг элементтэй байдаг. Хэд хэдэн дансны
    хуулга нийлүүлэгдсэн үед л олон элемент гарна. Санамсаргүй таарлыг
    шүүхийн тулд хангалттай олон дахин давтагдсаныг нь л авна."""
    counts: dict[str, int] = {}
    for t in txns:
        for code in set(_RECEIVER_RE.findall(t.description_raw or "")):
            counts[code] = counts.get(code, 0) + 1
    floor = max(3, len(txns) // 20)
    kept = [(c, n) for c, n in counts.items() if n >= floor]
    return [c for c, _ in sorted(kept, key=lambda kv: -kv[1])]


def diagnose(txns: list[CanonTxn], issues: list[GateIssue], *,
             opening_minor: int | None,
             expected_account: str | None = None) -> list[str]:
    """Gate уналтын шалтгааныг хүнд ойлгомжтой байдлаар тодорхойлно.

    Тоон зөрүү (V1/V2)-г жагсаах нь хэрэглэгчид юу хийхийг хэлж өгөхгүй.
    Хамгийн түгээмэл хоёр шалтгааныг тусад нь таньж, зөвлөгөө өгнө."""
    out: list[str] = []
    if not txns or not any(i.check in ("V1", "V2") for i in issues):
        return out

    blocks = _split_blocks(txns, opening_minor)

    # 1) Нэг файлд хэд хэдэн дансны хуулга нийлүүлэгдсэн эсэх
    if len(blocks) > 1:
        accounts = _receiver_accounts(txns)
        if len(accounts) > 1:
            declared = f"зөвхөн {expected_account} данс" if expected_account else "нэг данс"
            out.append(
                f"Энэ файлд {len(accounts)} өөр дансны гүйлгээ холилдсон байна "
                f"({', '.join(accounts)}), харин толгой хэсэгт {declared} "
                f"бичигдсэн. Данс тус бүрийн хуулгыг ТУСАД нь татаж оруулна уу."
            )
        else:
            out.append(
                f"Үлдэгдлийн гинж {len(blocks) - 1} газар тасарсан тул хуулга "
                f"{len(blocks)} тусдаа хэсгээс бүрдэж байна. Хуулгаа нэг "
                f"тасралтгүй хугацаагаар, шүүлтгүйгээр дахин татаж үзнэ үү."
            )

    # 2) Зөвхөн орлого (эсвэл зөвхөн зарлага) — шүүлттэй татсан хуулга
    directions = {t.direction for t in txns}
    if len(directions) == 1 and any(i.check in ("V1", "V2") for i in issues):
        only = "орлогын" if "credit" in directions else "зарлагын"
        out.append(
            f"Бүх {len(txns)} гүйлгээ зөвхөн {only} байна — эсрэг талын "
            f"гүйлгээ дутуу тул үлдэгдэл нийцэхгүй. Хуулгаа гүйлгээний "
            f"төрлөөр ШҮҮЛТГҮЙГЭЭР дахин татаж оруулна уу."
        )

    return out
