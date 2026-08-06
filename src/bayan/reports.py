"""Санхүүгийн тайлан — СТ-1 (Санхүү байдал), СТ-2 (Орлогын тайлан), СТ-3 (Өмчийн өөрчлөлт), СТ-4 (Мөнгөн гүйлгээ).

Гүйлгээ балансаас (ledger.trial_balance) кодын бүлгээр нэгтгэнэ.
СТ-1-ийн баланс барих invariant: Актив = Өр төлбөр + Өмч + Тайлант ашиг.
"""

from __future__ import annotations

from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .ledger import trial_balance
from .models import Account, JournalEntry, JournalLine, EntryStatus


# СТ-1-ийн бүлэглэлт: (мөрийн нэр, кодын угтварууд[, тооцохгүй угтварууд])
# Гурав дахь элемент нь тухайн мөрөөс ХАСАХ дансны угтваруудыг заана — өөр мөрөнд
# тусад нь харуулдаг хасагдуулагч (contra) данс давхардан тооцогдохоос сэргийлнэ.
DEPRECIATION_CODES = ("2509", "2609", "2002")

BS_ASSET_ROWS = [
    ("Мөнгө ба түүнтэй адилтгах хөрөнгө", ("10", "11")),
    # 1209 нь найдваргүй авлагын хасалт — авлагын мөрөөс шууд хасагдана
    ("Дансны авлага",                    ("1201", "1202", "1203", "1206", "1207", "1209")),
    ("Татвар, НДШ-ийн авлага",           ("1204", "1205")),
    # 2199 хорогдол мөн энэ мөрөнд хасагдана
    ("Бараа материал",                    ("21",)),
    ("Урьдчилж төлсөн зардал, хангамж",   ("14", "15")),
    # "12"/"31"-ийн адил "25"/"26" бүлгийг бүхэлд нь авч, хасагдуулагч дансыг
    # тусдаа мөрөнд харуулна — ингэснээр аль ч данс тайлангаас гээгдэхгүй
    ("Үндсэн хөрөнгө",                    ("2001", "25", "26"), DEPRECIATION_CODES),
    ("Хуримтлагдсан элэгдэл",            DEPRECIATION_CODES),
]

BS_LIAB_ROWS = [
    ("Дансны өглөг",                     ("3101", "3109")),
    ("Цалингийн өглөг",                  ("3102", "3301")),
    ("Татвар, НДШ-ийн өглөг",            ("3103", "3104", "3105", "3106",
                                          "3401", "3402", "3403")),
    ("Богино хугацаат зээл",             ("32",)),
    # 31-ийн үлдсэн бүх данс энд орж, шинэ дэд данс нэмэгдсэн ч гээгдэхгүй
    ("Бусад богино хугацаат өр төлбөр",  ("3107", "3108", "31"),
                                         ("3101", "3102", "3103", "3104",
                                          "3105", "3106", "3109", "3301",
                                          "3401", "3402", "3403")),
    ("Урт хугацаат өр төлбөр",           ("33", "34", "35", "36", "37", "38", "39"),
                                         ("3301", "3401", "3402", "3403")),
]

# СТ-2-ын бүлэглэлт. 5x бүхэлдээ орлого, 6x өртөг, 7x зардал болж хуваагдана —
# ингэснээр шинэ дэд данс нэмэгдсэн ч аль нэг бүлэгт заавал орж, ашиг тооцоолол
# (мөн түүгээр дамжин СТ-1-ийн өмч) гажихгүй.
IS_REVENUE_PREFIXES = ("51",)
IS_OTHER_INCOME_PREFIXES = ("50", "52", "53", "54", "55", "56", "57", "58", "59")
IS_COGS_PREFIXES = ("60", "61", "62", "63", "64", "65", "66", "67", "68", "69")
IS_EXPENSE_PREFIXES = ("70", "71", "72", "73", "74", "75", "76", "77", "78", "79")

