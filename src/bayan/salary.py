"""Цалин — цагийн бүртгэл, амралт, өвчтэй хугацааны бодолт болон 2026 оны шаталсан ХХОАТ, НДШ-ийн тооцоолуур.

  * Шаталсан ХХОАТ (10%, 15%, 20%) бодолт
  * НДШ бодох цалингийн дээд хязгаар (ceiling = 7,830,000₮)
  * Цагийн бүртгэл (timesheet): ажилласан хоног, амралт, өвчтэй өдрүүд
  * Өвчтэй өдрүүдийн цалинг хувиар бодох (default 60%)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Float, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import ledger
from .models import Base, SourceType


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class SalaryConfig:
    ndsh_employee_pct: float = 11.5     # Ажилтны НДШ %
    ndsh_employer_pct: float = 12.5     # Ажил олгогчийн НДШ %
    ndsh_ceiling_minor: int | None = 7_830_000_00  # 2026 оны НДШ-ийн дээд хязгаар (10 * доод хэмжээ)
    hhoat_credit_minor: int = 20_000_00 # сарын татварын хөнгөлөлт (жишээ нь 20,000₮)


class Employee(Base):
    __tablename__ = "employee"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    code: Mapped[str] = mapped_column(String(16))
    last_name: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(100))
    base_salary_minor: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TimeSheet(Base):
    """Цагийн бүртгэл — ажилласан хоног, амралт, өвчний хоногийн бүртгэл."""
    __tablename__ = "time_sheet"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    worked_days: Mapped[float] = mapped_column(Float, default=22.0)
    vacation_days: Mapped[float] = mapped_column(Float, default=0.0)
    sick_days: Mapped[float] = mapped_column(Float, default=0.0)
    sick_pay_pct: Mapped[float] = mapped_column(Float, default=60.0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    holiday_hours: Mapped[float] = mapped_column(Float, default=0.0)


class PayrollLine(Base):
    __tablename__ = "payroll_line"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    gross_minor: Mapped[int] = mapped_column(BigInteger)
    ndsh_employee_minor: Mapped[int] = mapped_column(BigInteger)
    ndsh_employer_minor: Mapped[int] = mapped_column(BigInteger)
    hhoat_minor: Mapped[int] = mapped_column(BigInteger)
    net_minor: Mapped[int] = mapped_column(BigInteger)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36))


def calc_progressive_pit(taxable_minor: int) -> int:
    """Монгол улсын шаталсан ХХОАТ бодох логик (2026):

      * 10 сая₮ хүртэл: 10%
      * 10 сая₮ - 15 сая₮: 1 сая₮ + 15% (10 саяас давсан дүнд)
      * 15 сая₮-оос дээш: 1.75 сая₮ + 20% (15 саяас давсан дүнд)
    """
    limit1 = 10_000_000_00
    limit2 = 15_000_000_00
    
    if taxable_minor <= limit1:
        return int(round(taxable_minor * 0.10))
    elif taxable_minor <= limit2:
        tax1 = int(limit1 * 0.10)
        tax2 = int((taxable_minor - limit1) * 0.15)
        return tax1 + tax2
    else:
        tax1 = int(limit1 * 0.10)
        tax2 = int((limit2 - limit1) * 0.15)
        tax3 = int((taxable_minor - limit2) * 0.20)
        return tax1 + tax2 + tax3


def calc_one(base_salary_minor: int, worked_days: float, vacation_days: float,
             sick_days: float, sick_pay_pct: float, cfg: SalaryConfig,
             overtime_hours: float = 0.0, holiday_hours: float = 0.0,
             vacation_daily_rate: float | None = None) -> dict:
    """Нэг ажилтны цалинг цагийн бүртгэл ба татварын шатлалаар бодно."""
    total_working_days = 22.0  # Сарын стандарт ажлын өдөр
    daily_rate = base_salary_minor / total_working_days
    hourly_rate = daily_rate / 8.0
    
    # Бүрэлдэхүүнүүд
    worked_pay = daily_rate * worked_days
    v_rate = vacation_daily_rate if vacation_daily_rate is not None else daily_rate
    vacation_pay = v_rate * vacation_days
    sick_pay = daily_rate * sick_days * (sick_pay_pct / 100.0)
    overtime_pay = hourly_rate * overtime_hours * 1.5
    holiday_pay = hourly_rate * holiday_hours * 2.0
    
    gross_minor = int(round(worked_pay + vacation_pay + sick_pay + overtime_pay + holiday_pay))
    
    # НДШ бодох суурь
    ndsh_base = gross_minor
    if cfg.ndsh_ceiling_minor:
        ndsh_base = min(ndsh_base, cfg.ndsh_ceiling_minor)
        
    ndsh_emp = int(round(ndsh_base * cfg.ndsh_employee_pct / 100))
    ndsh_er = int(round(ndsh_base * cfg.ndsh_employer_pct / 100))
    
    # Татвар ногдох орлого = Бохир цалин - Ажилтны НДШ
    taxable = gross_minor - ndsh_emp
    
    # Шаталсан ХХОАТ
    hhoat = max(calc_progressive_pit(taxable) - cfg.hhoat_credit_minor, 0)
    
    net = gross_minor - ndsh_emp - hhoat
    return {
        "gross": gross_minor,
        "ndsh_emp": ndsh_emp,
        "ndsh_er": ndsh_er,
        "hhoat": hhoat,
        "net": net,
        "worked_pay": int(worked_pay),
        "vacation_pay": int(vacation_pay),
        "sick_pay": int(sick_pay),
        "overtime_pay": int(overtime_pay),
        "holiday_pay": int(holiday_pay)
    }


def run_payroll(session: Session, company_id: str, year: int, month: int,
                cfg: SalaryConfig | None = None,
                entry_date: date | None = None) -> dict:
    """Сарын цалингийн тооцоолуур. Цагийн бүртгэлийг баазаас уншина."""
    from .ledger import LedgerError
    cfg = cfg or SalaryConfig()
    entry_date = entry_date or date(year, month, 25)
    
    employees = session.scalars(select(Employee).where(
        Employee.company_id == company_id, Employee.active == True)).all()  # noqa: E712
    if not employees:
        return {"count": 0}

    # Тухайн сарын цагийн бүртгэлүүд
    ts_list = session.scalars(select(TimeSheet).where(
        TimeSheet.company_id == company_id,
        TimeSheet.year == year,
        TimeSheet.month == month
    )).all()
    ts_map = {ts.employee_id: ts for ts in ts_list}

    totals = {"gross": 0, "ndsh_emp": 0, "ndsh_er": 0, "hhoat": 0, "net": 0}
    plines = []
    
    for e in employees:
        # Цагийн бүртгэл заавал байх ёстой (Табель тулгалт)
        ts = ts_map.get(e.id)
        if not ts:
            raise LedgerError(f"Цагийн бүртгэл (timesheet) дутуу байна: {e.last_name} {e.first_name}-ийн цагийг оруулна уу.")
            
        w_days = ts.worked_days
        v_days = ts.vacation_days
        s_days = ts.sick_days
        s_pct = ts.sick_pay_pct
        ot_hours = ts.overtime_hours
        hol_hours = ts.holiday_hours
        
        # Сүүлийн 12 сарын дундаж цалингаар ээлжийн амралтын олговрын суурь бодох
        hist = session.scalars(
            select(PayrollLine)
            .where(PayrollLine.employee_id == e.id)
            .order_by(PayrollLine.year.desc(), PayrollLine.month.desc())
            .limit(12)
        ).all()
        
        vacation_rate = None
        if hist:
            avg_gross = sum(h.gross_minor for h in hist) / len(hist)
            vacation_rate = avg_gross / 22.0
            
        c = calc_one(e.base_salary_minor, w_days, v_days, s_days, s_pct, cfg,
                     overtime_hours=ot_hours, holiday_hours=hol_hours,
                     vacation_daily_rate=vacation_rate)
        
        for k in totals:
            totals[k] += c[k]
            
        plines.append(PayrollLine(
            company_id=company_id, employee_id=e.id, year=year, month=month,
            gross_minor=c["gross"], ndsh_employee_minor=c["ndsh_emp"],
            ndsh_employer_minor=c["ndsh_er"], hhoat_minor=c["hhoat"],
            net_minor=c["net"]))

    entry = ledger.post_entry(session, company_id, entry_date, [
        ledger.LineInput("7101", debit_minor=totals["gross"],
                         description=f"Цалингийн зардал {year}-{month:02d}"),
        ledger.LineInput("7102", debit_minor=totals["ndsh_er"],
                         description=f"АО НДШ {year}-{month:02d}"),
        ledger.LineInput("3102", credit_minor=totals["net"],
                         description="Гарт олгох цалин"),
        ledger.LineInput("3103", credit_minor=totals["ndsh_emp"] + totals["ndsh_er"],
                         description="НДШ өглөг"),
        ledger.LineInput("3104", credit_minor=totals["hhoat"],
                         description="ХХОАТ өглөг"),
    ], source_type=SourceType.salary, memo=f"Цалин {year}-{month:02d}")

    for p in plines:
        p.journal_entry_id = entry.id
        session.add(p)
        
    session.flush()
    return {"count": len(employees), "entry_id": entry.id, **totals}
