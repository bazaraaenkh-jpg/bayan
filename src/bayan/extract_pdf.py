"""Зам Б — текст давхаргатай PDF задлалт (pdfplumber) (§4.2).

Хуудас бүрээс хүснэгт татаж, давтагдсан толгойн мөрийг хасч, огноогүй
мөрийг өмнөх мөрийн утгын үргэлжлэл гэж нийлүүлнэ (F6). Чанарын хаалт:
validation gate давахгүй бол pipeline Зам В (vision) руу чиглүүлнэ.

Цөм логик (rows_to_statement) нь цэвэр функц тул PDF-гүйгээр тестлэгдэнэ.
"""

from __future__ import annotations

import re
from pathlib import Path

from .extract_excel import ExtractionError, RawRow, RawStatement
from .registry import Descriptor


def _match_header(row: list, header_match: list[str]) -> bool:
    texts = [str(c).strip().lower() for c in row if c]
    return bool(texts) and all(
        any(m.lower() in t for t in texts) for m in header_match)


def _map_columns_from_header(header_row: list, desc: Descriptor) -> dict[str, int]:
    headers = {i: str(c).strip().lower() for i, c in enumerate(header_row) if c}
    mapping: dict[str, int] = {}
    for field_name, spec in desc.columns.items():
        if spec.index is not None:              # толгойгүй формат
            mapping[field_name] = spec.index
            continue
        for i, h in headers.items():
            if any(a.lower() in h for a in (spec.aliases or [])):
                mapping[field_name] = i
                break
        else:
            if not spec.optional:
                raise ExtractionError(
                    f"PDF багана олдсонгүй: {field_name} ({spec.aliases})")
    return mapping


def _metadata_from_text(text: str, desc: Descriptor, locale: dict) -> dict:
    from .amounts import parse_amount
    meta: dict = {}
    for key, cfg in desc.metadata_block.items():
        m = re.search(re.escape(cfg["anchor"]) + r"\s*:?\s*([^\n]+)", text)
        if not m:
            continue
        val = m.group(1).strip()
        custom = cfg.get("regex")
        if custom:
            mm = re.search(custom, val)
            val = mm.group(1) if mm else val
        if cfg.get("type") == "amount":
            mm = re.search(r"[-()\d,\.]+", val)
            if mm:
                meta[key + "_minor"] = parse_amount(
                    mm.group(0), thousands=locale.get("thousands", ","),
                    decimal=locale.get("decimal", "."))
        else:
            meta[key] = val
    return meta


def rows_to_statement(pages_rows: list[list[list]], first_page_text: str,
                      desc: Descriptor) -> RawStatement:
    """Хуудас бүрийн хүснэгтийн мөрүүд → RawStatement (цэвэр функц)."""
    stmt = RawStatement(descriptor_id=desc.id)
    stmt.metadata = _metadata_from_text(first_page_text, desc, desc.amount_locale)

    header_match = desc.table.get("header_row_match", [])
    stop_match = [s.lower() for s in desc.table.get("stop_row_match", [])]
    colmap: dict[str, int] | None = None
    row_no = 0

    for page_rows in pages_rows:
        for row in page_rows:
            row_no += 1
            if not row or all(c in (None, "") for c in row):
                continue
            if header_match and _match_header(row, header_match):
                if colmap is None:
                    colmap = _map_columns_from_header(row, desc)
                continue    # давтагдсан толгой (F6) — алгасна
            texts = [str(c).lower() for c in row if c]
            if stop_match and any(any(s in t for s in stop_match) for t in texts):
                return stmt
            if colmap is None:
                continue    # толгой олдоогүй байхад дата гарахгүй
            values = {f: (row[i] if i < len(row) else None)
                      for f, i in colmap.items()}
            posted = values.get("posted_at")
            if (posted in (None, "") and stmt.rows
                    and desc.table.get("continuation_rule") == "date_empty_merge_up"):
                # тасарсан утгыг өмнөх мөрөнд залгана (F6)
                extra = str(values.get("description") or "").strip()
                if extra:
                    prev = stmt.rows[-1].values
                    prev["description"] = f"{prev.get('description') or ''} {extra}".strip()
                continue
            if all(v in (None, "") for v in values.values()):
                continue
            stmt.rows.append(RawRow(row_index=row_no, values=values))
    return stmt


def extract_pdf(path: Path, desc: Descriptor) -> RawStatement:
    try:
        import pdfplumber
    except ImportError as e:
        raise ExtractionError("pdfplumber суулгаагүй: pip install pdfplumber") from e

    pages_rows: list[list[list]] = []
    first_text = ""
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            if i == 0:
                first_text = page.extract_text() or ""
            table = page.extract_table()
            if table is None:                    # lattice → stream fallback
                table = page.extract_table({"vertical_strategy": "text",
                                            "horizontal_strategy": "text"})
            pages_rows.append(table or [])
    return rows_to_statement(pages_rows, first_text, desc)


def scan_pdf_text(path: Path, max_chars: int = 3000) -> list[str]:
    """Fingerprinting-д зориулж эхний хуудасны текст."""
    try:
        import pdfplumber
    except ImportError:
        return []
    with pdfplumber.open(str(path)) as pdf:
        text = (pdf.pages[0].extract_text() or "")[:max_chars]
    return [line.strip() for line in text.splitlines() if line.strip()]
