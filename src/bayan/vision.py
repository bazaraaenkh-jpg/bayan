"""Зам В — скан/зурган PDF-ийг Fable 5 vision-ээр задлах (§4.2, §6).

Хуудас бүрийг зураг болгож, хатуу JSON schema-тай structured output-аар
мөрүүдийг гаргуулна. Хуудас бүр 2 удаа (T=0, T=0.4) уншигдаж, зөрсөн мөр
low_confidence тэмдэглэгдэнэ. Үр дүн мөн л validation gate-ээр орно —
vision алдаа gate-ээс нэвтэрч чадахгүй.

Шаардлага: pip install anthropic pymupdf; ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .extract_excel import ExtractionError, RawRow, RawStatement
from .registry import Descriptor

FABLE = "claude-fable-5"

_TOOL = {
    "name": "statement_rows",
    "description": "Банкны хуулгын хуудасны бүх гүйлгээний мөр",
    "input_schema": {
        "type": "object",
        "properties": {
            "opening_balance_on_page": {"type": ["string", "null"]},
            "closing_balance_on_page": {"type": ["string", "null"]},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "posted_at": {"type": "string"},
                        "debit": {"type": ["string", "null"]},
                        "credit": {"type": ["string", "null"]},
                        "balance_after": {"type": ["string", "null"]},
                        "counterparty_account": {"type": ["string", "null"]},
                        "description": {"type": "string"},
                    },
                    "required": ["posted_at", "description"],
                },
            },
        },
        "required": ["rows"],
    },
}

_PROMPT = """Энэ бол Монголын банкны дансны хуулгын хуудас. БҮХ гүйлгээний
мөрийг яг байгаагаар нь гарга. Дүнг харагдаж буй чигээр нь (таслал, цэгтэй)
бич — өөрчлөх, тооцоолохыг хориглоно. Толгой, хөл, нийт дүнгийн мөрийг бүү оруул.
Огноог YYYY.MM.DD [HH:MM] хэлбэрээр."""


def _pdf_pages_to_png(path: Path, dpi: int = 200) -> list[bytes]:
    import fitz  # pymupdf
    doc = fitz.open(str(path))
    out = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        out.append(pix.tobytes("png"))
    doc.close()
    return out


def _read_page(client, png: bytes, temperature: float) -> dict:
    msg = client.messages.create(
        model=FABLE, max_tokens=8192, temperature=temperature,
        tools=[_TOOL], tool_choice={"type": "tool", "name": "statement_rows"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode()}},
            {"type": "text", "text": _PROMPT},
        ]}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    return {"rows": []}


def extract_vision(path: Path, desc: Descriptor) -> RawStatement:
    try:
        import anthropic
    except ImportError as e:
        raise ExtractionError("anthropic суулгаагүй") from e
    client = anthropic.Anthropic()

    stmt = RawStatement(descriptor_id=desc.id)
    row_no = 0
    for png in _pdf_pages_to_png(path):
        pass1 = _read_page(client, png, 0.0)
        pass2 = _read_page(client, png, 0.4)    # self-consistency (§4.2)
        r1 = [json.dumps(r, sort_keys=True, ensure_ascii=False)
              for r in pass1["rows"]]
        r2 = {json.dumps(r, sort_keys=True, ensure_ascii=False)
              for r in pass2["rows"]}
        for r_json, r in zip(r1, pass1["rows"]):
            row_no += 1
            values = dict(r)
            values["_low_confidence"] = r_json not in r2   # зөрсөн мөр
            stmt.rows.append(RawRow(row_index=row_no, values=values))
    return stmt
