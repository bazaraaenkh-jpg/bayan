"""ETAX — ТЕГ-ийн цахим системд татварын тайлан илгээх багц (ТТ-02, ТТ-03а, ТТ-11).

Банкны хуулгад validation gate байдаг шиг, татварын тайланд ч **математик
хаалт** байх ёстой: маягтын мөр бүр ерөнхий дэвтрийн дүнтэй тэнцэж байж
илгээгдэнэ. Тэнцэхгүй тайланг илгээх нь татварын эрсдэл тул хаалтыг
давахгүй бол `submit()` илгээхээс ТАТГАЛЗАНА.

Гурван маягт:
  * **ТТ-02** ААНОАТ — жилээр, СТ-2-той тэнцүү байх ёстой
  * **ТТ-03а** НӨАТ — сараар, 3105/1203 дансны хөдөлгөөнтэй тэнцүү
  * **ТТ-11** ХХОАТ — цалингийн бүртгэлээс (7101/3104-тэй тэнцүү)

Хуулийн хувь хэмжээ нь **тохиргоо, код биш** — `TaxRates` дотор эффектив
огноотой. Хууль өөрчлөгдөхөд кодыг биш тохиргоог солино.

**ТЕГ-ийн албан ёсны XSD хараахан гарт байхгүй** тул XML нь маягтын кодоор
дугаарласан тодорхой бүтэцтэй, схем нь гарсан үед зурагласан талбарууд нь
шууд буулгагдахаар бэлтгэгдсэн. Илгээх клиент нь eBarimt-ийнхтэй ижил
загвартай: эрхийн мэдээлэл байхгүй үед mock горимд ажиллана (ТЕГ-ийн API
нь Монголын IP шаарддаг — [[ebarimt-posapi-integration]]-тэй ижил нөхцөл).
"""

from __future__ import annotations

import hashlib
import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .models import Base

FORMS = ("ТТ-02", "ТТ-03а", "ТТ-11")

#: HTTP толгойн утга латин-1-д багтах ёстой тул файлын нэрэнд кирилл
#: маягтын кодыг ашиглаж болохгүй — латин хувилбарыг нь хэрэглэнэ.
ASCII_FORM = {"ТТ-02": "TT-02", "ТТ-03а": "TT-03a", "ТТ-11": "TT-11"}

#: Тэнцлийн шалгалтын хүлээцтэй зөрүү — мөнгөн нэгжийн 1/100-аар 1₮
TOLERANCE_MINOR = 100


# ------------------------------------------------------------------ хуулийн хувь

@dataclass(frozen=True)
class TaxRates:
    """Хуулийн хувь хэмжээ — кодод биш, тохиргоонд. Эффектив огноотой."""
    effective_from: date = date(2026, 1, 1)

    #: ААНОАТ: 6 тэрбум хүртэл 10%, түүнээс дээш 600 сая + 25%
    cit_threshold_minor: int = 6_000_000_000_00
    cit_low_pct: float = 10.0
    cit_high_pct: float = 25.0
    #: Жижиг татвар төлөгчийн сонголт (жилийн орлого ≤ 300 сая бол 1%)
    cit_small_revenue_cap_minor: int = 300_000_000_00
    cit_small_pct: float = 1.0

    vat_pct: float = 10.0

    def corporate_tax(self, taxable_minor: int, revenue_minor: int = 0,
                      small_election: bool = False) -> int:
        if taxable_minor <= 0:
            return 0
        if small_election and revenue_minor <= self.cit_small_revenue_cap_minor:
            return int(round(revenue_minor * self.cit_small_pct / 100))
        if taxable_minor <= self.cit_threshold_minor:
            return int(round(taxable_minor * self.cit_low_pct / 100))
        low = int(round(self.cit_threshold_minor * self.cit_low_pct / 100))
        high = int(round((taxable_minor - self.cit_threshold_minor)
                         * self.cit_high_pct / 100))
        return low + high


DEFAULT_RATES = TaxRates()


