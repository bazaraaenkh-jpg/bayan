"""Харьцаа үзүүлэлт ба таамаглалын тооцооллын нарийвчлал."""

from datetime import date

import pytest

from bayan import assets, forecast, ledger, ratios, reports

M = 100


def _je(session, company, d, lines):
    return ledger.post_entry(session, company.id, d, [
        ledger.LineInput(c, debit_minor=dr, credit_minor=cr)
        for c, dr, cr in lines])


@pytest.fixture
def scenario(session, company):
    """Бүх дансны бүлгийг хамарсан хувилбар — зээл, урьдчилгаа, биет бус,
    бусад орлого зэрэг өмнө нь ГЭЭГДДЭГ байсан дансууд орсон."""
    _je(session, company, date(2026, 1, 1),
        [("1101", 50_000_000 * M, 0), ("4101", 0, 50_000_000 * M)])
    _je(session, company, date(2026, 1, 2),
        [("1101", 20_000_000 * M, 0), ("3201", 0, 20_000_000 * M)])   # б/х зээл
    _je(session, company, date(2026, 1, 3),
        [("1401", 3_000_000 * M, 0), ("1101", 0, 3_000_000 * M)])     # урьдчилгаа
    _je(session, company, date(2026, 1, 4),
        [("2601", 8_000_000 * M, 0), ("1101", 0, 8_000_000 * M)])     # биет бус
    _je(session, company, date(2026, 2, 1),
        [("1201", 30_000_000 * M, 0), ("5101", 0, 30_000_000 * M)])
    _je(session, company, date(2026, 2, 2),
        [("6101", 12_000_000 * M, 0), ("2101", 0, 12_000_000 * M)])
    _je(session, company, date(2026, 2, 3),
        [("2101", 12_000_000 * M, 0), ("3101", 0, 12_000_000 * M)])
    _je(session, company, date(2026, 2, 5),
        [("7103", 4_000_000 * M, 0), ("1101", 0, 4_000_000 * M)])
    _je(session, company, date(2026, 2, 6),
        [("1101", 2_000_000 * M, 0), ("5201", 0, 2_000_000 * M)])     # бусад орлого
    _je(session, company, date(2026, 2, 8),
        [("1101", 6_000_000 * M, 0), ("3501", 0, 6_000_000 * M)])     # у/х зээл
    assets.register_asset(session, company.id, "FA1", "Машин",
                          24_000_000 * M, 48, date(2026, 1, 1))
    assets.run_monthly_depreciation(session, company.id, date(2026, 2, 28))
    return date(2026, 2, 28)


def test_ratio_basis_ties_to_financial_statements(session, company, scenario):
    """Харьцааны суурь дүн СТ-1/СТ-2-той ЯГ таарна."""
    as_of = scenario
    r = ratios.calculate_financial_ratios(session, company.id, as_of)
    bs = reports.balance_sheet(session, company.id, as_of)
    inc = reports.income_statement(session, company.id, None, as_of)

    b = r["basis"]
    assert b["total_assets_minor"] == bs["total_assets_minor"]
    assert b["total_liabilities_minor"] == sum(
        x["amount_minor"] for x in bs["liabilities"])
    assert b["net_income_minor"] == inc["net_income_minor"]
    assert b["revenue_minor"] == inc["revenue_minor"]


def test_debt_ratio_counts_all_borrowings(session, company, scenario):
    """32xx/35xx зээл гээгдэж өрийн харьцаа дутуу гардаг байсан."""
    as_of = scenario
    r = ratios.calculate_financial_ratios(session, company.id, as_of)
    bs = reports.balance_sheet(session, company.id, as_of)
    liab = sum(x["amount_minor"] for x in bs["liabilities"])
    expected = round(liab / bs["total_assets_minor"] * 100, 1)

    assert r["debt_ratio"]["value"] == expected

    # 32xx + 35xx зээл (26 сая) өрийн дүнд БАГТСАН эсэх — өмнө нь зөвхөн
    # 31xx-ийг тоолж, өрийн харьцааг эрс дутуу харуулдаг байв
    only_31 = sum(x["amount_minor"] for x in bs["liabilities"]
                  if all(a["code"].startswith("31") for a in x["accounts"]))
    assert r["basis"]["total_liabilities_minor"] - only_31 == 26_000_000 * M
    old_style = round(only_31 / bs["total_assets_minor"] * 100, 1)
    assert expected > old_style + 15               # хэдэн арван нэгжээр зөрдөг


