"""§5.2 Таамаглал — арга сонголт, backtest, түүх хангалтгүй үеийн зан төлөв.

Гол дүрэм: таамаглал бүр нарийвчлалаа хамт харуулна; түүх хангалтгүй бол
таамаглахын оронд ТАТГАЛЗАНА.
"""

from datetime import date

import pytest

from bayan import forecast
from bayan.ledger import LineInput, post_entry
from bayan.models import SourceType

AS_OF = date(2026, 8, 31)


def _revenue(session, company, year, month, amount_minor):
    post_entry(session, company.id, date(year, month, 15), [
        LineInput("1101", debit_minor=amount_minor, description="Борлуулалт"),
        LineInput("5101", credit_minor=amount_minor, description="Борлуулалт"),
    ], source_type=SourceType.manual, memo="Борлуулалт")


# ------------------------------------------------------------- аргын сонголт

def test_short_history_is_refused_not_guessed():
    with pytest.raises(forecast.NotEnoughHistory):
        forecast.predict_next([100, 200])


def test_three_months_uses_the_mean():
    value, method = forecast.predict_next([100, 200, 300])
    assert value == 200 and "дундаж" in method


def test_six_months_uses_a_damped_trend():
    value, method = forecast.predict_next([100, 110, 120, 130, 140, 150])
    assert "чиг хандлага" in method
    # Намжаасан тул цэвэр шугамын 160-аас бага, түүхийн дунджаас их
    assert 125 < value < 160


def test_damping_keeps_a_steep_trend_from_running_away():
    steep = [100, 200, 300, 400, 500, 600]
    value, _ = forecast.predict_next(steep)
    assert value < 700          # шугамаар бол 700, намжаалт үүнийг барина


def test_two_years_uses_seasonality():
    """Цуваа 12-р сараар төгсөх тул дараагийн үе нь 1-р сар — оргилтой сар.

    Сүүлийн ажиглалт 100 байхад 500 гарч байгаа нь улирлын давталтыг
    үнэхээр ашигласны нотолгоо."""
    base = [500] + [100] * 11               # 1-р сард оргил
    series = base + base
    value, method = forecast.predict_next(series)
    assert "улирлын давталт" in method
    assert value == 500


def test_seasonal_method_applies_year_over_year_growth():
    base = [100] * 12
    series = base + [200] * 12          # хоёр дахь жил 2 дахин
    value, _ = forecast.predict_next(series)
    assert value == 400                 # 200 × 2.0 өсөлт


def test_growth_factor_is_capped():
    series = [1] * 12 + [1000] * 12     # 1000 дахин өсөлт
    value, _ = forecast.predict_next(series)
    assert value == 2000                # хязгаар 2.0 дахин


def test_never_predicts_a_negative_flow():
    value, _ = forecast.predict_next([500, 400, 300, 200, 100, 0])
    assert value >= 0


# ------------------------------------------------------------------ backtest

def test_backtest_is_perfect_on_a_flat_series():
    mape, n = forecast.backtest([100] * 12)
    assert mape == 0.0 and n > 0


def test_backtest_reports_error_on_a_noisy_series():
    mape, n = forecast.backtest([100, 500, 100, 500, 100, 500, 100, 500, 100])
    assert mape and mape > 20 and n > 0


def test_backtest_returns_none_without_enough_points():
    mape, n = forecast.backtest([100, 200])
    assert mape is None and n == 0


def test_backtest_never_looks_at_the_future(monkeypatch):
    """Таамаглал бүр зөвхөн өмнөх өгөгдлөөр хийгдэх ёстой."""
    seen: list[int] = []
    original = forecast.predict_next

    def spy(series):
        seen.append(len(series))
        return original(series)

    monkeypatch.setattr(forecast, "predict_next", spy)
    series = list(range(1, 13))
    forecast.backtest(series, points=4)

    # Хамгийн урт нь бүтэн цувааны уртаас бага байх ёстой
    assert seen and max(seen) < len(series)


# ------------------------------------------------------- дэвтэр дээрх таамаг

def test_revenue_forecast_reports_its_own_accuracy(session, company):
    for m in range(1, 8):
        _revenue(session, company, 2026, m, 10_000_000_00)
    session.flush()

    f = forecast.revenue_forecast(session, company.id, AS_OF)

    assert f.value_minor == 10_000_000_00
    assert f.mape_pct == 0.0                 # тогтмол цуваа
    assert f.backtest_points > 0
    assert f.trustworthy
    assert f.period_label == "2026 оны 8-р сар"


def test_the_unfinished_current_month_is_left_out_of_history(session, company):
    """8-р сар дуусаагүй байхад түүний хагас өгөгдөл таамгийг чирэх ёсгүй."""
    for m in range(1, 8):
        _revenue(session, company, 2026, m, 10_000_000_00)
    _revenue(session, company, 2026, 8, 200_000_00)      # сарын эхний өдрүүд
    session.flush()

    f = forecast.revenue_forecast(session, company.id, date(2026, 8, 3))

    assert [h["label"] for h in f.history][-1] == "2026-07"
    assert f.value_minor == 10_000_000_00


