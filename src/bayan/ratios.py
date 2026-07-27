"""CFO & Эздийн Санхүүгийн Харьцаа Үзүүлэлтийн Модуль.
"""

from __future__ import annotations

from datetime import date
from sqlalchemy.orm import Session
from .ledger import trial_balance


def calculate_financial_ratios(session: Session, company_id: str) -> dict:
    """Компанийн санхүүгийн 8 гол харьцаа үзүүлэлтийг тооцоолж, тайлбарын хамт буцаана."""
    # Сүүлийн 1 жилийн trial balance унших
    tb = trial_balance(session, company_id)
    
    # Дансдын үлдэгдлийг бүлэглэх
    cash = 0            # 10xx, 11xx
    receivables = 0     # 12xx
    inventory = 0       # 21xx
    fixed_assets = 0    # 25xx (accumulated depreciation 2509 хасагдана)
    liabilities = 0     # 31xx
    equity = 0          # 41xx (4101, 4501)
    revenue = 0         # 51xx
    cogs = 0            # 61xx
    expenses = 0        # 71xx
    
    for row in tb:
        code = row["code"]
        bal = row["balance_minor"]
        
        if code.startswith("10") or code.startswith("11"):
            cash += bal
        elif code.startswith("12"):
            receivables += bal
        elif code.startswith("21"):
            inventory += bal
        elif code.startswith("25"):
            # 2509 элэгдэл нь credit үлдэгдэлтэй тул хөрөнгийг бууруулж хасна
            fixed_assets += bal  
        elif code.startswith("31"):
            liabilities += bal
        elif code.startswith("41") or code.startswith("45"):
            equity += bal
        elif code.startswith("51"):
            revenue += bal
        elif code.startswith("61"):
            cogs += bal
        elif code.startswith("71"):
            expenses += bal

    # Дүн засах (minor units-ээс төгрөг рүү)
    cash_mnt = cash / 100
    receivables_mnt = receivables / 100
    inventory_mnt = inventory / 100
    current_assets_mnt = cash_mnt + receivables_mnt + inventory_mnt
    fixed_assets_mnt = fixed_assets / 100
    total_assets_mnt = current_assets_mnt + fixed_assets_mnt
    
    liabilities_mnt = liabilities / 100
    equity_mnt = equity / 100
    
    revenue_mnt = revenue / 100
    cogs_mnt = cogs / 100
    expenses_mnt = expenses / 100
    
    gross_profit_mnt = revenue_mnt - cogs_mnt
    net_profit_mnt = gross_profit_mnt - expenses_mnt
    
    # Хэрэв эздийн өмч баазад ороогүй эсвэл цэвэр ашиг нэмэгдээгүй бол
    if equity_mnt == 0:
        equity_mnt = total_assets_mnt - liabilities_mnt
    if equity_mnt <= 0:
        equity_mnt = 1.0  # Zero division-оос сэргийлэх
        
    # Үзүүлэлтүүд тооцоолох
    # 1. Ажлын капитал (Working Capital)
    working_capital = current_assets_mnt - liabilities_mnt
    
    # 2. Эргэцтэй харьцаа (Current Ratio)
    current_ratio = current_assets_mnt / liabilities_mnt if liabilities_mnt > 0 else 999.0
    
    # 3. Түргэн хөрвөх чадварын харьцаа (Quick Ratio)
    quick_ratio = (cash_mnt + receivables_mnt) / liabilities_mnt if liabilities_mnt > 0 else 999.0
    
    # 4. Нийт ашгийн маржин (Gross Profit Margin)
    gross_margin = (gross_profit_mnt / revenue_mnt * 100) if revenue_mnt > 0 else 0.0
    
    # 5. Цэвэр ашгийн маржин (Net Profit Margin)
    net_margin = (net_profit_mnt / revenue_mnt * 100) if revenue_mnt > 0 else 0.0
    
    # 6. Хөрөнгийн өгөөж (ROA)
    roa = (net_profit_mnt / total_assets_mnt * 100) if total_assets_mnt > 0 else 0.0
    
    # 7. Өмчийн өгөөж (ROE)
    roe = (net_profit_mnt / equity_mnt * 100)
    
    # 8. Өрийн харьцаа (Debt Ratio)
    debt_ratio = (liabilities_mnt / total_assets_mnt * 100) if total_assets_mnt > 0 else 0.0

    return {
        "working_capital": {
            "value": working_capital,
            "label": "Ажлын капитал (Working Capital)",
            "unit": "₮",
            "interpretation": "Богино хугацаат өр төлбөрөө эргэлтийн хөрөнгөөр бүрэн хаасны дараа бизнест үлдэж буй чөлөөт мөнгөн эх үүсвэр.",
            "status": "Сайн" if working_capital > 0 else "Анхаарах"
        },
        "current_ratio": {
            "value": round(current_ratio, 2),
            "label": "Эргэцтэй харьцаа (Current Ratio)",
            "unit": "дахин",
            "interpretation": "Бизнесийн богино хугацаат өр төлбөрөө эргэлтийн хөрөнгөөрөө хэд дахин даах чадвартайг харуулна. Тохиромжтой хэмжээ нь 1.5 - 2.0 хооронд байна.",
            "status": "Сайн" if 1.5 <= current_ratio <= 2.5 else ("Дундаж" if current_ratio >= 1.0 else "Муу")
        },
        "quick_ratio": {
            "value": round(quick_ratio, 2),
            "label": "Түргэн хөрвөх чадвар (Quick Ratio)",
            "unit": "дахин",
            "interpretation": "Бараа материалыг борлуулахыг хүлээхгүйгээр, зөвхөн бэлэн мөнгө болон авлагаараа богино хугацаат өрөө шууд дарах чадвар. Шаардлагатай түвшин нь 1.0-ээс дээш байна.",
            "status": "Сайн" if quick_ratio >= 1.0 else "Муу"
        },
        "gross_margin": {
            "value": round(gross_margin, 1),
            "label": "Нийт ашгийн маржин (Gross Profit Margin)",
            "unit": "%",
            "interpretation": "Борлуулалтын орлогоос бүтээгдэхүүний өртгийг хасаад үлдэж буй нийт ашгийн эзлэх хувь. Өндөр байх тусам үйлдвэрлэлийн үр ашиг сайн байна.",
            "status": "Сайн" if gross_margin >= 30 else "Дундаж"
        },
        "net_margin": {
            "value": round(net_margin, 1),
            "label": "Цэвэр ашгийн маржин (Net Profit Margin)",
            "unit": "%",
            "interpretation": "Борлуулалтын орлогоос үйл ажиллагааны бүх зардал, татварыг хассаны дараа цэвэр ашиг болон үлдэж буй хувь. Бизнесийн ерөнхий ашигт ажиллагааг илтгэнэ.",
            "status": "Сайн" if net_margin >= 10 else "Анхаарах"
        },
        "roa": {
            "value": round(roa, 1),
            "label": "Хөрөнгийн өгөөж (ROA)",
            "unit": "%",
            "interpretation": "Бизнесийн нийт хөрөнгө хэр зэрэг үр ашигтай ажиллаж цэвэр ашиг бүтээж байгааг харуулдаг. 5%-иас дээш байвал сайн гэж үздэг.",
            "status": "Сайн" if roa >= 5 else "Дундаж"
        },
        "roe": {
            "value": round(roe, 1),
            "label": "Өмчийн өгөөж (ROE)",
            "unit": "%",
            "interpretation": "Хөрөнгө оруулагч, эздийн оруулсан 1 төгрөг тутамд ногдож буй цэвэр ашгийн хэмжээ. 15%-иас дээш байх нь бизнесийн өсөлтийн чадавх сайн байгааг илтгэнэ.",
            "status": "Сайн" if roe >= 15 else "Дундаж"
        },
        "debt_ratio": {
            "value": round(debt_ratio, 1),
            "label": "Өрийн харьцаа (Debt Ratio)",
            "unit": "%",
            "interpretation": "Нийт хөрөнгийн хэдэн хувийг гадны өр төлбөрөөр санхүүжүүлснийг илэрхийлнэ. Аюулгүй дээд хязгаар нь 50% орчим байна.",
            "status": "Аюулгүй" if debt_ratio <= 50 else "Эрсдэлтэй"
        }
    }