# ------------------------------------------------------------------ бүртгэл

def _uuid() -> str:
    return str(uuid.uuid4())


class EtaxSubmission(Base):
    """Илгээлт бүрийн бүртгэл — юуг, хэзээ, хэн, ямар агуулгаар илгээсэн."""
    __tablename__ = "etax_submission"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    form_code: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(16))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16))     # blocked|mock|sent|failed
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    receipt_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ хаалт

@dataclass
class Check:
    code: str
    title: str
    ok: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "ok": self.ok,
                "detail": self.detail, "blocking": self.blocking}


@dataclass
class Package:
    form_code: str
    period: str
    company: dict
    rows: dict
    checks: list[Check] = field(default_factory=list)

    def blocked(self) -> bool:
        return any(c.blocking and not c.ok for c in self.checks)

    def to_dict(self) -> dict:
        return {"form_code": self.form_code, "period": self.period,
                "company": self.company, "rows": self.rows,
                "checks": [c.to_dict() for c in self.checks],
                "blocked": self.blocked()}


def _bounds(year: int, month: int | None) -> tuple[date, date]:
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    import calendar
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _gl(session: Session, company_id: str, d_from: date, d_to: date) -> dict:
    from .ledger import trial_balance
    return {r["code"]: r for r in trial_balance(session, company_id, d_from, d_to)}


def _movement(gl: dict, code: str, side: str) -> int:
    row = gl.get(code)
    if not row:
        return 0
    return row["debit_minor"] if side == "debit" else row["credit_minor"]


def _company(session: Session, company_id: str) -> dict:
    from .models import Company
    c = session.get(Company, company_id)
    return {"id": company_id, "name": c.name if c else "",
            "tin": (c.reg_no or "") if c else ""}


def _tie(code: str, title: str, form_value: int, gl_value: int,
         gl_label: str, blocking: bool = True) -> Check:
    diff = form_value - gl_value
    ok = abs(diff) <= TOLERANCE_MINOR
    return Check(code, title, ok,
                 (f"Маягт {form_value / 100:,.0f}₮ = {gl_label} {gl_value / 100:,.0f}₮"
                  if ok else
                  f"Маягт {form_value / 100:,.0f}₮ ≠ {gl_label} {gl_value / 100:,.0f}₮ "
                  f"(зөрүү {diff / 100:+,.0f}₮)"), blocking)


def _common_checks(company: dict, year: int, month: int | None,
                   today: date | None = None) -> list[Check]:
    today = today or date.today()
    d_to = _bounds(year, month)[1]
    return [
        Check("TIN", "Татвар төлөгчийн дугаар бүртгэгдсэн эсэх",
              bool(company["tin"]),
              company["tin"] or "Компанийн ТТД хоосон байна — тайлан илгээх боломжгүй."),
        Check("PERIOD", "Тайлант үе дууссан эсэх", d_to <= today,
              f"Тайлант үе {d_to:%Y-%m-%d}-нд дуусна"
              if d_to <= today else
              f"Тайлант үе {d_to:%Y-%m-%d}-нд дуусах тул хараахан илгээх боломжгүй."),
    ]


# ------------------------------------------------------------------ ТТ-11 ХХОАТ

