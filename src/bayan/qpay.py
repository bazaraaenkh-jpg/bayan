"""QPay төлбөрийн интеграци — SaaS багц идэвхжүүлэх нэхэмжлэх үүсгэх, төлөв шалгах.
"""

from __future__ import annotations

import logging
import uuid
import requests
from typing import Any

logger = logging.getLogger(__name__)


class QPayClient:
    def __init__(self, username: str | None = None, password: str | None = None, invoice_code: str | None = None):
        self.username = username
        self.password = password
        self.invoice_code = invoice_code
        self.token = None
        self.api_url = "https://merchant.qpay.mn/v2"

    def get_token(self) -> str | None:
        """QPay системээс хандах token авах."""
        if not self.username or not self.password:
            # Mock mode
            return "mock-token"
        
        try:
            res = requests.post(
                f"{self.api_url}/auth/token",
                auth=(self.username, self.password),
                timeout=10
            )
            if res.status_code == 200:
                self.token = res.json().get("access_token")
                return self.token
        except Exception as e:
            logger.error("Failed to fetch QPay token: %s", e)
        return None

    def create_invoice(self, company_name: str, amount: int, memo: str) -> dict[str, Any]:
        """QPay нэхэмжлэх үүсгэж QR код болон банкнуудын линкийг буцаана."""
        invoice_id = str(uuid.uuid4())
        
        # Real integration if credentials provided
        token = self.get_token()
        if token and token != "mock-token":
            headers = {"Authorization": f"Bearer {token}"}
            payload = {
                "invoice_code": self.invoice_code,
                "sender_invoice_no": invoice_id,
                "sender_branch_code": "MAIN",
                "invoice_receiver_code": "TERMINAL",
                "invoice_description": f"{company_name} - {memo}",
                "amount": float(amount),
                "callback_url": "https://bayan.ai/api/payment/callback"
            }
            try:
                res = requests.post(f"{self.api_url}/invoice", json=payload, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "invoice_id": data.get("invoice_id", invoice_id),
                        "qr_text": data.get("qr_text", "mock-qr-text"),
                        "qr_image": data.get("qr_image", ""),
                        "urls": data.get("urls", []),
                        "mock": False
                    }
            except Exception as e:
                logger.error("QPay invoice creation failed, falling back to mock: %s", e)

        # Mock Response
        return {
            "invoice_id": invoice_id,
            "qr_text": f"qpay://payment?id={invoice_id}&amount={amount}",
            "qr_image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADIAAAAyBAMAAADtEsWJAAAABGdBTUEAALGPC/xhBQAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAA",
            "urls": [
                {"name": "Khan Bank", "logo": "https://example.com/khan.png", "link": f"khanbank://qpay?id={invoice_id}"},
                {"name": "Golomt Bank", "logo": "https://example.com/golomt.png", "link": f"golomtbank://qpay?id={invoice_id}"},
                {"name": "State Bank", "logo": "https://example.com/state.png", "link": f"statebank://qpay?id={invoice_id}"}
            ],
            "mock": True
        }

    def check_payment(self, invoice_id: str) -> bool:
        """Нэхэмжлэх төлөгдсөн эсэхийг шалгах."""
        token = self.get_token()
        if token and token != "mock-token":
            headers = {"Authorization": f"Bearer {token}"}
            payload = {
                "object_type": "INVOICE",
                "object_id": invoice_id,
                "offset": 0,
                "limit": 10
            }
            try:
                res = requests.post(f"{self.api_url}/payment/check", json=payload, headers=headers, timeout=10)
                if res.status_code == 200:
                    # 'PAID' status check
                    return res.json().get("payment_status") == "PAID"
            except Exception as e:
                logger.error("QPay payment check failed: %s", e)

        # Mock Mode: only returns True if QPAY_MOCK is explicitly enabled
        import os
        if os.environ.get("QPAY_MOCK", "0") == "1":
            return True
        return False