BS_EQUITY_ROWS = [
    # 45xx нь "Хуримтлагдсан ашиг" мөрөнд ордог тул "41"-ийн бүлгээс тусад нь
    ("Хувь нийлүүлсэн хөрөнгө",          ("4101", "41", "42", "43", "44")),
    ("Хуримтлагдсан ашиг",               ("4501", "4502", "4503", "45", "46",
                                          "47", "48", "49")),
]


def _prior_period(date_from: date | None, date_to: date | None) -> tuple[date | None, date | None]:
    """Өмнөх оны харгалзах хугацааг тооцоолно."""
    if not date_to:
        return None, None
    try:
        p_from = date(date_from.year - 1, date_from.month, date_from.day) if date_from else None
        p_to = date(date_to.year - 1, date_to.month, date_to.day)
        return p_from, p_to
    except ValueError:
        # Үсрэнгүй жил (Feb 29)-ийн тохируулга
        p_from = date(date_from.year - 1, date_from.month, date_from.day - 1) if date_from else None
        p_to = date(date_to.year - 1, date_to.month, date_to.day - 1)
        return p_from, p_to


def _collect_by_prefix(tb: list[dict], prefixes: tuple[str, ...],
                       side: str = "debit",
                       exclude: tuple[str, ...] = ()) -> tuple[int, list[dict]]:
    """Кодын угтвараар нэгтгэж, данс тус бүрийн задаргааг хамт буцаана.
    side нь тайлангийн талыг заана: актив мөрөнд debit (contra-asset кредит
    данс автоматаар хасагдана), өр/өмчийн мөрөнд credit.
    exclude угтварт таарсан данс тухайн мөрөнд огт орохгүй."""
    total = 0
    accounts = []
    for r in tb:
        if exclude and any(r["code"].startswith(p) for p in exclude):
            continue
        if any(r["code"].startswith(p) for p in prefixes):
            net_debit = r["debit_minor"] - r["credit_minor"]
            amount = net_debit if side == "debit" else -net_debit
            total += amount
            if amount:
                accounts.append({
                    "code": r["code"],
                    "name": r["name"],
                    "amount_minor": amount,
                })
    accounts.sort(key=lambda a: a["code"])
    return total, accounts


def _sum_by_prefix(tb: list[dict], prefixes: tuple[str, ...],
                   side: str = "debit") -> int:
    """Кодын угтвараар нэгтгэсэн дүн (задаргаагүй)."""
    return _collect_by_prefix(tb, prefixes, side)[0]


def _merge_accounts(cur: list[dict], prior: list[dict]) -> list[dict]:
    """Тайлант ба өмнөх үеийн дансны задаргааг код тус бүрээр нийлүүлнэ."""
    merged: dict[str, dict] = {}
    for a in cur:
        merged[a["code"]] = {**a, "prior_amount_minor": 0}
    for a in prior:
        row = merged.get(a["code"])
        if row:
            row["prior_amount_minor"] = a["amount_minor"]
        else:
            merged[a["code"]] = {
                "code": a["code"],
                "name": a["name"],
                "amount_minor": 0,
                "prior_amount_minor": a["amount_minor"],
            }
    return sorted(merged.values(), key=lambda a: a["code"])


def _statement_row(spec: tuple, side: str,
                   tb: list[dict], prior_tb: list[dict],
                   has_prior: bool) -> dict:
    """СТ-1-ийн нэг мөр: нийт дүн + харьцуулалт + дансны задаргаа.
    spec нь (нэр, угтварууд[, хасах угтварууд])."""
    name, prefixes = spec[0], spec[1]
    exclude = spec[2] if len(spec) > 2 else ()
    total, accounts = _collect_by_prefix(tb, prefixes, side, exclude)
    p_total, p_accounts = (_collect_by_prefix(prior_tb, prefixes, side, exclude)
                           if has_prior else (0, []))
    return {
        "name": name,
        "codes": list(prefixes),
        "amount_minor": total,
        "prior_amount_minor": p_total,
        "accounts": _merge_accounts(accounts, p_accounts),
    }


