"""§5.4 L4 — Гажилт илрүүлэлт. Хүн шалгахгүй бол үлддэг алдааг барина.

Аудитын түүхээс гарсан сургамж: «зээлийн хүү → 7101 Цалин», «түлш → 2101
Түүхий эд» гэх мэт дансны буруу хослол тестээр баригддаггүй, тайлан ч
тэнцсэн хэвээр байдаг — зөвхөн хүн нүдээрээ хартал үлддэг. Энэ модуль
тэдгээрийг өдөр бүр хайна.

Илрүүлэгч бүр **нотолгоотой** — ямар бичилт, ямар дүн, яагаад сэжигтэй
болохыг зааж өгнө. Гажилт нь буруутгал БИШ, хүний хяналтад тавих дохио:
шийдвэрийг нябо гаргана.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

#: Хүндийн зэрэг — эрэмбэлэхэд ашиглана
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

#: Давхар төлөлт хэдэн хоногийн дотор давтагдвал сэжиглэх вэ
DUPLICATE_WINDOW_DAYS = 7
#: Цалин өмнөх дунджаас хэдэн хувиар хазайвал дохиолох вэ
SALARY_JUMP_PCT = 30.0
#: НӨАТ-ын хувь
VAT_PCT = 10.0


@dataclass
class Anomaly:
    code: str
    severity: str
    title: str
    detail: str
    amount_minor: int = 0
    occurred_on: date | None = None
    refs: list[str] = field(default_factory=list)      # journal_entry / invoice id

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "title": self.title, "detail": self.detail,
                "amount_minor": self.amount_minor,
                "occurred_on": self.occurred_on.isoformat() if self.occurred_on else None,
                "refs": self.refs}


def _fmt(minor: int) -> str:
    return f"{minor / 100:,.0f}₮"


# ------------------------------------------------------- давхар төлөлт

def duplicate_payments(session, company_id: str, d_from: date,
                       d_to: date) -> list[Anomaly]:
    """Ижил данс, ижил дүн, ойрхон огноотой бичилт — давхар төлөлтийн шинж."""
    from sqlalchemy import select

    from .models import Account, EntryStatus, JournalEntry, JournalLine

    rows = session.execute(
        select(JournalEntry.id, JournalEntry.entry_date, JournalEntry.memo,
               Account.code, Account.name, JournalLine.credit_minor,
               JournalLine.debit_minor)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalEntry.company_id == company_id,
               JournalEntry.status != EntryStatus.draft,
               JournalEntry.entry_date >= d_from,
               JournalEntry.entry_date <= d_to)).all()

    # Мөнгө ГАРСАН бичилтүүд: 10/11 дансны кредит
    payments = [
        {"entry_id": r[0], "date": r[1], "memo": r[2] or "", "amount": r[5]}
        for r in rows if r[3].startswith(("10", "11")) and r[5]
    ]

    found: list[Anomaly] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, a in enumerate(payments):
        for b in payments[i + 1:]:
            if a["amount"] != b["amount"] or a["entry_id"] == b["entry_id"]:
                continue
            if abs((a["date"] - b["date"]).days) > DUPLICATE_WINDOW_DAYS:
                continue
            key = tuple(sorted((a["entry_id"], b["entry_id"])))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            found.append(Anomaly(
                code="DUPLICATE_PAYMENT", severity="high",
                title="Давхар төлөлтийн сэжиг",
                detail=(f"{_fmt(a['amount'])} дүнтэй хоёр төлөлт "
                        f"{a['date']:%Y-%m-%d} ба {b['date']:%Y-%m-%d}-нд "
                        f"хийгдсэн. «{a['memo'][:40]}» / «{b['memo'][:40]}»"),
                amount_minor=a["amount"], occurred_on=b["date"],
                refs=[a["entry_id"], b["entry_id"]]))
    return found


# ------------------------------------------------- дансны буруу хослол

def misposted_accounts(session, company_id: str, d_from: date,
                       d_to: date) -> list[Anomaly]:
    """Гүйлгээний утга нябо-гийн дүрмээр өөр данс заасан байх тохиолдол.

    Нябо өөрөө «түлш → 7110» гэсэн дүрэм тавьсан атлаа бичилт нь 2101 руу
    орсон бол хүний алдаа эсвэл ангилалт буруу давсан гэсэн үг."""
    from sqlalchemy import select

    from .models import (Account, ClassifierRule, EntryStatus, JournalEntry,
                         JournalLine, SourceType)

    # ЭРЭМБЭЭР — classify.apply_rules-тэй яг ижил сонголт. Дурын таарсан
    # дүрмээр зэмлэвэл өгөгдмөл 68 дүрэм бараг бүх гүйлгээнд таарч, нябо-г
    # худал дохиогоор дүжирнэ. Ангилагч ЮУ СОНГОХ БАЙСАН — түүнтэй л
    # харьцуулах ёстой.
    rules = session.scalars(select(ClassifierRule).where(
        ClassifierRule.company_id == company_id,
        ClassifierRule.active == True)  # noqa: E712
        .order_by(ClassifierRule.priority)).all()
    if not rules:
        return []

    # ЗӨВХӨН банкны хуулгаас үүссэн бичилт. Ангиллын дүрэм нь банкны
    # гүйлгээний утгад зориулагдсан: «цалин» → 3102 (өглөг барагдуулах)
    # гэсэн дүрэм нь цалингийн ЖУРНАЛД буруу — тэнд 7101 зардал дебит
    # хийгддэг. Системийн үүсгэсэн бичилтэд дүрмийг тулгавал зөв
    # бичилтүүдийг зэмлэж, нябо-г худал дохиогоор дүжирнэ.
    rows = session.execute(
        select(JournalEntry.id, JournalEntry.entry_date, JournalEntry.memo,
               Account.code, Account.name, JournalLine.debit_minor,
               JournalLine.description)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalEntry.company_id == company_id,
               JournalEntry.status != EntryStatus.draft,
               JournalEntry.source_type == SourceType.bank_txn,
               JournalEntry.entry_date >= d_from,
               JournalEntry.entry_date <= d_to)).all()

    found: list[Anomaly] = []
    for entry_id, entry_date, memo, code, name, debit, line_desc in rows:
        if not debit or code.startswith(("10", "11")):
            continue
        text = f"{memo or ''} {line_desc or ''}".lower()

        # Ангилагчийн сонгох байсан дүрэм = эрэмбээр эхний таарсан нь
        chosen = next((r for r in rules if r.keyword.lower() in text), None)
        if chosen is None or chosen.account_code == code:
            continue

        found.append(Anomaly(
            code="MISPOSTED_ACCOUNT", severity="medium",
            title="Дансны хослол дүрэмтэй зөрчилдөж байна",
            detail=(f"«{chosen.keyword}» гэсэн утгатай {_fmt(debit)} гүйлгээ "
                    f"{code} {name} руу бичигдсэн боловч дүрмээр "
                    f"{chosen.account_code} байх ёстой."),
            amount_minor=debit, occurred_on=entry_date, refs=[entry_id]))
    return found


# ---------------------------------------------------------- цалингийн гажилт

def payroll_anomalies(session, company_id: str, year: int,
                      month: int) -> list[Anomaly]:
    """M4.6 — өмнөх саруудтай харьцуулж сэжигтэй зөрүү."""
    from sqlalchemy import select

    from .salary import Employee, PayrollLine

    lines = session.scalars(select(PayrollLine).where(
        PayrollLine.company_id == company_id)).all()
    current = [l for l in lines if (l.year, l.month) == (year, month)]
    if not current:
        return []

    employees = {e.id: e for e in session.scalars(select(Employee).where(
        Employee.company_id == company_id))}

    found: list[Anomaly] = []
    seen: set[str] = set()

    for line in current:
        emp = employees.get(line.employee_id)
        who = f"{emp.last_name} {emp.first_name}" if emp else line.employee_id

        # Нэг ажилтанд нэг сард хоёр мөр — давхар олголт
        if line.employee_id in seen:
            found.append(Anomaly(
                code="PAYROLL_DUPLICATE", severity="high",
                title="Нэг ажилтанд давхар цалин",
                detail=f"{who} — {year}-{month:02d} сард хоёр удаа бодогдсон.",
                amount_minor=line.gross_minor, refs=[line.id]))
            continue
        seen.add(line.employee_id)

        history = [l.gross_minor for l in lines
                   if l.employee_id == line.employee_id
                   and (l.year, l.month) < (year, month)]
        if not history:
            found.append(Anomaly(
                code="PAYROLL_NEW", severity="low",
                title="Шинэ ажилтанд эхний цалин",
                detail=f"{who} — өмнөх түүхгүй, {_fmt(line.gross_minor)} бодогдсон.",
                amount_minor=line.gross_minor, refs=[line.id]))
            continue

        avg = sum(history) / len(history)
        if avg <= 0:
            continue
        change = (line.gross_minor - avg) / avg * 100
        if abs(change) >= SALARY_JUMP_PCT:
            found.append(Anomaly(
                code="PAYROLL_JUMP",
                severity="high" if abs(change) >= 2 * SALARY_JUMP_PCT else "medium",
                title="Цалин огцом өөрчлөгдсөн",
                detail=(f"{who} — {_fmt(line.gross_minor)}, өмнөх {len(history)} "
                        f"сарын дундаж {_fmt(int(avg))} ({change:+.0f}%)."),
                amount_minor=line.gross_minor, refs=[line.id]))
    return found


# -------------------------------------------------------------- НӨАТ-ын зөрүү

def vat_anomalies(session, company_id: str, d_from: date,
                  d_to: date) -> list[Anomaly]:
    """Нэхэмжлэхийн НӨАТ цэвэр дүнгийн 10%-иас хазайсан эсэх."""
    from sqlalchemy import select

    from .partners import Invoice

    found: list[Anomaly] = []
    for inv in session.scalars(select(Invoice).where(
            Invoice.company_id == company_id)):
        if not (d_from <= inv.issue_date <= d_to) or not inv.vat_minor:
            continue
        expected = int(round(inv.net_minor * VAT_PCT / 100))
        if abs(inv.vat_minor - expected) > 100:        # 1₮-оос дээш зөрүү
            found.append(Anomaly(
                code="VAT_MISMATCH", severity="medium",
                title="НӨАТ-ын дүн таарахгүй байна",
                detail=(f"№{inv.number} — цэвэр {_fmt(inv.net_minor)}-ийн 10% нь "
                        f"{_fmt(expected)} байх ёстой ч {_fmt(inv.vat_minor)} "
                        f"бичигдсэн."),
                amount_minor=inv.vat_minor - expected,
                occurred_on=inv.issue_date, refs=[inv.id]))
    return found


# ---------------------------------------------------- ирээдүйн огноотой бичилт

def future_entries(session, company_id: str, as_of: date) -> list[Anomaly]:
    """Ирээдүйн огноотой батлагдсан бичилт — бараг үргэлж бичих алдаа."""
    from sqlalchemy import select

    from .models import EntryStatus, JournalEntry

    found = []
    for e in session.scalars(select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.status != EntryStatus.draft,
            JournalEntry.entry_date > as_of)):
        found.append(Anomaly(
            code="FUTURE_ENTRY", severity="medium",
            title="Ирээдүйн огноотой бичилт",
            detail=(f"{e.entry_date:%Y-%m-%d}-ний огноотой бичилт өнөөдрөөс "
                    f"хойш байна: «{(e.memo or '')[:60]}»"),
            occurred_on=e.entry_date, refs=[e.id]))
    return found


# ------------------------------------------------------------------ багц

def scan(session, company_id: str, year: int, month: int,
         as_of: date | None = None) -> dict:
    """Сарын багц шалгалт — бүх илрүүлэгчийг ажиллуулж нэгтгэнэ."""
    import calendar

    as_of = as_of or date.today()
    d_from = date(year, month, 1)
    d_to = date(year, month, calendar.monthrange(year, month)[1])

    found: list[Anomaly] = []
    found += duplicate_payments(session, company_id, d_from, d_to)
    found += misposted_accounts(session, company_id, d_from, d_to)
    found += payroll_anomalies(session, company_id, year, month)
    found += vat_anomalies(session, company_id, d_from, d_to)
    found += future_entries(session, company_id, as_of)

    found.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 9),
                              -abs(a.amount_minor)))

    by_severity = {s: sum(1 for a in found if a.severity == s)
                   for s in ("high", "medium", "low")}
    return {
        "period": f"{year}-{month:02d}",
        "total": len(found),
        "by_severity": by_severity,
        "anomalies": [a.to_dict() for a in found],
    }
