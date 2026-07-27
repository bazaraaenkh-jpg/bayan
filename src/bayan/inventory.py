"""Бараа материал — Дундаж ба FIFO өртөг бодолт, олон агуулах болон дэлгэрэнгүй карт тайлан.

  * Орлого:  Дт 2101 (бараа) / Кт 3101 (дансны өглөг)
  * Зарлага: Дт 6101 (ББӨ) / Кт 2101 (бараа)
  * Вариант: Дундаж ба FIFO аргыг компанийн тохиргооноос хамааран сонгоно.
  * Олон агуулахын бүртгэл болон шилжүүлэг.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date
from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Integer, String, select, func, Date
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import ledger
from .models import Base, SourceType, Company


def _uuid() -> str:
    return str(uuid.uuid4())


class Warehouse(Base):
    """Агуулахын бүртгэл."""
    __tablename__ = "warehouse"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Item(Base):
    __tablename__ = "inventory_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(16), default="ш")
    gl_account: Mapped[str] = mapped_column(String(16), default="2101")
    qty: Mapped[int] = mapped_column(BigInteger, default=0)
    total_cost_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    reorder_point: Mapped[int] = mapped_column(BigInteger, default=0)
    batch_no: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, default=None, nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True)

    @property
    def avg_cost_minor(self) -> int:
        return self.total_cost_minor // self.qty if self.qty else 0


class StockBatch(Base):
    """FIFO багцын бүртгэл."""
    __tablename__ = "stock_batch"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouse.id"), nullable=True)
    entry_date: Mapped[date] = mapped_column()
    original_qty: Mapped[int] = mapped_column(BigInteger)
    remaining_qty: Mapped[int] = mapped_column(BigInteger)
    unit_cost_minor: Mapped[int] = mapped_column(BigInteger)


class MoveKind(str, enum.Enum):
    receipt = "receipt"
    issue = "issue"


class StockMove(Base):
    __tablename__ = "stock_move"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_item.id"))
    kind: Mapped[MoveKind] = mapped_column(Enum(MoveKind))
    move_date: Mapped[date] = mapped_column()
    qty: Mapped[int] = mapped_column(BigInteger)
    cost_minor: Mapped[int] = mapped_column(BigInteger)
    target_account: Mapped[str | None] = mapped_column(String(16))
    journal_entry_id: Mapped[str | None] = mapped_column(String(36))
    ref: Mapped[str | None] = mapped_column(String(64))
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouse.id"), nullable=True)


class InventoryError(Exception):
    pass


def receive(session: Session, company_id: str, item: Item, move_date: date,
            qty: int, total_cost_minor: int, credit_account: str = "3101",
            ref: str | None = None, warehouse_id: str | None = None) -> StockMove:
    """Орлого авах."""
    if qty <= 0 or total_cost_minor < 0:
        raise InventoryError("Тоо/өртөг буруу")

    # Журналын бичилт үүсгэх
    entry = ledger.post_entry(session, company_id, move_date, [
        ledger.LineInput(item.gl_account, debit_minor=total_cost_minor,
                         description=f"БМ орлого {item.code} x{qty}"),
        ledger.LineInput(credit_account, credit_minor=total_cost_minor,
                         description=f"БМ орлого {item.code}"),
    ], source_type=SourceType.inventory, memo=ref)

    # Item шинэчлэх
    item.qty += qty
    item.total_cost_minor += total_cost_minor

    # FIFO багц үүсгэх
    unit_cost = total_cost_minor // qty if qty > 0 else 0
    batch = StockBatch(
        company_id=company_id, item_id=item.id, warehouse_id=warehouse_id,
        entry_date=move_date, original_qty=qty, remaining_qty=qty,
        unit_cost_minor=unit_cost
    )
    session.add(batch)

    # Хөдөлгөөн үүсгэх
    move = StockMove(
        company_id=company_id, item_id=item.id, kind=MoveKind.receipt,
        move_date=move_date, qty=qty, cost_minor=total_cost_minor,
        journal_entry_id=entry.id, ref=ref, warehouse_id=warehouse_id
    )
    session.add(move)
    session.flush()
    return move


def issue(session: Session, company_id: str, item: Item, move_date: date,
          qty: int, target_account: str = "6101",
          ref: str | None = None, warehouse_id: str | None = None) -> StockMove:
    from .models import ItemKit
    kits = session.scalars(select(ItemKit).where(ItemKit.company_id == company_id, ItemKit.parent_item_id == item.id)).all()
    if kits:
        moves = []
        for k in kits:
            child = session.get(Item, k.child_item_id)
            if child:
                comp_qty = int(round(qty * k.quantity))
                m = issue(session, company_id, child, move_date, comp_qty, target_account, f"{ref or ''} (Сет: {item.code})", warehouse_id)
                moves.append(m)
        return moves[0] if moves else None

    if qty <= 0 or qty > item.qty:
        raise InventoryError(f"{item.code}: үлдэгдэл {item.qty}, шаардсан {qty}")

    comp = session.get(Company, company_id)
    method = comp.inventory_method if comp else "average"

    # Агуулах шүүх багцуудыг олох
    q = select(StockBatch).where(
        StockBatch.company_id == company_id,
        StockBatch.item_id == item.id,
        StockBatch.remaining_qty > 0
    )
    if warehouse_id:
        q = q.where(StockBatch.warehouse_id == warehouse_id)
    q = q.order_by(StockBatch.entry_date.asc(), StockBatch.id.asc())
    batches = session.scalars(q).all()

    available = sum(b.remaining_qty for b in batches)
    if qty > available:
        raise InventoryError(f"{item.code}: агуулахад үлдэгдэл хүрэлцэхгүй байна (Багцаар: {available}, Хүссэн: {qty})")

    needed = qty
    cost = 0

    if method == "fifo":
        # FIFO дарааллаар багцуудаас суутгана
        for b in batches:
            if needed <= 0:
                break
            take = min(b.remaining_qty, needed)
            b.remaining_qty -= take
            cost += take * b.unit_cost_minor
            needed -= take
    else:
        # Дундаж өртгөөр бодно
        cost = item.total_cost_minor * qty // item.qty
        if qty == item.qty:
            cost = item.total_cost_minor
        # Тоо хэмжээг зөв байлгахын тулд багцуудыг бас дарааллаар нь суутгана
        for b in batches:
            if needed <= 0:
                break
            take = min(b.remaining_qty, needed)
            b.remaining_qty -= take
            needed -= take

    # Журналын бичилт үүсгэх
    entry = ledger.post_entry(session, company_id, move_date, [
        ledger.LineInput(target_account, debit_minor=cost,
                         description=f"БМ зарлага {item.code} x{qty}"),
        ledger.LineInput(item.gl_account, credit_minor=cost,
                         description=f"БМ зарлага {item.code}"),
    ], source_type=SourceType.inventory, memo=ref)

    # Item шинэчлэх
    item.qty -= qty
    item.total_cost_minor -= cost

    # Хөдөлгөөн үүсгэх
    move = StockMove(
        company_id=company_id, item_id=item.id, kind=MoveKind.issue,
        move_date=move_date, qty=qty, cost_minor=cost,
        target_account=target_account, journal_entry_id=entry.id,
        ref=ref, warehouse_id=warehouse_id
    )
    session.add(move)
    session.flush()
    return move


def transfer(session: Session, company_id: str, item: Item, from_warehouse_id: str,
             to_warehouse_id: str, qty: int, move_date: date, ref: str | None = None) -> list[StockMove]:
    """Агуулах хооронд бараа шилжүүлэх (Сорсоос хасаад, гарсан өртгөөр нь таргет руу нэмнэ)."""
    if qty <= 0:
        raise InventoryError("Шилжүүлэх тоо хэмжээ буруу")

    comp = session.get(Company, company_id)
    method = comp.inventory_method if comp else "average"

    # Сорс агуулахын үлдэгдлийг олно
    q = select(StockBatch).where(
        StockBatch.company_id == company_id,
        StockBatch.item_id == item.id,
        StockBatch.warehouse_id == from_warehouse_id,
        StockBatch.remaining_qty > 0
    ).order_by(StockBatch.entry_date.asc(), StockBatch.id.asc())
    batches = session.scalars(q).all()

    available = sum(b.remaining_qty for b in batches)
    if qty > available:
        raise InventoryError(f"{item.code}: Шилжүүлэх үлдэгдэл хүрэлцэхгүй байна. Сорсод байгаа: {available}, Шилжүүлэх: {qty}")

    needed = qty
    total_cost = 0
    consumed_batches = []

    # Сорсоос хасна
    for b in batches:
        if needed <= 0:
            break
        take = min(b.remaining_qty, needed)
        
        # Багцын өртөг бодолт
        if method == "fifo":
            batch_cost = take * b.unit_cost_minor
        else:
            batch_cost = item.total_cost_minor * take // item.qty
            
        b.remaining_qty -= take
        total_cost += batch_cost
        needed -= take
        consumed_batches.append((take, b.unit_cost_minor))

    # Таргет руу орлого авна (шинэ багцууд үүсгэнэ)
    for c_qty, c_unit_cost in consumed_batches:
        new_batch = StockBatch(
            company_id=company_id, item_id=item.id, warehouse_id=to_warehouse_id,
            entry_date=move_date, original_qty=c_qty, remaining_qty=c_qty,
            unit_cost_minor=c_unit_cost
        )
        session.add(new_batch)

    # Журнал бичих (дансны код нь ижил байвал Дт/Кт ижил тул баланс өөрчлөхгүй ч түүх үүснэ)
    entry = ledger.post_entry(session, company_id, move_date, [
        ledger.LineInput(item.gl_account, debit_minor=total_cost,
                         description=f"Шилжүүлэг (Орлого) {item.code} x{qty}"),
        ledger.LineInput(item.gl_account, credit_minor=total_cost,
                         description=f"Шилжүүлэг (Зарлага) {item.code}"),
    ], source_type=SourceType.inventory, memo=ref)

    # Хөдөлгөөнүүд
    move_out = StockMove(
        company_id=company_id, item_id=item.id, kind=MoveKind.issue,
        move_date=move_date, qty=qty, cost_minor=total_cost,
        warehouse_id=from_warehouse_id, journal_entry_id=entry.id, ref=ref
    )
    move_in = StockMove(
        company_id=company_id, item_id=item.id, kind=MoveKind.receipt,
        move_date=move_date, qty=qty, cost_minor=total_cost,
        warehouse_id=to_warehouse_id, journal_entry_id=entry.id, ref=ref
    )
    session.add(move_out)
    session.add(move_in)
    session.flush()

    return [move_out, move_in]


def detailed_card_report(session: Session, company_id: str, item_id: str,
                         date_from: date, date_to: date) -> dict:
    """Барааны дэлгэрэнгүй карт тайлан (Эхний үлдэгдэл, орлого, зарлага, эцсийн үлдэгдэл)."""
    item = session.get(Item, item_id)
    if not item or item.company_id != company_id:
        raise InventoryError("Бараа олдсонгүй")

    # 1. Эхний үлдэгдэл (date_from-оос өмнөх бүх хөдөлгөөний нийлбэр)
    q_open = select(StockMove).where(
        StockMove.item_id == item_id,
        StockMove.move_date < date_from
    )
    moves_open = session.scalars(q_open).all()
    open_qty = 0
    open_cost_minor = 0
    for m in moves_open:
        if m.kind == MoveKind.receipt:
            open_qty += m.qty
            open_cost_minor += m.cost_minor
        else:
            open_qty -= m.qty
            open_cost_minor -= m.cost_minor

    # 2. Тайлант үеийн хөдөлгөөнүүд
    q_range = select(StockMove).where(
        StockMove.item_id == item_id,
        StockMove.move_date >= date_from,
        StockMove.move_date <= date_to
    ).order_by(StockMove.move_date.asc(), StockMove.id.asc())
    moves_range = session.scalars(q_range).all()

    lines = []
    curr_qty = open_qty
    curr_cost = open_cost_minor

    for m in moves_range:
        if m.kind == MoveKind.receipt:
            curr_qty += m.qty
            curr_cost += m.cost_minor
            lines.append({
                "date": m.move_date.isoformat(),
                "kind": "receipt",
                "qty_in": m.qty,
                "cost_in_minor": m.cost_minor,
                "qty_out": 0,
                "cost_out_minor": 0,
                "qty_bal": curr_qty,
                "cost_bal_minor": curr_cost,
                "ref": m.ref
            })
        else:
            curr_qty -= m.qty
            curr_cost -= m.cost_minor
            lines.append({
                "date": m.move_date.isoformat(),
                "kind": "issue",
                "qty_in": 0,
                "cost_in_minor": 0,
                "qty_out": m.qty,
                "cost_out_minor": m.cost_minor,
                "qty_bal": curr_qty,
                "cost_bal_minor": curr_cost,
                "ref": m.ref
            })

    return {
        "item_code": item.code,
        "item_name": item.name,
        "unit": item.unit,
        "open_qty": open_qty,
        "open_cost_minor": open_cost_minor,
        "lines": lines,
        "close_qty": curr_qty,
        "close_cost_minor": curr_cost
    }


def stock_report(session: Session, company_id: str) -> list[dict]:
    """Компанийн барааны үлдэгдлийн нэгдсэн тайлан."""
    items = session.scalars(select(Item).where(Item.company_id == company_id)).all()
    return [{
        "id": i.id, "code": i.code, "name": i.name, "unit": i.unit, "qty": i.qty,
        "avg_cost_minor": i.avg_cost_minor, "total_cost_minor": i.total_cost_minor,
        "barcode": i.barcode,
        "reorder_point": i.reorder_point,
        "batch_no": i.batch_no,
        "expiry_date": i.expiry_date.isoformat() if i.expiry_date else None,
    } for i in items]


def warehouse_stock_report(session: Session, company_id: str, warehouse_id: str | None = None) -> list[dict]:
    """Агуулахын үлдэгдлийн тайлан (StockBatch дээр суурилсан)."""
    # Item-үүдийн агуулахын үлдэгдлийг багцын дагуу нэгтгэнэ
    q = (
        select(
            Item.code, Item.name, Item.unit,
            func.coalesce(func.sum(StockBatch.remaining_qty), 0),
            func.coalesce(func.sum(StockBatch.remaining_qty * StockBatch.unit_cost_minor), 0)
        )
        .join(StockBatch, StockBatch.item_id == Item.id)
        .where(Item.company_id == company_id)
    )
    if warehouse_id:
        q = q.where(StockBatch.warehouse_id == warehouse_id)
        
    q = q.group_by(Item.code, Item.name, Item.unit).order_by(Item.code)
    
    res = []
    for code, name, unit, qty, total_cost in session.execute(q):
        avg_cost = int(total_cost // qty) if qty > 0 else 0
        res.append({
            "code": code,
            "name": name,
            "unit": unit,
            "qty": int(qty),
            "avg_cost_minor": avg_cost,
            "total_cost_minor": int(total_cost)
        })
    return res


def _get_or_create_transit_account(session: Session, company_id: str) -> str:
    from .models import Account, NormalSide
    acc = session.scalar(
        select(Account).where(Account.company_id == company_id, Account.code == "1502")
    )
    if not acc:
        parent = session.scalar(
            select(Account).where(Account.company_id == company_id, Account.code == "15")
        )
        if not parent:
            parent = Account(company_id=company_id, code="15", name="Бусад эргэлтийн хөрөнгө",
                             normal_side=NormalSide.debit, is_postable=False)
            session.add(parent)
            session.flush()
        acc = Account(
            company_id=company_id, code="1502", name="Замд яваа бараа материал",
            normal_side=NormalSide.debit, is_postable=True, parent_id=parent.id
        )
        session.add(acc)
        session.flush()
    return "1502"


def post_stocktake_variance(session: Session, company_id: str, item: Item,
                           actual_qty: int, move_date: date, ref: str | None = None) -> StockMove | None:
    """Тооллогын зөрүүг бүртгэж автомат журналын бичилт үүсгэх (Няравын санал 17)."""
    diff = actual_qty - item.qty
    if diff == 0:
        return None
        
    ref = ref or "Тооллогын зөрүү"
    if diff > 0:
        # Илүүдэл (Surplus): Dt 2101 / Kt 5105 (Бусад орлого)
        cost_minor = diff * item.avg_cost_minor
        if cost_minor <= 0:
            cost_minor = diff * 100  # 1₮ per unit
        return receive(session, company_id, item, move_date, qty=diff,
                       total_cost_minor=cost_minor, credit_account="5105", ref=ref)
    else:
        # Дуртагдал (Deficit): Dt 7199 (Бусад зардал) / Kt 2101
        return issue(session, company_id, item, move_date, qty=abs(diff),
                     target_account="7199", ref=ref)


def ship_transfer(session: Session, company_id: str, item: Item, from_warehouse_id: str,
                  qty: int, move_date: date, ref: str | None = None) -> StockMove:
    """Замд яваа барааны шилжүүлэг эхлүүлэх (Няравын санал 20): Dt 1502 / Kt 2101."""
    ref_str = f"TRANSIT:{ref or ''}"
    transit_acc = _get_or_create_transit_account(session, company_id)
    return issue(session, company_id, item, move_date, qty=qty,
                 target_account=transit_acc, ref=ref_str, warehouse_id=from_warehouse_id)


def receive_transfer(session: Session, company_id: str, transit_move_id: str,
                     to_warehouse_id: str, move_date: date) -> StockMove:
    """Замд яваа барааг хүлээн авах (Няравын санал 20): Dt 2101 / Kt 1502."""
    transit_move = session.get(StockMove, transit_move_id)
    if not transit_move or transit_move.company_id != company_id:
        raise InventoryError("Шилжүүлгийн гүйлгээ олдсонгүй")
    if transit_move.target_account != "1502":
        raise InventoryError("Энэ гүйлгээ нь замд яваа бараа биш байна")
        
    item = session.get(Item, transit_move.item_id)
    if not item:
        raise InventoryError("Бараа олдсонгүй")
        
    ref_str = f"RECEIVED:{transit_move.ref or ''}"
    transit_move.ref = f"SHIPPED:{transit_move.ref or ''}"
    transit_acc = _get_or_create_transit_account(session, company_id)
    
    return receive(session, company_id, item, move_date, qty=transit_move.qty,
                   total_cost_minor=transit_move.cost_minor, credit_account=transit_acc,
                   ref=ref_str, warehouse_id=to_warehouse_id)