def _income_position(tb: list[dict]) -> dict:
    """Гүйлгээ балансаас СТ-2-ын бүлгүүдийг задаргаатай нь салгана."""
    revenue, revenue_acc = _collect_by_prefix(tb, IS_REVENUE_PREFIXES, "credit")
    other, other_acc = _collect_by_prefix(tb, IS_OTHER_INCOME_PREFIXES, "credit")
    cogs, cogs_acc = _collect_by_prefix(tb, IS_COGS_PREFIXES, "debit")
    expenses, expenses_acc = _collect_by_prefix(tb, IS_EXPENSE_PREFIXES, "debit")
    return {
        "revenue": (revenue, revenue_acc),
        "other_income": (other, other_acc),
        "cogs": (cogs, cogs_acc),
        "expenses": (expenses, expenses_acc),
        "net_income": revenue + other - cogs - expenses,
    }


def income_statement(session: Session, company_id: str,
                     date_from: date | None = None,
                     date_to: date | None = None) -> dict:
    """СТ-2: Орлого + Бусад орлого − ББӨ − Зардал = Тайлант үеийн ашиг
    (Харьцуулсан оны хамт)."""
    tb = trial_balance(session, company_id, date_from, date_to)
    pos = _income_position(tb)
    revenue, revenue_acc = pos["revenue"]
    other, other_acc = pos["other_income"]
    cogs, cogs_acc = pos["cogs"]
    expenses, expenses_acc = pos["expenses"]

    res = {
        "revenue_minor": revenue,
        "other_income_minor": other,
        "cogs_minor": cogs,
        "gross_profit_minor": revenue - cogs,
        "expenses_minor": expenses,
        "net_income_minor": pos["net_income"],
        "detail": [r for r in tb if r["code"][0] in "567" and r["balance_minor"]],
    }

    # Өмнөх оны харьцуулалт бодох
    p_from, p_to = _prior_period(date_from, date_to)
    p_revenue_acc: list[dict] = []
    p_other_acc: list[dict] = []
    p_cogs_acc: list[dict] = []
    p_expenses_acc: list[dict] = []
    if p_to:
        p_pos = _income_position(trial_balance(session, company_id, p_from, p_to))
        p_revenue, p_revenue_acc = p_pos["revenue"]
        p_other, p_other_acc = p_pos["other_income"]
        p_cogs, p_cogs_acc = p_pos["cogs"]
        p_expenses, p_expenses_acc = p_pos["expenses"]
        res.update({
            "prior_revenue_minor": p_revenue,
            "prior_other_income_minor": p_other,
            "prior_cogs_minor": p_cogs,
            "prior_gross_profit_minor": p_revenue - p_cogs,
            "prior_expenses_minor": p_expenses,
            "prior_net_income_minor": p_pos["net_income"],
        })

    # Мөр бүрийн дансны задаргаа (dropdown-д ашиглана)
    res["breakdown"] = {
        "revenue": _merge_accounts(revenue_acc, p_revenue_acc),
        "other_income": _merge_accounts(other_acc, p_other_acc),
        "cogs": _merge_accounts(cogs_acc, p_cogs_acc),
        "expenses": _merge_accounts(expenses_acc, p_expenses_acc),
    }
    return res


