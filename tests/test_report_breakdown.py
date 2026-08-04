"""СТ-1..СТ-4-ийн дансны задаргаа (dropdown) ба тайлан хоорондын уялдаа."""

from datetime import date

from bayan import ledger, reports
from bayan.ledger import LineInput


def _post(session, company, d, lines):
    return ledger.post_entry(session, company.id, d, lines)


def _seed(session, company):
    # Хувь нийлүүлсэн хөрөнгө
    _post(session, company, date(2026, 1, 5), [
        LineInput("1001", debit_minor=50_000_00, description="Мөнгөн хувь оруулалт"),
        LineInput("4101", credit_minor=50_000_00, description="Хувь нийлүүлсэн хөрөнгө"),
    ])
    # Үндсэн хөрөнгө худалдан авалт
    _post(session, company, date(2026, 2, 1), [
        LineInput("2502", debit_minor=30_000_00, description="Тоног төхөөрөмж"),
        LineInput("1001", credit_minor=30_000_00, description="Мөнгөөр төлөв"),
    ])
    # Элэгдэл байгуулав (хасагдуулагч данс)
    _post(session, company, date(2026, 6, 30), [
        LineInput("7105", debit_minor=3_000_00, description="Элэгдлийн зардал"),
        LineInput("2509", credit_minor=3_000_00, description="Хуримтлагдсан элэгдэл"),
    ])
    # Борлуулалт ба өртөг
    _post(session, company, date(2026, 7, 1), [
        LineInput("1001", debit_minor=20_000_00, description="Борлуулалтын орлого"),
        LineInput("5101", credit_minor=20_000_00, description="Борлуулалт"),
    ])
    _post(session, company, date(2026, 7, 2), [
        LineInput("6101", debit_minor=8_000_00, description="Борлуулсан барааны өртөг"),
        LineInput("1001", credit_minor=8_000_00, description="Мөнгөөр төлөв"),
    ])
    # Хүүгийн орлого — 52 бүлэг (СТ-2-ын "Бусад орлого")
    _post(session, company, date(2026, 9, 1), [
        LineInput("1101", debit_minor=1_500_00, description="Хадгаламжийн хүү"),
        LineInput("5202", credit_minor=1_500_00, description="Хүүгийн орлого"),
    ])
    # Банкны зээл авав, хэсэгчлэн эргэн төлөв — санхүүжилтийн гүйлгээ
    _post(session, company, date(2026, 9, 10), [
        LineInput("1101", debit_minor=40_000_00, description="Зээл авав"),
        LineInput("3201", credit_minor=40_000_00, description="Богино хугацаат зээл"),
    ])
    _post(session, company, date(2026, 11, 10), [
        LineInput("3201", debit_minor=10_000_00, description="Зээл төлөв"),
        LineInput("1101", credit_minor=10_000_00, description="Мөнгөөр төлөв"),
    ])


def _rows(bs):
    return bs["assets"] + bs["liabilities"] + bs["equity"]


def _row(bs, name):
    return next(r for r in _rows(bs) if name in r["name"])


def test_every_row_has_account_breakdown_that_ties(session, company):
    _seed(session, company)
    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))

    for row in _rows(bs):
        assert "accounts" in row, row["name"]
        total = sum(a["amount_minor"] for a in row["accounts"])
        assert total == row["amount_minor"], f"{row['name']} задаргаа дүнтэй таарахгүй"

        # Хэрэглэгчийн тал задаргааг кодоор түлхүүрлэн нэгтгэдэг тул код
        # мөрийн дотор давтагдвал мөрүүд бие биенээ дарна
        codes = [a["code"] for a in row["accounts"]]
        assert len(codes) == len(set(codes)), f"{row['name']} код давхардсан: {codes}"


def test_contra_account_not_double_counted(session, company):
    _seed(session, company)
    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))

    ppe = _row(bs, "Үндсэн хөрөнгө")
    depr = _row(bs, "Хуримтлагдсан элэгдэл")

    # Элэгдлийн данс зөвхөн өөрийн мөрөнд орно
    assert all(not a["code"].startswith("2509") for a in ppe["accounts"])
    assert ppe["amount_minor"] == 30_000_00        # бүртгэлийн (брутто) үнэ
    assert depr["amount_minor"] == -3_000_00       # хасагдуулагч тул сөрөг
    assert ppe["amount_minor"] + depr["amount_minor"] == 27_000_00  # цэвэр дүн


def test_balance_sheet_balances_with_depreciation(session, company):
    _seed(session, company)
    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))
    assert bs["balanced"], (bs["total_assets_minor"], bs["total_liab_equity_minor"])