def build_tt11(session: Session, company_id: str, year: int,
               month: int | None = None, today: date | None = None) -> Package:
    """ХХОАТ — цалингийн бүртгэлээс (тооцоолсон таамаг БИШ).

    Гүйлгээний балансаас ойролцоо тооцох нь татварын тайланд тохирохгүй:
    шаталсан ХХОАТ, НДШ-ийн таазыг ажилтан тус бүрээр бодсон бодит дүн
    `payroll_line`-д бий."""
    from .salary import PayrollLine

    d_from, d_to = _bounds(year, month)
    months = {(year, m) for m in (range(1, 13) if month is None else [month])}

    lines = [l for l in session.scalars(select(PayrollLine).where(
        PayrollLine.company_id == company_id)) if (l.year, l.month) in months]

    gross = sum(l.gross_minor for l in lines)
    ndsh_emp = sum(l.ndsh_employee_minor for l in lines)
    ndsh_er = sum(l.ndsh_employer_minor for l in lines)
    hhoat = sum(l.hhoat_minor for l in lines)
    net = sum(l.net_minor for l in lines)

    rows = {
        "form_name": "ХХОАТ-ын тайлан (Маягт ТТ-11)",
        "employee_count": len({l.employee_id for l in lines}),
        "gross_salary_minor": gross,
        "ndsh_deduction_minor": ndsh_emp,
        "ndsh_employer_minor": ndsh_er,
        "taxable_income_minor": max(gross - ndsh_emp, 0),
        "haoat_tax_minor": hhoat,
        "net_pay_minor": net,
        "net_tax_payable_minor": hhoat,
    }

    company = _company(session, company_id)
    gl = _gl(session, company_id, d_from, d_to)
    checks = _common_checks(company, year, month, today)
    checks += [
        Check("PAYROLL_EXISTS", "Цалингийн бүртгэл байгаа эсэх", bool(lines),
              f"{len(lines)} мөр" if lines else
              "Тухайн үед цалин бодогдоогүй байна — тайлан хоосон гарна."),
        _tie("TT11_GROSS", "Бохир цалин ерөнхий дэвтэртэй тэнцэх",
             gross, _movement(gl, "7101", "debit"), "7101 дебит"),
        _tie("TT11_PIT", "ХХОАТ өглөгтэй тэнцэх",
             hhoat, _movement(gl, "3104", "credit"), "3104 кредит"),
        Check("TT11_BALANCE", "Бохир = гарт олгох + НДШ + ХХОАТ",
              abs(gross - (net + ndsh_emp + hhoat)) <= TOLERANCE_MINOR,
              f"{gross / 100:,.0f} vs {(net + ndsh_emp + hhoat) / 100:,.0f}"),
    ]
    return Package("ТТ-11", _period(year, month), company, rows, checks)


# ------------------------------------------------------------------ ТТ-03а НӨАТ

def build_tt03a(session: Session, company_id: str, year: int,
                month: int | None = None, today: date | None = None) -> Package:
    """НӨАТ — нэхэмжлэхээс, ерөнхий дэвтрийн 3105/1203-тай тулгана."""
    from . import vat

    d_from, d_to = _bounds(year, month)
    data = vat.tt03a(session, company_id, year, month)
    r = data["rows"]

    company = _company(session, company_id)
    gl = _gl(session, company_id, d_from, d_to)
    checks = _common_checks(company, year, month, today)
    checks += [
        _tie("VAT_OUTPUT", "Ногдуулсан НӨАТ өглөгтэй тэнцэх",
             r["31_nogduulsan_niit"], _movement(gl, "3105", "credit"),
             "3105 кредит"),
        _tie("VAT_INPUT", "Төлсөн НӨАТ авлагатай тэнцэх",
             r["42_tolson_noat"], _movement(gl, "1203", "debit"),
             "1203 дебит"),
        Check("VAT_NET", "Төлбөл зохих ба буцаан авах зэрэг гарахгүй",
              not (r["64_etssiin_tolboh"] and r["65_etssiin_butsaan_avah"]),
              f"төлбөл зохих {r['64_etssiin_tolboh'] / 100:,.0f}₮ · "
              f"буцаан авах {r['65_etssiin_butsaan_avah'] / 100:,.0f}₮"),
    ]
    rows = {"form_name": "НӨАТ-ын тайлан (Маягт ТТ-03а)", **r}
    return Package("ТТ-03а", _period(year, month), company, rows, checks)


# ------------------------------------------------------------------ ТТ-02 ААНОАТ