def balance_sheet(session: Session, company_id: str,
                   as_of: date | None = None) -> dict:
    """СТ-1: Санхүү байдлын тайлан (Харьцуулсан оны хамт)."""
    tb = trial_balance(session, company_id, None, as_of)
    inc = income_statement(session, company_id, None, as_of)

    # Өмнөх оны харьцуулалт бодох
    p_as_of = None
    prior_tb = []
    prior_inc = {}
    if as_of:
        _, p_as_of = _prior_period(None, as_of)
        if p_as_of:
            prior_tb = trial_balance(session, company_id, None, p_as_of)
            prior_inc = income_statement(session, company_id, None, p_as_of)

    has_prior = bool(p_as_of)

    assets = [_statement_row(spec, "debit", tb, prior_tb, has_prior)
              for spec in BS_ASSET_ROWS]
    liabs = [_statement_row(spec, "credit", tb, prior_tb, has_prior)
             for spec in BS_LIAB_ROWS]
    equity = [_statement_row(spec, "credit", tb, prior_tb, has_prior)
              for spec in BS_EQUITY_ROWS]

    # Тайлант үеийн ашгийг нэмнэ — задаргаа нь орлого/ББӨ/зардлын мөрүүд
    inc_bd = inc.get("breakdown", {})
    prior_bd = prior_inc.get("breakdown", {}) if has_prior else {}

    # (код, түлхүүр, нэр, тэмдэг) — ашгийн бүрдүүлэгч мөрүүд.
    # Код нь мөр тус бүрд ДАВТАГДАХГҮЙ байх ёстой: хэрэглэгчийн тал задаргааг
    # кодоор нь түлхүүрлэн нэгтгэдэг тул давхардвал мөрүүд бие биенээ дарна.
    INCOME_LINES = [
        ("51",    "revenue",      "Борлуулалтын орлого",       1),
        ("52-59", "other_income", "Бусад орлого",              1),
        ("6x",    "cogs",         "Борлуулалтын өртөг (ББӨ)", -1),
        ("7x",    "expenses",     "Үйл ажиллагааны зардал",   -1),
    ]

    def _income_line(code: str, key: str, name: str, sign: int) -> dict:
        return {
            "code": code,
            "name": name,
            "amount_minor": sign * sum(a["amount_minor"] for a in inc_bd.get(key, [])),
            "prior_amount_minor": sign * sum(
                a["amount_minor"] for a in prior_bd.get(key, [])),
        }

    equity.append({
        "name": "Тайлант үеийн ашиг (алдагдал)",
        "codes": ["5x", "6x", "7x"],
        "amount_minor": inc["net_income_minor"],
        "prior_amount_minor": prior_inc.get("net_income_minor", 0) if has_prior else 0,
        "accounts": [_income_line(*spec) for spec in INCOME_LINES],
    })

    total_assets = sum(r["amount_minor"] for r in assets)
    total_le = (sum(r["amount_minor"] for r in liabs)
                + sum(r["amount_minor"] for r in equity))
                
    prior_total_assets = sum(r["prior_amount_minor"] for r in assets) if p_as_of else 0
    prior_total_le = (sum(r["prior_amount_minor"] for r in liabs)
                      + sum(r["prior_amount_minor"] for r in equity)) if p_as_of else 0

    return {
        "assets": assets, "liabilities": liabs, "equity": equity,
        "total_assets_minor": total_assets,
        "total_liab_equity_minor": total_le,
        "balanced": total_assets == total_le,
        "prior_total_assets_minor": prior_total_assets,
        "prior_total_liab_equity_minor": prior_total_le,
        "prior_balanced": prior_total_assets == prior_total_le if p_as_of else True,
    }


def _equity_position(session: Session, company_id: str, as_of: date) -> dict:
    """Тухайн өдрийн эздийн өмчийн байрлал — СТ-1-ийн ижил бүлэглэлтээр.
    Хаалтын бичилт хийгээгүй тул хуримтлагдсан ашигт өнөөг хүртэлх нийт
    цэвэр ашгийг нэмж тооцно (СТ-1-ийн өмчтэй яг таарна)."""
    tb = trial_balance(session, company_id, None, as_of)
    capital, capital_acc = _collect_by_prefix(
        tb, BS_EQUITY_ROWS[0][1], "credit",
        BS_EQUITY_ROWS[0][2] if len(BS_EQUITY_ROWS[0]) > 2 else ())
    retained_posted, retained_acc = _collect_by_prefix(
        tb, BS_EQUITY_ROWS[1][1], "credit",
        BS_EQUITY_ROWS[1][2] if len(BS_EQUITY_ROWS[1]) > 2 else ())
    cumulative_profit = _income_position(tb)["net_income"]
    return {
        "capital": capital,
        "retained": retained_posted + cumulative_profit,
        "capital_accounts": capital_acc,
        "retained_accounts": retained_acc,
    }


