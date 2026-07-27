"""Харилцагч + Нэхэмжлэх (Авлага/Өглөг) — насжилтын тайлантай.

Нэхэмжлэх баталгаажихдаа журналын бичилтээ АТОМООР үүсгэнэ (G6):
  борлуулалт:      Дт 1201 нийт / Кт 5101 цэвэр [/ Кт 3105 НӨАТ]
  худалдан авалт:  Дт зардлын данс цэвэр [+ Дт 1205 НӨАТ] / Кт 3101 нийт
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy import select

from . import ledger
from .models import Base, SourceType

VAT_RATE = 10  # НӨАТ %


def _uuid() -> str:
    return str(uuid.uuid4())


class PartnerKind(str, enum.Enum):
    customer = "customer"
    supplier = "supplier"
    both = "both"


class Counterparty(Base):
    __tablename__ = "counterparty"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    name: Mapped[str] = mapped_column(String(200))
    reg_no: Mapped[str | None] = mapped_column(String(20))
    kind: Mapped[PartnerKind] = mapped_column(Enum(PartnerKind), default=PartnerKind.both)
    bank_account: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(36), default=None, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    credit_limit_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class InvoiceKind(str, enum.Enum):
    sales = "sales"        # авлага үүсгэнэ
    purchase = "purchase"  # өглөг үүсгэнэ


class Invoice(Base):
    __tablename__ = "invoice"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"))
    kind: Mapped[InvoiceKind] = mapped_column(Enum(InvoiceKind))
    number: Mapped[str] = mapped_column(String(40))
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    net_minor: Mapped[int] = mapped_column(BigInteger)
    vat_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    expense_account: Mapped[str | None] = mapped_column(String(16))  # purchase-д
    journal_entry_id: Mapped[str | None] = mapped_column(String(36))
    city_tax_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    is_wholesale: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def total_minor(self) -> int:
        return self.net_minor + self.vat_minor + self.city_tax_minor

    @property
    def outstanding_minor(self) -> int:
        return self.total_minor - self.paid_minor


def get_counterparty_ar_balance(session: Session, company_id: str, counterparty_id: str) -> int:
    from .models import JournalLine, JournalEntry, Account
    q = select(JournalLine.debit_minor, JournalLine.credit_minor).join(JournalEntry).join(Account, Account.id == JournalLine.account_id).where(
        JournalEntry.company_id == company_id,
        JournalLine.counterparty_id == counterparty_id,
        Account.code.like("12%")
    )
    res = session.execute(q).all()
    return sum(row[0] - row[1] for row in res)


def post_invoice(
    session: Session,
    company_id: str,
    counterparty_id: str,
    kind: InvoiceKind,
    number: str,
    issue_date: date,
    due_date: date,
    net_minor: int,
    with_vat: bool = False,
    expense_account: str = "7199",
    actor_id: str | None = None,
    is_wholesale: bool = False,
) -> Invoice:
    vat = net_minor * VAT_RATE // 100 if with_vat else 0

    from .models import Company
    c = session.get(Company, company_id)
    city_tax = 0
    city_tax_acc = "3106"
    if c and getattr(c, "city_tax_payer", False) and kind == InvoiceKind.sales and not is_wholesale:
        city_tax = net_minor * 1 // 100
        city_tax_acc = getattr(c, "city_tax_account", "3106") or "3106"

    # Credit limit control check (Bayan AI rule)
    if kind == InvoiceKind.sales:
        cp = session.get(Counterparty, counterparty_id)
        if cp and getattr(cp, "credit_limit_minor", 0) > 0:
            current_bal = get_counterparty_ar_balance(session, company_id, counterparty_id)
            new_bal = current_bal + net_minor + vat + city_tax
            if new_bal > cp.credit_limit_minor:
                raise ledger.LedgerError(
                    f"Харилцагчийн зээлийн хязгаар хэтэрсэн байна! "
                    f"Зээлийн хязгаар: {cp.credit_limit_minor/100:,.2f}₮. "
                    f"Гүйлгээний дараах нийт авлага: {new_bal/100:,.2f}₮"
                )

    inv = Invoice(
        company_id=company_id, counterparty_id=counterparty_id, kind=kind,
        number=number, issue_date=issue_date, due_date=due_date,
        net_minor=net_minor, vat_minor=vat, city_tax_minor=city_tax if kind == InvoiceKind.sales else 0,
        expense_account=expense_account if kind == InvoiceKind.purchase else None,
        is_wholesale=is_wholesale,
    )
    session.add(inv)
    session.flush()

    if kind == InvoiceKind.sales:
        lines = [ledger.LineInput("1201", debit_minor=net_minor + vat + city_tax,
                                  counterparty_id=counterparty_id,
                                  description=f"Нэхэмжлэх {number}"),
                 ledger.LineInput("5101", credit_minor=net_minor,
                                  description=f"Нэхэмжлэх {number}")]
        if vat:
            lines.append(ledger.LineInput("3105", credit_minor=vat,
                                          description=f"НӨАТ {number}"))
        if city_tax:
            lines.append(ledger.LineInput(city_tax_acc, credit_minor=city_tax,
                                          description=f"НХАТ {number}"))
    else:
        lines = [ledger.LineInput(expense_account, debit_minor=net_minor,
                                  description=f"Нэхэмжлэх {number}")]
        if vat:
            lines.append(ledger.LineInput("1205", debit_minor=vat,
                                          description=f"НӨАТ {number}"))
        lines.append(ledger.LineInput("3101", credit_minor=net_minor + vat,
                                      counterparty_id=counterparty_id,
                                      description=f"Нэхэмжлэх {number}"))

    entry = ledger.post_entry(session, company_id, issue_date, lines,
                              source_type=SourceType.manual, source_id=inv.id,
                              memo=f"Нэхэмжлэх {number}", actor_id=actor_id)
    inv.journal_entry_id = entry.id
    session.flush()
    return inv


AGING_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 120), (121, 10 ** 6)]


def aging_report(session: Session, company_id: str, kind: InvoiceKind,
                 as_of: date) -> list[dict]:
    """Насжилтын тайлан — BAAZ-ийн 0-30/…/120+ ангилалтай ижил."""
    rows = []
    invoices = session.scalars(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == kind)).all()
    partners = {p.id: p.name for p in session.scalars(
        select(Counterparty).where(Counterparty.company_id == company_id))}
    for inv in invoices:
        if inv.outstanding_minor <= 0:
            continue
        overdue = (as_of - inv.due_date).days
        buckets = [0] * len(AGING_BUCKETS)
        for i, (lo, hi) in enumerate(AGING_BUCKETS):
            if lo <= max(overdue, 0) <= hi:
                buckets[i] = inv.outstanding_minor
                break
        rows.append({
            "counterparty": partners.get(inv.counterparty_id, "?"),
            "number": inv.number, "due_date": inv.due_date.isoformat(),
            "overdue_days": max(overdue, 0),
            "outstanding_minor": inv.outstanding_minor,
            "buckets": buckets,
        })
    return rows