def build_tt02(session: Session, company_id: str, year: int,
               rates: TaxRates | None = None, small_election: bool = False,
               today: date | None = None) -> Package:
    """ААНОАТ — жилийн тайлан, СТ-2-той тэнцэнэ."""
    from . import reports

    rates = rates or DEFAULT_RATES
    d_from, d_to = _bounds(year, None)
    inc = reports.income_statement(session, company_id, d_from, d_to)

    revenue = inc["revenue_minor"] + inc.get("other_income_minor", 0)
    allowable = inc["cogs_minor"] + inc["expenses_minor"]
    taxable = max(revenue - allowable, 0)
    tax = rates.corporate_tax(taxable, revenue, small_election)

    rows = {
        "form_name": "ААНОАТ-ын тайлан (Маягт ТТ-02)",
        "gross_revenue_minor": revenue,
        "allowable_expenses_minor": allowable,
        "taxable_income_minor": taxable,
        "tax_rate_percent": (rates.cit_small_pct if small_election
                             and revenue <= rates.cit_small_revenue_cap_minor
                             else rates.cit_low_pct),
        "calculated_tax_minor": tax,
        "net_tax_payable_minor": tax,
    }

    company = _company(session, company_id)
    checks = _common_checks(company, year, None, today)
    checks += [
        _tie("CIT_TAXABLE", "Татвар ногдох орлого СТ-2-той тэнцэх",
             taxable, max(inc["net_income_minor"], 0), "СТ-2 цэвэр ашиг",
             blocking=False),
        Check("CIT_RATE", "Хэрэглэсэн хувь хэмжээ",
              True, f"{rows['tax_rate_percent']}% "
                    f"(эффектив {rates.effective_from:%Y-%m-%d}-наас)", False),
    ]
    return Package("ТТ-02", str(year), company, rows, checks)


def _period(year: int, month: int | None) -> str:
    return f"{year}-{month:02d}" if month else str(year)


BUILDERS = {"ТТ-02": build_tt02, "ТТ-03а": build_tt03a, "ТТ-11": build_tt11}


def build(session: Session, company_id: str, form_code: str, year: int,
          month: int | None = None, **kw) -> Package:
    if form_code not in BUILDERS:
        raise ValueError(f"Маягт {form_code} дэмжигдээгүй. Боломжтой: {FORMS}")
    if form_code == "ТТ-02":
        return build_tt02(session, company_id, year, **kw)
    return BUILDERS[form_code](session, company_id, year, month, **kw)


# ------------------------------------------------------------------ XML

def build_xml(pkg: Package) -> str:
    """Илгээх XML. ТЕГ-ийн XSD гармагц Header/Row-г түүн рүү буулгана."""
    root = ET.Element("TaxReturn", {"schemaVersion": "0.1", "source": "Bayan AI"})

    head = ET.SubElement(root, "Header")
    ET.SubElement(head, "FormCode").text = pkg.form_code
    ET.SubElement(head, "TIN").text = pkg.company["tin"]
    ET.SubElement(head, "CompanyName").text = pkg.company["name"]
    ET.SubElement(head, "Period").text = pkg.period
    ET.SubElement(head, "GeneratedAt").text = datetime.utcnow().isoformat(timespec="seconds")

    body = ET.SubElement(root, "Rows")
    for key, value in pkg.rows.items():
        if key == "form_name":
            continue
        el = ET.SubElement(body, "Row", {"code": key})
        el.text = (f"{value / 100:.2f}" if key.endswith("_minor")
                   else str(value))

    gate = ET.SubElement(root, "Validation", {"blocked": str(pkg.blocked()).lower()})
    for c in pkg.checks:
        ET.SubElement(gate, "Check", {"code": c.code, "ok": str(c.ok).lower()}).text = c.detail

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ------------------------------------------------------------------ клиент

@dataclass
class EtaxConfig:
    """Орчны хувьсагчийг үүсгэх үед уншина — import үед БИШ.

    Import үед уншвал програм асаасны дараа эрхийн мэдээлэл тохируулах,
    тестээр орлуулах аль аль нь боломжгүй болно."""
    base_url: str = field(
        default_factory=lambda: os.environ.get("ETAX_URL", "https://api.etax.mta.mn"))
    tin: str = field(default_factory=lambda: os.environ.get("ETAX_TIN", ""))
    token: str = field(default_factory=lambda: os.environ.get("ETAX_TOKEN", ""))