def statement_of_changes_in_equity(
    session: Session, company_id: str,
    date_from: date, date_to: date,
) -> dict:
    """СТ-3: Өмчийн өөрчлөлтийн тайлан.

    Эхний үлдэгдэл + Тайлант үеийн ашиг + Бусад өөрчлөлт = Эцсийн үлдэгдэл.
    Эцсийн үлдэгдэл нь СТ-1-ийн "Нийт эздийн өмч"-тэй заавал тэнцэнэ.
    """
    from datetime import timedelta

    p_from, p_to = _prior_period(date_from, date_to)

    def get_data(d_from: date, d_to: date) -> dict:
        start = _equity_position(session, company_id, d_from - timedelta(days=1))
        end = _equity_position(session, company_id, d_to)
        net_income = income_statement(session, company_id, d_from, d_to)["net_income_minor"]

        # Ашгаар тайлбарлагдахгүй үлдсэн хэсэг: хувь нэмэгдүүлэлт, ногдол ашиг г.м.
        capital_change = end["capital"] - start["capital"]
        retained_change = end["retained"] - start["retained"] - net_income

        return {
            "start": {"capital": start["capital"], "retained": start["retained"],
                      "total": start["capital"] + start["retained"]},
            "net_income": {"capital": 0, "retained": net_income, "total": net_income},
            "changes": {"capital": capital_change, "retained": retained_change,
                        "total": capital_change + retained_change},
            "end": {"capital": end["capital"], "retained": end["retained"],
                    "total": end["capital"] + end["retained"]},
            "end_accounts": {
                "capital": end["capital_accounts"],
                "retained": end["retained_accounts"],
            },
        }

    res = {"current": get_data(date_from, date_to)}
    if p_to and p_from:
        res["prior"] = get_data(p_from, p_to)
    return res


# ---------------------------------------------------------------- СТ-4 бүлэглэлт
CF_DETAIL_KEYS = (
    "op_in_sales", "op_in_other",
    "op_out_materials", "op_out_salary", "op_out_ndsh", "op_out_tax", "op_out_other",
    "inv_in_assets", "inv_out_assets",
    "fin_in_capital", "fin_in_loans", "fin_out_dividends", "fin_out_loans",
)

# Мөнгө ОРОХ үед эсрэг дансны кодоор ангилах дараалал (эхний таарсан нь хүчинтэй)
CF_INFLOW_RULES = (
    (("51", "12"),                    "op_in_sales"),
    (("52", "53", "54"),              "op_in_other"),
    (("25", "26", "13"),              "inv_in_assets"),
    (("41", "42", "43", "44"),        "fin_in_capital"),
    (("32", "35"),                    "fin_in_loans"),
)

# Мөнгө ГАРАХ үед
CF_OUTFLOW_RULES = (
    (("61", "21", "3101", "3109"),    "op_out_materials"),
    (("3102", "3301", "7101"),        "op_out_salary"),
    (("3103", "7102"),                "op_out_ndsh"),
    (("3104", "3105", "3106", "3401", "3402", "3403", "7120"), "op_out_tax"),
    (("25", "26", "13"),              "inv_out_assets"),
    (("32", "35"),                    "fin_out_loans"),
    (("3107", "4503", "45", "41"),    "fin_out_dividends"),
)


def _cf_bucket(code: str, is_inflow: bool) -> str:
    """Эсрэг дансны кодоор мөнгөн гүйлгээний ангиллыг тодорхойлно.
    Аль ч дүрэмд таараагүй тохиолдолд үйл ажиллагааны гүйлгээ гэж үзнэ."""
    rules = CF_INFLOW_RULES if is_inflow else CF_OUTFLOW_RULES
    for prefixes, bucket in rules:
        if code.startswith(prefixes):
            return bucket
    return "op_in_sales" if is_inflow else "op_out_other"


