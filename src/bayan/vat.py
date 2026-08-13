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


def summarize_ebarimt(items: list[dict]) -> dict:
    """eBarimt-ын баримтуудаас ТТ-03а-гийн гол дүнг тооцно (юу ч бичихгүй).

    Баримтын дүн нь НӨАТ ОРСОН нийт дүн, харин ТТ-03а-гийн 1, 3-р мөр нь
    цэвэр дүн тул хасаж өгнө. «Татварын төрөл» багана байвал НӨАТ ногдохыг
    түүгээр, байхгүй бол НӨАТ-ын дүнгээр ялгана.
    """
    def side(direction: str) -> dict:
        rows = [i for i in items if i.get("direction") == direction]
        vatable = [i for i in rows if i.get("tax_type") == "VAT_ABLE"]
        gross = sum(int(i["total_minor"]) for i in rows)
        vat = sum(int(i.get("vat_minor") or 0) for i in rows)
        return {
            "count": len(rows),
            "gross_minor": gross,
            "net_minor": gross - vat,
            "vat_minor": vat,
            "vatable_count": len(vatable),
            "vatable_gross_minor": sum(int(i["total_minor"]) for i in vatable),
            "exempt_gross_minor": gross - sum(int(i["total_minor"]) for i in vatable),
        }

    sales, purchases = side("in"), side("out")
    return {
        "sales": sales,
        "purchases": purchases,
        "net_payable_minor": sales["vat_minor"] - purchases["vat_minor"],
    }


def compare_with_book(session: Session, company_id: str, items: list[dict],
                      year: int, month: int | None = None) -> dict:
    """eBarimt-ын дүн ба дэвтрийн ТТ-03а-г зэрэгцүүлж зөрүүг харуулна.

    ЮУ Ч БИЧИХГҮЙ — журнал ч, нэхэмжлэх ч үүсгэхгүй. eBarimt дээр байгаа
    боловч дэвтэрт бүртгэгдээгүй борлуулалт/худалдан авалтыг илрүүлэх
    зорилготой (ТЕГ-т илгээхээс өмнөх шалгалт).
    """
    eb = summarize_ebarimt(items)
    book = tt03a(session, company_id, year, month)
    r = book["rows"]

    lines = [
        ("Нийт борлуулалт (цэвэр)", eb["sales"]["net_minor"], r["1_niit_borluulalt"]),
        ("Ногдуулсан НӨАТ", eb["sales"]["vat_minor"], r["26_nogduulsan_tatvar"]),
        ("Нийт худалдан авалт", eb["purchases"]["gross_minor"],
         r["32_niit_hudaldan_avalt"]),
        ("Төлсөн НӨАТ", eb["purchases"]["vat_minor"], r["42_tolson_noat"]),
    ]
    return {
        "period": f"{year}" + (f"-{month:02d}" if month else ""),
        "ebarimt": eb,
        "book_rows": r,
        "lines": [
            {"label": label, "ebarimt_minor": a, "book_minor": b,
             "diff_minor": a - b}
            for label, a, b in lines
        ],
        "net_payable_minor": eb["net_payable_minor"],
        "book_net_payable_minor": r["64_etssiin_tolboh"] - r["65_etssiin_butsaan_avah"],
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
