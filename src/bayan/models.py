"""Өгөгдлийн загвар — даалгаврын §5.1.

Мөнгөн дүн ХЭЗЭЭ Ч float биш: amount_minor = мөнгөөр илэрхийлсэн bigint (1₮ = 100 мөнгө).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, JSON, Float,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- лавлахууд

class NormalSide(str, enum.Enum):
    debit = "debit"
    credit = "credit"


class SourceType(str, enum.Enum):
    bank_txn = "bank_txn"
    manual = "manual"
    salary = "salary"
    depreciation = "depreciation"
    inventory = "inventory"
    wip = "wip"
    fx_reval = "fx_reval"
    closing = "closing"
    reversal = "reversal"


class EntryStatus(str, enum.Enum):
    draft = "draft"
    posted = "posted"
    reversed = "reversed"


class Direction(str, enum.Enum):
    debit = "debit"
    credit = "credit"


class ExtractionPath(str, enum.Enum):
    excel = "excel"
    pdf_text = "pdf_text"
    vision = "vision"


class StatementStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsed_verified = "parsed_verified"   # validation gate давсан
    failed_validation = "failed_validation"
    posted = "posted"


# ---------------------------------------------------------------- компани, данс

class Company(Base):
    __tablename__ = "company"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    reg_no: Mapped[str | None] = mapped_column(String(20))
    director: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=1)
    vat_payer: Mapped[bool] = mapped_column(Boolean, default=False)
    inventory_method: Mapped[str] = mapped_column(String(10), default="average")
    
    # Bayan AI extensions
    taxpayer_no: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True)
    director_name: Mapped[str | None] = mapped_column(String(200), default=None, nullable=True)
    accountant_name: Mapped[str | None] = mapped_column(String(200), default=None, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True)
    stamp_url: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True)
    city_tax_payer: Mapped[bool] = mapped_column(Boolean, default=False)
    city_tax_account: Mapped[str] = mapped_column(String(16), default="3106")


class Account(Base):
    """Дансны төлөвлөгөө — шатлалт код (G4: зөвхөн is_postable данс руу бичнэ)."""
    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("account.id"))
    normal_side: Mapped[NormalSide] = mapped_column(Enum(NormalSide))
    currency: Mapped[str] = mapped_column(String(3), default="MNT")
    is_postable: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- журнал (GL цөм)

class JournalEntry(Base):
    __tablename__ = "journal_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    entry_no: Mapped[int] = mapped_column(Integer)
    entry_date: Mapped[date] = mapped_column(Date)
    memo: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    source_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[EntryStatus] = mapped_column(Enum(EntryStatus), default=EntryStatus.posted)
    reversal_of: Mapped[str | None] = mapped_column(ForeignKey("journal_entry.id"))
    is_system_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_realized: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class JournalLine(Base):
    __tablename__ = "journal_line"
    __table_args__ = (
        # Мөр бүр яг нэг талдаа дүнтэй (дебит XOR кредит)
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_line_one_side",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("journal_entry.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"))
    debit_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    counterparty_id: Mapped[str | None] = mapped_column(String(36))
    cost_center_id: Mapped[str | None] = mapped_column(String(36))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(3), default=None)
    amount_currency: Mapped[float | None] = mapped_column(Float, default=None)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


class PeriodLock(Base):
    """Сарын түгжээ (G3) — Fino/BAAZ-аас авсан сайн санаа."""
    __tablename__ = "period_lock"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    locked_by: Mapped[str | None] = mapped_column(String(36))
    cpa_license_no: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Append-only аудит мөр — BAAZ-ийн change_log-оос авсан санаа."""
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36))
    detail: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- банк, хуулга

class BankAccount(Base):
    __tablename__ = "bank_account"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    bank: Mapped[str] = mapped_column(String(32))          # khan, golomt, tdb, xac, statebank
    account_no: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3), default="MNT")
    gl_account_id: Mapped[str] = mapped_column(ForeignKey("account.id"))  # 1101xxxx


class Statement(Base):
    __tablename__ = "statement"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    bank_account_id: Mapped[str | None] = mapped_column(ForeignKey("bank_account.id"))
    file_name: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64))
    descriptor_id: Mapped[str | None] = mapped_column(String(100))
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    opening_minor: Mapped[int | None] = mapped_column(BigInteger)
    closing_minor: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[StatementStatus] = mapped_column(
        Enum(StatementStatus), default=StatementStatus.uploaded
    )
    validation_report: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("company_id", "file_sha256"),)  # файлын давхардал


