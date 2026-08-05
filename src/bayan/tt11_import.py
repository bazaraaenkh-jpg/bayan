"""ТТ-11 (ХХОАТ-ын тайлан) маягтаас ажилтан ба цалингийн бүртгэл үүсгэнэ.

Систем ТТ-11-ийг гаргадаг байсан ч уншдаггүй байв. Өөр системээс шилжиж
буй нягтлан гарт нь байгаа ганц бүрэн эх сурвалж нь татварт тушаасан
ТТ-11 — ажилтан бүрийн орлого, НДШ, ХХОАТ нь тэнд бий. Түүнийг уншиж
ажилтны карт ба өнгөрсөн сарын цалингийн мөрийг сэргээнэ.

Загвар шаардахгүй: e-tax-аас татсан, нягтлангийн өөрийн, эсвэл энэ
системээс гарсан хувилбар — толгойн үгээр багануудыг өөрөө таьна.

Тэнцлийн зарчим: бохир = гарт олгох + ажилтны НДШ + ХХОАТ. Файлын
"гарт олгосон" багана энэ тэнцлээс зөрвөл зөрүүг мэдээлээд, журналын
бичилт тэнцэхийн тулд тооцоолсон утгыг хэрэглэнэ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .coa_import import read_grid

# --------------------------------------------------------------- толгой таних
#
# Дараалал ЧУХАЛ: тодорхой утгыг ерөнхийгөөс нь өмнө шалгана. Тухайлбал
# "Татвар ногдох орлого" нь "татвар" гэсэн үгийг агуулах тул ХХОАТ-аас
# өмнө, "Овог нэр" нь "нэр"-ийг агуулах тул нэрнээс өмнө таарах ёстой.
_COLUMN_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("register", ("регистр", "рег.", "рег №", "рд", "иргэний үнэмлэх")),
    ("full_name", ("овог нэр", "овог, нэр", "овог/нэр", "ажилтны нэр",
                   "ажиллагчийн нэр", "нэр /", "албан хаагчийн нэр")),
    ("last_name", ("овог",)),
    ("taxable", ("татвар ногдох", "ногдох орлого")),
    ("credit", ("хөнгөлөлт", "чөлөөлөлт")),
    ("hhoat", ("ххоат", "ногдуулсан татвар", "суутгасан татвар",
               "төлбөл зохих", "татвар")),
    ("ndsh", ("ндш", "нийгмийн даатгал", "шимтгэл", "ниг.даатгал")),
    ("net", ("гарт олго", "гарт авах", "гарт", "цэвэр цалин", "цэвэр дүн")),
    ("gross", ("нийт орлого", "бохир", "хөдөлмөрийн хөлс", "хөлс",
               "нийт цалин", "олгосон цалин", "цалин", "орлого")),
    ("first_name", ("нэр",)),
)

_TOTAL_WORDS = ("нийт", "дүн", "нийлбэр", "бүгд", "total", "нэгтгэл")

# Мөнгөн дүнтэй байх ёстой багана — эдгээрийн аль нэг нь заавал танигдана
_AMOUNT_KEYS = ("gross", "ndsh", "hhoat", "net", "taxable")


@dataclass
class ImportResult:
    rows: list[dict] = field(default_factory=list)
    header_row: int | None = None
    columns: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def totals(self) -> dict:
        keys = ("gross_minor", "ndsh_employee_minor", "hhoat_minor", "net_minor")
        return {k: sum(r[k] for r in self.rows) for k in keys}

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "header_row": self.header_row,
            "columns": self.columns,
            "skipped": self.skipped[:20],
            "skipped_count": len(self.skipped),
            "warnings": self.warnings[:20],
            "warnings_count": len(self.warnings),
            "totals": self.totals(),
        }


def _to_minor(raw: str | None) -> int | None:
    """Мөнгөн дүнг мөнгөн нэгжийн 1/100 болгож хөрвүүлнэ.

    "2 500 000.00₮", "2,500,000", "1234567.5", "(1000)" — бүгдийг уншина."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d,.\-]", "", s.strip("()"))
    if not s or not any(ch.isdigit() for ch in s):
        return None

    if "," in s and "." in s:
        s = s.replace(",", "")                      # таслал = мянгатын тусгаарлагч
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")

    try:
        value = Decimal(s)
    except Exception:
        return None
    minor = int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return -minor if negative else minor


