"""Ebarimt / НӨАТУС холболтын албан ёсны клиент.

  * Борлуулалтын баримт үүсгэх (хувь хүн болон байгууллагад)
  * Баримт буцаах (хүчингүй болгох)
  * Худалдан авалтын падаануудыг ТЕГ-ын системээс автоматаар татах
  * Тест болон локал горимд Mock хариу буцаах дэмжлэгтэй.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class EbarimtConfig:
    base_url: str = os.environ.get("EBARIMT_URL", "https://api.ebarimt.mn")
    tin: str = os.environ.get("EBARIMT_TIN", "")
    token: str = os.environ.get("EBARIMT_TOKEN", "")


class EbarimtClient:
    def __init__(self, cfg: EbarimtConfig | None = None):
        self.cfg = cfg or EbarimtConfig()
        # Хэрэв токен нь "mock" эсвэл хоосон бол туршилтын горимд ажиллана
        self.is_mock = self.cfg.token == "mock" or not (self.cfg.tin and self.cfg.token)

    def fetch_receipts(self, year: int, month: int) -> list[dict]:
        """Тухайн сарын байгууллагын борлуулалтын e-barimts татах."""
        if self.is_mock:
            # Mock дата буцаана
            return [
                {"date": f"{year}-{month:02d}-05", "total_minor": 220000000, "receipt_id": "MOCK-REC-01"},
                {"date": f"{year}-{month:02d}-10", "total_minor": 5500000, "receipt_id": "MOCK-REC-02"}
            ]

        import httpx
        r = httpx.get(
            f"{self.cfg.base_url}/receipts",
            params={"tin": self.cfg.tin, "year": year, "month": month},
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=60
        )
        r.raise_for_status()
        return [
            {"date": x["date"][:10],
             "total_minor": int(round(float(x["totalAmount"]) * 100)),
             "receipt_id": x["id"]}
            for x in r.json().get("receipts", [])
        ]

    def create_receipt(self, amount_minor: int, vat_minor: int,
                       customer_tin: str | None = None,
                       items: list[dict] | None = None) -> dict:
        """Борлуулалтын баримт үүсгэнэ (НӨАТУС API).

        amount_minor: нийт дүн (мөнгөөр)
        vat_minor: НӨАТ-ын дүн (мөнгөөр)
        customer_tin: Байгууллагын РД (байгууллагын баримт бол)
        items: барааны дэлгэрэнгүй жагсаалт: [{"code": "...", "name": "...", "qty": 1, "price_minor": 100}]

        Буцаах утга: {"receipt_id": str, "qr_data": str, "lottery": str, "success": bool}
        """
        amount_tug = float(amount_minor) / 100.0
        vat_tug = float(vat_minor) / 100.0

        if self.is_mock:
            # Mock баримт үүсгэх
            import uuid
            import random
            receipt_id = f"MOCK-REC-{uuid.uuid4().hex[:8].upper()}"
            lottery_code = f"LOT-{random.randint(10000000, 99999999)}"
            return {
                "receipt_id": receipt_id,
                "qr_data": f"https://ebarimt.mn/qr/{receipt_id}",
                "lottery": lottery_code,
                "success": True
            }

        import httpx
        payload = {
            "amount": amount_tug,
            "vat": vat_tug,
            "cashAmount": amount_tug,
            "nonCashAmount": 0.0,
            "billType": "3" if customer_tin else "1",
            "customerTin": customer_tin or "",
            "stocks": []
        }

        if items:
            for it in items:
                price_tug = float(it["price_minor"]) / 100.0
                qty = float(it["qty"])
                payload["stocks"].append({
                    "code": it["code"],
                    "name": it["name"],
                    "qty": qty,
                    "price": price_tug,
                    "vat": price_tug * qty * 0.1, # 10% VAT
                    "totalAmount": price_tug * qty
                })

        r = httpx.post(
            f"{self.cfg.base_url}/receipt/create",
            json=payload,
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=60
        )
        r.raise_for_status()
        res = r.json()
        
        return {
            "receipt_id": res.get("billId"),
            "qr_data": res.get("qrData"),
            "lottery": res.get("lottery"),
            "success": res.get("success", False)
        }

    def void_receipt(self, receipt_id: str) -> dict:
        """Баримтыг буцааж хүчингүй болгоно."""
        if self.is_mock:
            return {"receipt_id": receipt_id, "success": True}

        import httpx
        r = httpx.post(
            f"{self.cfg.base_url}/receipt/void",
            json={"billId": receipt_id},
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=60
        )
        r.raise_for_status()
        res = r.json()
        return {
            "receipt_id": receipt_id,
            "success": res.get("success", False)
        }

    def fetch_purchase_invoices(self, year: int, month: int) -> list[dict]:
        """Байгууллагын регистрт ТЕГ-ын системээс ирүүлсэн худалдан авалтын падаануудыг автоматаар татна.

        Буцаах бүтэц: [{"date": "YYYY-MM-DD", "total_minor": int, "vat_minor": int,
                        "supplier_tin": str, "supplier_name": str, "invoice_id": str}]
        """
        if self.is_mock:
            # Mock худалдан авалтын падаанууд
            return [
                {
                    "date": f"{year}-{month:02d}-08",
                    "total_minor": 132000000, # 1,320,000₮ (120,000₮ НӨАТ орсон)
                    "vat_minor": 12000000,
                    "supplier_tin": "5011223344",
                    "supplier_name": "Хангамж Групп ХХК",
                    "invoice_id": "INV-MOCK-P1"
                }
            ]

        import httpx
        r = httpx.get(
            f"{self.cfg.base_url}/purchase-invoices",
            params={"tin": self.cfg.tin, "year": year, "month": month},
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=60
        )
        r.raise_for_status()
        return [
            {
                "date": x["date"][:10],
                "total_minor": int(round(float(x["totalAmount"]) * 100)),
                "vat_minor": int(round(float(x["vatAmount"]) * 100)),
                "supplier_tin": x["supplierTin"],
                "supplier_name": x["supplierName"],
                "invoice_id": x["invoiceId"]
            }
            for x in r.json().get("invoices", [])
        ]


def parse_ebarimt_excel(file_bytes: bytes) -> list[dict]:
    """eBarimt Excel эсвэл CSV экспорт файлыг уншиж жагсаалт буцаана."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active
    items = []
    
    col_date = col_amt = col_ddtd = col_party = -1
    header_found = False
    
    for row in sheet.iter_rows(values_only=True):
        row_vals = [str(cell).strip() if cell is not None else "" for cell in row]
        if not any(row_vals):
            continue
        if not header_found:
            for idx, val in enumerate(row_vals):
                val_l = val.lower()
                if any(k in val_l for k in ["огноо", "date", "өдөр"]): col_date = idx
                elif any(k in val_l for k in ["дүн", "тооцоо", "amount", "нийт"]): col_amt = idx
                elif any(k in val_l for k in ["ддтд", "баримт", "id", "дугаар", "ddtd"]): col_ddtd = idx
                elif any(k in val_l for k in ["харилцагч", "байгууллага", "нэр", "регистр", "tin", "party"]): col_party = idx
            if col_amt != -1:
                header_found = True
            continue
        
        amt_str = row_vals[col_amt] if col_amt != -1 and col_amt < len(row_vals) else ""
        if not amt_str or amt_str == "None": continue
        
        try:
            clean_amt = amt_str.replace(",", "").replace("₮", "").replace(" ", "").replace("MNT", "")
            amount = float(clean_amt)
            if amount <= 0: continue
            amount_minor = int(round(amount * 100))
        except ValueError:
            continue
            
        date_str = row_vals[col_date] if col_date != -1 and col_date < len(row_vals) else "2026-07-01"
        ddtd_str = row_vals[col_ddtd] if col_ddtd != -1 and col_ddtd < len(row_vals) else f"EB-{len(items)+1}"
        party_str = row_vals[col_party] if col_party != -1 and col_party < len(row_vals) else "eBarimt"
        
        items.append({
            "date": date_str[:10],
            "total_minor": amount_minor,
            "receipt_id": ddtd_str,
            "party": party_str
        })
    return items
