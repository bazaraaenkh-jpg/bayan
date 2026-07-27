"""Эхний үлдэгдэл импортлогч — 1С эсвэл BAAZ системээс экспортолсон Excel үлдэгдлийг уншиж журналын бичилт үүсгэнэ.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .models import Account
from .amounts import parse_amount

# Calamine support
try:
    from python_calamine import CalamineWorkbook
    HAS_CALAMINE = True
except ImportError:
    HAS_CALAMINE = False

from openpyxl import load_workbook

logger = logging.getLogger(__name__)


def _clean_code(val: Any) -> str:
    """Дансны кодыг цэвэрлэж цэг болон хоосон зайг арилгана (жишээ нь 1001.01 -> 100101)."""
    if val is None:
        return ""
    text = str(val).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.replace(".", "").replace(" ", "").strip()


def import_opening_balances(session: Session, company_id: str, file_path: Path, opening_date: date) -> dict:
    """1С / BAAZ Excel файлаас дансны үлдэгдлийг уншиж эхний үлдэгдлийн журналын бичилт үүсгэнэ."""
    
    # 1. Excel унших (Calamine-ийг илүүд үзэж, openpyxl-ийг fallback болгоно)
    grid: list[list[Any]] = []
    if HAS_CALAMINE:
        try:
            wb = CalamineWorkbook.from_path(str(file_path))
            grid = wb.get_sheet_by_name(wb.sheet_names[0]).to_python()
        except Exception as e:
            logger.warning("Calamine failed to load opening balances file: %s", e)

    if not grid:
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            grid = [[cell.value for cell in row] for row in ws.iter_rows()]
        finally:
            wb.close()

    # 2. Толгойн мөрийг олох (Данс/Код, Дебит, Кредит гэсэн үгс орсон)
    header_row_idx = None
    code_col = None
    debit_col = None
    credit_col = None

    for r_idx, row in enumerate(grid[:100]):
        row_str = [str(cell).strip().lower() for cell in row if cell is not None]
        # Багануудыг ухаалгаар хайх
        temp_code = None
        temp_debit = None
        temp_credit = None

        for c_idx, val in enumerate(row):
            if val is None:
                continue
            val_str = str(val).strip().lower()
            if "код" in val_str or "данс" in val_str:
                temp_code = c_idx
            elif "дебит" in val_str or "дэв" in val_str or "дүн" in val_str and "авлага" in val_str:
                temp_debit = c_idx
            elif "кредит" in val_str or "кре" in val_str or "дүн" in val_str and "өглөг" in val_str:
                temp_credit = c_idx

        if temp_code is not None and (temp_debit is not None or temp_credit is not None):
            header_row_idx = r_idx
            code_col = temp_code
            debit_col = temp_debit if temp_debit is not None else temp_credit + 1
            credit_col = temp_credit if temp_credit is not None else temp_debit + 1
            break

    if header_row_idx is None or code_col is None:
        raise ValueError("Excel файлаас 'Дансны код/Данс', 'Дебит', 'Кредит' баганыг таньж чадсангүй.")

    # 3. Мөрүүдийг уншиж үлдэгдлийн бичилт бэлдэх
    lines: list[ledger.LineInput] = []
    total_debit = 0
    total_credit = 0

    # Одоо байгаа компанийн данснуудын жагсаалт
    accounts = {a.code: a for a in session.scalars(
        select(Account).where(Account.company_id == company_id, Account.is_postable == True)
    ).all()}

    for r_idx in range(header_row_idx + 1, len(grid)):
        row = grid[r_idx]
        if len(row) <= code_col:
            continue
            
        raw_code = row[code_col]
        code = _clean_code(raw_code)
        if not code or not code.isdigit():
            # Дансны код биш бол алгасна (жишээ нь нийлбэр дүн эсвэл хоосон мөр)
            continue

        # Зөвхөн бүртгэгдсэн дансуудыг уншина
        if code not in accounts:
            logger.info("Дансны төлөвлөгөөнд байхгүй кодыг алгаслаа: %s", code)
            continue

        raw_debit = row[debit_col] if debit_col < len(row) else None
        raw_credit = row[credit_col] if credit_col < len(row) else None

        debit_val = parse_amount(raw_debit) or 0
        credit_val = parse_amount(raw_credit) or 0

        if debit_val == 0 and credit_val == 0:
            continue

        if debit_val > 0:
            lines.append(ledger.LineInput(account_code=code, debit_minor=debit_val, description="Эхний үлдэгдэл"))
            total_debit += debit_val
        if credit_val > 0:
            lines.append(ledger.LineInput(account_code=code, credit_minor=credit_val, description="Эхний үлдэгдэл"))
            total_credit += credit_val

    if not lines:
        raise ValueError("Excel файлаас ямар нэгэн дансны үлдэгдэл олдсонгүй.")

    # 4. Дебит болон Кредит тэнцэж буйг шалгана (G1 инвариант)
    if total_debit != total_credit:
        # Зөрүүг хэрэглэгчид тодорхой мэдээлнэ
        diff = abs(total_debit - total_credit)
        raise ValueError(
            f"Баланс зөрүүтэй байна! Нийт Дебит: {total_debit/100:,.2f}₮, "
            f"Нийт Кредит: {total_credit/100:,.2f}₮. Зөрүү: {diff/100:,.2f}₮"
        )

    # 5. Гүйлгээг илгээнэ
    entry = ledger.post_entry(
        session, company_id, opening_date, lines,
        source_type=ledger.SourceType.manual,
        memo="Эхний үлдэгдэл импортоор оруулав"
    )

    return {
        "entry_id": entry.id,
        "lines_count": len(lines),
        "total_minor": total_debit,
        "entry_no": entry.entry_no
    }


def import_inventory_from_excel(session: Session, company_id: str, file_path: Path, opening_date: date) -> dict:
    """Excel хуудаснаас бараа болон тэдгээрийн эхний үлдэгдлийг импортлон оруулах."""
    from . import inventory
    
    grid: list[list[Any]] = []
    if HAS_CALAMINE:
        try:
            wb = CalamineWorkbook.from_path(str(file_path))
            grid = wb.get_sheet_by_name(wb.sheet_names[0]).to_python()
        except Exception:
            pass

    if not grid:
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            grid = [[cell.value for cell in row] for row in ws.iter_rows()]
        finally:
            wb.close()

    header_row_idx = None
    code_col = None
    name_col = None
    qty_col = None
    cost_col = None
    cur_col = None
    rate_col = None
    duty_col = None
    vat_col = None
    freight_col = None
    date_col = None
    barcode_col = None
    wh_col = None

    for r_idx, row in enumerate(grid[:20]):
        for c_idx, val in enumerate(row):
            if val is None:
                continue
            val_str = str(val).strip().lower()
            if "код" in val_str:
                code_col = c_idx
            elif "нэр" in val_str or "бараа" in val_str:
                name_col = c_idx
            elif "тоо" in val_str or "ширхэг" in val_str or "хэмжээ" in val_str:
                qty_col = c_idx
            elif "өртөг" in val_str or "дүн" in val_str or "үнэ" in val_str:
                cost_col = c_idx
            elif "валют" in val_str:
                cur_col = c_idx
            elif "ханш" in val_str:
                rate_col = c_idx
            elif "гааль" in val_str:
                duty_col = c_idx
            elif "нөат" in val_str or "ноат" in val_str:
                vat_col = c_idx
            elif "тээвэр" in val_str or "нэмэлт" in val_str or "бусад" in val_str:
                freight_col = c_idx
            elif "огноо" in val_str or "date" in val_str:
                date_col = c_idx
            elif "баркод" in val_str or "бар код" in val_str or "barcode" in val_str:
                barcode_col = c_idx
            elif "агуулах" in val_str:
                wh_col = c_idx

        if code_col is not None and name_col is not None:
            header_row_idx = r_idx
            break

    if header_row_idx is None:
        code_col, name_col, qty_col, cost_col = 0, 1, 2, 3
        header_row_idx = 0

    added = 0
    received = 0
    total_vat = 0
    
    default_wh = session.scalar(select(inventory.Warehouse).where(inventory.Warehouse.company_id == company_id))
    if not default_wh:
        default_wh = inventory.Warehouse(company_id=company_id, code="WH01", name="Төв агуулах")
        session.add(default_wh)
        session.flush()

    for row in grid[header_row_idx + 1:]:
        if len(row) <= max(code_col, name_col):
            continue
        code = _clean_code(row[code_col])
        if not code:
            continue
        name = str(row[name_col]).strip() if name_col < len(row) and row[name_col] is not None else f"Бараа {code}"
        
        barcode = str(row[barcode_col]).strip() if barcode_col is not None and barcode_col < len(row) and row[barcode_col] is not None else None
        item = session.scalar(select(inventory.Item).where(inventory.Item.company_id == company_id, inventory.Item.code == code))
        if not item:
            item = inventory.Item(company_id=company_id, code=code, name=name, unit="ш", gl_account="2101", barcode=barcode)
            session.add(item)
            session.flush()
            added += 1
        elif barcode:
            item.barcode = barcode
            
        qty = int(parse_amount(row[qty_col]) / 100) if qty_col is not None and qty_col < len(row) and row[qty_col] is not None else 0
        base_cost = int(parse_amount(row[cost_col])) if cost_col is not None and cost_col < len(row) and row[cost_col] is not None else 0
        
        # Row date
        row_date = opening_date
        if date_col is not None and date_col < len(row) and row[date_col] is not None:
            val = row[date_col]
            if isinstance(val, (date, datetime)):
                row_date = val.date() if hasattr(val, "date") else val
            else:
                try:
                    row_date = date.fromisoformat(str(val).strip()[:10])
                except Exception:
                    pass

        # Currency & exchange rate conversion
        currency = str(row[cur_col]).strip().upper() if cur_col is not None and cur_col < len(row) and row[cur_col] is not None else "MNT"
        rate = float(row[rate_col]) if rate_col is not None and rate_col < len(row) and row[rate_col] is not None else 1.0
        if currency != "MNT" and rate > 0:
            base_cost = int(base_cost * rate)
            
        # Customs duty
        duty = int(parse_amount(row[duty_col])) if duty_col is not None and duty_col < len(row) and row[duty_col] is not None else 0
        
        # Freight / shipping / additional cost
        freight = int(parse_amount(row[freight_col])) if freight_col is not None and freight_col < len(row) and row[freight_col] is not None else 0
        
        # VAT
        vat = int(parse_amount(row[vat_col])) if vat_col is not None and vat_col < len(row) and row[vat_col] is not None else 0
        total_vat += vat
        
        final_cost = base_cost + duty + freight
        
        if qty > 0:
            wh_id = default_wh.id
            if wh_col is not None and wh_col < len(row) and row[wh_col] is not None:
                wh_code = str(row[wh_col]).strip()
                wh = session.scalar(select(inventory.Warehouse).where(inventory.Warehouse.company_id == company_id, inventory.Warehouse.code == wh_code))
                if wh:
                    wh_id = wh.id
                    
            inventory.receive(session, company_id, item, row_date, qty, final_cost, credit_account="3101", ref="Эхний үлдэгдэл импорт (Excel)", warehouse_id=wh_id)
            received += 1

    # Post total import VAT if any
    if total_vat > 0:
        try:
            from . import ledger
            ledger.post_entry(session, company_id, opening_date, [
                ledger.LineInput("1203", debit_minor=total_vat, description="Импортын НӨАТ-ын авлага"),
                ledger.LineInput("3101", credit_minor=total_vat, description="Импортын НӨАТ-ын авлага"),
            ], source_type=ledger.SourceType.manual, memo="Импортын НӨАТ-ын авлага бүртгэв (Excel импорт)")
        except Exception:
            pass

    return {"items_added": added, "balances_imported": received}


def import_assets_from_excel(session: Session, company_id: str, file_path: Path) -> dict:
    """Excel хуудаснаас үндсэн хөрөнгийн жагсаалт ба элэгдлийг бүртгэх."""
    from . import assets
    
    grid: list[list[Any]] = []
    if HAS_CALAMINE:
        try:
            wb = CalamineWorkbook.from_path(str(file_path))
            grid = wb.get_sheet_by_name(wb.sheet_names[0]).to_python()
        except Exception:
            pass

    if not grid:
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            grid = [[cell.value for cell in row] for row in ws.iter_rows()]
        finally:
            wb.close()

    header_row_idx = None
    code_col = None
    name_col = None
    cost_col = None
    life_col = None
    date_col = None

    for r_idx, row in enumerate(grid[:20]):
        for c_idx, val in enumerate(row):
            if val is None:
                continue
            val_str = str(val).strip().lower()
            if "код" in val_str:
                code_col = c_idx
            elif "нэр" in val_str or "хөрөнгө" in val_str:
                name_col = c_idx
            elif "өртөг" in val_str or "дүн" in val_str or "үнэ" in val_str:
                cost_col = c_idx
            elif "хугацаа" in val_str or "сар" in val_str:
                life_col = c_idx
            elif "огноо" in val_str or "ашиглалтад" in val_str:
                date_col = c_idx

        if code_col is not None and name_col is not None:
            header_row_idx = r_idx
            break

    if header_row_idx is None:
        code_col, name_col, cost_col, life_col = 0, 1, 2, 3
        header_row_idx = 0

    added = 0
    for row in grid[header_row_idx + 1:]:
        if len(row) <= max(code_col, name_col):
            continue
        code = _clean_code(row[code_col])
        if not code:
            continue
        name = str(row[name_col]).strip() if name_col < len(row) and row[name_col] is not None else f"Хөрөнгө {code}"
        
        asset = session.scalar(select(assets.Asset).where(assets.Asset.company_id == company_id, assets.Asset.code == code))
        if not asset:
            cost = int(parse_amount(row[cost_col])) if cost_col is not None and cost_col < len(row) and row[cost_col] is not None else 0
            life = int(parse_amount(row[life_col]) / 100) if life_col is not None and life_col < len(row) and row[life_col] is not None else 36
            if life <= 0:
                life = 36
            
            in_service_date = date.today()
            if date_col is not None and date_col < len(row) and row[date_col] is not None:
                try:
                    in_service_date = date.fromisoformat(str(row[date_col]).strip().split(" ")[0])
                except Exception:
                    pass
                    
            assets.register_asset(session, company_id, code, name, cost, life, in_service_date)
            added += 1

    return {"assets_added": added}


def import_inventory_issue_from_excel(session, company_id: str, file_path: Path) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(file_path, data_only=True)
    ws = wb.active
    
    grid = []
    for r in ws.iter_rows(values_only=True):
        grid.append(list(r))
    wb.close()
    
    header_row_idx = None
    date_col = None
    code_col = None
    qty_col = None
    target_col = None
    wh_col = None
    
    for r_idx, row in enumerate(grid[:20]):
        for c_idx, val in enumerate(row):
            if val is None:
                continue
            val_str = str(val).strip().lower()
            if "огноо" in val_str or "date" in val_str:
                date_col = c_idx
            elif "код" in val_str:
                code_col = c_idx
            elif "тоо" in val_str or "ширхэг" in val_str or "хэмжээ" in val_str:
                qty_col = c_idx
            elif "данс" in val_str or "харьцах" in val_str:
                target_col = c_idx
            elif "агуулах" in val_str:
                wh_col = c_idx
                
        if code_col is not None and qty_col is not None:
            header_row_idx = r_idx
            break
            
    if header_row_idx is None:
        date_col, code_col, qty_col, target_col = 0, 1, 2, 3
        header_row_idx = 0
        
    issued_count = 0
    total_cost_minor = 0
    
    from . import inventory
    
    for row in grid[header_row_idx + 1:]:
        if len(row) <= max(code_col, qty_col):
            continue
            
        code = _clean_code(row[code_col])
        if not code:
            continue
            
        item = session.scalar(select(inventory.Item).where(
            inventory.Item.company_id == company_id,
            inventory.Item.code == code
        ))
        if not item:
            continue
            
        qty = int(parse_amount(row[qty_col]) / 100) if qty_col is not None and qty_col < len(row) and row[qty_col] is not None else 0
        if qty <= 0:
            continue
            
        target_account = "6101"
        if target_col is not None and target_col < len(row) and row[target_col] is not None:
            target_account = str(row[target_col]).strip().split(" ")[0]
            
        # Parse date
        move_date = date.today()
        if date_col is not None and date_col < len(row) and row[date_col] is not None:
            val = row[date_col]
            if isinstance(val, (date, datetime)):
                move_date = val.date() if hasattr(val, "date") else val
            else:
                try:
                    move_date = date.fromisoformat(str(val).strip().split(" ")[0])
                except Exception:
                    pass
                    
        warehouse_code = None
        if wh_col is not None and wh_col < len(row) and row[wh_col] is not None:
            warehouse_code = str(row[wh_col]).strip()
            
        warehouse_id = None
        if warehouse_code:
            wh = session.scalar(select(inventory.Warehouse).where(
                inventory.Warehouse.company_id == company_id,
                inventory.Warehouse.code == warehouse_code
            ))
            if wh:
                warehouse_id = wh.id
                
        try:
            mv = inventory.issue(session, company_id, item, move_date, qty, target_account, warehouse_id=warehouse_id)
            issued_count += 1
            total_cost_minor += mv.cost_minor
        except Exception:
            pass
            
    return {"issued_count": issued_count, "total_cost_minor": total_cost_minor}


