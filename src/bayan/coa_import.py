"""Нягтлангийн өөрийн Excel дансны төлөвлөгөөг уншиж системд оруулна.

Шинэ хэрэглэгч бүр өөрийн олон жил хөтөлсөн дансны төлөвлөгөөтэй ирдэг.
Түүнийг гараар дахин бичүүлэх нь шилжилтийн хамгийн том саад тул файлын
багануудыг автоматаар таньж оруулна.

Загвар шаардахгүй: толгой мөрийг хайж, "код"/"нэр"/"дебит-кредит" утгатай
баганыг өөрөө олно. Толгой олдохгүй бол баганы агуулгаар таамаглана
(дансны код гэдэг нь ихэвчлэн 2-10 оронтой тоо).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------- толгой таних

_CODE_WORDS = ("код", "дугаар", "code", "acc", "данс.no", "дансны код")
_NAME_WORDS = ("нэр", "name", "тайлбар", "дансны нэр", "үзүүлэлт")
_SIDE_WORDS = ("тал", "side", "дебит", "кредит", "d/c", "дт/кт", "ердийн тал")

# Дансны код: 2-10 орон. Хэт урт нь регистр/утас байх магадлалтай.
_CODE_RE = re.compile(r"^\d{2,10}$")


@dataclass
class ImportResult:
    accounts: list[dict] = field(default_factory=list)
    header_row: int | None = None
    columns: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "accounts": self.accounts,
            "header_row": self.header_row,
            "columns": self.columns,
            "skipped": self.skipped[:20],
            "skipped_count": len(self.skipped),
        }


def read_grid(file_bytes: bytes, filename: str = "") -> list[list[str]]:
    """xlsx/xls/csv-г мөр, баганы хүснэгт болгож уншина."""
    name = (filename or "").lower()

    if not name.endswith((".csv", ".txt")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            return [[("" if c is None else str(c)).strip() for c in row]
                    for row in wb.active.iter_rows(values_only=True)]
        except Exception:
            pass
        try:
            import xlrd
            sh = xlrd.open_workbook(file_contents=file_bytes).sheet_by_index(0)
            return [[str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
                    for r in range(sh.nrows)]
        except Exception:
            pass

    import csv
    text = file_bytes.decode("utf-8-sig", errors="ignore")
    delim = ";" if text.count(";") > text.count(",") else ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _match(cell: str, words: tuple[str, ...]) -> bool:
    low = cell.lower()
    return any(w in low for w in words)


def _find_header(grid: list[list[str]]) -> tuple[int | None, dict[str, int]]:
    """Толгой мөрийг олж, багануудын байрлалыг тодорхойлно."""
    for idx, row in enumerate(grid[:25]):
        cols: dict[str, int] = {}
        for c, cell in enumerate(row):
            if not cell:
                continue
            if "code" not in cols and _match(cell, _CODE_WORDS):
                cols["code"] = c
            elif "name" not in cols and _match(cell, _NAME_WORDS):
                cols["name"] = c
            elif "side" not in cols and _match(cell, _SIDE_WORDS):
                cols["side"] = c
        if "code" in cols and "name" in cols:
            return idx, cols
    return None, {}


def _guess_columns(grid: list[list[str]]) -> dict[str, int]:
    """Толгой олдохгүй бол агуулгаар таамаглана.

    Кодын багана = дансны код шиг утга хамгийн олон агуулсан багана.
    Нэрийн багана = түүний баруун талын, текст агуулсан хамгийн ойрх багана."""
    width = max((len(r) for r in grid), default=0)
    if not width:
        return {}

    code_hits = [0] * width
    text_hits = [0] * width
    for row in grid:
        for c in range(min(len(row), width)):
            v = row[c]
            if not v:
                continue
            if _CODE_RE.match(v):
                code_hits[c] += 1
            elif len(v) >= 3 and not v.replace(".", "").replace(",", "").isdigit():
                text_hits[c] += 1

    if not any(code_hits):
        return {}
    code_col = code_hits.index(max(code_hits))
    right = [(c, text_hits[c]) for c in range(code_col + 1, width) if text_hits[c]]
    if right:
        name_col = max(right, key=lambda x: x[1])[0]
    else:
        others = [(c, text_hits[c]) for c in range(width) if c != code_col and text_hits[c]]
        if not others:
            return {}
        name_col = max(others, key=lambda x: x[1])[0]
    return {"code": code_col, "name": name_col}


def normal_side_for(code: str, hint: str = "") -> str:
    """Ердийн талыг тодорхойлно — файлд заасан бол түүнийг, үгүй бол кодоор.

    Монголын дансны бүлэглэлт: 1-2 хөрөнгө (дебит), 3-4 өр төлбөр ба өмч
    (кредит), 5 орлого (кредит), 6-7 өртөг ба зардал (дебит)."""
    low = (hint or "").lower()
    if "деб" in low or low.startswith("d") or "дт" in low:
        return "debit"
    if "кре" in low or low.startswith("c") or "кт" in low:
        return "credit"
    return "credit" if code[:1] in ("3", "4", "5") else "debit"


def parse(file_bytes: bytes, filename: str = "") -> ImportResult:
    """Файлаас дансны жагсаалтыг задална (өгөгдлийн санд хараахан бичихгүй)."""
    grid = read_grid(file_bytes, filename)
    res = ImportResult()
    if not grid:
        return res

    header_row, cols = _find_header(grid)
    if not cols:
        cols = _guess_columns(grid)
        header_row = None
    res.header_row = header_row
    res.columns = cols
    if "code" not in cols or "name" not in cols:
        return res

    start = (header_row + 1) if header_row is not None else 0
    seen: set[str] = set()
    for row in grid[start:]:
        if cols["code"] >= len(row):
            continue
        code = (row[cols["code"]] or "").strip()
        # Excel тоог "1101.0" гэж уншсан тохиолдлыг цэвэрлэнэ
        if code.endswith(".0") and code[:-2].isdigit():
            code = code[:-2]
        name = (row[cols["name"]] or "").strip() if cols["name"] < len(row) else ""

        if not _CODE_RE.match(code):
            if code or name:
                res.skipped.append(f"{code or '—'} {name}".strip())
            continue
        if not name:
            res.skipped.append(f"{code} (нэргүй)")
            continue
        if code in seen:
            res.skipped.append(f"{code} (давхардсан)")
            continue
        seen.add(code)

        hint = row[cols["side"]] if "side" in cols and cols["side"] < len(row) else ""
        res.accounts.append({
            "code": code,
            "name": name,
            "normal_side": normal_side_for(code, hint),
        })

    # Бүлгийн данс (өөр дансны угтвар болж байгаа) руу бичилт хийхийг хориглоно
    codes = {a["code"] for a in res.accounts}
    for a in res.accounts:
        a["is_postable"] = not any(
            other != a["code"] and other.startswith(a["code"]) for other in codes)
    return res


def apply(session, company_id: str, accounts: list[dict]) -> dict:
    """Задалсан данснуудыг компанид үүсгэнэ. Байгаа кодыг хөндөхгүй."""
    from sqlalchemy import select
    from .models import Account, NormalSide

    existing = {a.code: a for a in session.scalars(
        select(Account).where(Account.company_id == company_id))}

    created = 0
    for item in sorted(accounts, key=lambda x: x["code"]):
        if item["code"] in existing:
            continue
        parent = None
        for i in range(len(item["code"]) - 1, 0, -1):
            parent = existing.get(item["code"][:i])
            if parent is not None:
                break
        acc = Account(
            company_id=company_id, code=item["code"], name=item["name"],
            normal_side=NormalSide(item["normal_side"]),
            is_postable=item.get("is_postable", True),
            parent_id=parent.id if parent else None)
        session.add(acc)
        session.flush()
        existing[acc.code] = acc
        created += 1

    return {"created": created, "skipped_existing": len(accounts) - created,
            "total_after": len(existing)}