def cash_flow_statement(
    session: Session, company_id: str,
    date_from: date, date_to: date,
) -> dict:
    """СТ-4: Мөнгөн гүйлгээний тайлан (Шууд арга)."""
    p_from, p_to = _prior_period(date_from, date_to)

    def get_flow(d_from: date, d_to: date) -> dict:
        # Cash/Bank дансны ID-уудыг олно
        cash_acc_ids = [a.id for a in session.scalars(
            select(Account).where(
                Account.company_id == company_id,
                Account.code.like("10%") | Account.code.like("11%")
            )
        )]
        if not cash_acc_ids:
            return {
                "operating_inflow": 0, "operating_outflow": 0, "operating_net": 0,
                "investing_inflow": 0, "investing_outflow": 0, "investing_net": 0,
                "financing_inflow": 0, "financing_outflow": 0, "financing_net": 0,
                "net_change": 0,
                "detail": dict.fromkeys(CF_DETAIL_KEYS, 0),
            }

        # Posted журналын бичилтүүдээс Cash/Bank данстай холбоотой мөрүүдийг татна
        q = (
            select(JournalEntry)
            .join(JournalLine)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.entry_date >= d_from,
                JournalEntry.entry_date <= d_to,
                JournalEntry.status == EntryStatus.posted,
                JournalLine.account_id.in_(cash_acc_ids)
            )
            .options(selectinload(JournalEntry.lines))
            .distinct()
        )
        entries = session.scalars(q).all()

        # Дансны код бэлдэх
        acc_codes = {a.id: a.code for a in session.scalars(
            select(Account).where(Account.company_id == company_id))}

        flow = dict.fromkeys(CF_DETAIL_KEYS, 0)

        for entry in entries:
            cash_lines = [l for l in entry.lines if l.account_id in cash_acc_ids]
            offset_lines = [l for l in entry.lines if l.account_id not in cash_acc_ids]

            cash_change = sum(l.debit_minor - l.credit_minor for l in cash_lines)
            if cash_change == 0 or not offset_lines:
                continue

            total_offset_val = sum(l.debit_minor + l.credit_minor for l in offset_lines)
            if total_offset_val == 0:
                continue

            # Мөнгөний өөрчлөлтийг эсрэг мөрүүдэд жингээр хуваарилна. Сүүлийн
            # мөрөнд үлдэгдлийг өгснөөр нийлбэр нь бодит өөрчлөлттэй яг таарна.
            allocated_total = 0
            for idx, ol in enumerate(offset_lines):
                if idx == len(offset_lines) - 1:
                    allocated_cash = cash_change - allocated_total
                else:
                    weight = (ol.debit_minor + ol.credit_minor) / total_offset_val
                    allocated_cash = int(cash_change * weight)
                allocated_total += allocated_cash
                if allocated_cash == 0:
                    continue

                code = acc_codes.get(ol.account_id, "")
                flow[_cf_bucket(code, allocated_cash > 0)] += abs(allocated_cash)

        op_in = flow["op_in_sales"] + flow["op_in_other"]
        op_out = (flow["op_out_materials"] + flow["op_out_salary"] +
                  flow["op_out_ndsh"] + flow["op_out_tax"] + flow["op_out_other"])
        inv_in = flow["inv_in_assets"]
        inv_out = flow["inv_out_assets"]
        fin_in = flow["fin_in_capital"] + flow["fin_in_loans"]
        fin_out = flow["fin_out_dividends"] + flow["fin_out_loans"]

        return {
            "operating_inflow": op_in,
            "operating_outflow": op_out,
            "operating_net": op_in - op_out,
            "investing_inflow": inv_in,
            "investing_outflow": inv_out,
            "investing_net": inv_in - inv_out,
            "financing_inflow": fin_in,
            "financing_outflow": fin_out,
            "financing_net": fin_in - fin_out,
            "net_change": (op_in - op_out) + (inv_in - inv_out) + (fin_in - fin_out),
            "detail": flow
        }

    current = get_flow(date_from, date_to)
    res = {"current": current}
    if p_from and p_to:
        res["prior"] = get_flow(p_from, p_to)
    return res


