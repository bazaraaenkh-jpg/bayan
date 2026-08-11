"""Метрикийн каталог — AI туслахын тоо гаргах ЦОРЫН ГАНЦ эх сурвалж (§5.3 L3).

LLM-д түүхий SQL хэзээ ч өгөхгүй. Хэрэглэгчийн асуултыг энд бүртгэгдсэн
метрикийн дуудлага болгож хөрвүүлээд, тоог гар бичсэн, тестлэгдсэн функцээр
ledger-ээс гаргана. LLM зөвхөн гарсан тоог ҮГЭЭР тайлбарлана.

Метрик бүр:
  * **эрхийн шошготой** — цалингийн метрик няравт ХЭЗЭЭ Ч буцахгүй
  * задаргаа ба эх дансаа хамт буцаана (хаанаас гарсныг харуулах)
  * тайлангийн модультай ижил функц дуудна — тоо СТ-1/СТ-2-той заавал таарна

Каталогт байхгүй асуултад таамаглахгүй, «мэдэхгүй» гэж хэлнэ — энэ дүрэм
assistant.py-д хэрэгждэг.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# Эрхийн шошго → тухайн шошготой метрикийг харж болох үүргүүд.
# Нярав (cashier), нярав-агуулах, viewer нар цалин харахгүй.
PERMISSION_ROLES: dict[str, tuple[str, ...]] = {
    "read": ("owner", "chief_accountant", "accountant", "approver",
             "cashier", "warehouse_manager", "viewer", "auditor"),
    "salary": ("owner", "chief_accountant", "accountant", "auditor"),
}


# ------------------------------------------------------------------ хугацаа

@dataclass(frozen=True)
class Period:
    date_from: date | None
    date_to: date
    label: str
    #: өгүүлбэрт ороход зохих хэлбэр — «2026 оны 7-р сард», «2026 онд»
    locative: str = ""

    def __post_init__(self):
        if not self.locative:
            object.__setattr__(self, "locative", self.label)

    def months(self) -> list[tuple[int, int]]:
        """Хугацаанд багтах (жил, сар) хосууд — цалингийн метрикт хэрэгтэй."""
        start = self.date_from or date(self.date_to.year, 1, 1)
        out, y, m = [], start.year, start.month
        while (y, m) <= (self.date_to.year, self.date_to.month):
            out.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out


def _month(year: int, month: int) -> Period:
    return Period(date(year, month, 1), _last_day(year, month),
                  f"{year} оны {month}-р сар", f"{year} оны {month}-р сард")


def _last_day(year: int, month: int) -> date:
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first_of_next - timedelta(days=1)


def _quarter(year: int, q: int) -> Period:
    first, last_m = 3 * q - 2, 3 * q
    return Period(date(year, first, 1), _last_day(year, last_m),
                  f"{year} оны {q}-р улирал", f"{year} оны {q}-р улиралд")


def _year(year: int) -> Period:
    return Period(date(year, 1, 1), date(year, 12, 31), f"{year} он", f"{year} онд")


_MONTH_RE = re.compile(r"(20\d{2})\s*он[ыийны]*\s*(\d{1,2})\s*-?\s*р?\s*сар")
_MONTH_ONLY_RE = re.compile(r"(?<!\d)(\d{1,2})\s*-?\s*р\s*сар")
_ISO_RE = re.compile(r"(20\d{2})[-/](\d{1,2})(?![\d-])")
# «улирал» нь «улирлын», «улиралд» болж хувирдаг тул үндсээр нь таана
_QUARTER_RE = re.compile(r"(?:(20\d{2})\s*он[ыийны]*\s*)?(\d)\s*-?\s*р\s*улир")
_YEAR_RE = re.compile(r"(20\d{2})\s*он(?!ы?\s*\d)")


def parse_period(text: str, today: date | None = None) -> Period:
    """Асуултаас хугацааг таьна. Олдохгүй бол энэ сар."""
    today = today or date.today()
    t = (text or "").lower()

    if m := _MONTH_RE.search(t):
        return _month(int(m.group(1)), min(max(int(m.group(2)), 1), 12))
    if m := _ISO_RE.search(t):
        return _month(int(m.group(1)), min(max(int(m.group(2)), 1), 12))
    if m := _QUARTER_RE.search(t):
        year = int(m.group(1)) if m.group(1) else today.year
        return _quarter(year, min(max(int(m.group(2)), 1), 4))

    if "өнгөрсөн жил" in t or "өмнөх жил" in t:
        return _year(today.year - 1)
    if "энэ жил" in t or "оны эхнээс" in t or "энэ он" in t:
        return Period(date(today.year, 1, 1), today,
                      f"{today.year} оны эхнээс {today.month}-р сарын "
                      f"{today.day} хүртэл")
    if "өнгөрсөн сар" in t or "өмнөх сар" in t:
        y, mth = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return _month(y, mth)
    if m := _YEAR_RE.search(t):
        return _year(int(m.group(1)))
    if m := _MONTH_ONLY_RE.search(t):
        return _month(today.year, min(max(int(m.group(1)), 1), 12))

    return _month(today.year, today.month)


# ------------------------------------------------------------------ үр дүн

@dataclass
class MetricResult:
    metric: str
    title: str
    period_label: str
    value_minor: int
    unit: str = "MNT"                       # MNT | count
    rows: list[dict] = field(default_factory=list)     # задаргаа
    accounts: list[str] = field(default_factory=list)  # эх данс (drill-down)
    source: str = ""
    compare: dict | None = None             # өмнөх үетэй харьцуулалт
    period_phrase: str = ""                 # өгүүлбэрт ороход зохих хэлбэр
    masked_rows: int = 0                    # эрхийн улмаас нуусан мөрийн тоо

    def __post_init__(self):
        if not self.period_phrase:
            self.period_phrase = self.period_label

    def to_dict(self) -> dict:
        return {
            "metric": self.metric, "title": self.title,
            "period": self.period_label, "value_minor": self.value_minor,
            "unit": self.unit, "rows": self.rows[:20], "accounts": self.accounts,
            "source": self.source, "compare": self.compare,
            "masked_rows": self.masked_rows,
        }


# ------------------------------------------------------------- тооцоолуурууд

def _income(session, company_id: str, p: Period) -> dict:
    from . import reports
    return reports.income_statement(session, company_id, p.date_from, p.date_to)


def _stock(session, company_id: str, p: Period, prefixes: tuple[str, ...],
           side: str, exclude: tuple[str, ...] = ()) -> tuple[int, list[dict]]:
    """Үлдэгдлийн метрик — эхнээс тухайн өдөр хүртэл хуримтлагдсан дүн."""
    from .ledger import trial_balance
    from .reports import _collect_by_prefix
    tb = trial_balance(session, company_id, None, p.date_to)
    return _collect_by_prefix(tb, prefixes, side=side, exclude=exclude)


def _flow_metric(name: str, title: str, key: str, breakdown_key: str | None,
                 source: str):
    def fn(session, company_id: str, p: Period) -> MetricResult:
        inc = _income(session, company_id, p)
        rows = inc.get("breakdown", {}).get(breakdown_key, []) if breakdown_key else []
        prior = inc.get(f"prior_{key}")
        return MetricResult(
            metric=name, title=title, period_label=p.label,
            period_phrase=p.locative, value_minor=inc[key],
            rows=[{"code": r["code"], "name": r["name"],
                   "amount_minor": r.get("amount_minor", 0)} for r in rows],
            accounts=sorted({r["code"] for r in rows}),
            source=source,
            compare=({"prior_value_minor": prior,
                      "prior_label": "өмнөх оны мөн үе"} if prior is not None else None),
        )
    return fn


def _stock_metric(name: str, title: str, prefixes: tuple[str, ...], side: str,
                  source: str, exclude: tuple[str, ...] = ()):
    def fn(session, company_id: str, p: Period) -> MetricResult:
        total, rows = _stock(session, company_id, p, prefixes, side, exclude)
        return MetricResult(
            metric=name, title=title,
            period_label=f"{p.date_to:%Y-%m-%d}-ны байдлаар",
            value_minor=total, rows=rows,
            accounts=sorted({r["code"] for r in rows}), source=source)
    return fn


def _top_expenses(session, company_id: str, p: Period) -> MetricResult:
    inc = _income(session, company_id, p)
    rows = sorted(inc.get("breakdown", {}).get("expenses", []),
                  key=lambda r: r.get("amount_minor", 0), reverse=True)[:10]
    return MetricResult(
        metric="top_expenses", title="Хамгийн их зардлын данснууд",
        period_label=p.label, period_phrase=p.locative, value_minor=sum(r.get("amount_minor", 0) for r in rows),
        rows=[{"code": r["code"], "name": r["name"],
               "amount_minor": r.get("amount_minor", 0)} for r in rows],
        accounts=sorted({r["code"] for r in rows}),
        source="Ерөнхий журнал — 6, 7 бүлгийн данснууд, дүнгээр эрэмбэлэв")


def _payroll(session, company_id: str, p: Period) -> MetricResult:
    from sqlalchemy import select
    from .salary import Employee, PayrollLine

    months = p.months()
    lines = [l for l in session.scalars(select(PayrollLine).where(
        PayrollLine.company_id == company_id)) if (l.year, l.month) in months]

    rows = []
    for l in lines:
        emp = session.get(Employee, l.employee_id)
        rows.append({
            "code": emp.code if emp else "",
            "name": f"{emp.last_name} {emp.first_name}" if emp else "—",
            "amount_minor": l.gross_minor,
        })
    rows.sort(key=lambda r: r["amount_minor"], reverse=True)

    return MetricResult(
        metric="payroll", title="Цалингийн зардал (бохир)",
        period_label=p.label, period_phrase=p.locative, value_minor=sum(l.gross_minor for l in lines),
        rows=rows, accounts=["7101", "3102"],
        source="Цалингийн бүртгэл (payroll_line) — ажилтан тус бүрийн бохир цалин",
        compare={"employees": len({l.employee_id for l in lines}),
                 "net_minor": sum(l.net_minor for l in lines),
                 "hhoat_minor": sum(l.hhoat_minor for l in lines)})


def _unclassified(session, company_id: str, p: Period) -> MetricResult:
    from sqlalchemy import func, select
    from .models import ClassificationSuggestion

    n = session.scalar(select(func.count()).select_from(ClassificationSuggestion)
                       .where(ClassificationSuggestion.company_id == company_id,
                              ClassificationSuggestion.status == "pending")) or 0
    return MetricResult(
        metric="unclassified", title="Батлагдаагүй ангиллын санал",
        period_label="одоогийн байдлаар", value_minor=int(n), unit="count",
        source="Ангиллын санал (classification_suggestion), төлөв = pending")


def _vat(session, company_id: str, p: Period) -> MetricResult:
    payable, rows_p = _stock(session, company_id, p, ("3105",), "credit")
    claimable, rows_c = _stock(session, company_id, p, ("1203",), "debit")
    return MetricResult(
        metric="vat", title="НӨАТ-ын цэвэр байдал",
        period_label=f"{p.date_to:%Y-%m-%d}-ны байдлаар",
        value_minor=payable - claimable,
        rows=rows_p + rows_c, accounts=["3105", "1203"],
        source="3105 НӨАТ-ын өглөг хасах 1203 НӨАТ-ын авлага",
        compare={"payable_minor": payable, "claimable_minor": claimable})


# -------------------------------------------------- таамаглал ба гажилт (L2/L4)

def _forecast_result(f, metric: str) -> MetricResult:
    """Таамаглалыг метрикийн хэлбэрт буулгана.

    Нарийвчлалыг `compare`-д ЗААВАЛ хийнэ — assistant үүнийг хариулт бүрд
    хавсаргана. «Тоо ганцаараа гарахгүй» гэсэн дүрэм ингэж хэрэгждэг."""
    return MetricResult(
        metric=metric, title=f.title, period_label=f.period_label,
        period_phrase=f.period_label, value_minor=f.value_minor,
        rows=[{"code": h["label"], "name": "бодит гүйцэтгэл",
               "amount_minor": h["value_minor"]} for h in f.history[-6:]],
        source=f"{f.method} — {f.note}",
        compare={"mape_pct": f.mape_pct, "backtest_points": f.backtest_points,
                 "low_minor": f.low_minor, "high_minor": f.high_minor,
                 "trustworthy": f.trustworthy, "method": f.method})


def _revenue_forecast(session, company_id: str, p: Period) -> MetricResult:
    from . import forecast as fc
    return _forecast_result(
        fc.revenue_forecast(session, company_id, p.date_to), "revenue_forecast")


def _expense_forecast(session, company_id: str, p: Period) -> MetricResult:
    from . import forecast as fc
    return _forecast_result(
        fc.expense_forecast(session, company_id, p.date_to), "expense_forecast")


def _cash_forecast(session, company_id: str, p: Period) -> MetricResult:
    from . import forecast as fc

    cf = fc.cash_forecast(session, company_id, p.date_to)
    low = cf.lowest_minor if cf.lowest_minor is not None else 0
    return MetricResult(
        metric="cash_forecast", title="13 долоо хоногийн мөнгөн урсгал",
        period_label=f"{p.date_to:%Y-%m-%d}-наас хойш 13 долоо хоног",
        period_phrase=f"{p.date_to:%Y-%m-%d}-наас хойших 13 долоо хоногт",
        value_minor=low,
        rows=[{"code": f"{w.week}-р 7 хоног", "name": w.starts_on.isoformat(),
               "amount_minor": w.closing_minor} for w in cf.weeks],
        source="; ".join(cf.assumptions),
        compare={"opening_minor": cf.opening_minor,
                 "lowest_week": cf.lowest_week,
                 "goes_negative": low < 0,
                 "mape_pct": cf.mape_pct,
                 "backtest_points": cf.backtest_points})


def _balance_check(session, company_id: str, p: Period) -> MetricResult:
    from .ledger import check_balance

    res = check_balance(session, company_id, None, p.date_to)
    return MetricResult(
        metric="balance_check", title="Дебит/кредитийн тэнцэл",
        period_label=f"{p.date_to:%Y-%m-%d}-ны байдлаар",
        period_phrase=f"{p.date_to:%Y-%m-%d}-ны байдлаар",
        value_minor=res["difference_minor"],
        rows=[{"code": e["entry_date"] or "", "name": e["memo"] or "—",
               "amount_minor": e["difference_minor"]}
              for e in res["unbalanced_entries"][:10]],
        accounts=[], source="Ерөнхий журналын бүх батлагдсан бичилт",
        compare={"balanced": res["balanced"],
                 "entry_count": res["entry_count"],
                 "total_debit_minor": res["total_debit_minor"],
                 "total_credit_minor": res["total_credit_minor"],
                 "unbalanced_count": res["unbalanced_count"],
                 "checks": res["checks"]})


def _anomalies(session, company_id: str, p: Period) -> MetricResult:
    from . import anomalies as an

    res = an.scan(session, company_id, p.date_to.year, p.date_to.month, p.date_to)
    return MetricResult(
        metric="anomalies", title="Илэрсэн гажилт", period_label=p.label,
        period_phrase=p.locative, value_minor=res["total"], unit="count",
        rows=[{"code": a["severity"], "name": a["title"],
               "amount_minor": a["amount_minor"]} for a in res["anomalies"][:20]],
        source="Давхар төлөлт, дансны буруу хослол, цалингийн гажилт, "
               "НӨАТ-ын зөрүү, ирээдүйн огноо",
        compare={"by_severity": res["by_severity"],
                 "details": [a["detail"] for a in res["anomalies"][:5]]})


# ------------------------------------------------------------------ каталог

@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    description: str
    permission: str
    keywords: tuple[str, ...]
    fn: object


CATALOG: dict[str, Metric] = {}


def _register(name, title, description, permission, keywords, fn):
    CATALOG[name] = Metric(name, title, description, permission, keywords, fn)


_register("revenue", "Борлуулалтын орлого",
          "Тайлант үеийн борлуулалтын орлого (СТ-2-ийн орлогын мөр)", "read",
          ("борлуулалт", "орлого", "revenue", "sales", "хэдэн төгрөгийн бараа"),
          _flow_metric("revenue", "Борлуулалтын орлого", "revenue_minor",
                       "revenue", "Орлогын тайлан СТ-2 — 5 бүлгийн данснууд"))

_register("cogs", "Борлуулсан бүтээгдэхүүний өртөг",
          "Тайлант үеийн ББӨ", "read",
          ("өртөг", "ббө", "cogs", "борлуулсан бүтээгдэхүүний"),
          _flow_metric("cogs", "Борлуулсан бүтээгдэхүүний өртөг", "cogs_minor",
                       "cogs", "Орлогын тайлан СТ-2 — 6 бүлгийн данснууд"))

_register("gross_profit", "Нийт ашиг",
          "Орлого хасах ББӨ", "read",
          ("нийт ашиг", "gross profit", "бохир ашиг"),
          _flow_metric("gross_profit", "Нийт ашиг", "gross_profit_minor",
                       None, "Орлогын тайлан СТ-2 — орлого хасах ББӨ"))

_register("expenses", "Үйл ажиллагааны зардал",
          "Тайлант үеийн нийт зардал", "read",
          ("зардал", "expense", "хэр их зарцуулсан", "зарлага"),
          _flow_metric("expenses", "Үйл ажиллагааны зардал", "expenses_minor",
                       "expenses", "Орлогын тайлан СТ-2 — 7 бүлгийн данснууд"))

_register("net_income", "Цэвэр ашиг",
          "Тайлант үеийн цэвэр ашиг/алдагдал", "read",
          ("цэвэр ашиг", "ашиг", "алдагдал", "profit", "ашигтай"),
          _flow_metric("net_income", "Цэвэр ашиг", "net_income_minor",
                       None, "Орлогын тайлан СТ-2 — цэвэр ашгийн мөр"))

_register("cash", "Мөнгөн хөрөнгө",
          "Касс ба харилцах дансны үлдэгдэл", "read",
          ("мөнгө", "касс", "харилцах", "үлдэгдэл", "cash", "данс дээр"),
          _stock_metric("cash", "Мөнгөн хөрөнгө", ("10", "11"), "debit",
                        "Гүйлгээний баланс — 10 касс, 11 харилцах данс"))

_register("receivables", "Авлага",
          "Худалдан авагч болон бусдаас авах авлага", "read",
          ("авлага", "receivable", "авах өр", "төлөөгүй"),
          _stock_metric("receivables", "Авлага", ("12",), "debit",
                        "Гүйлгээний баланс — 12 бүлгийн авлагын данснууд"))

_register("payables", "Дансны өглөг",
          "Нийлүүлэгчид төлөх өглөг (татвар, цалингаас бусад)", "read",
          ("өглөг", "payable", "төлөх өр", "нийлүүлэгчид"),
          _stock_metric("payables", "Дансны өглөг", ("3101", "3109"), "credit",
                        "Гүйлгээний баланс — 3101, 3109 данс"))

_register("tax_payable", "Татварын өглөг",
          "НДШ, ААНОАТ, НӨАТ, ХХОАТ-ын өглөгийн үлдэгдэл", "read",
          ("татварын өглөг", "татвар төлөх", "тевд өгөх", "татварын өр"),
          _stock_metric("tax_payable", "Татварын өглөг",
                        ("3103", "3104", "3105", "3106"), "credit",
                        "Гүйлгээний баланс — 3103-3106 татварын данснууд"))

_register("vat", "НӨАТ", "НӨАТ-ын өглөг хасах авлага", "read",
          ("нөат", "vat", "нэмэгдсэн өртөг"), _vat)

_register("top_expenses", "Хамгийн их зардал",
          "Тайлант үеийн хамгийн том зардлын данснууд", "read",
          ("хамгийн их зардал", "юунд хамгийн", "том зардал", "хаана зарцуулсан"),
          _top_expenses)

_register("payroll", "Цалингийн зардал",
          "Ажилтнуудын бохир цалин, ХХОАТ, гарт олгосон дүн", "salary",
          # «цалингийн зардал» нь «зардал»-аас урт тул зардлын метрикийг дийлнэ
          ("цалингийн зардал", "цалингийн сан", "цалин", "salary", "payroll",
           "ажилтны цалин", "хөлс"), _payroll)

_register("revenue_forecast", "Борлуулалтын таамаг",
          "Дараагийн сарын борлуулалтын таамаг, нарийвчлалынхаа хамт", "read",
          # «борлуулалтын таамаг» нь «борлуулалт»-аас урт тул түүнийг дийлнэ
          ("борлуулалтын таамаг", "борлуулалт хэд болох", "орлогын таамаг",
           "таамаг", "прогноз", "хэд болох бол"), _revenue_forecast)

_register("expense_forecast", "Зардлын таамаг",
          "Дараагийн сарын зардлын таамаг", "read",
          ("зардлын таамаг", "зардал хэд болох"), _expense_forecast)

_register("cash_forecast", "Мөнгөн урсгалын таамаг",
          "13 долоо хоногийн мөнгөн урсгал, хамгийн доод цэг", "read",
          ("мөнгөн урсгалын таамаг", "13 долоо хоног", "мөнгө хүрэлцэх",
           "мөнгө дуусах", "мөнгөн урсгал"), _cash_forecast)

_register("balance_check", "Дебит/кредитийн тэнцэл",
          "Дэвтэр тэнцэж байгаа эсэх, тэнцэхгүй бол аль бичилт вэ", "read",
          ("тэнцэл", "тэнцэж байна уу", "тэнцсэн үү", "дебит кредит",
           "дебет кредит", "баланс тэнцэх", "тэнцэхгүй"), _balance_check)

_register("anomalies", "Илэрсэн гажилт",
          "Давхар төлөлт, дансны буруу хослол, цалингийн гажилт", "read",
          ("гажилт", "сэжигтэй", "алдаа байна уу", "давхар төлөлт",
           "шалгах зүйл"), _anomalies)

_register("unclassified", "Батлагдаагүй ангилал",
          "Нябо-гийн хянах шаардлагатай ангиллын саналын тоо", "read",
          ("ангилаагүй", "батлагдаагүй", "хүлээгдэж", "хянах"), _unclassified)


def allowed(metric: Metric, role: str) -> bool:
    return role in PERMISSION_ROLES.get(metric.permission, ())


def catalog_for(role: str) -> list[Metric]:
    """Тухайн үүрэгт харагдах метрикүүд — LLM-д ч энэ жагсаалт л очно."""
    return [m for m in CATALOG.values() if allowed(m, role)]


#: Цалингийн мэдээлэл агуулсан данснууд — эрхгүй хэрэглэгчид задаргаанд
#: харагдахгүй. Нийт дүн (жишээ нь бүх зардлын нийлбэр) үлдэнэ, зөвхөн
#: мөрийн түвшний задаргаа нуугдана.
SALARY_ACCOUNTS = ("7101", "7102", "3102")


def compute(session, company_id: str, name: str, period: Period,
            role: str = "owner") -> MetricResult:
    """Метрикийг тооцоод, хэрэглэгчийн эрхэд тохируулж задаргааг шүүнэ.

    Зардлын нийт дүн нь цалинг агуулсан хэвээр (санхүүгийн тайлангийн
    ердийн үзүүлэлт), гэхдээ «7101 Цалингийн зардал — X₮» гэсэн МӨР
    эрхгүй хэрэглэгчид очихгүй — тэр нь цалингийн дүнг шууд задруулна."""
    result = CATALOG[name].fn(session, company_id, period)

    if role not in PERMISSION_ROLES["salary"]:
        kept = [r for r in result.rows
                if not str(r.get("code", "")).startswith(SALARY_ACCOUNTS)]
        result.masked_rows = len(result.rows) - len(kept)
        result.rows = kept
        result.accounts = [c for c in result.accounts
                           if not c.startswith(SALARY_ACCOUNTS)]
    return result
