import pytest
from bayan.amounts import AmountError, format_minor, parse_amount, parse_date


def test_text_with_thousands():
    assert parse_amount("1,234,567.89") == 123456789


def test_numeric_cell():
    assert parse_amount(1234567.89) == 123456789
    assert parse_amount(100) == 10000


def test_empty_is_none_not_zero():
    assert parse_amount(None) is None
    assert parse_amount("") is None
    assert parse_amount("-") is None


def test_negative_paren_and_minus():
    assert parse_amount("(5,000.00)") == -500000
    assert parse_amount("-5,000.00") == -500000


def test_currency_symbol_stripped():
    assert parse_amount("12,000.50₮") == 1200050


def test_garbage_raises():
    with pytest.raises(AmountError):
        parse_amount("N/A")


def test_float_binary_error_avoided():
    # 0.1 + 0.2 маягийн binary алдаа орж ирэхгүй
    assert parse_amount(0.29) == 29


def test_format_minor():
    assert format_minor(123456789) == "1,234,567.89"
    assert format_minor(-500000) == "-5,000.00"


def test_parse_date_multi_format():
    d = parse_date("2026.03.15 14:30", "%Y.%m.%d %H:%M|%Y.%m.%d")
    assert (d.year, d.month, d.day, d.hour) == (2026, 3, 15, 14)
    d2 = parse_date("2026.03.15", "%Y.%m.%d %H:%M|%Y.%m.%d")
    assert (d2.year, d2.month, d2.day) == (2026, 3, 15)
