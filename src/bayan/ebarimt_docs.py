"""eBarimt-ын баримтуудыг нэхэмжлэх + журналын бичилт болгон буулгах.

Энэ модулиас өмнө eBarimt-ын тулгалт нь зөвхөн дэлгэцийн тайлан байсан —
ТТ-03а нь гараар бүртгэсэн нэхэмжлэхээс гардаг тул хоёрын хооронд холбоо
байхгүй байв.

Бичилтийн загвар (`partners.post_invoice`-той ижил):

    борлуулалт:      Дт 1201 нийт / Кт 5101 цэвэр [/ Кт 3105 НӨАТ] [/ Кт НХАТ]
    худалдан авалт:  Дт зардал цэвэр [+ Дт 1203 НӨАТ] / Кт 3101 нийт

Ингэснээр 3105 кредит ба 1203 дебитийн хөдөлгөөн ТТ-03а-гийн 31, 42-р
мөртэй тэнцэж, `etax.build_tt03a`-гийн тулгалт давна.

ХАМГИЙН ЧУХАЛ ЭРСДЭЛ — ДАВХАР БҮРТГЭЛ. Банкны хуулгын ангилалт (`pipeline`)
нь мөнгө орсон гүйлгээг шууд «Дт банк / Кт орлого + Кт 3105» гэж бичдэг.
Хэрэв тухайн сарын гүйлгээ аль хэдийн ингэж ангилагдсан байхад баримтуудаас
дахин нэхэмжлэх үүсгэвэл орлого ба НӨАТ хоёр дахин тоологдоно. Тиймээс:

  * өгөгдмөл нь ХУУРАМЧ АЖИЛЛУУЛАЛТ (dry_run) — юу ч бичихгүй, төлөвлөгөө
    буцаана;
  * тухайн хугацаанд 3105 кредит эсвэл 1203 дебитийн хөдөлгөөн аль хэдийн
    байвал `force` өгөхгүйгээр бичихгүй;
  * ДДТД-г нэхэмжлэхийн дугаар болгоно — дахин ажиллуулахад давхардахгүй.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .partners import Counterparty, Invoice, InvoiceKind, PartnerKind, post_invoice

#: Худалдан авалтын өгөгдмөл зардлын данс (нягтлан дараа нь ангилна)
DEFAULT_EXPENSE_ACCOUNT = "7199"
#: Борлуулалтын нэхэмжлэхийн төлбөрийн хугацаа (ПОС-ын баримт шууд төлөгддөг)
DEFAULT_DUE_DAYS = 0


class DocumentError(RuntimeError):
    """Бүртгэл үүсгэхээс татгалзсан шалтгаан."""


def _period_of(items: list[dict]) -> tuple[int, int] | None:
    months: dict[str, int] = {}
    for i in items:
        if i.get("date"):
            months[i["date"][:7]] = months.get(i["date"][:7], 0) + 1
    if not months:
        return None
    ym = max(months.items(), key=lambda kv: kv[1])[0]
    return int(ym[:4]), int(ym[5:7])


def _vat_movement(session: Session, company_id: str,
                  d_from: date, d_to: date) -> dict[str, int]:
    """Тухайн хугацааны 3105 кредит ба 1203 дебитийн хөдөлгөөн."""
    from .models import JournalEntry, JournalLine, Account, EntryStatus

    rows = session.execute(
        select(Account.code, JournalLine.debit_minor, JournalLine.credit_minor)
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(JournalEntry.company_id == company_id,
               JournalEntry.entry_date >= d_from,
               JournalEntry.entry_date <= d_to,
               JournalEntry.status == EntryStatus.posted)
    ).all()
    out = {"3105_credit": 0, "1203_debit": 0}
    for code, dr, cr in rows:
        if code == "3105":
            out["3105_credit"] += cr or 0
        elif code == "1203":
            out["1203_debit"] += dr or 0
    return out


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    d_from = date(year, month, 1)
    d_to = (date(year + 1, 1, 1) if month == 12
            else date(year, month + 1, 1)) - timedelta(days=1)
    return d_from, d_to


def plan(session: Session, company_id: str, items: list[dict],
         expense_account: str = DEFAULT_EXPENSE_ACCOUNT) -> dict:
    """Юу үүсэхийг тооцно — юу ч бичихгүй.

    Буцаах: {"period", "to_create", "already_exists", "skipped",
             "totals", "existing_vat_movement", "warnings"}
    """
    period = _period_of(items)
    existing_numbers = {
        n for (n,) in session.execute(
            select(Invoice.number).where(Invoice.company_id == company_id)).all()
    }

    to_create: list[dict] = []
    already: list[dict] = []
    skipped: list[dict] = []

    for it in items:
        rid = (it.get("receipt_id") or "").strip()
        direction = it.get("direction")
        if not rid or rid.startswith("EB-"):
            skipped.append({**_brief(it), "reason": "ДДТД байхгүй"})
            continue
        if not it.get("date"):
            skipped.append({**_brief(it), "reason": "огноогүй"})
            continue
        if direction not in ("in", "out"):
            skipped.append({**_brief(it), "reason": "тайлангийн төрөл тодорхойгүй"})
            continue
        if rid in existing_numbers:
            already.append(_brief(it))
            continue

        gross = int(it["total_minor"])
        vat = int(it.get("vat_minor") or 0)
        city = int(it.get("city_tax_minor") or 0)
        to_create.append({
            **_brief(it),
            "kind": "sales" if direction == "in" else "purchase",
            "net_minor": gross - vat - city,
            "vat_minor": vat,
            "city_tax_minor": city,
            "expense_account": expense_account if direction == "out" else None,
        })

    sales = [d for d in to_create if d["kind"] == "sales"]
    purch = [d for d in to_create if d["kind"] == "purchase"]
    totals = {
        "sales_count": len(sales),
        "sales_net_minor": sum(d["net_minor"] for d in sales),
        "sales_vat_minor": sum(d["vat_minor"] for d in sales),
        "purchase_count": len(purch),
        "purchase_net_minor": sum(d["net_minor"] for d in purch),
        "purchase_vat_minor": sum(d["vat_minor"] for d in purch),
    }

    existing = {"3105_credit": 0, "1203_debit": 0}
    warnings: list[str] = []
    if period:
        existing = _vat_movement(session, company_id, *_month_bounds(*period))
        if existing["3105_credit"] or existing["1203_debit"]:
            warnings.append(
                f"{period[0]}-{period[1]:02d} сард НӨАТ-ын бичилт аль хэдийн "
                f"байна (3105 кредит {existing['3105_credit'] / 100:,.2f}₮, "
                f"1203 дебит {existing['1203_debit'] / 100:,.2f}₮). Банкны "
                f"хуулгаа орлогоор ангилсан бол баримтуудаас дахин бичвэл "
                f"орлого, НӨАТ ХОЁР ДАХИН тоологдоно.")
    if already:
        warnings.append(f"{len(already)} баримт өмнө нь бүртгэгдсэн тул алгасана.")

    return {
        "period": f"{period[0]}-{period[1]:02d}" if period else None,
        "to_create": to_create,
        "already_exists": already,
        "skipped": skipped,
        "totals": totals,
        "existing_vat_movement": existing,
        "warnings": warnings,
    }


def _brief(it: dict) -> dict:
    return {
        "receipt_id": it.get("receipt_id"),
        "date": it.get("date"),
        "party": it.get("party"),
        "party_tin": it.get("party_tin"),
        "total_minor": int(it["total_minor"]),
        "dataset_label": it.get("dataset_label"),
        "direction": it.get("direction"),
        "tax_type": it.get("tax_type"),
    }


def _counterparty(session: Session, company_id: str, cache: dict,
                  name: str, tin: str | None, kind: PartnerKind) -> Counterparty:
    key = (tin or "").strip() or f"name:{name.strip().lower()}"
    if key in cache:
        return cache[key]

    cp = None
    if tin:
        cp = session.scalar(select(Counterparty).where(
            Counterparty.company_id == company_id, Counterparty.reg_no == tin))
    if cp is None:
        cp = session.scalar(select(Counterparty).where(
            Counterparty.company_id == company_id, Counterparty.name == name))
    if cp is None:
        cp = Counterparty(company_id=company_id, name=name or "eBarimt харилцагч",
                          reg_no=tin or None, kind=kind)
        session.add(cp)
        session.flush()
    cache[key] = cp
    return cp


def create(session: Session, company_id: str, items: list[dict],
           expense_account: str = DEFAULT_EXPENSE_ACCOUNT,
           force: bool = False, actor_id: str | None = None) -> dict:
    """Нэхэмжлэх + журналын бичилтийг ҮНЭХЭЭР үүсгэнэ.

    `force=False` үед тухайн сард НӨАТ-ын бичилт байвал татгалзана —
    давхар бүртгэлээс хамгаална.
    """
    p = plan(session, company_id, items, expense_account)
    ex = p["existing_vat_movement"]
    if not force and (ex["3105_credit"] or ex["1203_debit"]):
        raise DocumentError(
            f"{p['period']} сард НӨАТ-ын бичилт аль хэдийн байна "
            f"(3105 кредит {ex['3105_credit'] / 100:,.2f}₮, 1203 дебит "
            f"{ex['1203_debit'] / 100:,.2f}₮). Давхар бүртгэл үүсэх эрсдэлтэй "
            f"тул зөвхөн шалгасны дараа force=true-гээр үргэлжлүүлнэ үү.")

    cache: dict = {}
    created: list[dict] = []
    failed: list[dict] = []

    for d in p["to_create"]:
        is_sales = d["kind"] == "sales"
        try:
            cp = _counterparty(
                session, company_id, cache,
                d["party"] or ("Худалдан авагч" if is_sales else "Нийлүүлэгч"),
                d["party_tin"],
                PartnerKind.customer if is_sales else PartnerKind.supplier)
            issue = date.fromisoformat(d["date"])
            inv = post_invoice(
                session, company_id, cp.id,
                InvoiceKind.sales if is_sales else InvoiceKind.purchase,
                number=d["receipt_id"],
                issue_date=issue,
                due_date=issue + timedelta(days=DEFAULT_DUE_DAYS),
                net_minor=d["net_minor"],
                vat_minor=d["vat_minor"],
                city_tax_minor=d["city_tax_minor"],
                expense_account=d["expense_account"] or expense_account,
                actor_id=actor_id,
                memo=f"eBarimt {d['receipt_id']}",
            )
            created.append({"receipt_id": d["receipt_id"], "invoice_id": inv.id,
                            "journal_entry_id": inv.journal_entry_id,
                            "total_minor": inv.total_minor})
        except (ledger.LedgerError, ValueError) as e:
            failed.append({"receipt_id": d["receipt_id"], "error": str(e)})

    session.flush()
    return {
        "period": p["period"],
        "created_count": len(created),
        "created": created,
        "failed": failed,
        "already_exists_count": len(p["already_exists"]),
        "skipped_count": len(p["skipped"]),
        "totals": p["totals"],
        "warnings": p["warnings"],
    }