def get_period_financial_summary(session: Session, company_id: str,
                                 date_from: date | None = None,
                                 date_to: date | None = None) -> dict:
    """Тодорхой хугацааны санхүүгийн гол үзүүлэлтүүдийн хураангуйг тооцоолно."""
    tb = trial_balance(session, company_id, date_from=date_from, date_to=date_to)
    
    revenue = 0
    expenses = 0
    net_cash_flow = 0
    receivables_change = 0
    payables_change = 0
    
    for row in tb:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        
        # 1. Орлого (5xx): Кредит - Дебит
        if code.startswith("5"):
            revenue += (cr - dr)
            
        # 2. Зардал (6xx, 7xx): Дебит - Кредит
        elif code.startswith("6") or code.startswith("7"):
            expenses += (dr - cr)
            
        # 3. Мөнгөн хөрөнгө (10xx, 11xx): Дебит - Кредит
        elif code.startswith("10") or code.startswith("11"):
            net_cash_flow += (dr - cr)
            
        # 4. Авлага (12xx): Дебит - Кредит
        elif code.startswith("12"):
            receivables_change += (dr - cr)
            
        # 5. Өглөг (31xx): Кредит - Дебит
        elif code.startswith("31"):
            payables_change += (cr - dr)
            
    return {
        "period": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None
        },
        "revenue": revenue / 100,
        "expenses": expenses / 100,
        "net_income": (revenue - expenses) / 100,
        "net_cash_flow": net_cash_flow / 100,
        "receivables_change": receivables_change / 100,
        "payables_change": payables_change / 100
    }