def test_no_balance_sheet_account_is_silently_dropped(session, company):
    """Баланс дахь ангиллын (1-4) данс бүр яг НЭГ мөрөнд орсон байх ёстой.
    Гээгдсэн данс СТ-1-ийг чимээгүйхэн тэнцвэргүй болгодог."""
    _seed(session, company)
    # Тайланд ховор ордог дансууд руу нэмж бичилт хийнэ
    _post(session, company, date(2026, 8, 1), [
        LineInput("1209", credit_minor=1_000_00, description="Найдваргүй авлагын хасалт"),
        LineInput("7108", debit_minor=1_000_00, description="Найдваргүй авлагын зардал"),
    ])
    _post(session, company, date(2026, 8, 2), [
        LineInput("2601", debit_minor=5_000_00, description="Програм хангамж"),
        LineInput("3109", credit_minor=5_000_00, description="Бусад өглөг"),
    ])
    _post(session, company, date(2026, 8, 3), [
        LineInput("1101", debit_minor=2_000_00, description="НӨАТ хураав"),
        LineInput("3105", credit_minor=2_000_00, description="НӨАТ-ын өглөг"),
    ])

    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))
    mapped: dict[str, list[str]] = {}
    for row in _rows(bs):
        for a in row["accounts"]:
            mapped.setdefault(a["code"], []).append(row["name"])

    tb = ledger.trial_balance(session, company.id, None, date(2026, 12, 31))
    for r in tb:
        if r["code"][0] not in "1234" or not r["balance_minor"]:
            continue
        rows_hit = mapped.get(r["code"], [])
        assert len(rows_hit) == 1, (
            f"{r['code']} {r['name']} → {rows_hit or 'аль ч мөрөнд ороогүй'}")

    assert bs["balanced"], (bs["total_assets_minor"], bs["total_liab_equity_minor"])


def test_income_statement_breakdown_ties(session, company):
    _seed(session, company)
    inc = reports.income_statement(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    bd = inc["breakdown"]

    assert sum(a["amount_minor"] for a in bd["revenue"]) == inc["revenue_minor"]
    assert sum(a["amount_minor"] for a in bd["other_income"]) == inc["other_income_minor"]
    assert sum(a["amount_minor"] for a in bd["cogs"]) == inc["cogs_minor"]
    assert sum(a["amount_minor"] for a in bd["expenses"]) == inc["expenses_minor"]
    # Задаргаа бүр дансны код, нэртэй
    for accounts in bd.values():
        for a in accounts:
            assert a["code"] and a["name"]


def test_other_income_reaches_profit_and_equity(session, company):
    """52 бүлгийн орлого СТ-2-ын ашиг, улмаар СТ-1-ийн өмчид хүрнэ."""
    _seed(session, company)
    inc = reports.income_statement(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    assert inc["other_income_minor"] == 1_500_00
    assert inc["net_income_minor"] == (
        inc["revenue_minor"] + inc["other_income_minor"]
        - inc["cogs_minor"] - inc["expenses_minor"])

    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))
    assert bs["balanced"], (bs["total_assets_minor"], bs["total_liab_equity_minor"])


def test_no_income_account_is_silently_dropped(session, company):
    """5-7 ангиллын данс бүр СТ-2-ын яг НЭГ бүлэгт орно."""
    _seed(session, company)
    inc = reports.income_statement(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    mapped: dict[str, list[str]] = {}
    for group, accounts in inc["breakdown"].items():
        for a in accounts:
            mapped.setdefault(a["code"], []).append(group)

    tb = ledger.trial_balance(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    for r in tb:
        if r["code"][0] not in "567" or not r["balance_minor"]:
            continue
        assert len(mapped.get(r["code"], [])) == 1, f"{r['code']} {r['name']}"


# ------------------------------------------------------------------ СТ-3

def test_equity_statement_ties_to_balance_sheet(session, company):
    """СТ-3-ын эцсийн үлдэгдэл = СТ-1-ийн нийт эздийн өмч."""
    _seed(session, company)
    eq = reports.statement_of_changes_in_equity(
        session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    bs = reports.balance_sheet(session, company.id, date(2026, 12, 31))

    bs_equity = sum(r["amount_minor"] for r in bs["equity"])
    assert eq["current"]["end"]["total"] == bs_equity

    # Эхний + ашиг + өөрчлөлт = эцсийн (мөр бүрээр)
    cur = eq["current"]
    for col in ("capital", "retained", "total"):
        assert (cur["start"][col] + cur["net_income"][col]
                + cur["changes"][col]) == cur["end"][col], col


# ------------------------------------------------------------------ СТ-4

def test_cash_flow_net_change_equals_actual_cash_movement(session, company):
    """СТ-4-ийн цэвэр өөрчлөлт нь мөнгө/банкны дансны бодит хөдөлгөөнтэй таарна."""
    _seed(session, company)
    cf = reports.cash_flow_statement(
        session, company.id, date(2026, 1, 1), date(2026, 12, 31))

    tb = ledger.trial_balance(session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    actual = sum(r["debit_minor"] - r["credit_minor"] for r in tb
                 if r["code"].startswith(("10", "11")))

    assert cf["current"]["net_change"] == actual


def test_cash_flow_classifies_loans_as_financing(session, company):
    _seed(session, company)
    cf = reports.cash_flow_statement(
        session, company.id, date(2026, 1, 1), date(2026, 12, 31))
    d = cf["current"]["detail"]

    assert d["fin_in_loans"] == 40_000_00        # зээл авсан
    assert d["fin_out_loans"] == 10_000_00       # зээл төлсөн
    assert d["fin_in_capital"] == 50_000_00      # хувь оруулсан
    assert d["inv_out_assets"] == 30_000_00      # үндсэн хөрөнгө авсан
    assert d["op_in_other"] == 1_500_00          # хүүгийн орлого
    assert cf["current"]["financing_net"] == 50_000_00 + 40_000_00 - 10_000_00


def test_cash_flow_sections_sum_to_net_change(session, company):
    _seed(session, company)
    cur = reports.cash_flow_statement(
        session, company.id, date(2026, 1, 1), date(2026, 12, 31))["current"]
    assert (cur["operating_net"] + cur["investing_net"]
            + cur["financing_net"]) == cur["net_change"]