def test_forecast_band_widens_with_error(session, company):
    amounts = [5_000_000_00, 15_000_000_00, 5_000_000_00, 15_000_000_00,
               5_000_000_00, 15_000_000_00, 5_000_000_00]
    for m, a in enumerate(amounts, start=1):
        _revenue(session, company, 2026, m, a)
    session.flush()

    f = forecast.revenue_forecast(session, company.id, AS_OF)
    assert f.mape_pct and f.mape_pct > 0
    assert f.low_minor < f.value_minor < f.high_minor


def test_forecast_refuses_when_the_company_is_too_young(session, company):
    _revenue(session, company, 2026, 7, 1_000_000_00)
    session.flush()

    with pytest.raises(forecast.NotEnoughHistory):
        forecast.revenue_forecast(session, company.id, AS_OF)


def test_months_before_the_first_sale_are_not_counted_as_history(session, company):
    """Компани нээгдэхээс өмнөх тэг сарууд дунджийг гажуудуулах ёсгүй."""
    for m in (5, 6, 7):
        _revenue(session, company, 2026, m, 9_000_000_00)
    session.flush()

    f = forecast.revenue_forecast(session, company.id, AS_OF)
    assert f.value_minor == 9_000_000_00     # 0-үүд орсон бол их бага гарна
    assert len(f.history) == 3


def test_expense_forecast_reads_expense_accounts(session, company):
    for m in range(1, 8):   # 1-7 сар дууссан, 8-р сар нь as_of
        post_entry(session, company.id, date(2026, m, 10), [
            LineInput("7103", debit_minor=2_000_000_00, description="Түрээс"),
            LineInput("1101", credit_minor=2_000_000_00, description="Түрээс"),
        ], source_type=SourceType.manual, memo="Түрээс")
    session.flush()

    f = forecast.expense_forecast(session, company.id, AS_OF)
    assert f.value_minor == 2_000_000_00


# ------------------------------------------- 13 долоо хоногийн мөнгөн урсгал

def test_cash_forecast_starts_from_the_real_balance(session, company):
    _revenue(session, company, 2026, 7, 30_000_000_00)
    session.flush()

    cf = forecast.cash_forecast(session, company.id, AS_OF)
    assert cf.opening_minor == 30_000_000_00
    assert len(cf.weeks) == 13
    assert cf.weeks[0].starts_on == AS_OF


def test_cash_forecast_schedules_invoices_on_their_due_date(session, company):
    from bayan.partners import Counterparty, Invoice, InvoiceKind

    _revenue(session, company, 2026, 7, 10_000_000_00)
    cp = Counterparty(company_id=company.id, name="Худалдан авагч", reg_no="111")
    session.add(cp)
    session.flush()
    session.add(Invoice(
        company_id=company.id, counterparty_id=cp.id, kind=InvoiceKind.sales,
        number="INV-1", issue_date=date(2026, 8, 1),
        due_date=AS_OF + __import__("datetime").timedelta(days=10),
        net_minor=4_000_000_00, vat_minor=0, paid_minor=0))
    session.flush()

    cf = forecast.cash_forecast(session, company.id, AS_OF)
    assert cf.weeks[1].inflow_minor >= 4_000_000_00      # 2 дахь долоо хоног


def test_cash_forecast_flags_going_negative(session, company):
    from bayan.partners import Counterparty, Invoice, InvoiceKind

    _revenue(session, company, 2026, 7, 1_000_000_00)
    cp = Counterparty(company_id=company.id, name="Нийлүүлэгч", reg_no="222")
    session.add(cp)
    session.flush()
    session.add(Invoice(
        company_id=company.id, counterparty_id=cp.id, kind=InvoiceKind.purchase,
        number="BILL-1", issue_date=date(2026, 8, 1),
        due_date=AS_OF + __import__("datetime").timedelta(days=20),
        net_minor=50_000_000_00, vat_minor=0, paid_minor=0))
    session.flush()

    cf = forecast.cash_forecast(session, company.id, AS_OF).to_dict()
    assert cf["goes_negative"] is True
    assert cf["lowest_week"] is not None


def test_cash_forecast_states_its_assumptions(session, company):
    _revenue(session, company, 2026, 7, 5_000_000_00)
    session.flush()

    cf = forecast.cash_forecast(session, company.id, AS_OF)
    joined = " ".join(cf.assumptions)
    assert "Нэхэмжлэх" in joined and "Зээл" in joined


def test_cash_forecast_survives_a_company_with_no_history(session, company):
    cf = forecast.cash_forecast(session, company.id, AS_OF)
    assert cf.opening_minor == 0
    assert len(cf.weeks) == 13
    assert "түүх хангалтгүй" in " ".join(cf.assumptions)