def get_unified_dashboard_summary(session: Session, company_id: str,
                                  date_from: date | None = None,
                                  date_to: date | None = None) -> dict:
    """Хугацааны шүүлтүүртэй санхүүгийн харьцаа үзүүлэлтүүд болон товчоо мэдээллийг нэгтгэн буцаана."""
    from datetime import timedelta
    
    # 1. Default dates if not provided
    if not date_to:
        date_to = date.today()
    if not date_from:
        date_from = date_to - timedelta(days=365)
        
    # 2. Cumulative balances for Balance Sheet items (up to date_to)
    tb_cum = trial_balance(session, company_id, date_to=date_to)
    
    cash = 0
    receivables = 0
    inventory = 0
    fixed_assets = 0
    liabilities = 0
    equity = 0
    
    for row in tb_cum:
        code = row["code"]
        bal = row["balance_minor"]
        if code.startswith("10") or code.startswith("11"):
            cash += bal
        elif code.startswith("12"):
            receivables += bal
        elif code.startswith("21"):
            inventory += bal
        elif code.startswith("25"):
            fixed_assets += bal
        elif code.startswith("31"):
            liabilities += bal
        elif code.startswith("41") or code.startswith("45"):
            equity += bal

    # Convert to MNT
    cash_mnt = cash / 100
    receivables_mnt = receivables / 100
    inventory_mnt = inventory / 100
    current_assets_mnt = cash_mnt + receivables_mnt + inventory_mnt
    fixed_assets_mnt = fixed_assets / 100
    total_assets_mnt = current_assets_mnt + fixed_assets_mnt
    liabilities_mnt = liabilities / 100
    equity_mnt = equity / 100
    
    # 3. Period balances for Income Statement items (between date_from and date_to)
    tb_period = trial_balance(session, company_id, date_from=date_from, date_to=date_to)
    
    revenue = 0
    cogs = 0
    expenses = 0
    net_cash_flow = 0
    receivables_change = 0
    payables_change = 0
    
    for row in tb_period:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        
        if code.startswith("5"):
            revenue += (cr - dr)
        elif code.startswith("6"):
            cogs += (dr - cr)
        elif code.startswith("7"):
            expenses += (dr - cr)
        elif code.startswith("10") or code.startswith("11"):
            net_cash_flow += (dr - cr)
        elif code.startswith("12"):
            receivables_change += (dr - cr)
        elif code.startswith("31"):
            payables_change += (cr - dr)
            
    revenue_mnt = revenue / 100
    cogs_mnt = cogs / 100
    expenses_mnt = expenses / 100
    gross_profit_mnt = revenue_mnt - cogs_mnt
    net_profit_mnt = gross_profit_mnt - expenses_mnt
    
    # Avoid zero division
    if equity_mnt == 0:
        equity_mnt = total_assets_mnt - liabilities_mnt
    if equity_mnt <= 0:
        equity_mnt = 1.0

    # Ratios calculation
    working_capital = current_assets_mnt - liabilities_mnt
    current_ratio = current_assets_mnt / liabilities_mnt if liabilities_mnt > 0 else 999.0
    quick_ratio = (cash_mnt + receivables_mnt) / liabilities_mnt if liabilities_mnt > 0 else 999.0
    gross_margin = (gross_profit_mnt / revenue_mnt * 100) if revenue_mnt > 0 else 0.0
    net_margin = (net_profit_mnt / revenue_mnt * 100) if revenue_mnt > 0 else 0.0
    roa = (net_profit_mnt / total_assets_mnt * 100) if total_assets_mnt > 0 else 0.0
    roe = (net_profit_mnt / equity_mnt * 100)
    debt_ratio = (liabilities_mnt / total_assets_mnt * 100) if total_assets_mnt > 0 else 0.0

    ratios = {
        "working_capital": {
            "value": working_capital,
            "label": "Ажлын капитал (Working Capital)",
            "unit": "₮",
            "interpretation": "Богино хугацаат өр төлбөрөө эргэлтийн хөрөнгөөр бүрэн хаасны дараа бизнест үлдэж буй чөлөөт мөнгөн эх үүсвэр.",
            "status": "Сайн" if working_capital > 0 else "Анхаарах"
        },
        "current_ratio": {
            "value": round(current_ratio, 2),
            "label": "Эргэцтэй харьцаа (Current Ratio)",
            "unit": "дахин",
            "interpretation": "Бизнесийн богино хугацаат өр төлбөрөө эргэлтийн хөрөнгөөрөө хэд дахин даах чадвартайг харуулна. Тохиромжтой хэмжээ нь 1.5 - 2.0 хооронд байна.",
            "status": "Сайн" if 1.5 <= current_ratio <= 2.5 else ("Дундаж" if current_ratio >= 1.0 else "Муу")
        },
        "quick_ratio": {
            "value": round(quick_ratio, 2),
            "label": "Түргэн хөрвөх чадвар (Quick Ratio)",
            "unit": "дахин",
            "interpretation": "Бараа материалыг борлуулахыг хүлээхгүйгээр, зөвхөн бэлэн мөнгө болон авлагаараа богино хугацаат өрөө шууд дарах чадвар. Шаардлагатай түвшин нь 1.0-ээс дээш байна.",
            "status": "Сайн" if quick_ratio >= 1.0 else "Муу"
        },
        "gross_margin": {
            "value": round(gross_margin, 1),
            "label": "Нийт ашгийн маржин (Gross Profit Margin)",
            "unit": "%",
            "interpretation": "Борлуулалтын орлогоос бүтээгдэхүүний өртгийг хасаад үлдэж буй нийт ашгийн эзлэх хувь. Өндөр байх тусам үйлдвэрлэлийн үр ашиг сайн байна.",
            "status": "Сайн" if gross_margin >= 30 else "Дундаж"
        },
        "net_margin": {
            "value": round(net_margin, 1),
            "label": "Цэвэр ашгийн маржин (Net Profit Margin)",
            "unit": "%",
            "interpretation": "Борлуулалтын орлогоос үйл ажиллагааны бүх зардал, татварыг хассаны дараа цэвэр ашиг болон үлдэж буй хувь. Бизнесийн ерөнхий ашигт ажиллагааг илтгэнэ.",
            "status": "Сайн" if net_margin >= 10 else "Анхаарах"
        },
        "roa": {
            "value": round(roa, 1),
            "label": "Хөрөнгийн өгөөж (ROA)",
            "unit": "%",
            "interpretation": "Бизнесийн нийт хөрөнгө хэр зэрэг үр ашигтай ажиллаж цэвэр ашиг бүтээж байгааг харуулдаг. 5%-иас дээш байвал сайн гэж үздэг.",
            "status": "Сайн" if roa >= 5 else "Дундаж"
        },
        "roe": {
            "value": round(roe, 1),
            "label": "Өмчийн өгөөж (ROE)",
            "unit": "%",
            "interpretation": "Хөрөнгө оруулагч, эздийн оруулсан 1 төгрөг тутамд ногдож буй цэвэр ашгийн хэмжээ. 15%-иас дээш байх нь бизнесийн өсөлтийн чадавх сайн байгааг илтгэнэ.",
            "status": "Сайн" if roe >= 15 else "Дундаж"
        },
        "debt_ratio": {
            "value": round(debt_ratio, 1),
            "label": "Өрийн харьцаа (Debt Ratio)",
            "unit": "%",
            "interpretation": "Нийт хөрөнгийн хэдэн хувийг гадны өр төлбөрөөр санхүүжүүлснийг илэрхийлнэ. Аюулгүй дээд хязгаар нь 50% орчим байна.",
            "status": "Аюулгүй" if debt_ratio <= 50 else "Эрсдэлтэй"
        }
    }
    
    # EBITDA, DSCR, and CCC calculations (Bank Credit Officer proposals 6, 8, 10)
    depreciation_mnt = 0.0
    tax_mnt = 0.0
    interest_mnt = 0.0
    for row in tb_period:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        if code == "7107":
            depreciation_mnt += (dr - cr) / 100.0
        elif code == "7109":
            tax_mnt += (dr - cr) / 100.0
        elif code == "7110" or code == "7102":
            interest_mnt += (dr - cr) / 100.0
            
    ebitda_mnt = net_profit_mnt + depreciation_mnt + tax_mnt + interest_mnt
    
    from .models import LoanContract, LoanSchedule
    from sqlalchemy import select, func
    debt_service_minor = session.scalar(
        select(func.coalesce(func.sum(LoanSchedule.principal_due_minor + LoanSchedule.interest_due_minor), 0))
        .join(LoanContract, LoanContract.id == LoanSchedule.contract_id)
        .where(LoanContract.company_id == company_id,
               LoanSchedule.due_date >= date_from,
               LoanSchedule.due_date <= date_to)
    ) or 0
    debt_service_mnt = debt_service_minor / 100.0
    if debt_service_mnt > 0:
        dscr = ebitda_mnt / debt_service_mnt
    else:
        dscr = 999.0
        
    duration = date_to - date_from
    days = max(1, duration.days)
    dso = (receivables_mnt / revenue_mnt * days) if revenue_mnt > 0 else 0.0
    dio = (inventory_mnt / cogs_mnt * days) if cogs_mnt > 0 else 0.0
    dpo = (payables_mnt / cogs_mnt * days) if cogs_mnt > 0 else 0.0
    ccc = dso + dio - dpo
    
    ratios["ebitda"] = {
        "value": round(ebitda_mnt, 2),
        "label": "EBITDA",
        "unit": "₮",
        "interpretation": "Хүү, татвар, элэгдлийн өмнөх үйл ажиллагааны ашиг. Бизнесийн үндсэн мөнгөн урсгал үүсгэх чадварыг илтгэнэ.",
        "status": "Сайн" if ebitda_mnt > 0 else "Анхаарах"
    }
    ratios["dscr"] = {
        "value": round(dscr, 2) if dscr != 999.0 else "Хязгааргүй",
        "label": "Өрийн үйлчилгээ хаах харьцаа (DSCR)",
        "unit": "дахин",
        "interpretation": "Үйл ажиллагааны ашиг нь зээлийн үндсэн төлбөр болон хүүг хаах чадвар. Банкууд 1.2-оос дээш байхыг шаарддаг.",
        "status": "Сайн" if dscr >= 1.2 or dscr == 999.0 else ("Муу" if dscr < 1.0 else "Анхаарах")
    }
    ratios["ccc"] = {
        "value": round(ccc, 1),
        "label": "Мөнгөний эргэц мөчлөг (Cash Conversion Cycle)",
        "unit": "хоног",
        "interpretation": "Бараа худалдан авахаас эхлээд борлуулалтын авлагыг бодит бэлэн мөнгө болгож цуглуулах хүртэлх хугацаа. Бага байх тусам сайн.",
        "status": "Сайн" if ccc <= 60 else "Анхаарах"
    }
    
    # Cash Burn Rate & Runway calculation
    duration = date_to - date_from
    days = max(1, duration.days)
    months_in_period = days / 30.0
    total_expenses_mnt = cogs_mnt + expenses_mnt
    burn_rate_mnt = total_expenses_mnt / months_in_period if months_in_period > 0 else 0.0
    
    runway_months = (cash_mnt / burn_rate_mnt) if (burn_rate_mnt > 0 and cash_mnt > 0) else (999.0 if cash_mnt > 0 else 0.0)

    summary = {
        "revenue": revenue_mnt,
        "expenses": total_expenses_mnt,
        "net_income": net_profit_mnt,
        "net_cash_flow": net_cash_flow / 100,
        "receivables_change": receivables_change / 100,
        "payables_change": payables_change / 100,
        "burn_rate": round(burn_rate_mnt, 2),
        "cash_runway_months": round(runway_months, 1) if runway_months != 999.0 else "Хязгааргүй"
    }
    
    # Сөрөг үлдэгдэлтэй дансдыг илрүүлэх хяналт
    alerts = []
    if cash_mnt < 0:
        alerts.append({
            "type": "CASH_NEGATIVE",
            "message": f"Касс, харилцахын нийт үлдэгдэл сөрөг гарлаа: {cash_mnt:,.2f}₮. Гүйлгээнүүдээ шалгана уу."
        })
    if receivables_mnt < 0:
        alerts.append({
            "type": "RECEIVABLES_NEGATIVE",
            "message": f"Авлагын нийт үлдэгдэл сөрөг гарлаа: {receivables_mnt:,.2f}₮. Гүйлгээнүүдээ шалгана уу."
        })
    if inventory_mnt < 0:
        alerts.append({
            "type": "INVENTORY_NEGATIVE",
            "message": f"Бараа материалын нийт үлдэгдэл сөрөг гарлаа: {inventory_mnt:,.2f}₮. Орлого ба зарлагын баримтыг тулгана уу."
        })
        
    # Reorder point alerts check (Warehouse Manager proposal 16)
    from .inventory import Item
    items = session.scalars(select(Item).where(Item.company_id == company_id)).all()
    for item in items:
        if item.qty <= item.reorder_point:
            alerts.append({
                "type": "INVENTORY_REORDER_ALERT",
                "message": f"Барааны доод хязгаар хэтэрлээ: {item.name} ({item.code}) үлдэгдэл {item.qty}ш (Доод хязгаар: {item.reorder_point}ш)."
            })
        
    # MoM Expense Variance check (огцом зардлын хэлбэлзэл)
    prev_date_to = date_from - timedelta(days=1)
    prev_date_from = prev_date_to - duration
    
    try:
        tb_prev = trial_balance(session, company_id, date_from=prev_date_from, date_to=prev_date_to)
        prev_expenses = {}
        for row in tb_prev:
            code = row["code"]
            dr = row["debit_minor"]
            cr = row["credit_minor"]
            if code.startswith("6") or code.startswith("7"):
                prev_expenses[code] = dr - cr
                
        curr_expenses = {}
        for row in tb_period:
            code = row["code"]
            dr = row["debit_minor"]
            cr = row["credit_minor"]
            if code.startswith("6") or code.startswith("7"):
                curr_expenses[code] = dr - cr
                
        for code, curr_val in curr_expenses.items():
            prev_val = prev_expenses.get(code, 0)
            if prev_val > 0 and curr_val > prev_val:
                pct_increase = (curr_val - prev_val) / prev_val * 100
                if pct_increase >= 20.0:
                    acc_name = next((r["name"] for r in tb_period if r["code"] == code), "Зардал")
                    alerts.append({
                        "type": "EXPENSE_VARIANCE",
                        "message": f"Зардлын огцом өсөлт: {acc_name} ({code}) өмнөх үеэс {pct_increase:.1f}%-иар өссөн байна."
                    })
    except Exception:
        pass  # ignore failures if trial_balance fails during nested query

    return {
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat()
        },
        "ratios": ratios,
        "summary": summary,
        "alerts": alerts
    }


