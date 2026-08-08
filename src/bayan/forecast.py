"""§5.2 L2 — Таамаглал. Нотолгоотой, LLM-гүй.

Даалгаврын **заавал биелэх дүрэм:** таамаглал бүр өөрийн нарийвчлалаа хамт
харуулна (MAPE, хэдэн үеэр шалгасан). Тоо ганцаараа гарахгүй. «+18%» гэж
хэлээд ямар нарийвчлалтай нь тодорхойгүй байх нь ашиггүй.

Энд **LLM хэрэггүй** — сонгодог арга. Түүх богино байх нь Монголын ЖДҮ-д
энгийн үзэгдэл тул түүхийн уртаас хамааруулж аргаа сонгоно:

    ≥24 сар  улирлын давталт (өнгөрсөн оны мөн сар) × чиг хандлага
    ≥6 сар   намжаасан шугаман чиг хандлага
    ≥3 сар   сүүлийн 3 сарын дундаж
    <3 сар   ТААМАГЛАХГҮЙ — «хангалттай түүх алга» гэж хэлнэ

Backtest нь rolling-origin: сүүлийн N үе бүрд зөвхөн ӨМНӨХ өгөгдлөөр
таамаглаж, бодит утгатай харьцуулж MAPE бодно. Тухайн аргыг сонгосон нь
хэрхэн ажиллаж байсныг харуулна.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

#: Хамгийн багадаа хэдэн сарын түүх байж таамаглах вэ
MIN_HISTORY = 3
#: Backtest-д хэдэн үеийг шалгах вэ
BACKTEST_POINTS = 6


@dataclass
class Forecast:
    metric: str
    title: str
    period_label: str
    value_minor: int
    method: str
    history: list[dict] = field(default_factory=list)
    mape_pct: float | None = None
    backtest_points: int = 0
    low_minor: int | None = None
    high_minor: int | None = None
    note: str = ""

    @property
    def trustworthy(self) -> bool:
        """MAPE 30%-иас бага бол шийдвэрт ашиглаж болно гэж үзнэ."""
        return self.mape_pct is not None and self.mape_pct < 30

    def to_dict(self) -> dict:
        return {
            "metric": self.metric, "title": self.title,
            "period": self.period_label, "value_minor": self.value_minor,
            "method": self.method, "history": self.history[-24:],
            "mape_pct": self.mape_pct, "backtest_points": self.backtest_points,
            "low_minor": self.low_minor, "high_minor": self.high_minor,
            "note": self.note, "trustworthy": self.trustworthy,
        }


class NotEnoughHistory(Exception):
    """Түүх хангалтгүй — таамаглахын оронд шууд хэлнэ."""


# ------------------------------------------------------------------- аргууд

def _mean(xs: list[int]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def predict_next(series: list[int]) -> tuple[int, str]:
    """Дараагийн үеийг таамаглана. (утга, ашигласан арга) буцаана.

    Түүхийн уртаас хамаарч аргаа сонгоно — богино түүхэнд нарийн загвар
    тохируулах нь дуу чимээг загварчилж эхэлдэг."""
    n = len(series)
    if n < MIN_HISTORY:
        raise NotEnoughHistory(
            f"Таамаглахад дор хаяж {MIN_HISTORY} сарын түүх шаардлагатай "
            f"(одоо {n}).")

    if n >= 24:
        # Улирлын давталт: өнгөрсөн оны мөн сар × сүүлийн 12 сарын өсөлт
        same_month_last_year = series[-12]
        last_year = _mean(series[-24:-12])
        this_year = _mean(series[-12:])
        growth = (this_year / last_year) if last_year else 1.0
        growth = min(max(growth, 0.5), 2.0)          # хэт огцом өсөлтийг барина
        return int(round(same_month_last_year * growth)), "улирлын давталт × чиг хандлага"

    if n >= 6:
        # Намжаасан шугаман чиг хандлага (сүүлийн 6 үе)
        window = series[-6:]
        x_mean = 2.5                                  # (0..5)-ийн дундаж
        y_mean = _mean(window)
        num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(window))
        den = sum((i - x_mean) ** 2 for i in range(6))
        slope = (num / den) if den else 0.0
        damped = slope * 0.6                          # чиг хандлагыг намжаана
        return int(round(max(y_mean + damped * 3.5, 0))), "намжаасан шугаман чиг хандлага"

    return int(round(_mean(series[-3:]))), "сүүлийн 3 сарын дундаж"


def backtest(series: list[int], points: int = BACKTEST_POINTS) -> tuple[float | None, int]:
    """Rolling-origin шалгалт — (MAPE %, шалгасан үеийн тоо).

    Үе бүрд зөвхөн түүнээс ӨМНӨХ өгөгдлөөр таамаглана — ирээдүйг харахгүй."""
    errors: list[float] = []
    for cut in range(len(series) - points, len(series)):
        if cut < MIN_HISTORY:
            continue
        actual = series[cut]
        if actual == 0:
            continue                                  # 0 дээр хувийн алдаа утгагүй
        try:
            predicted, _ = predict_next(series[:cut])
        except NotEnoughHistory:
            continue
        errors.append(abs(predicted - actual) / abs(actual) * 100)
    if not errors:
        return None, 0
    return round(sum(errors) / len(errors), 1), len(errors)


# --------------------------------------------------------------- түүх татах

def _month_key(d: date) -> tuple[int, int]:
    return d.year, d.month


def _add_month(y: int, m: int, step: int = 1) -> tuple[int, int]:
    idx = (y * 12 + (m - 1)) + step
    return idx // 12, idx % 12 + 1


def monthly_series(session, company_id: str, prefixes: tuple[str, ...],
                   side: str, months: int, as_of: date) -> list[dict]:
    """Сар бүрийн хөдөлгөөнийг цуваа болгож буцаана (эрт → сүүл)."""
    from .ledger import trial_balance
    from .reports import _collect_by_prefix

    out: list[dict] = []
    y, m = _add_month(as_of.year, as_of.month, -(months - 1))
    for _ in range(months):
        import calendar
        d_from = date(y, m, 1)
        d_to = date(y, m, calendar.monthrange(y, m)[1])
        tb = trial_balance(session, company_id, d_from, d_to)
        total, _rows = _collect_by_prefix(tb, prefixes, side=side)
        out.append({"year": y, "month": m, "label": f"{y}-{m:02d}",
                    "value_minor": total})
        y, m = _add_month(y, m)
    return out


def _trim_leading_zeros(history: list[dict]) -> list[dict]:
    """Компани нээгдэхээс өмнөх хоосон саруудыг түүхэнд тооцохгүй."""
    first = next((i for i, h in enumerate(history) if h["value_minor"]), None)
    return [] if first is None else history[first:]


# ------------------------------------------------------------ таамаглалууд

def complete_history(session, company_id: str, prefixes: tuple[str, ...],
                     side: str, as_of: date, lookback: int = 30) -> list[dict]:
    """ДУУССАН саруудын түүх.

    Тухайн сар дуусаагүй байхад түүнийг түүхэнд оруулах нь таамгийг доош
    чирнэ — сарын 6-нд байхад тэр сарын гүйлгээ бараг байхгүй. Тиймээс
    түүх нь `as_of`-ийн ӨМНӨХ сараар төгсөнө."""
    prev_y, prev_m = _add_month(as_of.year, as_of.month, -1)
    import calendar
    last_complete = date(prev_y, prev_m, calendar.monthrange(prev_y, prev_m)[1])
    return _trim_leading_zeros(
        monthly_series(session, company_id, prefixes, side, lookback, last_complete))


def _forecast_flow(session, company_id: str, metric: str, title: str,
                   prefixes: tuple[str, ...], side: str, as_of: date,
                   lookback: int = 30) -> Forecast:
    history = complete_history(session, company_id, prefixes, side, as_of, lookback)
    series = [h["value_minor"] for h in history]

    value, method = predict_next(series)             # NotEnoughHistory дээшээ
    mape, n = backtest(series)

    band = (mape or 0) / 100
    return Forecast(
        metric=metric, title=title,
        # Түүх өмнөх сараар төгссөн тул таамаглаж буй нь ЯГ энэ сар
        period_label=f"{as_of.year} оны {as_of.month}-р сар", value_minor=value,
        method=method, history=history, mape_pct=mape, backtest_points=n,
        low_minor=int(value * (1 - band)) if mape else None,
        high_minor=int(value * (1 + band)) if mape else None,
        note=(f"{len(series)} дууссан сарын түүхэн дээр" if series else ""))


def revenue_forecast(session, company_id: str, as_of: date | None = None) -> Forecast:
    return _forecast_flow(session, company_id, "revenue_forecast",
                          "Борлуулалтын таамаг",
                          ("5",), "credit", as_of or date.today())


def expense_forecast(session, company_id: str, as_of: date | None = None) -> Forecast:
    return _forecast_flow(session, company_id, "expense_forecast",
                          "Зардлын таамаг",
                          ("6", "7"), "debit", as_of or date.today())


# ------------------------------------------------- 13 долоо хоногийн мөнгө

@dataclass
class CashWeek:
    week: int
    starts_on: date
    inflow_minor: int
    outflow_minor: int
    closing_minor: int

    def to_dict(self) -> dict:
        return {"week": self.week, "starts_on": self.starts_on.isoformat(),
                "inflow_minor": self.inflow_minor,
                "outflow_minor": self.outflow_minor,
                "closing_minor": self.closing_minor}


@dataclass
class CashForecast:
    opening_minor: int
    weeks: list[CashWeek]
    assumptions: list[str]
    mape_pct: float | None
    backtest_points: int
    lowest_week: int | None
    lowest_minor: int | None

    def to_dict(self) -> dict:
        return {"opening_minor": self.opening_minor,
                "weeks": [w.to_dict() for w in self.weeks],
                "assumptions": self.assumptions, "mape_pct": self.mape_pct,
                "backtest_points": self.backtest_points,
                "lowest_week": self.lowest_week,
                "lowest_minor": self.lowest_minor,
                "goes_negative": self.lowest_minor is not None and self.lowest_minor < 0}


def cash_forecast(session, company_id: str, as_of: date | None = None,
                  weeks: int = 13) -> CashForecast:
    """13 долоо хоногийн мөнгөн урсгал — нэхэмжлэхийн хугацаа + түүхэн урсгал.

    Мэдэгдэж буй зүйлийг (нэхэмжлэхийн төлөх хугацаа) таамаглахгүй, шууд
    хуваарилна. Зөвхөн үлдсэн үйл ажиллагааны урсгалыг л таамаглана —
    тэр хэсгийн нарийвчлалыг backtest-ээр хэмжинэ."""
    from sqlalchemy import select

    from .ledger import trial_balance
    from .partners import Invoice, InvoiceKind
    from .reports import _collect_by_prefix

    as_of = as_of or date.today()

    tb = trial_balance(session, company_id, None, as_of)
    opening, _ = _collect_by_prefix(tb, ("10", "11"), side="debit")

    invoices = session.scalars(select(Invoice).where(
        Invoice.company_id == company_id)).all()

    # Үйл ажиллагааны сарын цэвэр урсгалыг түүхээс — таамаглах ганц хэсэг
    rev_hist = complete_history(session, company_id, ("5",), "credit", as_of, 18)
    exp_hist = complete_history(session, company_id, ("6", "7"), "debit", as_of, 18)
    net_series = [r["value_minor"] - e["value_minor"]
                  for r, e in zip(rev_hist, exp_hist)]

    try:
        monthly_net, method = predict_next(net_series)
    except NotEnoughHistory:
        monthly_net, method = 0, "түүх хангалтгүй — үйл ажиллагааны урсгалыг 0 гэж үзэв"
    mape, n = backtest(net_series) if net_series else (None, 0)
    weekly_net = int(monthly_net / 4.33)

    out: list[CashWeek] = []
    balance = opening
    for w in range(1, weeks + 1):
        start = as_of + timedelta(days=7 * (w - 1))
        end = start + timedelta(days=6)

        inflow = sum(i.outstanding_minor for i in invoices
                     if i.kind == InvoiceKind.sales
                     and i.outstanding_minor > 0 and start <= i.due_date <= end)
        outflow = sum(i.outstanding_minor for i in invoices
                      if i.kind == InvoiceKind.purchase
                      and i.outstanding_minor > 0 and start <= i.due_date <= end)

        if weekly_net >= 0:
            inflow += weekly_net
        else:
            outflow += -weekly_net

        balance += inflow - outflow
        out.append(CashWeek(w, start, inflow, outflow, balance))

    lowest = min(out, key=lambda w: w.closing_minor) if out else None
    return CashForecast(
        opening_minor=opening, weeks=out,
        assumptions=[
            "Нэхэмжлэх төлөх хугацаандаа бүрэн төлөгдөнө гэж үзэв",
            f"Үйл ажиллагааны сарын цэвэр урсгал {monthly_net / 100:,.0f}₮ "
            f"({method}), долоо хоногт {weekly_net / 100:,.0f}₮",
            "Зээл, хөрөнгө оруулалт, татварын төлөлтийг тусад нь тооцоогүй",
        ],
        mape_pct=mape, backtest_points=n,
        lowest_week=lowest.week if lowest else None,
        lowest_minor=lowest.closing_minor if lowest else None)
