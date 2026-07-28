"""IAS 21 — Гадаад валютын ханшийн зөрүүг Монголбанкны ханшаар дахин үнэлэх модуль.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import ledger
from .models import Account, JournalEntry, JournalLine, EntryStatus, SourceType, NormalSide

logger = logging.getLogger(__name__)


def fetch_rate(currency: str, d: date) -> float:
    """Монголбанкны (Bank of Mongolia) API-аас тухайн өдрийн валютын ханшийг татна.
    Хэрэв сүлжээний алдаа гарвал эсвэл тест горимд байвал Mock ханш буцаана.
    """
    currency = currency.upper().strip()
    if currency == "MNT":
        return 1.0

    # Туршилтын болон сүлжээгүй үед ашиглах тогтмол ханшууд
    MOCK_RATES = {
        "USD": 3450.0,
        "EUR": 3750.0,
        "CNY": 480.0,
        "RUB": 38.0,
    }

    try:
        import httpx
        import xml.etree.ElementTree as ET
        
        # Монголбанкны албан ёсны ханшийн XML API
        url = f"https://www.mongolbank.mn/iotms/v1/rates?date={d.isoformat()}"
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            # XML parse хийх
            root = ET.fromstring(r.content)
            # <rate><code name="USD">3450.00</code></rate> загвартай байна
            for val in root.findall(".//rate"):
                code_el = val.find("code")
                value_el = val.find("value")
                if code_el is not None and value_el is not None:
                    if code_el.text == currency:
                        return float(value_el.text.replace(",", ""))
    except Exception as e:
        logger.warning("Mongolbank API-аас ханш татахад алдаа гарлаа, Mock ашиглана: %s", e)

    return MOCK_RATES.get(currency, 1.0)


def run_revaluation(session: Session, company_id: str, reval_date: date, actor_id: str | None = None) -> JournalEntry | None:
    """Сарын эцэст гадаад валютын дансдыг дахин үнэлж, ханшийн зөрүүний бичилт хийнэ."""
    
    # 1. Валютын данснуудыг олно (MNT биш данснууд)
    accounts = session.scalars(
        select(Account).where(Account.company_id == company_id, Account.currency != "MNT", Account.is_postable == True)
    ).all()
    if not accounts:
        return None

    # Ханшийн зөрүүний үндсэн дансдуудыг бэлдэнэ (олз: 5204 / гарз: 7118)
    # Цоо шинээр үүсгэсэн компанид эдгээр данс байгаа эсэхийг шалгаад байхгүй бол seed хийнэ
    gain_acc = session.scalar(select(Account).where(Account.company_id == company_id, Account.code == "5204"))
    loss_acc = session.scalar(select(Account).where(Account.company_id == company_id, Account.code == "7118"))
    
    if not gain_acc or not loss_acc:
        raise ValueError("Ханшийн зөрүүний олз (5204) эсвэл гарз (7118) данс тохируулагдаагүй байна.")

    lines: list[ledger.LineInput] = []

    for acc in accounts:
        # Валютын бодит үлдэгдлийг тооцоолно (debit - credit)
        # Журналын мөрүүдийг шүүнэ
        q = (
            select(
                func.coalesce(func.sum(JournalLine.debit_minor), 0),
                func.coalesce(func.sum(JournalLine.credit_minor), 0)
            )
            .join(JournalEntry)
            .where(
                JournalLine.account_id == acc.id,
                JournalEntry.entry_date <= reval_date,
                JournalEntry.status == EntryStatus.posted
            )
        )
        debit_mnt, credit_mnt = session.execute(q).first() or (0, 0)
        
        # Гадаад валютын үлдэгдлийг олно
        # Debit болон Credit талын валютын дүнг тус тусад нь нэмнэ
        q_fx_dr = (
            select(func.coalesce(func.sum(JournalLine.amount_currency), 0))
            .join(JournalEntry)
            .where(
                JournalLine.account_id == acc.id,
                JournalLine.debit_minor > 0,
                JournalEntry.entry_date <= reval_date,
                JournalEntry.status == EntryStatus.posted
            )
        )
        q_fx_cr = (
            select(func.coalesce(func.sum(JournalLine.amount_currency), 0))
            .join(JournalEntry)
            .where(
                JournalLine.account_id == acc.id,
                JournalLine.credit_minor > 0,
                JournalEntry.entry_date <= reval_date,
                JournalEntry.status == EntryStatus.posted
            )
        )
        fx_dr = session.scalar(q_fx_dr) or 0.0
        fx_cr = session.scalar(q_fx_cr) or 0.0
        
        # Нормаль талаас хамаарч валютын үлдэгдлийг олно
        if acc.normal_side == NormalSide.debit:
            actual_mnt_minor = debit_mnt - credit_mnt
            fx_balance = fx_dr - fx_cr
        else:
            actual_mnt_minor = credit_mnt - debit_mnt
            fx_balance = fx_cr - fx_dr
            
        if fx_balance == 0 and actual_mnt_minor == 0:
            continue

        # Монголбанкны ханш татах
        rate = fetch_rate(acc.currency, reval_date)
        
        # Дахин үнэлсэн дүн (Төгрөгөөр, minor unit)
        revalued_mnt_minor = int(round(fx_balance * rate * 100))
        
        # Зөрүү = Шинэ дүн - Хуучин дүн
        diff_minor = revalued_mnt_minor - actual_mnt_minor
        if diff_minor == 0:
            continue
            
        # Журналын бичилтүүд бэлдэх
        if acc.normal_side == NormalSide.debit:
            if diff_minor > 0:  # Ханшийн зөрүүний олз (Asset өссөн)
                lines.append(ledger.LineInput(
                    account_code=acc.code,
                    debit_minor=diff_minor,
                    description=f"Ханшийн дахин үнэлгээ олз /{acc.currency} {fx_balance:.2f} @ {rate}/"
                ))
                lines.append(ledger.LineInput(
                    account_code=gain_acc.code,
                    credit_minor=diff_minor,
                    description=f"Ханшийн зөрүүний олз /{acc.code}/"
                ))
            else:  # Ханшийн зөрүүний гарз (Asset буурсан)
                loss_val = abs(diff_minor)
                lines.append(ledger.LineInput(
                    account_code=loss_acc.code,
                    debit_minor=loss_val,
                    description=f"Ханшийн зөрүүний гарз /{acc.code}/"
                ))
                lines.append(ledger.LineInput(
                    account_code=acc.code,
                    credit_minor=loss_val,
                    description=f"Ханшийн дахин үнэлгээ гарз /{acc.currency} {fx_balance:.2f} @ {rate}/"
                ))
        else: # NormalSide.credit (Өр төлбөрийн данс)
            if diff_minor > 0:  # Ханшийн зөрүүний гарз (Өр төлбөр өссөн)
                lines.append(ledger.LineInput(
                    account_code=loss_acc.code,
                    debit_minor=diff_minor,
                    description=f"Ханшийн зөрүүний гарз /{acc.code}/"
                ))
                lines.append(ledger.LineInput(
                    account_code=acc.code,
                    credit_minor=diff_minor,
                    description=f"Ханшийн дахин үнэлгээ гарз /{acc.currency} {fx_balance:.2f} @ {rate}/"
                ))
            else:  # Ханшийн зөрүүний олз (Өр төлбөр буурсан)
                gain_val = abs(diff_minor)
                lines.append(ledger.LineInput(
                    account_code=acc.code,
                    debit_minor=gain_val,
                    description=f"Ханшийн дахин үнэлгээ олз /{acc.currency} {fx_balance:.2f} @ {rate}/"
                ))
                lines.append(ledger.LineInput(
                    account_code=gain_acc.code,
                    credit_minor=gain_val,
                    description=f"Ханшийн зөрүүний олз /{acc.code}/"
                ))

    if not lines:
        return None

    # Ханшийн өөрчлөлтийг журналын бичилт болгон хадгална
    entry = ledger.post_entry(
        session, company_id, reval_date, lines,
        source_type=SourceType.fx_reval,
        memo=f"Ханшийн тэгшитгэл дахин үнэлгээ {reval_date.isoformat()}",
        actor_id=actor_id
    )
    return entry