class BankTxn(Base):
    """Каноник гүйлгээ — §3."""
    __tablename__ = "bank_txn"
    __table_args__ = (UniqueConstraint("company_id", "bank_account_key", "canonical_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statement.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    bank_account_key: Mapped[str] = mapped_column(String(64))  # bank_account_id эсвэл дансны №
    seq_no: Mapped[int] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(DateTime)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="MNT")
    balance_after_minor: Mapped[int | None] = mapped_column(BigInteger)
    counterparty_account: Mapped[str | None] = mapped_column(String(40))
    counterparty_name: Mapped[str | None] = mapped_column(String(200))
    description_raw: Mapped[str] = mapped_column(Text)
    description_norm: Mapped[str] = mapped_column(Text)
    channel_ref: Mapped[str | None] = mapped_column(String(64))
    canonical_hash: Mapped[str] = mapped_column(String(80))
    extraction_path: Mapped[ExtractionPath] = mapped_column(Enum(ExtractionPath))
    extraction_confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=1.0)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciled_line_id: Mapped[str | None] = mapped_column(String(36), default=None)


class ItemKit(Base):
    """Барааны багц / Сет барааны орц нормын тохиргоо."""
    __tablename__ = "item_kit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    parent_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    child_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    quantity: Mapped[float] = mapped_column(Float, default=1.0)


class PaymentSchedule(Base):
    """Харилцагчийн төлбөрийн хуваарь."""
    __tablename__ = "payment_schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.id"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"))
    due_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, paid


class ClassificationSuggestion(Base):
    """AI/дүрмийн санал — журналд ШУУД орохгүй (G5), хүн батлаад л бичилт үүснэ."""
    __tablename__ = "classification_suggestion"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bank_txn_id: Mapped[str] = mapped_column(ForeignKey("bank_txn.id"))
    company_id: Mapped[str] = mapped_column(String(36))
    account_code: Mapped[str] = mapped_column(String(16))       # харьцах данс
    vat_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    counterparty_name: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))             # rule | haiku | fable
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|edited|rejected
    journal_entry_id: Mapped[str | None] = mapped_column(String(36))


class ClassifierRule(Base):
    """Нябо-гийн гар дүрэм — AI-аас ҮРГЭЛЖ дээгүүр (§4.5)."""
    __tablename__ = "classifier_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    keyword: Mapped[str] = mapped_column(String(200))           # description_norm дотор хайх
    direction: Mapped[Direction | None] = mapped_column(Enum(Direction), nullable=True)
    account_code: Mapped[str] = mapped_column(String(16))
    vat_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    """SaaS хэрэглэгчийн багцын бүртгэл."""
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    plan: Mapped[str] = mapped_column(String(32), default="TRIAL")
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LoanContract(Base):
    """Бизнесийн зээлийн гэрээ (Зээлийн эдийн засагчийн санал 10)."""
    __tablename__ = "loan_contract"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    contract_no: Mapped[str] = mapped_column(String(100))
    bank_name: Mapped[str] = mapped_column(String(200))
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    interest_rate: Mapped[float] = mapped_column(Float)  # сарын хүүгийн хувь (жишээ нь 1.5)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoanSchedule(Base):
    """Зээл эргэн төлөлтийн амортизацийн хуваарь."""
    __tablename__ = "loan_schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    contract_id: Mapped[str] = mapped_column(ForeignKey("loan_contract.id"))
    due_date: Mapped[date] = mapped_column(Date)
    principal_due_minor: Mapped[int] = mapped_column(BigInteger)
    interest_due_minor: Mapped[int] = mapped_column(BigInteger)
    paid_principal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_interest_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, paid


class FxRate(Base):
    __tablename__ = "fx_rate"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    currency: Mapped[str] = mapped_column(String(3))
    rate_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[float] = mapped_column(Float)


class JournalTemplate(Base):
    __tablename__ = "journal_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    template_name: Mapped[str] = mapped_column(String(200))
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    lines: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Budget(Base):
    """Төсөвт данс болон төсөвлөсөн дүн."""
    __tablename__ = "budget"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    account_code: Mapped[str] = mapped_column(String(16))
    budget_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class UserPermission(Base):
    """Хэрэглэгчийн модуль болон үйлдэл бүрийн нарийвчилсан эрх."""
    __tablename__ = "user_permission"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    permissions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Contract(Base):
    """Харилцагчийн борлуулалт/худалдан авалтын гэрээ."""
    __tablename__ = "contract"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"))
    contract_no: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active")


