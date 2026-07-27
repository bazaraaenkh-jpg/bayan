"""Дүн ба огнооны parser — форматын асуудал F3, F4, F5-ийн шийдэл.

Дүн үргэлж minor unit (мөнгө) бүхэл тоо болж гарна. Float зам байхгүй:
Excel-ийн тоон нүд Decimal-аар дамжина, текст нүд шууд цифрээр задарна.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

MINOR_PER_UNIT = 100  # 1₮ = 100 мөнгө


class AmountError(ValueError):
    pass


class DateError(ValueError):
    pass


def parse_amount(
    value,
    thousands: str = ",",
    decimal: str = ".",
    negative: str = "minus|paren",
) -> int | None:
    """Ямар ч хэлбэрийн дүнг minor unit бүхэл тоо болгоно. Хоосон → None (0 биш!)."""
    if value is None:
        return None
    # Excel-ийн тоон нүд: float-ийг str-ээр дамжуулж Decimal болгоно (binary алдаа арилна)
    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value))
        return _to_minor(d)

    text = str(value).strip()
    if text == "" or text == "-":
        return None

    neg = False
    if "paren" in negative and text.startswith("(") and text.endswith(")"):
        neg, text = True, text[1:-1].strip()
    if "minus" in negative and text.startswith("-"):
        neg, text = True, text[1:].strip()
    text = text.replace("₮", "").replace("MNT", "").strip()
    if thousands:
        text = text.replace(thousands, "")
    if decimal != ".":
        text = text.replace(decimal, ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise AmountError(f"Дүн танигдсангүй: {value!r}")
    try:
        d = Decimal(text)
    except InvalidOperation as e:  # pragma: no cover
        raise AmountError(f"Дүн танигдсангүй: {value!r}") from e
    return -_to_minor(d) if neg else _to_minor(d)


def _to_minor(d: Decimal) -> int:
    minor = (d * MINOR_PER_UNIT).quantize(Decimal("1"))
    if abs(d * MINOR_PER_UNIT - minor) > Decimal("0.001"):
        raise AmountError(f"Мөнгөнөөс нарийн бутархай дүн: {d}")
    return int(minor)


def format_minor(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    return f"{sign}{minor // 100:,}.{minor % 100:02d}"


def parse_date(value, formats: str) -> datetime:
    """formats: '|'-ээр тусгаарласан strptime жагсаалт (descriptor-оос)."""
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day") and not hasattr(value, "hour"):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    
    # Try ISO format first
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
        
    clean_text = text.replace("T", " ")
    for fmt in formats.split("|"):
        fmt_clean = fmt.strip()
        try:
            return datetime.strptime(clean_text, fmt_clean)
        except ValueError:
            try:
                return datetime.strptime(text, fmt_clean)
            except ValueError:
                continue
    raise DateError(f"Огноо танигдсангүй: {value!r} (formats: {formats})")