# ================================================================= САНХҮҮГИЙН ТАЙЛАНГИЙН ТОДРУУЛГА (12 МАЯГТ)

def financial_notes(session: Session, company_id: str,
                    date_from: date | None = None,
                    date_to: date | None = None) -> dict:
    """Санхүүгийн тайлангийн тодруулга 12 маягтын тооцоолол (Мастер Excel загварын дагуу)."""
    tb = trial_balance(session, company_id, date_from, date_to)
    
    # Note 1: Мөнгө, түүнтэй адилтгах хөрөнгө
    cash_box = _sum_by_prefix(tb, ("1001", "10"), side="debit")
    bank_account = _sum_by_prefix(tb, ("1011", "1101", "11"), side="debit")
    
    # Note 2: Авлага
    receivables_trade = _sum_by_prefix(tb, ("1210", "1201"), side="debit")
    receivables_other = _sum_by_prefix(tb, ("1240", "1204", "1207"), side="debit")
    
    # Note 3: Бараа материал
    inventory_raw = _sum_by_prefix(tb, ("1510", "1405", "21"), side="debit")
    
    # Note 4: Үндсэн хөрөнгө ба Элэгдэл
    ppe_cost = _sum_by_prefix(tb, ("1810", "2001"), side="debit")
    ppe_depr = _sum_by_prefix(tb, ("1811", "2002"), side="credit")
    
    # Note 5: Өр төлбөр
    payables_trade = _sum_by_prefix(tb, ("2110", "3101"), side="credit")
    payroll_payable = _sum_by_prefix(tb, ("2120", "3301"), side="credit")
    tax_payable = _sum_by_prefix(tb, ("2130", "3401", "3402", "3403", "3406"), side="credit")
    
    # Note 6: Зардал (Борлуулалт ба Ерөнхий удирдлага)
    sales_exp = _sum_by_prefix(tb, ("7101", "71"), side="debit")
    admin_exp = _sum_by_prefix(tb, ("7102", "72"), side="debit")
    
    return {
        "note_1_cash": {
            "cash_box_minor": cash_box,
            "bank_account_minor": bank_account,
            "total_minor": cash_box + bank_account
        },
        "note_2_receivables": {
            "trade_minor": receivables_trade,
            "other_minor": receivables_other,
            "total_minor": receivables_trade + receivables_other
        },
        "note_3_inventory": {
            "raw_materials_minor": inventory_raw,
            "total_minor": inventory_raw
        },
        "note_4_ppe": {
            "cost_minor": ppe_cost,
            "depreciation_minor": ppe_depr,
            "net_book_value_minor": ppe_cost - ppe_depr
        },
        "note_5_liabilities": {
            "trade_payables_minor": payables_trade,
            "payroll_payable_minor": payroll_payable,
            "tax_payable_minor": tax_payable,
            "total_minor": payables_trade + payroll_payable + tax_payable
        },
        "note_6_expenses": {
            "sales_marketing_minor": sales_exp,
            "general_admin_minor": admin_exp,
            "total_minor": sales_exp + admin_exp
        }
    }


# ================================================================= СЗХ ББСБ САНХҮҮГИЙН ТАЙЛАНГУУД (СТ-1, СТ-2 ББСБ)

