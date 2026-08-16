"""Дуусаагүй үйлдвэрлэл (WIP) — Bayan AI-ийн гол ялгарлын модуль (даалгаврын §6).

Fino-гийн нэг алхамт «үйлдвэрлэлийн баримт»-аас ялгаатай нь: ажлын захиалга
САР ДАМЖИН нээлттэй байж Материал + Хөдөлмөр + НЗ хуримтлуулж, балансын
2145 «Дуусаагүй үйлдвэрлэл» дансанд үлдэгдэл болж тусна. Бүх хөдөлгөөн
журналын бичилтээр (G6) — 2145-ийн GL үлдэгдэл ↔ нээлттэй захиалгуудын
хуримтлагдсан өртөг ҮРГЭЛЖ тэнцүү (reconciliation test-ээр баталгаажина).

Өртгийн урсгал:
  материал олголт:  Дт 2145 / Кт 2101   (inventory.issue-ээр)
  шууд хөдөлмөр:    Дт 2145 / Кт 7101   (цалингийн ЗАРДЛААС шингээнэ)
  НЗ хуваарилалт:   Дт 2145 / Кт 7108
  хүлээлгэн өгөх:   Дт 2151 / Кт 2145   (бүрэн/хэсэгчилсэн)

Хөдөлмөр ба НЗ хоёулаа ЗАРДЛЫН данснаас шингээгддэг: цалин нь эхлээд
salary.run_payroll-оор Дт 7101 / Кт 3102,3103,3104 гэж бүртгэгдээд, дараа нь
шууд хөдөлмөрийн хэсэг нь ДҮ рүү шилжинэ. Өглөгийн данс (3102) руу шууд
бичвэл цалингийн модультай давхардаж, ажилтанд төлөх өглөг хоёр дахин
харагдана — тиймээс LABOR_ACCOUNT нь 7101 (өглөгийн данс биш).
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Enum, ForeignKey, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import inventory, ledger
from .models import Base, SourceType

WIP_ACCOUNT = "2145"
FG_ACCOUNT = "2151"      # бэлэн бүтээгдэхүүн
LABOR_ACCOUNT = "7101"   # цалингийн зардал — эндээс ДҮ рүү шингээнэ
OH_ACCOUNT = "7108"


def _uuid() -> str:
    return str(uuid.uuid4())


class TechCard(Base):
    """Орц (BOM) — бүтээгдэхүүн нэг нэгжид шаардагдах материал."""
    __tablename__ = "tech_card"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    product_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    name: Mapped[str] = mapped_column(String(200))


class TechCardLine(Base):
    __tablename__ = "tech_card_line"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tech_card_id: Mapped[str] = mapped_column(ForeignKey("tech_card.id"))
    material_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    qty_per_unit: Mapped[int] = mapped_column(BigInteger)   # 1 нэгжид шаардагдах тоо


class OrderStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


class WorkOrder(Base):
    __tablename__ = "work_order"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    order_no: Mapped[str] = mapped_column(String(32))
    tech_card_id: Mapped[str | None] = mapped_column(ForeignKey("tech_card.id"))
    product_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    qty_planned: Mapped[int] = mapped_column(BigInteger)
    qty_completed: Mapped[int] = mapped_column(BigInteger, default=0)
    opened_on: Mapped[date] = mapped_column()
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.open)
    material_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    labor_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    overhead_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    transferred_minor: Mapped[int] = mapped_column(BigInteger, default=0)

    @property
    def accumulated_minor(self) -> int:
        return self.material_minor + self.labor_minor + self.overhead_minor

    @property
    def wip_balance_minor(self) -> int:
        """Балансад тусах ДҮ үлдэгдэл = хуримтлагдсан − шилжүүлсэн."""
        return self.accumulated_minor - self.transferred_minor


class WipError(Exception):
    pass


def open_order(session: Session, company_id: str, order_no: str,
               product_item: inventory.Item, qty_planned: int,
               opened_on: date, tech_card_id: str | None = None) -> WorkOrder:
    order = WorkOrder(company_id=company_id, order_no=order_no,
                      product_item_id=product_item.id, qty_planned=qty_planned,
                      opened_on=opened_on, tech_card_id=tech_card_id)
    session.add(order)
    session.flush()
    return order


def issue_materials(session: Session, order: WorkOrder,
                    issues: list[tuple[inventory.Item, int]],
                    move_date: date) -> int:
    """Материал олголт агуулахаас — Дт 2145 / Кт барааны данс."""
    if order.status == OrderStatus.closed:
        raise WipError("Хаагдсан захиалгад материал олгохгүй")
    total = 0
    for item, qty in issues:
        move = inventory.issue(session, order.company_id, item, move_date,
                               qty, target_account=WIP_ACCOUNT,
                               ref=f"WO {order.order_no}")
        total += move.cost_minor
    order.material_minor += total
    order.status = OrderStatus.in_progress
    session.flush()
    return total


def add_labor(session: Session, order: WorkOrder, amount_minor: int,
              entry_date: date, memo: str = "",
              credit_account: str | None = None) -> None:
    """Шууд хөдөлмөр — Дт 2145 / Кт 7101 (цалингийн зардлаас ДҮ рүү шингээлт).

    credit_account-аар өөр данс (жишээ нь гэрээт ажилчны 3101 өглөг) зааж
    болно, харин 3102 руу шууд бичихийг цалингийн модультай давхардахаас
    сэргийлж хориглоно.
    """
    if order.status == OrderStatus.closed:
        raise WipError("Хаагдсан захиалга")
    credit = credit_account or LABOR_ACCOUNT
    if credit == "3102":
        raise WipError(
            "Шууд хөдөлмөрийг 3102 (цалингийн өглөг) руу шууд бичихгүй — "
            "цалингийн модультай давхардана. 7101 зардлаас шингээнэ үү.")
    ledger.post_entry(session, order.company_id, entry_date, [
        ledger.LineInput(WIP_ACCOUNT, debit_minor=amount_minor,
                         description=f"WO {order.order_no} хөдөлмөр {memo}"),
        ledger.LineInput(credit, credit_minor=amount_minor,
                         description=f"WO {order.order_no} хөдөлмөр"),
    ], source_type=SourceType.wip, source_id=order.id)
    order.labor_minor += amount_minor
    order.status = OrderStatus.in_progress
    session.flush()


def absorption_status(session: Session, company_id: str,
                      account: str = OH_ACCOUNT,
                      as_of: date | None = None) -> dict:
    """Зардлын дансны бодит гарсан ба ДҮ рүү шингээсэн дүнг зэрэгцүүлнэ.

    Шингээлт нь тухайн дансны КРЕДИТ тал (Дт 2145 / Кт 7108), бодит зардал нь
    ДЕБИТ тал. Илүү шингээвэл (over-absorbed) данс сөрөг үлдэгдэлтэй болж
    СТ-2-т хиймэл ашиг үүсгэдэг тул хаалтын өмнө энэ зөрүүг хянана.
    """
    row = next((r for r in ledger.trial_balance(session, company_id, None, as_of)
                if r["code"] == account), None)
    actual = row["debit_minor"] if row else 0
    absorbed = row["credit_minor"] if row else 0
    return {
        "account": account,
        "actual_minor": actual,
        "absorbed_minor": absorbed,
        "variance_minor": actual - absorbed,   # + дутуу шингээсэн, − илүү
        "over_absorbed": absorbed > actual,
    }


def apply_overhead(session: Session, order: WorkOrder, amount_minor: int,
                   entry_date: date, strict: bool = False) -> dict:
    """НЗ хуваарилалт — Дт 2145 / Кт 7108 (зардлаас ДҮ рүү шингээлт).

    strict=True үед бодит гарсан зардлаас илүү шингээхийг зогсооно. Урьдчилсан
    нормоор шингээдэг тохиолдолд түр илүү/дутуу гарч болох тул анхдагчаар
    зөвхөн сэрэмжлүүлэг (over_absorbed) буцаана.
    """
    if order.status == OrderStatus.closed:
        raise WipError("Хаагдсан захиалга")

    # strict үед бичилт хийхээс ӨМНӨ шалгана — татгалзсан гүйлгээ дэвтэрт үлдэхгүй
    if strict:
        before = absorption_status(session, order.company_id, OH_ACCOUNT)
        if before["absorbed_minor"] + amount_minor > before["actual_minor"]:
            raise WipError(
                f"Илүү шингээлт: бодит НЗ {before['actual_minor'] / 100:,.0f}₮, "
                f"шингээх гэж буй нийт "
                f"{(before['absorbed_minor'] + amount_minor) / 100:,.0f}₮")

    ledger.post_entry(session, order.company_id, entry_date, [
        ledger.LineInput(WIP_ACCOUNT, debit_minor=amount_minor,
                         description=f"WO {order.order_no} НЗ хуваарилалт"),
        ledger.LineInput(OH_ACCOUNT, credit_minor=amount_minor,
                         description=f"WO {order.order_no} НЗ"),
    ], source_type=SourceType.wip, source_id=order.id)
    order.overhead_minor += amount_minor
    session.flush()

    return absorption_status(session, order.company_id, OH_ACCOUNT)


def complete(session: Session, order: WorkOrder, qty: int,
             entry_date: date, warehouse_id: str | None = None) -> int:
    """Хүлээлгэн өгөлт (бүрэн/хэсэгчилсэн) — Дт 2151 / Кт 2145.

    Хэсэгчилсэн өртөг = ҮЛДСЭН ДҮ үлдэгдэл × qty / ҮЛДСЭН төлөвлөсөн тоо.
    Төлөвлөсөн НИЙТ тоогоор хуваавал хожим нэмэгдсэн зардал аль хэдийн
    шилжсэн нэгжүүдэд хуваарилагдаж чадахгүй тул үлдэгдэл нь сүүлийн
    нэгжүүд дээр овоорч, ижил бүтээгдэхүүний нэгжийн өртөг хэд дахин
    зөрдөг байсан. Үлдэгдлээр хуваахад нэгжийн өртөг тэгшилнэ.
    """
    if order.status == OrderStatus.closed:
        raise WipError("Аль хэдийн хаагдсан")
    if qty <= 0 or order.qty_completed + qty > order.qty_planned:
        raise WipError(f"Тоо буруу: {order.qty_completed}+{qty}/{order.qty_planned}")

    final = order.qty_completed + qty == order.qty_planned
    if final:
        cost = order.wip_balance_minor          # үлдсэн бүх өртөг
    else:
        qty_remaining = order.qty_planned - order.qty_completed
        cost = order.wip_balance_minor * qty // qty_remaining

    if cost <= 0:
        # Ledger 0 дүнтэй бичилт хүлээж авдаггүй тул ойлгомжгүй
        # UnbalancedEntryError гарахаас өмнө шалтгааныг нь хэлнэ
        raise WipError(
            f"WO {order.order_no}: хуримтлагдсан өртөг байхгүй — материал/"
            f"хөдөлмөр/НЗ бүртгэсний дараа хүлээлгэн өгнө үү")

    entry = ledger.post_entry(session, order.company_id, entry_date, [
        ledger.LineInput(FG_ACCOUNT, debit_minor=cost,
                         description=f"WO {order.order_no} бэлэн болов x{qty}"),
        ledger.LineInput(WIP_ACCOUNT, credit_minor=cost,
                         description=f"WO {order.order_no} ДҮ хаалт"),
    ], source_type=SourceType.wip, source_id=order.id)

    # Бэлэн бүтээгдэхүүний агуулахад орлого. GL аль хэдийн бичигдсэн тул
    # inventory.receive дуудахгүй (давхар бичилт болно), харин FIFO багц ба
    # хөдөлгөөнийг гараар үүсгэнэ — эдгээргүй бол үйлдвэрлэсэн бүтээгдэхүүн
    # inventory.issue-ээр борлуулагдахгүй, барааны картад ч харагдахгүй.
    product = session.get(inventory.Item, order.product_item_id)
    product.qty += qty
    product.total_cost_minor += cost

    session.add(inventory.StockBatch(
        company_id=order.company_id, item_id=product.id,
        warehouse_id=warehouse_id, entry_date=entry_date,
        original_qty=qty, remaining_qty=qty,
        unit_cost_minor=cost // qty if qty else 0))
    session.add(inventory.StockMove(
        company_id=order.company_id, item_id=product.id,
        kind=inventory.MoveKind.receipt, move_date=entry_date,
        qty=qty, cost_minor=cost, journal_entry_id=entry.id,
        ref=f"WO {order.order_no}", warehouse_id=warehouse_id))

    order.qty_completed += qty
    order.transferred_minor += cost
    if final:
        order.status = OrderStatus.closed
    session.flush()
    return cost


def wip_balance_report(session: Session, company_id: str) -> list[dict]:
    """Нээлттэй захиалгуудын ДҮ үлдэгдэл — СТ-1-ийн 2145-тай тулгагдана."""
    orders = session.scalars(select(WorkOrder).where(
        WorkOrder.company_id == company_id)).all()
    return [{
        "id": o.id,
        "order_no": o.order_no, "status": o.status.value,
        "qty": f"{o.qty_completed}/{o.qty_planned}",
        "material_minor": o.material_minor, "labor_minor": o.labor_minor,
        "overhead_minor": o.overhead_minor,
        "wip_balance_minor": o.wip_balance_minor,
    } for o in orders]