def get_indirect_cash_flow(session: Session, company_id: str, date_from: date, date_to: date) -> dict:
    from .ledger import trial_balance
    
    tb_period = trial_balance(session, company_id, date_from=date_from, date_to=date_to)
    
    ar_change_minor = 0
    ap_change_minor = 0
    inv_change_minor = 0
    depreciation_minor = 0
    
    for row in tb_period:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        if code.startswith("1201"):
            ar_change_minor += (dr - cr)
        elif code.startswith("3101"):
            ap_change_minor += (cr - dr)
        elif code.startswith("2101") or code.startswith("2151"):
            inv_change_minor += (dr - cr)
        elif code == "7107":
            depreciation_minor += (dr - cr)
            
    revenue_minor = 0
    cogs_minor = 0
    expenses_minor = 0
    for row in tb_period:
        code = row["code"]
        dr = row["debit_minor"]
        cr = row["credit_minor"]
        if code.startswith("5"):
            revenue_minor += (cr - dr)
        elif code.startswith("6"):
            cogs_minor += (dr - cr)
        elif code.startswith("7"):
            expenses_minor += (dr - cr)
            
    net_income_mnt = (revenue_minor - cogs_minor - expenses_minor) / 100.0
    depreciation_mnt = depreciation_minor / 100.0
    ar_change_mnt = ar_change_minor / 100.0
    ap_change_mnt = ap_change_minor / 100.0
    inv_change_mnt = inv_change_minor / 100.0
    
    operating_cf_mnt = net_income_mnt + depreciation_mnt - ar_change_mnt + ap_change_mnt - inv_change_mnt
    
    return {
        "net_income": round(net_income_mnt, 2),
        "adjustments": {
            "depreciation": round(depreciation_mnt, 2),
            "receivables_change": round(-ar_change_mnt, 2),
            "payables_change": round(ap_change_mnt, 2),
            "inventory_change": round(-inv_change_mnt, 2)
        },
        "operating_cash_flow": round(operating_cf_mnt, 2)
    }