class EtaxClient:
    """ТЕГ-ийн цахим татварын системийн клиент.

    Эрхийн мэдээлэлгүй үед mock горимд ажиллана — ТЕГ-ийн API нь Монголын
    IP ба гэрээт хандалт шаарддаг тул хөгжүүлэлтэд бодит дуудлага хийхгүй."""

    def __init__(self, cfg: EtaxConfig | None = None):
        self.cfg = cfg or EtaxConfig()
        self.is_mock = self.cfg.token in ("", "mock") or not self.cfg.tin

    def send(self, form_code: str, period: str, xml_payload: str) -> dict:
        if self.is_mock:
            digest = hashlib.sha256(xml_payload.encode()).hexdigest()
            return {"status": "mock", "receipt_id": f"MOCK-{form_code}-{period}",
                    "detail": f"Mock горим — ТЕГ рүү илгээгээгүй (sha256 {digest[:12]})"}

        import httpx
        r = httpx.post(
            f"{self.cfg.base_url}/returns",
            params={"tin": self.cfg.tin, "formCode": form_code, "period": period},
            content=xml_payload.encode(),
            headers={"Authorization": f"Bearer {self.cfg.token}",
                     "Content-Type": "application/xml"},
            timeout=120)
        r.raise_for_status()
        data = r.json()
        return {"status": "sent", "receipt_id": data.get("receiptId"),
                "detail": data.get("message", "")}


# ------------------------------------------------------------------ илгээх

def submit(session: Session, company_id: str, form_code: str, year: int,
           month: int | None = None, actor_id: str | None = None,
           client: EtaxClient | None = None, today: date | None = None,
           **kw) -> dict:
    """Маягтыг бэлдэж, хаалтыг давуулж, ТЕГ рүү илгээнэ.

    Хаалт давахгүй бол ИЛГЭЭХГҮЙ — оролдлого нь бүртгэлд `blocked` төлөвөөр
    үлдэнэ. Тэнцээгүй тайлан илгээх нь татварын эрсдэл."""
    pkg = build(session, company_id, form_code, year, month, today=today, **kw)
    payload = build_xml(pkg)
    digest = hashlib.sha256(payload.encode()).hexdigest()

    if pkg.blocked():
        failed = [c for c in pkg.checks if c.blocking and not c.ok]
        detail = "; ".join(f"{c.title}: {c.detail}" for c in failed)
        session.add(EtaxSubmission(
            company_id=company_id, actor_id=actor_id, form_code=pkg.form_code,
            period=pkg.period, status="blocked", payload_sha256=digest,
            detail=detail[:2000]))
        session.flush()
        return {"status": "blocked", "package": pkg.to_dict(),
                "detail": detail, "xml": payload}

    result = (client or EtaxClient()).send(pkg.form_code, pkg.period, payload)
    session.add(EtaxSubmission(
        company_id=company_id, actor_id=actor_id, form_code=pkg.form_code,
        period=pkg.period, status=result["status"], payload_sha256=digest,
        receipt_id=result.get("receipt_id"), detail=(result.get("detail") or "")[:2000]))
    session.flush()
    return {"status": result["status"], "receipt_id": result.get("receipt_id"),
            "detail": result.get("detail"), "package": pkg.to_dict(),
            "xml": payload}


def history(session: Session, company_id: str, limit: int = 50) -> list[dict]:
    rows = session.scalars(
        select(EtaxSubmission)
        .where(EtaxSubmission.company_id == company_id)
        .order_by(EtaxSubmission.submitted_at.desc()).limit(limit)).all()
    return [{
        "id": r.id, "form_code": r.form_code, "period": r.period,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "status": r.status, "receipt_id": r.receipt_id,
        "payload_sha256": r.payload_sha256, "detail": r.detail,
    } for r in rows]
