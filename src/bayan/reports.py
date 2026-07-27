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


# СТ-1-ийн бүлэглэлт: (мөрийн нэр, кодын угтварууд, тал)
BS_ASSET_ROWS = [
    ("Мөнгө, түүнтэй адилтгах хөрөнгө", ("10", "11")),
    ("Дансны авлага",                    ("12",)),
    ("Бараа материал (ДҮ орсон)",        ("21",)),
    ("Үндсэн хөрөнгө (цэвэр)",           ("25",)),
]
BS_LIAB_ROWS = [
    ("Богино хугацаат өр төлбөр", ("31",)),
]
BS_EQUITY_ROWS = [
    ("Эздийн өмч", ("41", "45")),
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


def _sum_by_prefix(tb: list[dict], prefixes: tuple[str, ...],
                   side: str = "debit") -> int:
    """Кодын угтвараар нэгтгэнэ. side нь тайлангийн талыг заана:
    актив мөрөнд debit (contra-asset кредит данс автоматаар хасагдана),
    өр/өмчийн мөрөнд credit."""
    total = 0
    for r in tb:
        if any(r["code"].startswith(p) for p in prefixes):
            net_debit = r["debit_minor"] - r["credit_minor"]
            total += net_debit if side == "debit" else -net_debit
    return total


def income_statement(session: Session, company_id: str,
                     date_from: date | None = None,
                     date_to: date | None = None) -> dict:
    """СТ-2: Орлого − ББӨ − Зардал = Тайлант үеийн ашиг (Харьцуулсан оны хамт)."""
    tb = trial_balance(session, company_id, date_from, date_to)
    revenue = _sum_by_prefix(tb, ("51",), side="credit")
    cogs = _sum_by_prefix(tb, ("61",), side="debit")
    expenses = _sum_by_prefix(tb, ("71",), side="debit")
    
    res = {
        "revenue_minor": revenue,
        "cogs_minor": cogs,
        "gross_profit_minor": revenue - cogs,
        "expenses_minor": expenses,
        "net_income_minor": revenue - cogs - expenses,
        "detail": [r for r in tb if r["code"][0] in "567" and r["balance_minor"]],
    }

    # Өмнөх оны харьцуулалт бодох
    p_from, p_to = _prior_period(date_from, date_to)
    if p_to:
        prior_tb = trial_balance(session, company_id, p_from, p_to)
        p_revenue = _sum_by_prefix(prior_tb, ("51",), side="credit")
        p_cogs = _sum_by_prefix(prior_tb, ("61",), side="debit")
        p_expenses = _sum_by_prefix(prior_tb, ("71",), side="debit")
        res.update({
            "prior_revenue_minor": p_revenue,
            "prior_cogs_minor": p_cogs,
            "prior_gross_profit_minor": p_revenue - p_cogs,
            "prior_expenses_minor": p_expenses,
            "prior_net_income_minor": p_revenue - p_cogs - p_expenses,
        })
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

    assets = []
    for n, p in BS_ASSET_ROWS:
        assets.append({
            "name": n,
            "amount_minor": _sum_by_prefix(tb, p, "debit"),
            "prior_amount_minor": _sum_by_prefix(prior_tb, p, "debit") if p_as_of else 0
        })

    liabs = []
    for n, p in BS_LIAB_ROWS:
        liabs.append({
            "name": n,
            "amount_minor": _sum_by_prefix(tb, p, "credit"),
            "prior_amount_minor": _sum_by_prefix(prior_tb, p, "credit") if p_as_of else 0
        })

    equity = []
    for n, p in BS_EQUITY_ROWS:
        equity.append({
            "name": n,
            "amount_minor": _sum_by_prefix(tb, p, "credit"),
            "prior_amount_minor": _sum_by_prefix(prior_tb, p, "credit") if p_as_of else 0
        })

    # Тайлант үеийн ашгийг нэмнэ
    equity.append({
        "name": "Тайлант үеийн ашиг (алдагдал)",
        "amount_minor": inc["net_income_minor"],
        "prior_amount_minor": prior_inc.get("net_income_minor", 0) if p_as_of else 0
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


def statement_of_changes_in_equity(
    session: Session, company_id: str,
    date_from: date, date_to: date,
) -> dict:
    """СТ-3: Өмчийн өөрчлөлтийн тайлан (Хувь нийлүүлсэн хөрөнгө 4101 + Хуримтлагдсан ашиг 4501)."""
    p_from, p_to = _prior_period(date_from, date_to)
    
    from datetime import timedelta
    
    def get_data(d_from: date, d_to: date) -> dict:
        # Эхний үлдэгдэл: d_from-ийн өмнөх өдөр хүртэлх бүх хугацааны хуримтлал
        start_date = d_from - timedelta(days=1)
        tb_start = trial_balance(session, company_id, None, start_date)
        capital_start = _sum_by_prefix(tb_start, ("4101",), "credit")
        retained_start = (_sum_by_prefix(tb_start, ("4501",), "credit")
                          + _sum_by_prefix(tb_start, ("51",), "credit")
                          - _sum_by_prefix(tb_start, ("61", "71"), "debit"))
        
        # Тайлант үеийн цэвэр ашиг
        inc = income_statement(session, company_id, d_from, d_to)
        net_income = inc["net_income_minor"]
        
        # Эцсийн үлдэгдэл
        tb_end = trial_balance(session, company_id, None, d_to)
        capital_end = _sum_by_prefix(tb_end, ("4101",), "credit")
        retained_end = (_sum_by_prefix(tb_end, ("4501",), "credit")
                        + _sum_by_prefix(tb_end, ("51",), "credit")
                        - _sum_by_prefix(tb_end, ("61", "71"), "debit"))
        
        # Өөрчлөлт (Ногдол ашиг эсвэл бусад өөрчлөлт)
        capital_change = capital_end - capital_start
        retained_change = retained_end - retained_start - net_income
        
        return {
            "start": {"capital": capital_start, "retained": retained_start, "total": capital_start + retained_start},
            "net_income": {"capital": 0, "retained": net_income, "total": net_income},
            "changes": {"capital": capital_change, "retained": retained_change, "total": capital_change + retained_change},
            "end": {"capital": capital_end, "retained": retained_end, "total": capital_end + retained_end},
        }

    current = get_data(date_from, date_to)
    res = {"current": current}
    if p_to and p_from:
        res["prior"] = get_data(p_from, p_to)
    return res


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

        flow = {
            "op_in_sales": 0,
            "op_out_materials": 0,
            "op_out_salary": 0,
            "op_out_ndsh": 0,
            "op_out_tax": 0,
            "op_out_other": 0,
            "inv_out_assets": 0,
            "inv_in_assets": 0,
            "fin_in_capital": 0,
            "fin_out_dividends": 0,
            "fin_out_loans": 0,
        }

        for entry in entries:
            cash_lines = [l for l in entry.lines if l.account_id in cash_acc_ids]
            offset_lines = [l for l in entry.lines if l.account_id not in cash_acc_ids]
            
            cash_change = sum(l.debit_minor - l.credit_minor for l in cash_lines)
            if cash_change == 0 or not offset_lines:
                continue
                
            total_offset_val = sum(l.debit_minor + l.credit_minor for l in offset_lines)
            if total_offset_val == 0:
                continue
                
            for ol in offset_lines:
                weight = (ol.debit_minor + ol.credit_minor) / total_offset_val
                allocated_cash = int(cash_change * weight)
                
                code = acc_codes.get(ol.account_id, "")
                
                if allocated_cash > 0:  # Мөнгө орсон (Inflow)
                    if code.startswith("51") or code.startswith("12"):
                        flow["op_in_sales"] += allocated_cash
                    elif code.startswith("41"):
                        flow["fin_in_capital"] += allocated_cash
                    elif code.startswith("25"):
                        flow["inv_in_assets"] += allocated_cash
                    else:
                        flow["op_in_sales"] += allocated_cash
                else:  # Мөнгө гарсан (Outflow)
                    allocated_cash_abs = abs(allocated_cash)
                    if code.startswith("61") or code.startswith("21") or code.startswith("3101"):
                        flow["op_out_materials"] += allocated_cash_abs
                    elif code.startswith("3102") or code.startswith("7101"):
                        flow["op_out_salary"] += allocated_cash_abs
                    elif code.startswith("3103") or code.startswith("7102"):
                        flow["op_out_ndsh"] += allocated_cash_abs
                    elif code.startswith("3104") or code.startswith("3105") or code.startswith("7107"):
                        flow["op_out_tax"] += allocated_cash_abs
                    elif code.startswith("25"):
                        flow["inv_out_assets"] += allocated_cash_abs
                    elif code.startswith("45") or code.startswith("41"):
                        flow["fin_out_dividends"] += allocated_cash_abs
                    elif code.startswith("31") or code.startswith("42"):
                        flow["fin_out_loans"] += allocated_cash_abs
                    else:
                        flow["op_out_other"] += allocated_cash_abs

        op_in = flow["op_in_sales"]
        op_out = (flow["op_out_materials"] + flow["op_out_salary"] + 
                  flow["op_out_ndsh"] + flow["op_out_tax"] + flow["op_out_other"])
        inv_in = flow["inv_in_assets"]
        inv_out = flow["inv_out_assets"]
        fin_in = flow["fin_in_capital"]
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