class LoyaltyCard(Base):
    """Гишүүнчлэлийн ба Бэлгийн карт (Лоялти)."""
    __tablename__ = "loyalty_card"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    card_no: Mapped[str] = mapped_column(String(64))
    card_type: Mapped[str] = mapped_column(String(32), default="membership")  # membership | gift
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("counterparty.id"), nullable=True)
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    points: Mapped[float] = mapped_column(Float, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PosTerminal(Base):
    """ПОС Кассуудын бүртгэл."""
    __tablename__ = "pos_terminal"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    terminal_name: Mapped[str] = mapped_column(String(100))
    terminal_code: Mapped[str] = mapped_column(String(32))
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PosTable(Base):
    """Заал ширээний зураглал."""
    __tablename__ = "pos_table"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    section_name: Mapped[str] = mapped_column(String(100))
    table_name: Mapped[str] = mapped_column(String(50))
    capacity: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(32), default="available")  # available, occupied, reserved


class CostCenter(Base):
    """Салбар ба Хэлтэс (Cost Center / Branch)."""
    __tablename__ = "cost_center"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32), default="branch")  # branch | department
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EbarimtVerify(Base):
    """И-Баримт ДДТД ба QR код тулгалт."""
    __tablename__ = "ebarimt_verify"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    ddtd: Mapped[str] = mapped_column(String(64))
    lottery_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    vat_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="valid")  # valid, invalid, matched
    matched_invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoice.id"), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ItemUom(Base):
    """Барааны олон хэмжих нэгж ба шилжүүлэх коэффицент."""
    __tablename__ = "item_uom"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    uom_name: Mapped[str] = mapped_column(String(50))  # Хайрцаг, Шил, Литр, Тонн
    conversion_factor: Mapped[float] = mapped_column(Float, default=1.0)  # Үндсэн нэгж дэх тоо хэмжээ
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)
    price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class EmployeeAdvance(Base):
    """Ажилтны урьдчилгаа ба Томилолтын тооцоо (1401)."""
    __tablename__ = "employee_advance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    advance_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, cleared, deducted
    cleared_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    gl_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entry.id"), nullable=True)


class AuditAlert(Base):
    """AI Санхүүгийн Алдагдал ба Содон Гүйлгээний Сэрэмжлүүлэг."""
    __tablename__ = "audit_alert"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    alert_type: Mapped[str] = mapped_column(String(64))  # duplicate_entry, negative_stock, unusual_expense
    severity: Mapped[str] = mapped_column(String(16), default="medium")  # high, medium, low
    message: Mapped[str] = mapped_column(String(255))
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open, resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PurchaseOrder(Base):
    """Худалдан авалтын захиалга (PO)."""
    __tablename__ = "purchase_order"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    po_number: Mapped[str] = mapped_column(String(50))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"))
    po_date: Mapped[date] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft, sent, received, billed


class PurchaseOrderItem(Base):
    """Худалдан авалтын захиалгын мөрүүд."""
    __tablename__ = "purchase_order_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_order.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    item_code: Mapped[str] = mapped_column(String(32))
    item_name: Mapped[str] = mapped_column(String(200))
    qty_ordered: Mapped[float] = mapped_column(Float, default=1.0)
    qty_received: Mapped[float] = mapped_column(Float, default=0.0)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class WithholdingTax(Base):
    """Суутган Татварын Тооцоо (WHT 10%/20%)."""
    __tablename__ = "withholding_tax"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("counterparty.id"), nullable=True)
    tax_type: Mapped[str] = mapped_column(String(50))  # non_resident_20, resident_contractor_10, royalty_10
    gross_amount_minor: Mapped[int] = mapped_column(BigInteger)
    wht_rate: Mapped[float] = mapped_column(Float, default=10.0)
    wht_amount_minor: Mapped[int] = mapped_column(BigInteger)
    net_paid_minor: Mapped[int] = mapped_column(BigInteger)
    tax_date: Mapped[date] = mapped_column(Date)
    gl_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entry.id"), nullable=True)


class ProjectCosting(Base):
    """Төслийн өртөг ба төсөв (Project Costing & Job Profitability)."""
    __tablename__ = "project_costing"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    project_code: Mapped[str] = mapped_column(String(32))
    project_name: Mapped[str] = mapped_column(String(200))
    budget_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    contract_value_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")  # ACTIVE, COMPLETED, ON_HOLD