def _find_header(grid: list[list[str]]) -> tuple[int | None, dict[str, int]]:
    """Толгой мөрийг олж, таньсан багануудын байрлалыг буцаана."""
    best: tuple[int | None, dict[str, int]] = (None, {})
    for idx, row in enumerate(grid[:25]):
        cols: dict[str, int] = {}
        for c, cell in enumerate(row):
            low = (cell or "").lower().strip()
            if not low:
                continue
            for key, words in _COLUMN_SPECS:
                if key in cols:
                    continue
                if any(w in low for w in words):
                    cols[key] = c
                    break
        has_name = "full_name" in cols or "last_name" in cols or "first_name" in cols
        has_amount = any(k in cols for k in _AMOUNT_KEYS)
        if has_name and has_amount and len(cols) > len(best[1]):
            best = (idx, cols)
    return best


def split_name(full: str) -> tuple[str, str]:
    """«Бат-Эрдэнэ Болд» → (овог, нэр). Эхний үг нь овог гэж үзнэ."""
    parts = [p for p in re.split(r"[\s,]+", full.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], " ".join(parts[1:])


def _cell(row: list[str], cols: dict[str, int], key: str) -> str:
    idx = cols.get(key)
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def parse(file_bytes: bytes, filename: str = "") -> ImportResult:
    """ТТ-11 файлаас ажилтны мөрүүдийг задална (санд хараахан бичихгүй)."""
    grid = read_grid(file_bytes, filename)
    res = ImportResult()
    if not grid:
        return res

    header_row, cols = _find_header(grid)
    res.header_row = header_row
    res.columns = cols
    if not cols:
        return res

    seen_registers: set[str] = set()
    for row in grid[header_row + 1:]:
        last = _cell(row, cols, "last_name")
        first = _cell(row, cols, "first_name")
        full = _cell(row, cols, "full_name")
        register = re.sub(r"\s+", "", _cell(row, cols, "register")).upper()[:16]

        if full and not (last or first):
            last, first = split_name(full)
        display = " ".join(p for p in (last, first) if p).strip()

        gross = _to_minor(_cell(row, cols, "gross"))
        ndsh = _to_minor(_cell(row, cols, "ndsh")) or 0
        hhoat = _to_minor(_cell(row, cols, "hhoat")) or 0
        net_file = _to_minor(_cell(row, cols, "net"))
        taxable = _to_minor(_cell(row, cols, "taxable"))

        if not display and not register:
            continue
        if any(w in display.lower() for w in _TOTAL_WORDS):
            res.skipped.append(f"{display} (нийлбэрийн мөр)")
            continue

        # Бохир дүн байхгүй бол бүрдэл хэсгүүдээс сэргээнэ
        if gross is None:
            if net_file is not None:
                gross = net_file + ndsh + hhoat
            elif taxable is not None:
                gross = taxable + ndsh
            else:
                res.skipped.append(f"{display or register} (дүнгүй)")
                continue
        if gross <= 0:
            res.skipped.append(f"{display or register} (дүн тэг)")
            continue
        if not display:
            res.skipped.append(f"{register} (нэргүй)")
            continue
        if register and register in seen_registers:
            res.skipped.append(f"{display} {register} (давхардсан)")
            continue
        if register:
            seen_registers.add(register)

        net_calc = gross - ndsh - hhoat
        mismatch = 0
        if net_file is not None and abs(net_file - net_calc) > 100:  # 1₮-оос дээш
            mismatch = net_file - net_calc
            res.warnings.append(
                f"{display}: файлын «гарт олгох» {net_file / 100:,.0f}₮ нь "
                f"бохир − НДШ − ХХОАТ = {net_calc / 100:,.0f}₮-тэй тэнцэхгүй "
                f"({mismatch / 100:+,.0f}₮). Тэнцсэн дүнг ашиглана.")

        res.rows.append({
            "register": register,
            "last_name": last,
            "first_name": first,
            "name": display,
            "gross_minor": gross,
            "ndsh_employee_minor": ndsh,
            "hhoat_minor": hhoat,
            "net_minor": net_calc,
            "net_in_file_minor": net_file,
            "mismatch_minor": mismatch,
        })

    return res


def _employee_key(last: str, first: str) -> str:
    return re.sub(r"\s+", " ", f"{last} {first}".strip().lower())


def apply(session, company_id: str, year: int, month: int, rows: list[dict],
          cfg=None, post_entry: bool = False, entry_date=None) -> dict:
    """Задалсан мөрүүдээр ажилтан ба цалингийн бүртгэл үүсгэнэ.

    Ажилтныг эхлээд регистрээр (=ажилтны код), дараа нь нэрээр тааруулна —
    байгаа ажилтныг давхардуулахгүй. Тухайн сард аль хэдийн цалин
    бодогдсон ажилтныг алгасана.

    post_entry=True үед л журналд бичилт хийнэ. Нүүлгэн шилжүүлэлтийн үед
    эхний үлдэгдэл нь цалингийн өглөгийг аль хэдийн агуулсан байдаг тул
    үүнийг сонголтоор үлдээв — өгөгдмөл нь зөвхөн түүх сэргээх."""
    from datetime import date as _date

    from sqlalchemy import select

    from . import ledger
    from .models import SourceType
    from .salary import Employee, PayrollLine, SalaryConfig

    cfg = cfg or SalaryConfig()

    employees = list(session.scalars(
        select(Employee).where(Employee.company_id == company_id)))
    by_code = {e.code.strip().upper(): e for e in employees if e.code}
    by_name = {_employee_key(e.last_name, e.first_name): e for e in employees}

    existing_lines = {
        p.employee_id for p in session.scalars(select(PayrollLine).where(
            PayrollLine.company_id == company_id,
            PayrollLine.year == year, PayrollLine.month == month))}

    created_employees = 0
    created_lines = 0
    skipped_existing = 0
    seq = len(employees)
    totals = {"gross": 0, "ndsh_emp": 0, "ndsh_er": 0, "hhoat": 0, "net": 0}
    new_lines: list[PayrollLine] = []

    for row in rows:
        register = (row.get("register") or "").strip().upper()
        last = row.get("last_name") or ""
        first = row.get("first_name") or ""
        if not (last or first):
            last, first = split_name(row.get("name") or "")

        emp = by_code.get(register) if register else None
        if emp is None:
            emp = by_name.get(_employee_key(last, first))
        if emp is None:
            seq += 1
            emp = Employee(
                company_id=company_id,
                code=(register or f"TT11-{seq:03d}")[:16],
                last_name=last, first_name=first, position=None,
                base_salary_minor=row["gross_minor"], active=True)
            session.add(emp)
            session.flush()
            created_employees += 1
            if emp.code:
                by_code[emp.code.upper()] = emp
            by_name[_employee_key(last, first)] = emp

        if emp.id in existing_lines:
            skipped_existing += 1
            continue
        existing_lines.add(emp.id)

        gross = row["gross_minor"]
        ndsh_emp = row.get("ndsh_employee_minor") or 0
        hhoat = row.get("hhoat_minor") or 0
        net = gross - ndsh_emp - hhoat

        ndsh_base = min(gross, cfg.ndsh_ceiling_minor) if cfg.ndsh_ceiling_minor else gross
        ndsh_er = int(round(ndsh_base * cfg.ndsh_employer_pct / 100))

        totals["gross"] += gross
        totals["ndsh_emp"] += ndsh_emp
        totals["ndsh_er"] += ndsh_er
        totals["hhoat"] += hhoat
        totals["net"] += net

        new_lines.append(PayrollLine(
            company_id=company_id, employee_id=emp.id, year=year, month=month,
            gross_minor=gross, ndsh_employee_minor=ndsh_emp,
            ndsh_employer_minor=ndsh_er, hhoat_minor=hhoat, net_minor=net))
        created_lines += 1

    entry_id = None
    if post_entry and new_lines:
        entry = ledger.post_entry(
            session, company_id, entry_date or _date(year, month, 25), [
                ledger.LineInput("7101", debit_minor=totals["gross"],
                                 description=f"Цалингийн зардал {year}-{month:02d} (ТТ-11)"),
                ledger.LineInput("7102", debit_minor=totals["ndsh_er"],
                                 description=f"АО НДШ {year}-{month:02d}"),
                ledger.LineInput("3102", credit_minor=totals["net"],
                                 description="Гарт олгох цалин"),
                ledger.LineInput("3103", credit_minor=totals["ndsh_emp"] + totals["ndsh_er"],
                                 description="НДШ өглөг"),
                ledger.LineInput("3104", credit_minor=totals["hhoat"],
                                 description="ХХОАТ өглөг"),
            ], source_type=SourceType.salary,
            memo=f"ТТ-11 импорт {year}-{month:02d}")
        entry_id = entry.id
        for line in new_lines:
            line.journal_entry_id = entry_id

    for line in new_lines:
        session.add(line)
    session.flush()

    return {
        "created_employees": created_employees,
        "created_lines": created_lines,
        "skipped_existing": skipped_existing,
        "entry_id": entry_id,
        **totals,
    }
