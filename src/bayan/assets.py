"""Үндсэн хөрөнгө — шулуун шугамын элэгдэл, сар бүрийн автомат бичилт.

  элэгдэл: Дт 7107 Элэгдлийн зардал / Кт 2509 Хуримтлагдсан элэгдэл
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import ledger
from .models import Base, SourceType


def _uuid() -> str:
    return str(uuid.uuid4())


class FixedAsset(Base):
    __tablename__ = "fixed_asset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    gl_account: Mapped[str] = mapped_column(String(16), default="2501")
    cost_minor: Mapped[int] = mapped_column(BigInteger)
    salvage_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    life_months: Mapped[int] = mapped_column(Integer)
    in_service_from: Mapped[date] = mapped_column(Date)
    accumulated_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    months_depreciated: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def monthly_depreciation_minor(self) -> int:
        return (self.cost_minor - self.salvage_minor) // self.life_months

    @property
    def book_value_minor(self) -> int:
        return self.cost_minor - self.accumulated_minor


class AssetError(Exception):
    pass


def register_asset(session: Session, company_id: str, code: str, name: str,
                   cost_minor: int, life_months: int, in_service_from: date,
                   salvage_minor: int = 0, gl_account: str = "2501",
                   credit_account: str = "3101") -> FixedAsset:
    """Хөрөнгө бүртгэх — Дт 2501 / Кт 3101 (өглөгөөр авсан гэж үзье)."""
    # Баталгаажуулалтгүй үед: life_months=0 → ZeroDivisionError, cost=0 →
    # ойлгомжгүй UnbalancedEntryError, salvage > cost → СӨРӨГ элэгдэл бодогдож
    # хуримтлагдсан элэгдэл ухарч эхэлдэг байв
    if cost_minor <= 0:
        raise AssetError(f"{code}: өртөг 0-ээс их байх ёстой "
                         f"(өгсөн: {cost_minor / 100:,.2f}₮)")
    if life_months <= 0:
        raise AssetError(f"{code}: ашиглалтын хугацаа сараар 0-ээс их байх ёстой "
                         f"(өгсөн: {life_months})")
    if salvage_minor < 0 or salvage_minor >= cost_minor:
        raise AssetError(
            f"{code}: үлдэх өртөг 0 ба өртгийн хооронд байх ёстой "
            f"(үлдэх {salvage_minor / 100:,.2f}₮ ≥ өртөг {cost_minor / 100:,.2f}₮)")

    asset = FixedAsset(company_id=company_id, code=code, name=name,
                       gl_account=gl_account, cost_minor=cost_minor,
                       salvage_minor=salvage_minor, life_months=life_months,
                       in_service_from=in_service_from)
    session.add(asset)
    session.flush()
    ledger.post_entry(session, company_id, in_service_from, [
        ledger.LineInput(gl_account, debit_minor=cost_minor,
                         description=f"ҮХ бүртгэл {code} {name}"),
        ledger.LineInput(credit_account, credit_minor=cost_minor,
                         description=f"ҮХ бүртгэл {code}"),
    ], source_type=SourceType.manual, source_id=asset.id)
    return asset


def months_due(asset: FixedAsset, period_end: date) -> int:
    """Ашиглалтад орсноос хойш period_end хүртэл элэгдэх ЁСТОЙ сарын тоо."""
    if asset.in_service_from > period_end:
        return 0
    n = ((period_end.year - asset.in_service_from.year) * 12
         + (period_end.month - asset.in_service_from.month) + 1)
    return max(0, min(n, asset.life_months))


def run_monthly_depreciation(session: Session, company_id: str,
                             period_end: date) -> list[dict]:
    """Сарын элэгдлийн автомат гүйлт — идэвхтэй хөрөнгө бүрд нэг бичилт.

    Элэгдлийг «нэг гүйлт = нэг сар» гэж бус, ЁСТОЙ ба БИЧИГДСЭН сарын
    ЗӨРҮҮГЭЭР бодно. Ингэснээр нэг зэрэг хоёр асуудал шийдэгдэнэ:
      * давтан ажиллуулахад зөрүү 0 тул давхар бичилт үүсэхгүй
        (өмнө нь товч 3 удаа дарахад нэг сарын элэгдэл 3 дахин бичигдэж
        months_depreciated 3/48 болдог байв);
      * алгассан сар байвал гүйцээж бичнэ (өмнө нь 1-р сард ашиглалтад
        орсон хөрөнгийг 6-р сард гүйлгэхэд ердөө 1 сарын элэгдэл авдаг байв).
    """
    results = []
    assets = session.scalars(select(FixedAsset).where(
        FixedAsset.company_id == company_id, FixedAsset.active == True)).all()  # noqa: E712
    for a in assets:
        due = months_due(a, period_end)
        pending = due - a.months_depreciated
        if pending <= 0:
            continue
        amount = a.monthly_depreciation_minor * pending
        # сүүлийн сард хүрсэн бол үлдэгдэл дүнг бүрэн авна (бутархайн хуримтлал)
        if due == a.life_months:
            amount = (a.cost_minor - a.salvage_minor) - a.accumulated_minor
        if amount <= 0:
            continue
        label = f"{period_end:%Y-%m}" + (f" ({pending} сар)" if pending > 1 else "")
        ledger.post_entry(session, company_id, period_end, [
            ledger.LineInput("7107", debit_minor=amount,
                             description=f"Элэгдэл {a.code} {label}"),
            ledger.LineInput("2509", credit_minor=amount,
                             description=f"Хуримтлагдсан элэгдэл {a.code}"),
        ], source_type=SourceType.depreciation, source_id=a.id)
        a.accumulated_minor += amount
        a.months_depreciated = due
        results.append({"code": a.code, "amount_minor": amount,
                        "months": pending,
                        "book_value_minor": a.book_value_minor})
    session.flush()
    return results


def asset_register(session: Session, company_id: str) -> list[dict]:
    assets = session.scalars(select(FixedAsset).where(
        FixedAsset.company_id == company_id)).all()
    return [{
        "id": a.id,
        "code": a.code, "name": a.name, "cost_minor": a.cost_minor,
        "accumulated_minor": a.accumulated_minor,
        "book_value_minor": a.book_value_minor,
        "months": f"{a.months_depreciated}/{a.life_months}",
        "active": a.active
    } for a in assets]


def retire_asset(session: Session, company_id: str, asset_id: str,
                 retire_date: date, proceeds_minor: int = 0,
                 proceeds_account: str = "1101") -> dict:
    """Ашиглалтаас хасах / борлуулах — хөрөнгийг дэвтрээс БҮРЭН таслана.

      Дт 2509 хуримтлагдсан элэгдэл   (хасагдуулагчийг цэвэрлэнэ)
      Дт мөнгө                        (борлуулсан бол орлого)
      Дт 7199 гарз / Кт 5201 олз      (үлдэх өртөг ↔ орлогын зөрүү)
      Кт 2501 хөрөнгийн өртөг

    Хуримтлагдсан элэгдлийг хасахгүй бол 2509 үүрд өлгөөтэй үлдэж, гарз нь
    үлдэх өртгийн оронд БҮТЭН өртгөөр бичигддэг байв.
    """
    asset = session.get(FixedAsset, asset_id)
    if not asset or asset.company_id != company_id:
        raise AssetError("Үндсэн хөрөнгө олдсонгүй")
    if not asset.active:
        raise AssetError("Энэ хөрөнгө аль хэдийн идэвхгүй болсон байна")
    if proceeds_minor < 0:
        raise AssetError("Борлуулалтын орлого сөрөг байж болохгүй")

    lines = []
    if asset.accumulated_minor > 0:
        lines.append(
            ledger.LineInput("2509", debit_minor=asset.accumulated_minor,
                             description=f"Хуримтлагдсан элэгдэл хасах: {asset.code}")
        )
    if proceeds_minor > 0:
        lines.append(
            ledger.LineInput(proceeds_account, debit_minor=proceeds_minor,
                             description=f"ҮХ борлуулалтын орлого: {asset.code}")
        )

    result = proceeds_minor - asset.book_value_minor   # + олз, − гарз
    if result < 0:
        lines.append(
            ledger.LineInput("7199", debit_minor=-result,
                             description=f"ҮХ ашиглалтаас хасалтын гарз: {asset.code}")
        )
    elif result > 0:
        lines.append(
            ledger.LineInput("5201", credit_minor=result,
                             description=f"ҮХ борлуулалтын олз: {asset.code}")
        )

    lines.append(
        ledger.LineInput(asset.gl_account or "2501", credit_minor=asset.cost_minor,
                         description=f"Ашиглалтаас хасалт: {asset.code}")
    )

    entry = ledger.post_entry(
        session, company_id, retire_date, lines,
        source_type=SourceType.manual, source_id=asset.id,
        memo=f"Үндсэн хөрөнгийн ашиглалтаас хасалт: {asset.code} - {asset.name}")
    asset.active = False
    session.flush()
    return {
        "code": asset.code, "cost_minor": asset.cost_minor,
        "accumulated_minor": asset.accumulated_minor,
        "book_value_minor": asset.book_value_minor,
        "proceeds_minor": proceeds_minor,
        "gain_minor": max(result, 0), "loss_minor": max(-result, 0),
        "entry_id": entry.id,
    }
