"""Зам А' — хуучин .XLS (BIFF) задлалт, xlrd-ээр (F7 асуудлын шийдэл).

ХХБ-ны бодит хуулгын онцлогуудыг дэмжинэ (бодит файлаар баталгаажсан):
  * Толгойн мөр байхгүй — багана descriptor-ийн index-ээр буулгана
  * Мета нь r0-ийн нүднүүдэд «Нэр: утга» хэлбэрээр шахагдсан (inline anchors)
  * Огноо Excel-ийн date cell (serial) — datetime болгож хөрвүүлнэ
  * Нийт дүнгийн мөр = баланс баганагүй мөр → алгасна
"""

from __future__ import annotations

import re
from pathlib import Path

import xlrd
from xlrd.xldate import xldate_as_datetime

from .amounts import parse_amount
from .extract_excel import ExtractionError, RawRow, RawStatement
from .registry import Descriptor


def scan_cells_xls(path: Path, max_rows: int = 5) -> list[str]:
    """Fingerprinting: эхний мөрүүдийн текст нүднүүд."""
    wb = xlrd.open_workbook(str(path))
    ws = wb.sheet_by_index(0)
    texts = []
    for r in range(min(max_rows, ws.nrows)):
        for c in range(ws.ncols):
            v = ws.cell(r, c).value
            if v not in ("", None):
                texts.append(str(v).strip())
    return texts


def _inline_metadata(ws, desc: Descriptor, locale: dict,
                     scan_rows: int = 3) -> dict:
    """«Нэр:  утга» хэлбэрийн мета — нүд бүрээс anchor хайна."""
    meta: dict = {}
    cells = []
    for r in range(min(scan_rows, ws.nrows)):
        for c in range(ws.ncols):
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.strip():
                cells.append(v.strip())

    for key, cfg in desc.metadata_block.items():
        anchor = cfg["anchor"]
        for text in cells:
            if anchor.lower() not in text.lower():
                continue
            after = text.split(":", 1)[1].strip() if ":" in text else text
            custom = cfg.get("regex")
            if custom:
                m = re.search(custom, after)
                if m:
                    after = m.group(1)
            if cfg.get("type") == "amount":
                meta[key + "_minor"] = parse_amount(
                    after, thousands=locale.get("thousands", ","),
                    decimal=locale.get("decimal", "."))
            else:
                meta[key] = after
            break
    return meta


def extract_xls(path: Path, desc: Descriptor) -> RawStatement:
    wb = xlrd.open_workbook(str(path))
    ws = wb.sheet_by_index(0)
    stmt = RawStatement(descriptor_id=desc.id)
    locale = desc.amount_locale

    stmt.metadata = _inline_metadata(ws, desc, locale)

    # Багана index-ээр буулгана (толгойгүй формат)
    colmap: dict[str, int] = {}
    for field_name, spec in desc.columns.items():
        if spec.index is None:
            if not spec.optional:
                raise ExtractionError(
                    f"{desc.id}: '{field_name}' баганад index заагаагүй")
            continue
        colmap[field_name] = spec.index

    data_start = desc.table.get("data_start_row", 1)
    require = desc.table.get("require_numeric", [])

    for r in range(data_start, ws.nrows):
        values: dict = {}
        skip = False
        for field_name, col in colmap.items():
            if col >= ws.ncols:
                values[field_name] = None
                continue
            cell = ws.cell(r, col)
            if cell.ctype == xlrd.XL_CELL_DATE:
                values[field_name] = xldate_as_datetime(cell.value, wb.datemode)
            elif cell.ctype == xlrd.XL_CELL_EMPTY:
                values[field_name] = None
            else:
                values[field_name] = cell.value
        # Нийт дүнгийн/хоосон мөр: шаардлагатай тоон багана байхгүй бол алгасна
        for req in require:
            v = values.get(req)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                skip = True
                break
        if skip or all(v in (None, "", 0.0) for v in values.values()):
            continue
        stmt.rows.append(RawRow(row_index=r + 1, values=values))

    return stmt
