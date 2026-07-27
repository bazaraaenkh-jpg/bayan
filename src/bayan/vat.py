"""НӨАТ — ТТ-03а маягтын гол мөрүүдийн автомат тооцоолол + ebarimt тулгалт.

Fino-гийн ТТ-03а 65 мөрт маягттай ижил дугаарлалтын ГОЛ мөрүүд (бүрэн маягт
Үе 2-т). Эх сурвалж: батлагдсан борлуулалт/худалдан авалтын нэхэмжлэхүүд.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .partners import Invoice, InvoiceKind

VAT_RATE = 10


def tt03a(session: Session, company_id: str, year: int,
          month: int | None = None) -> dict:
    """ТТ-03а-гийн гол мөрүүд. month=None бол жилээр."""
    def in_period(d: date) -> bool:
        return d.year == year and (month is None or d.month == month)

    sales = [i for i in session.scalars(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == InvoiceKind.sales))
        if in_period(i.issue_date)]
    purch = [i for i in session.scalars(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == InvoiceKind.purchase))
        if in_period(i.issue_date)]

    r1 = sum(i.net_minor for i in sales)                     # нийт борлуулалт
    r3 = sum(i.net_minor for i in sales if i.vat_minor)      # НӨАТ ногдох
    r26 = sum(i.vat_minor for i in sales)                    # ногдуулсан
    r31 = r26
    r32 = sum(i.net_minor + i.vat_minor for i in purch)      # нийт худалдан авалт
    r33 = sum(i.net_minor for i in purch if i.vat_minor)     # НӨАТ-тай худ.авалт
    r42 = sum(i.vat_minor for i in purch)                    # төлсөн НӨАТ
    r43 = 0                                                  # хасагдахгүй (Үе 2)
    r49 = r42 - r43                                          # хасагдах
    r56 = r31
    r57 = r49
    net = r56 - r57
    return {
        "period": f"{year}" + (f"-{month:02d}" if month else " (жилийн)"),
        "rows": {
            "1_niit_borluulalt": r1,
            "3_noat_nogdoh_borluulalt": r3,
            "26_nogduulsan_tatvar": r26,
            "31_nogduulsan_niit": r31,
            "32_niit_hudaldan_avalt": r32,
            "33_noattai_hudaldan_avalt": r33,
            "42_tolson_noat": r42,
            "43_hasagdahgui_noat": r43,
            "49_hasagdah_noat": r49,
            "56_tolboh_zohih": r56,
            "57_butsaan_avah": r57,
            "64_etssiin_tolboh": max(net, 0),
            "65_etssiin_butsaan_avah": max(-net, 0),
        },
    }


def reconcile_ebarimt(session: Session, company_id: str,
                      receipts: list[dict], year: int, month: int) -> dict:
    """Ebarimt-аас татсан падаануудыг борлуулалтын нэхэмжлэхтэй тулгана.

    receipts: [{"date": "YYYY-MM-DD", "total_minor": int, "receipt_id": str}]
    (Ebarimt/НӨАТУС-ийн API холболт ebarimt.py-д; энд тулгалтын цөм логик.)
    """
    sales = [i for i in session.scalars(select(Invoice).where(
        Invoice.company_id == company_id, Invoice.kind == InvoiceKind.sales))
        if i.issue_date.year == year and i.issue_date.month == month]

    unmatched_inv = {i.id: i for i in sales}
    matched, unmatched_receipts = [], []
    for r in receipts:
        hit = next((i for i in unmatched_inv.values()
                    if i.total_minor == r["total_minor"]
                    and i.issue_date.isoformat() == r["date"]), None)
        if hit:
            matched.append({"receipt_id": r["receipt_id"], "invoice": hit.number})
            del unmatched_inv[hit.id]
        else:
            unmatched_receipts.append(r)
    return {
        "matched": matched,
        "unmatched_receipts": unmatched_receipts,       # падаан бий, нэхэмжлэх алга
        "unmatched_invoices": [i.number for i in unmatched_inv.values()],
    }
