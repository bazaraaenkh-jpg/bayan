"""Харилцагч + Нэхэмжлэх (Авлага/Өглөг) — насжилтын тайлантай.

Нэхэмжлэх баталгаажихдаа журналын бичилтээ АТОМООР үүсгэнэ (G6):
  борлуулалт:      Дт 1201 нийт / Кт 5101 цэвэр [/ Кт 3105 НӨАТ]
  худалдан авалт:  Дт зардлын данс цэвэр [+ Дт 1203 НӨАТ] / Кт 3101 нийт

Төлбөр (pay_invoice) нь мөчлөгийг хаана:
  авлага хаах:     Дт 1101 банк / Кт 1201   (counterparty_id-тэй)
  өглөг хаах:      Дт 3101 / Кт 1101 банк

Төлбөр бүр InvoicePayment мөр үүсгэнэ — ингэснээр өнгөрсөн үеийн насжилтыг
тухайн үеийн байдлаар (тэр өдрийн дараах төлбөрийг тооцохгүйгээр) гаргана.
Дэд данс ↔ ерөнхий дэвтрийн тулгалт subledger_reconciliation-оор шалгагдана.
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


class InvoicePayment(Base):
    """Нэхэмжлэхийн төлбөрийн бүртгэл — насжилтыг он сар өдрөөр гаргах үндэс."""
    __tablename__ = "invoice_payment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.id"))
    pay_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36))


def get_counterparty_ar_balance(session: Session, company_id: str, counterparty_id: str) -> int:
    """Харилцагчийн авлагын үлдэгдэл — зөвхөн 1201/1202 худалдааны авлага.

    Ерөнхий дэвтэрт ОРООГҮЙ draft бичилт тооцогдох ёсгүй (trial_balance-тай
    ижил шүүлт), эс тэгвэл батлагдаагүй ноорог нэхэмжлэх зээлийн хязгаарыг
    хэтрүүлж, бодит борлуулалт татгалзагддаг. Мөн «12» угтвар нь 1203
    «НӨАТ-ын авлага»-г ч хамардаг тул худалдааны авлагын данс руу нарийвчлав.
    """
    from .models import JournalLine, JournalEntry, Account, EntryStatus
    q = select(JournalLine.debit_minor, JournalLine.credit_minor).join(JournalEntry).join(Account, Account.id == JournalLine.account_id).where(
        JournalEntry.company_id == company_id,
        JournalEntry.status != EntryStatus.draft,
        JournalLine.counterparty_id == counterparty_id,
        Account.code.startswith("1201") | Account.code.startswith("1202")
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
    vat_minor: int | None = None,
    city_tax_minor: int | None = None,
    memo: str | None = None,
) -> Invoice:
    # Ledger 0 буюу сөрөг дүнтэй мөрийг хүлээж авдаггүй тул ойлгомжгүй
    # UnbalancedEntryError гарахаас өмнө шалтгааныг нь хэлнэ. Буцаалтыг
    # сөрөг нэхэмжлэхээр биш, тусдаа залруулга бичилтээр бүртгэнэ.
    if net_minor <= 0:
        raise ledger.LedgerError(
            f"Нэхэмжлэх {number}: цэвэр дүн 0-ээс их байх ёстой "
            f"(өгсөн: {net_minor / 100:,.2f}₮)")

    # Ижил дугаартай нэхэмжлэх давхар орвол өглөг/авлага хоёр дахин
    # бүртгэгдэж, дэд данс ерөнхий дэвтэртэй тэнцэхээ болино
    dup = session.scalar(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == kind,
        Invoice.number == number))
    if dup is not None:
        raise ledger.LedgerError(
            f"«{number}» дугаартай {'борлуулалтын' if kind == InvoiceKind.sales else 'худалдан авалтын'} "
            f"нэхэмжлэх аль хэдийн бүртгэгдсэн ({dup.issue_date})")

    # eBarimt-ын баримт дээрх НӨАТ нь цэвэр дүнгийн 10% ЯГ гардаггүй
    # (13,181.82 гэх мэт бутархай) тул дүнг нь шууд өгөх боломжтой —
    # эс тэгвэл 1 мөнгөний зөрүү 100+ баримт дээр хуримтлагдаж, ТТ-03а
    # ерөнхий дэвтэртэй тэнцэхээ болино.
    vat = vat_minor if vat_minor is not None else (
        net_minor * VAT_RATE // 100 if with_vat else 0)

    from .models import Company
    c = session.get(Company, company_id)
    city_tax = 0
    city_tax_acc = "3106"
    if city_tax_minor is not None:
        city_tax = city_tax_minor
        city_tax_acc = getattr(c, "city_tax_account", "3106") or "3106" if c else "3106"
    elif c and getattr(c, "city_tax_payer", False) and kind == InvoiceKind.sales and not is_wholesale:
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
            # 1203 «НӨАТ-ын авлага» — pipeline.VAT_INPUT_CODE ба
            # etax.build_tt03a-гийн тулгалт хоёулаа 1203 дебитийг шалгадаг.
            # Өмнө нь 1205 «Татвар, НДШ-ийн авлага» руу бичдэг байсан тул
            # ТТ-03а-гийн VAT_INPUT шалгуур үргэлж унаж, илгээлт блоклогддог байв.
            lines.append(ledger.LineInput("1203", debit_minor=vat,
                                          description=f"НӨАТ {number}"))
        lines.append(ledger.LineInput("3101", credit_minor=net_minor + vat,
                                      counterparty_id=counterparty_id,
                                      description=f"Нэхэмжлэх {number}"))

    entry = ledger.post_entry(session, company_id, issue_date, lines,
                              source_type=SourceType.manual, source_id=inv.id,
                              memo=memo or f"Нэхэмжлэх {number}", actor_id=actor_id)
    inv.journal_entry_id = entry.id
    session.flush()
    return inv


def pay_invoice(session: Session, company_id: str, invoice: Invoice,
                amount_minor: int, pay_date: date,
                bank_account: str = "1101", actor_id: str | None = None,
                memo: str | None = None) -> dict:
    """Нэхэмжлэхийн төлбөр — авлага/өглөгийн мөчлөгийг хаана.

      борлуулалт: Дт банк / Кт 1201 (харилцагчтай)
      худалдан авалт: Дт 3101 (харилцагчтай) / Кт банк

    Илүү төлөлтийг зөвшөөрөхгүй — эс тэгвэл дэд дансны үлдэгдэл сөрөг болж,
    ерөнхий дэвтэртэй тэнцэхээ болино. Хэсэгчилсэн төлбөр дэмжигдэнэ.
    """
    if amount_minor <= 0:
        raise ledger.LedgerError("Төлбөрийн дүн 0-ээс их байх ёстой")
    if invoice.company_id != company_id:
        raise ledger.LedgerError("Нэхэмжлэх өөр компанийнх байна")
    if amount_minor > invoice.outstanding_minor:
        raise ledger.LedgerError(
            f"Нэхэмжлэх {invoice.number}: үлдэгдэл "
            f"{invoice.outstanding_minor / 100:,.2f}₮, "
            f"төлөх гэж буй {amount_minor / 100:,.2f}₮ — илүү төлөлт")

    note = memo or f"Нэхэмжлэх {invoice.number} төлбөр"
    if invoice.kind == InvoiceKind.sales:
        lines = [
            ledger.LineInput(bank_account, debit_minor=amount_minor,
                             description=note),
            ledger.LineInput("1201", credit_minor=amount_minor,
                             counterparty_id=invoice.counterparty_id,
                             description=note),
        ]
    else:
        lines = [
            ledger.LineInput("3101", debit_minor=amount_minor,
                             counterparty_id=invoice.counterparty_id,
                             description=note),
            ledger.LineInput(bank_account, credit_minor=amount_minor,
                             description=note),
        ]

    entry = ledger.post_entry(session, company_id, pay_date, lines,
                              source_type=SourceType.manual,
                              source_id=invoice.id, memo=note,
                              actor_id=actor_id)
    session.add(InvoicePayment(
        company_id=company_id, invoice_id=invoice.id, pay_date=pay_date,
        amount_minor=amount_minor, journal_entry_id=entry.id))
    invoice.paid_minor += amount_minor
    session.flush()
    return {
        "invoice_id": invoice.id, "number": invoice.number,
        "paid_minor": invoice.paid_minor,
        "outstanding_minor": invoice.outstanding_minor,
        "settled": invoice.outstanding_minor == 0,
        "entry_id": entry.id,
    }


def paid_as_of(session: Session, invoice: Invoice, as_of: date) -> int:
    """Тухайн өдрийн байдлаарх төлөгдсөн дүн.

    InvoicePayment-д бүртгэгдээгүй хуучин төлбөр (eBarimt-ийн тулгалт болон
    энэ хүснэгт нэмэгдэхээс өмнөх өгөгдөл) байвал түүнийг «аль хэдийн
    төлөгдсөн» гэж үзнэ — эс тэгвэл хаагдсан нэхэмжлэх насжилтад дахин гарна.
    """
    pays = session.scalars(select(InvoicePayment).where(
        InvoicePayment.invoice_id == invoice.id)).all()
    tracked = sum(p.amount_minor for p in pays)
    untracked = invoice.paid_minor - tracked
    return untracked + sum(p.amount_minor for p in pays if p.pay_date <= as_of)


AGING_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 120), (121, 10 ** 6)]


def aging_report(session: Session, company_id: str, kind: InvoiceKind,
                 as_of: date) -> list[dict]:
    """Насжилтын тайлан — «хугацаа болоогүй» + BAAZ-ийн 0-30/…/120+ ангилал.

    buckets[0] нь хугацаа нь болоогүй үлдэгдэл. Өмнө нь max(overdue, 0)
    хийдэг байсан тул хугацаа болоогүй нэхэмжлэх «0-30 хоног хоцорсон»
    багцад орж, хоцрогдлын дүр зураг гажуудуулдаг байв.
    """
    rows = []
    invoices = session.scalars(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == kind)).all()
    partners = {p.id: p.name for p in session.scalars(
        select(Counterparty).where(Counterparty.company_id == company_id))}
    for inv in invoices:
        # as_of-оос ХОЙШ олгосон нэхэмжлэх тухайн үед хараахан үүсээгүй
        if inv.issue_date > as_of:
            continue
        outstanding = inv.total_minor - paid_as_of(session, inv, as_of)
        if outstanding <= 0:
            continue
        overdue = (as_of - inv.due_date).days
        buckets = [0] * (len(AGING_BUCKETS) + 1)
        if overdue < 0:
            buckets[0] = outstanding          # хугацаа болоогүй
        else:
            for i, (lo, hi) in enumerate(AGING_BUCKETS):
                if lo <= overdue <= hi:
                    buckets[i + 1] = outstanding
                    break
        rows.append({
            "counterparty": partners.get(inv.counterparty_id, "?"),
            "number": inv.number, "due_date": inv.due_date.isoformat(),
            "overdue_days": max(overdue, 0),
            "outstanding_minor": outstanding,
            "buckets": buckets,
        })
    return rows


def subledger_reconciliation(session: Session, company_id: str,
                             as_of: date | None = None) -> dict:
    """Нэхэмжлэхийн дэд данс ↔ ерөнхий дэвтрийн тулгалт (АУ-ийн үндсэн хяналт).

    Авлага: нээлттэй борлуулалтын нэхэмжлэхийн нийлбэр ↔ GL 1201
    Өглөг:  нээлттэй худалдан авалтын нэхэмжлэхийн нийлбэр ↔ GL 3101
    """
    as_of = as_of or date.today()
    tb = {r["code"]: r for r in ledger.trial_balance(
        session, company_id, None, as_of)}
    invoices = session.scalars(select(Invoice).where(
        Invoice.company_id == company_id)).all()

    out = {}
    for key, kind, code in (("receivable", InvoiceKind.sales, "1201"),
                            ("payable", InvoiceKind.purchase, "3101")):
        sub = sum(i.total_minor - paid_as_of(session, i, as_of)
                  for i in invoices
                  if i.kind == kind and i.issue_date <= as_of)
        gl = tb.get(code, {}).get("balance_minor", 0)
        out[key] = {
            "account": code, "subledger_minor": sub, "gl_minor": gl,
            "difference_minor": sub - gl, "matched": sub == gl,
        }
    out["as_of"] = as_of.isoformat()
    return out
