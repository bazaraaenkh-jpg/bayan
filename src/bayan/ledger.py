"""GL цөм — G1-G7 инвариантуудын сервисийн давхаргын хэрэгжилт (§5.2).

Бүх бичилт зөвхөн энэ модулиар дамжина. AI/pipeline журналд шууд бичдэггүй.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Account, AuditLog, EntryStatus, JournalEntry, JournalLine,
    PeriodLock, SourceType,
)


class LedgerError(Exception):
    pass


class UnbalancedEntryError(LedgerError):
    """G1: Σдебит ≠ Σкредит."""


class PeriodLockedError(LedgerError):
    """G3: түгжсэн сард бичих оролдлого."""


class ImmutableEntryError(LedgerError):
    """G2: posted бичилтийг өөрчлөх оролдлого."""


class NotPostableError(LedgerError):
    """G4: бүлэг данс руу шууд бичих оролдлого."""


@dataclass
class LineInput:
    account_code: str
    debit_minor: int = 0
    credit_minor: int = 0
    description: str | None = None
    counterparty_id: str | None = None
    cost_center_id: str | None = None
    currency: str | None = None
    amount_currency: float | None = None


def _get_account(session: Session, company_id: str, code: str) -> Account:
    acc = session.scalar(
        select(Account).where(Account.company_id == company_id, Account.code == code)
    )
    if acc is None:
        raise LedgerError(f"Данс олдсонгүй: {code}")
    return acc


def is_period_locked(session: Session, company_id: str, d: date) -> bool:
    return session.scalar(
        select(PeriodLock).where(
            PeriodLock.company_id == company_id,
            PeriodLock.year == d.year,
            PeriodLock.month == d.month,
        )
    ) is not None


def validate_period_for_lock(session: Session, company_id: str, year: int, month: int) -> None:
    import calendar
    from .models import ClassificationSuggestion, BankTxn, Statement, BankAccount, Account, JournalLine, JournalEntry
    from datetime import date
    
    last_day = calendar.monthrange(year, month)[1]
    date_from = date(year, month, 1)
    date_to = date(year, month, last_day)
    
    # 1. Шалгуур: Баланс тэнцсэн байх ёстой (Assets == Liabilities + Equity)
    tb = trial_balance(session, company_id, date_to=date_to)
    assets = 0
    liabilities = 0
    equity = 0
    revenue = 0
    cogs = 0
    expenses = 0
    
    for row in tb:
        code = row["code"]
        bal = row["balance_minor"]
        if code.startswith("1") or code.startswith("2"):
            assets += bal
        elif code.startswith("3"):
            liabilities += bal
        elif code.startswith("4"):
            equity += bal
            
    tb_period = trial_balance(session, company_id, date_from=date_from, date_to=date_to)
    for row in tb_period:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        if code.startswith("5"):
            revenue += (cr - dr)
        elif code.startswith("6"):
            cogs += (dr - cr)
        elif code.startswith("7"):
            expenses += (dr - cr)
            
    net_income = revenue - cogs - expenses
    total_liabilities_equity = liabilities + equity + net_income
    
    if abs(assets - total_liabilities_equity) > 100:  # 1 MNT threshold (100 minor units)
        raise LedgerError(
            f"Баланс тэнцээгүй байна: Нийт Хөрөнгө ({assets/100:,.2f}₮) ≠ "
            f"Нийт Өр төлбөр + Өмч ({total_liabilities_equity/100:,.2f}₮). Зөрүү: {abs(assets - total_liabilities_equity)/100:,.2f}₮"
        )

    # 2. Шалгуур: Батлагдаагүй draft банкны гүйлгээ үлдээгүй байх
    pending_count = session.scalar(
        select(func.count(ClassificationSuggestion.id))
        .join(BankTxn, BankTxn.id == ClassificationSuggestion.bank_txn_id)
        .where(BankTxn.company_id == company_id,
               ClassificationSuggestion.status == "pending",
               BankTxn.posted_at >= date_from,
               BankTxn.posted_at <= date_to)
    )
    if pending_count > 0:
        raise LedgerError(
            f"Тухайн сард батлагдаагүй {pending_count} банкны гүйлгээний ангилал (draft/pending) байна. "
            f"Сар хаахаас өмнө бүх гүйлгээг баталгаажуулна уу."
        )

    # 3. Шалгуур: Банкны хуулганы үлдэгдэл GL дансны үлдэгдэлтэй таарч байх
    statements = session.scalars(
        select(Statement)
        .where(Statement.company_id == company_id,
               Statement.period_to >= date_from,
               Statement.period_to <= date_to)
    ).all()
    
    for stmt in statements:
        if stmt.bank_account_id:
            bank_acc = session.get(BankAccount, stmt.bank_account_id)
            if bank_acc and bank_acc.gl_account_id:
                gl_acc = session.get(Account, bank_acc.gl_account_id)
                if gl_acc:
                    dr_sum = session.scalar(
                        select(func.coalesce(func.sum(JournalLine.debit_minor), 0))
                        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                        .where(JournalLine.account_id == gl_acc.id,
                               JournalEntry.entry_date <= date_to,
                               JournalEntry.status != EntryStatus.draft)
                    )
                    cr_sum = session.scalar(
                        select(func.coalesce(func.sum(JournalLine.credit_minor), 0))
                        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                        .where(JournalLine.account_id == gl_acc.id,
                               JournalEntry.entry_date <= date_to,
                               JournalEntry.status != EntryStatus.draft)
                    )
                    gl_bal = dr_sum - cr_sum if gl_acc.normal_side.value == "debit" else cr_sum - dr_sum
                    if gl_bal != stmt.closing_minor:
                        raise LedgerError(
                            f"Банкны тулгалтын зөрүүтэй: Дансны дугаар {bank_acc.account_no}-ны хуулганы хаалтын үлдэгдэл ({stmt.closing_minor/100:,.2f}₮) "
                            f"нь GL дансны үлдэгдэлтэй ({gl_bal/100:,.2f}₮) тохирохгүй байна. Зөрүүг засна уу."
                        )

    # 4. Шалгуур: Журналын бичилтийн дугаарын тасралтгүй байдал (Sequence Integrity)
    nos = session.scalars(
        select(JournalEntry.entry_no)
        .where(JournalEntry.company_id == company_id,
               JournalEntry.entry_date >= date_from,
               JournalEntry.entry_date <= date_to)
        .order_by(JournalEntry.entry_no)
    ).all()
    if nos:
        for idx in range(len(nos) - 1):
            if nos[idx + 1] != nos[idx] + 1:
                raise LedgerError(
                    f"Журналын бичилтийн дугаарын дараалал тасалдсан байна (Sequence Gap): "
                    f"{nos[idx]} болон {nos[idx + 1]} дугааруудын хооронд зөрүү байна. "
                    f"Аудитын шаардлагын дагуу дугаарлалтыг дахин цэгцэлнэ үү."
                )


def lock_period(session: Session, company_id: str, year: int, month: int,
                actor_id: str | None = None, cpa_license_no: str | None = None) -> None:
    # Сарын хаалтын шалгалтуудыг гүйцэтгэх
    validate_period_for_lock(session, company_id, year, month)
    
    if not session.scalar(select(PeriodLock).where(
        PeriodLock.company_id == company_id,
        PeriodLock.year == year, PeriodLock.month == month,
    )):
        session.add(PeriodLock(company_id=company_id, year=year, month=month,
                               locked_by=actor_id, cpa_license_no=cpa_license_no))
        session.add(AuditLog(company_id=company_id, actor_id=actor_id,
                             action="lock_period", entity="period",
                             entity_id=f"{year}-{month:02d}",
                             detail={"cpa_license_no": cpa_license_no}))
        session.flush()


def post_entry(
    session: Session,
    company_id: str,
    entry_date: date,
    lines: list[LineInput],
    source_type: SourceType = SourceType.manual,
    source_id: str | None = None,
    memo: str | None = None,
    actor_id: str | None = None,
    reversal_of: str | None = None,
    is_system_generated: bool = False,
    is_realized: bool = True,
    status: EntryStatus = EntryStatus.posted,
) -> JournalEntry:
    """Бичилт хийх цорын ганц зам. Бүх инвариант энд шалгагдана."""
    if not lines:
        raise LedgerError("Мөргүй бичилт")

    # User-level transaction lock check (Bayan AI rule)
    if actor_id:
        from .auth import User
        u = session.get(User, actor_id)
        if u and getattr(u, "transaction_lock_days", None) is not None:
            from datetime import date
            today = date.today()
            diff = (today - entry_date).days
            if diff > u.transaction_lock_days:
                raise LedgerError(
                    f"Та зөвхөн {u.transaction_lock_days} хоногийн өмнөх гүйлгээг засварлах/оруулах эрхтэй байна. "
                    f"Гүйлгээний огноо: {entry_date}, Хязгаар хоног: {u.transaction_lock_days} хоног. Админд хандана уу."
                )

    # G3 — сарын түгжээ
    if is_period_locked(session, company_id, entry_date):
        raise PeriodLockedError(
            f"{entry_date.year}-{entry_date.month:02d} сар түгжээтэй байна"
        )

    # G1 — тэнцвэр
    total_debit = sum(l.debit_minor for l in lines)
    total_credit = sum(l.credit_minor for l in lines)
    if total_debit != total_credit or total_debit == 0:
        raise UnbalancedEntryError(
            f"Дебит {total_debit} ≠ Кредит {total_credit}"
        )

    next_no = (session.scalar(
        select(func.max(JournalEntry.entry_no)).where(
            JournalEntry.company_id == company_id)
    ) or 0) + 1

    entry = JournalEntry(
        company_id=company_id, entry_no=next_no, entry_date=entry_date,
        memo=memo, source_type=source_type, source_id=source_id,
        status=status, created_by=actor_id, reversal_of=reversal_of,
        is_system_generated=is_system_generated, is_realized=is_realized,
    )

    for i, l in enumerate(lines, start=1):
        acc = _get_account(session, company_id, l.account_code)
        # G4 — зөвхөн postable данс
        if not acc.is_postable:
            raise NotPostableError(f"{acc.code} {acc.name} — бүлэг данс руу шууд бичихгүй")
        if (l.debit_minor > 0) == (l.credit_minor > 0):
            raise LedgerError(f"Мөр {i}: яг нэг талдаа дүнтэй байх ёстой")
        entry.lines.append(JournalLine(
            line_no=i, account_id=acc.id,
            debit_minor=l.debit_minor, credit_minor=l.credit_minor,
            description=l.description, counterparty_id=l.counterparty_id,
            cost_center_id=l.cost_center_id,
            currency=l.currency, amount_currency=l.amount_currency,
        ))

    session.add(entry)
    session.flush()   # entry.id үүснэ
    session.add(AuditLog(company_id=company_id, actor_id=actor_id,
                         action="post_entry", entity="journal_entry",
                         entity_id=entry.id,
                         detail={"entry_no": next_no, "debit": total_debit}))
    session.flush()
    return entry


def reverse_entry(session: Session, entry_id: str,
                  actor_id: str | None = None,
                  reversal_date: date | None = None) -> JournalEntry:
    """G2: posted бичилт засварлагдахгүй — зөвхөн reversal + шинэ бичилт."""
    original = session.get(JournalEntry, entry_id)
    if original is None:
        raise LedgerError("Бичилт олдсонгүй")
    if original.status == EntryStatus.reversed:
        raise ImmutableEntryError("Аль хэдийн reversed")

    acc_codes = {a.id: a.code for a in session.scalars(
        select(Account).where(Account.company_id == original.company_id))}

    rev_lines = [
        LineInput(
            account_code=acc_codes[l.account_id],
            debit_minor=l.credit_minor,   # эсрэгээр
            credit_minor=l.debit_minor,
            description=f"Reversal: {l.description or ''}".strip(),
        )
        for l in original.lines
    ]
    rev = post_entry(
        session, original.company_id,
        reversal_date or original.entry_date, rev_lines,
        source_type=SourceType.reversal, source_id=original.id,
        memo=f"Буцаалт — бичилт №{original.entry_no}",
        actor_id=actor_id, reversal_of=original.id,
    )
    original.status = EntryStatus.reversed
    session.flush()
    return rev


def trial_balance(
    session: Session, company_id: str,
    date_from: date | None = None, date_to: date | None = None,
) -> list[dict]:
    """Гүйлгээ баланс: данс бүрээр Σдебит, Σкредит, үлдэгдэл."""
    q = (
        select(
            Account.code, Account.name, Account.normal_side,
            func.coalesce(func.sum(JournalLine.debit_minor), 0),
            func.coalesce(func.sum(JournalLine.credit_minor), 0),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        # reversed бичилт ХАСАГДАХГҮЙ: эх + буцаалт хоёулаа дэвтэрт үлдэж
        # хоорондоо цуцлагдана (immutable ledger). Зөвхөн draft орохгүй.
        .where(Account.company_id == company_id,
               JournalEntry.status != EntryStatus.draft)
        .group_by(Account.code, Account.name, Account.normal_side)
        .order_by(Account.code)
    )
    if date_from:
        q = q.where(JournalEntry.entry_date >= date_from)
    if date_to:
        q = q.where(JournalEntry.entry_date <= date_to)

    rows = []
    for code, name, side, dr, cr in session.execute(q):
        balance = dr - cr if side.value == "debit" else cr - dr
        rows.append({"code": code, "name": name,
                     "debit_minor": int(dr), "credit_minor": int(cr),
                     "balance_minor": int(balance)})
    return rows


def next_entry_no(session: Session, company_id: str) -> int:
    """Дараагийн журналын дугаар — формд урьдчилан харуулахад."""
    return (session.scalar(
        select(func.max(JournalEntry.entry_no)).where(
            JournalEntry.company_id == company_id)) or 0) + 1


def document_no(prefix: str, entry_no: int, year: int) -> str:
    """«ЕЖ/26-001» хэлбэрийн баримтын дугаар."""
    return f"{prefix}/{year % 100:02d}-{entry_no:03d}"


# ============================================================ ТЭНЦЛИЙН ШАЛГУУР

#: Мөнгөн нэгжийн 1/100-аар илэрхийлсэн хүлээцтэй зөрүү. Давхар бичилтэд
#: бөөрөнхийлөлт байх ёсгүй тул тэг — 1 мөнгө ч зөрвөл алдаа.
BALANCE_TOLERANCE_MINOR = 0


def _check(code: str, title: str, ok: bool, detail: str) -> dict:
    return {"code": code, "title": title, "ok": ok, "detail": detail}


def check_balance(session: Session, company_id: str,
                  date_from: date | None = None,
                  date_to: date | None = None) -> dict:
    """Давхар бичилтийн тэнцлийг бүрэн шалгаж «тэнцлээ / тэнцэхгүй» гэж хэлнэ.

    Гурван түвшинд шалгана:

      G1  Бичилт бүр дотроо тэнцэх (Σдебит = Σкредит)
      Σ   Бүх бичилтийн нийлбэр тэнцэх
      СТ-1 Хөрөнгө = Өр төлбөр + Эздийн өмч

    Эхний хоёр нь `post_entry`-д аль хэдийн албадагддаг ч, шалгуур нь
    гадны хөндлөнгийн бичилт, миграц, өгөгдлийн эвдрэлийг барих зорилготой:
    инвариант байна гэж ИТГЭХ биш, ХЭМЖИХ нь чухал.

    Зөрүү гарвал аль бичилт буруу болохыг нэрлэж өгнө — «тэнцэхгүй байна»
    гэж хэлээд орхивол нябо хаанаас хайхаа мэдэхгүй.
    """
    entry_q = (
        select(JournalEntry.id, JournalEntry.entry_no, JournalEntry.entry_date,
               JournalEntry.memo,
               func.coalesce(func.sum(JournalLine.debit_minor), 0),
               func.coalesce(func.sum(JournalLine.credit_minor), 0))
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(JournalEntry.company_id == company_id,
               JournalEntry.status != EntryStatus.draft)
        .group_by(JournalEntry.id, JournalEntry.entry_no,
                  JournalEntry.entry_date, JournalEntry.memo)
        .order_by(JournalEntry.entry_date)
    )
    if date_from:
        entry_q = entry_q.where(JournalEntry.entry_date >= date_from)
    if date_to:
        entry_q = entry_q.where(JournalEntry.entry_date <= date_to)

    total_debit = total_credit = 0
    unbalanced: list[dict] = []
    entry_count = 0

    for eid, no, edate, memo, dr, cr in session.execute(entry_q):
        entry_count += 1
        total_debit += int(dr)
        total_credit += int(cr)
        diff = int(dr) - int(cr)
        if abs(diff) > BALANCE_TOLERANCE_MINOR:
            unbalanced.append({
                "entry_id": eid, "entry_no": no,
                "entry_date": edate.isoformat() if edate else None,
                "memo": (memo or "")[:120],
                "debit_minor": int(dr), "credit_minor": int(cr),
                "difference_minor": diff,
            })

    difference = total_debit - total_credit
    checks = [
        _check("G1_ENTRIES", "Бичилт бүр дотроо тэнцэх",
               not unbalanced,
               f"{entry_count} бичилт шалгав, бүгд тэнцсэн" if not unbalanced
               else f"{len(unbalanced)} бичилт тэнцэхгүй байна"),
        _check("SUM_TOTALS", "Нийт дебит = Нийт кредит",
               abs(difference) <= BALANCE_TOLERANCE_MINOR,
               f"Дебит {total_debit / 100:,.2f}₮ = Кредит {total_credit / 100:,.2f}₮"
               if abs(difference) <= BALANCE_TOLERANCE_MINOR else
               f"Зөрүү {difference / 100:+,.2f}₮ "
               f"(дебит {total_debit / 100:,.2f}₮, кредит {total_credit / 100:,.2f}₮)"),
    ]

    # СТ-1-ийн тэнцэл — тайлангийн давхарга дээрх шалгуур
    from . import reports
    bs = reports.balance_sheet(session, company_id, date_to or date.today())
    eq_diff = bs["total_assets_minor"] - bs["total_liab_equity_minor"]
    checks.append(_check(
        "BS_EQUATION", "Хөрөнгө = Өр төлбөр + Эздийн өмч",
        bool(bs.get("balanced")),
        f"Хөрөнгө {bs['total_assets_minor'] / 100:,.2f}₮ = "
        f"Өр төлбөр + өмч {bs['total_liab_equity_minor'] / 100:,.2f}₮"
        if bs.get("balanced") else f"Зөрүү {eq_diff / 100:+,.2f}₮"))

    return {
        "balanced": all(c["ok"] for c in checks),
        "entry_count": entry_count,
        "total_debit_minor": total_debit,
        "total_credit_minor": total_credit,
        "difference_minor": difference,
        "unbalanced_entries": unbalanced[:50],
        "unbalanced_count": len(unbalanced),
        "checks": checks,
    }
