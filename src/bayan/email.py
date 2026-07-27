"""Email Service — Хэрэглэгчийн урилга болон нууц үг сэргээх линкийг SMTP-ээр (эсвэл fallback log) илгээнэ.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

EMAILS_LOG = Path(__file__).resolve().parents[2] / "storage" / "emails.log"


def send_email(to_email: str, subject: str, html_content: str) -> None:
    """SMTP-ээр бодит имэйл илгээнэ. Тохиргоо байхгүй бол локал файл руу бичнэ."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    passwd = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_SENDER", "noreply@bayan.ai")
    
    # 1. Хэрэв SMTP тохиргоо бүрэн байвал
    if host and user and passwd:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = to_email
            msg.attach(MIMEText(html_content, "html", "utf-8"))
            
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.starttls()
                server.login(user, passwd)
                server.sendmail(sender, to_email, msg.as_string())
            logger.info("Email sent successfully to %s", to_email)
            return
        except Exception as e:
            logger.error("Failed to send real email via SMTP: %s. Falling back to log file.", e)

    # 2. Fallback: Локал файл болон лог руу бичнэ
    EMAILS_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_content = (
        f"==================================================\n"
        f"DATE: {smtplib.email.utils.formatdate()}\n"
        f"TO: {to_email}\n"
        f"SUBJECT: {subject}\n"
        f"CONTENT:\n{html_content}\n"
        f"==================================================\n\n"
    )
    with open(EMAILS_LOG, "a", encoding="utf-8") as f:
        f.write(log_content)
    
    logger.info("SMTP local simulation: Email to %s saved to %s", to_email, EMAILS_LOG)


def send_invite_email(to_email: str, company_name: str, temp_password: str) -> None:
    """Шинэ хэрэглэгчийн урилгыг илгээнэ."""
    subject = f"Bayan AI - {company_name} компаниас ирсэн урилга"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #4f46e5;">Bayan AI платформд тавтай морил!</h2>
        <p>Сайн байна уу,</p>
        <p>Танд <b>{company_name}</b> компаниас системд хамтран ажиллах урилга ирлээ.</p>
        <p>Нэвтрэх түр мэдээлэл:</p>
        <table style="border-collapse: collapse; width: 100%; max-width: 400px; margin: 20px 0;">
          <tr>
            <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Имэйл:</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{to_email}</td>
          </tr>
          <tr>
            <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Түр нууц үг:</td>
            <td style="padding: 8px; border: 1px solid #ddd; font-family: monospace;">{temp_password}</td>
          </tr>
        </table>
        <p style="color: #6b7280; font-size: 13px;">Нэвтэрснийхээ дараа нууц үгээ заавал солино уу.</p>
        <br>
        <hr style="border: none; border-top: 1px solid #ddd;">
        <p style="font-size: 12px; color: #9ca3af;">Энэхүү имэйл нь автоматаар үүссэн тул хариу бичих шаардлагагүй.</p>
      </body>
    </html>
    """
    send_email(to_email, subject, html)


def send_reset_password_email(to_email: str, reset_link: str) -> None:
    """Нууц үг сэргээх линк илгээнэ."""
    subject = "Bayan AI - Нууц үг сэргээх хүсэлт"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #4f46e5;">Нууц үг сэргээх заавар</h2>
        <p>Сайн байна уу,</p>
        <p>Таны бүртгэлд нууц үг сэргээх хүсэлт гарсан байна. Доорх холбоосоор орж нууц үгээ шинэчилнэ үү:</p>
        <p style="margin: 25px 0;">
          <a href="{reset_link}" style="background-color: #4f46e5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Нууц үг сэргээх</a>
        </p>
        <p style="color: #6b7280; font-size: 13px;">Энэхүү холбоос нь 1 цагийн хугацаанд хүчинтэй байна.</p>
        <br>
        <hr style="border: none; border-top: 1px solid #ddd;">
        <p style="font-size: 12px; color: #9ca3af;">Энэхүү имэйл нь автоматаар үүссэн тул хариу бичих шаардлагагүй.</p>
      </body>
    </html>
    """
    send_email(to_email, subject, html)