def nbfi_financial_reports(session: Session, company_id: str,
                           as_of: date | None = None) -> dict:
    """СЗХ ББСБ-ын тусгай санхүүгийн байдлын ба орлогын тайлан (Маягт.docx загвар)."""
    tb = trial_balance(session, company_id, None, as_of)
    inc = income_statement(session, company_id, None, as_of)
    
    cash = _sum_by_prefix(tb, ("10", "11"), side="debit")
    loans = _sum_by_prefix(tb, ("1250", "1251"), side="debit")  # Зээлийн портфель
    factoring = _sum_by_prefix(tb, ("1260",), side="debit")    # Факторинг
    investments = _sum_by_prefix(tb, ("13", "14"), side="debit") # Үүсмэл & Хөрөнгө оруулалт
    ppe = _sum_by_prefix(tb, ("18", "20"), side="debit")
    
    interest_income = _sum_by_prefix(tb, ("5101", "5102"), side="credit") # Зээлийн хүүний орлого
    other_income = _sum_by_prefix(tb, ("52", "53"), side="credit")
    loan_loss_provision = _sum_by_prefix(tb, ("62", "72"), side="debit") # Зээлийн эрсдэлийн сангийн зардал
    admin_exp = _sum_by_prefix(tb, ("71",), side="debit")
    
    return {
        "st1_nbfi_balance_sheet": {
            "cash_minor": cash,
            "loans_portfolio_minor": loans,
            "factoring_receivables_minor": factoring,
            "investments_minor": investments,
            "ppe_minor": ppe,
            "total_assets_minor": cash + loans + factoring + investments + ppe,
        },
        "st2_nbfi_income_statement": {
            "interest_income_minor": interest_income,
            "other_income_minor": other_income,
            "loan_loss_provision_minor": loan_loss_provision,
            "admin_expenses_minor": admin_exp,
            "net_income_minor": interest_income + other_income - loan_loss_provision - admin_exp,
        },
        "representation_statement": {
            "statement_title": "САНХҮҮГИЙН ТАЙЛАНГИЙН БОДИТ БАЙДЛЫН ТУХАЙ МЭДЭГДЭЛ",
            "compliance_standards": "СТОУС, НББОУС болон СЗХ-ны ББСБ журмын дагуу бэлтгэв.",
            "guarantees": [
                "Тайлант хугацааны бүх үйл явдал, ажил гүйлгээ бодитоор гарсан бөгөөд анхан шатны баримтад үндэслэсэн",
                "Санхүүгийн болон бусад мэдээллийг материаллаг хэмжээнд үнэн зөв тайлагнасан",
                "Орхигдсон болон давхардсан зүйл байхгүй, хөрөнгө өр төлбөрийг зохистой үнэлсэн"
            ]
        }
    }


# ================================================================= ТАТВАРЫН МАЯГТ ТТ-02 БА ТТ-11

def aanoat_tt02_report(session: Session, company_id: str, year: int) -> dict:
    """ААНОАТ-ын тайлан Маягт ТТ-02 тооцоолол."""
    d_from = date(year, 1, 1)
    d_to = date(year, 12, 31)
    inc = income_statement(session, company_id, d_from, d_to)
    
    gross_revenue = inc["revenue_minor"]
    allowable_expenses = inc["cogs_minor"] + inc["expenses_minor"]
    taxable_income = max(0, gross_revenue - allowable_expenses)
    
    # 10% стандарт ААНОАТ (1 тэрбум хүртэлх орлогод 1%)
    tax_rate = 0.10
    calculated_tax = int(taxable_income * tax_rate)
    
    return {
        "form_code": "ТТ-02",
        "form_name": "ААНОАТ-ын тайлан (Маягт ТТ-02)",
        "gross_revenue_minor": gross_revenue,
        "allowable_expenses_minor": allowable_expenses,
        "taxable_income_minor": taxable_income,
        "tax_rate_percent": 10.0,
        "calculated_tax_minor": calculated_tax,
        "net_tax_payable_minor": calculated_tax
    }


def haoat_tt11_report(session: Session, company_id: str, year: int) -> dict:
    """ХХОАТ-ын тайлан Маягт ТТ-11.

    Тоог `etax.build_tt11` гаргана — цалингийн бүртгэлээс, ажилтан тус бүрийн
    шаталсан ХХОАТ ба НДШ-ийн таазыг тооцсон БОДИТ дүнгээр. Өмнө нь гүйлгээний
    балансаас 11.5%/10% хавтгайруулж ойролцоо тооцдог байсан нь татварт
    илгээх тоотой зөрдөг байв — нэг л эх сурвалж байх ёстой."""
    from . import etax
    return {"form_code": "ТТ-11", **etax.build_tt11(session, company_id, year).rows}