def test_current_ratio_uses_only_short_term_liabilities(session, company, scenario):
    """Эргэцтэй харьцаанд урт хугацаат зээл орохгүй."""
    as_of = scenario
    r = ratios.calculate_financial_ratios(session, company.id, as_of)
    b = r["basis"]
    # 35xx урт хугацаат зээл нийт өрд орно, богино хугацаатд ОРОХГҮЙ
    assert b["current_liabilities_minor"] < b["total_liabilities_minor"]
    assert (b["total_liabilities_minor"] - b["current_liabilities_minor"]
            == 6_000_000 * M)
    assert r["current_ratio"]["value"] == round(
        b["current_assets_minor"] / b["current_liabilities_minor"], 2)
    assert r["quick_ratio"]["value"] <= r["current_ratio"]["value"]


def test_net_margin_includes_other_income(session, company, scenario):
    """5201 бусад орлого цэвэр ашгаас гээгддэг байсан."""
    as_of = scenario
    r = ratios.calculate_financial_ratios(session, company.id, as_of)
    inc = reports.income_statement(session, company.id, None, as_of)
    assert r["basis"]["net_income_minor"] == inc["net_income_minor"]
    # 2 сая бусад орлого цэвэр ашигт багтсан эсэх
    assert inc["net_income_minor"] > (inc["gross_profit_minor"]
                                      - inc["expenses_minor"])


def test_indirect_cash_flow_reconciles_with_direct(session, company, scenario):
    """Шууд бус аргын ҮА-ны урсгал СТ-4-тэй ҮРГЭЛЖ таарна."""
    as_of = scenario
    ind = ratios.get_indirect_cash_flow(session, company.id,
                                        date(2026, 1, 1), as_of)
    direct = reports.cash_flow_statement(session, company.id,
                                         date(2026, 1, 1), as_of)["current"]

    assert ind["reconciled"] is True
    assert ind["operating_cash_flow"] == round(direct["operating_net"] / 100, 2)
    # Бридж: цэвэр ашиг + тохируулгууд = ҮА-ны урсгал
    total = ind["net_income"] + sum(
        ind["adjustments"][k] for k in
        ("depreciation", "non_operating_income", "operating_assets_change",
         "operating_liabilities_change", "other_adjustments"))
    assert round(total, 2) == ind["operating_cash_flow"]


# ------------------------------------------------------------ таамаглал

def test_forecast_math():
    assert forecast.predict_next([500] * 6)[0] == 500          # тогтмол
    assert forecast.predict_next([100, 120, 140]) == (120, "сүүлийн 3 сарын дундаж")
    with pytest.raises(forecast.NotEnoughHistory):
        forecast.predict_next([100, 200])

    # Намжаасан шугаман: 6 үеийн налуу × 0.6, дунджаас 3.5 алхам урагш
    v, method = forecast.predict_next([100, 200, 300, 400, 500, 600])
    assert method == "намжаасан шугаман чиг хандлага"
    assert v == round(350 + (100 * 0.6) * 3.5)                 # = 560

    # 24 сарын түүхэнд өнгөрсөн оны мөн сар × өсөлт
    v, method = forecast.predict_next([100, 200] * 12)
    assert method == "улирлын давталт × чиг хандлага"
    assert v == 100                                            # өсөлт 1.0


def test_backtest_has_no_lookahead_and_no_zero_division():
    series = [100, 200, 300, 400, 500, 600, 700, 800, 900]
    mape, n = forecast.backtest(series)
    assert n > 0 and mape is not None
    assert forecast.backtest([0, 0, 0]) == (None, 0)           # тэгд хуваахгүй
    assert forecast.backtest([100, 200]) == (None, 0)          # түүх хүрэлцэхгүй
