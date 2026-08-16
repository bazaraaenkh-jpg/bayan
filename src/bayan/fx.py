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


class FxRateError(Exception):
    pass


# Сүлжээгүй/тест орчны нөөц ханш. ЭНЭ НЬ БОДИТ ХАНШ БИШ — эндээс авсан
# ханшаар бичилт хийвэл source="mock" гэж тэмдэглэгдэж, журналын тайлбарт
# «БАТАЛГААЖААГҮЙ ХАНШ» гэж бичигдэнэ.
MOCK_RATES = {"USD": 3450.0, "EUR": 3750.0, "CNY": 480.0, "RUB": 38.0}


def _rate_from_api(currency: str, d: date) -> float | None:
    """Монголбанкны API — JSON ба XML хэлбэр хоёуланг оролдоно."""
    import httpx

    url = f"https://www.mongolbank.mn/iotms/v1/rates?date={d.isoformat()}"
    r = httpx.get(url, timeout=10)
    if r.status_code != 200:
        return None

    # 1) JSON хэлбэр (одоогийн бодит API)
    try:
        data = r.json()
        payload = data.get("result", data) if isinstance(data, dict) else data
        if isinstance(payload, dict):
            for key in (currency, currency.lower()):
                if key in payload and payload[key] not in (None, ""):
                    return float(str(payload[key]).replace(",", ""))
        elif isinstance(payload, list):
            for row in payload:
                if str(row.get("code", "")).upper() == currency:
                    return float(str(row.get("rate", row.get("value", ""))).replace(",", ""))
    except Exception:
        pass

    # 2) XML хэлбэр (хуучин)
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.content)
        for val in root.findall(".//rate"):
            code_el, value_el = val.find("code"), val.find("value")
            if code_el is not None and value_el is not None and code_el.text == currency:
                return float(value_el.text.replace(",", ""))
    except Exception:
        pass
    return None


def resolve_rate(currency: str, d: date, session: Session | None = None,
                 company_id: str | None = None,
                 strict: bool = False) -> tuple[float, str]:
    """Ханш ба ТҮҮНИЙ ЭХ СУРВАЛЖИЙГ хамт буцаана: (rate, source).

    Дараалал: гараар оруулсан FxRate → Монголбанкны API → нөөц ханш.
    strict=True үед нөөц ханш руу уначихгүй, алдаа өгнө — бодит бүртгэлд
    баталгаажаагүй ханшаар журналын бичилт хийгдэхээс сэргийлнэ.
    """
    currency = (currency or "").upper().strip()
    if currency == "MNT":
        return 1.0, "mnt"

    # 1) Нягтлангийн гараар оруулсан албан ёсны ханш
    if session is not None and company_id:
        from .models import FxRate
        row = session.scalars(
            select(FxRate).where(FxRate.company_id == company_id,
                                 FxRate.currency == currency,
                                 FxRate.rate_date <= d)
            .order_by(FxRate.rate_date.desc()).limit(1)).first()
        if row is not None:
            return float(row.rate), "stored"

    # 2) Монголбанк
    try:
        api_rate = _rate_from_api(currency, d)
        if api_rate:
            return api_rate, "mongolbank"
    except Exception as e:
        logger.warning("Mongolbank API-аас ханш татахад алдаа: %s", e)

    # 3) Нөөц — зөвхөн зөвшөөрсөн үед
    if strict:
        raise FxRateError(
            f"{currency} валютын {d.isoformat()}-ний ханш олдсонгүй. "
            f"Монголбанкны ханш татагдсангүй тул ханшийг гараар оруулна уу "
            f"(баталгаажаагүй ханшаар бичилт хийхгүй).")
    rate = MOCK_RATES.get(currency)
    if rate is None:
        raise FxRateError(f"{currency} валютын ханш тодорхойгүй байна")
    logger.warning("%s ханш олдсонгүй — БАТАЛГААЖААГҮЙ нөөц ханш %s ашиглав",
                   currency, rate)
    return rate, "mock"


def fetch_rate(currency: str, d: date, session: Session | None = None,
               company_id: str | None = None, strict: bool = False) -> float:
    """Ханшийг л буцаана (эх сурвалж хэрэгтэй бол resolve_rate ашигла)."""
    return resolve_rate(currency, d, session, company_id, strict)[0]


def run_revaluation(session: Session, company_id: str, reval_date: date,
                    actor_id: str | None = None,
                    strict_rates: bool = False) -> JournalEntry | None:
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
                # trial_balance-тай ИЖИЛ шүүлт: буцаагдсан бичилт нь өөрийн
                # буцаалттайгаа цуцлагддаг тул хоёуланг нь авна. Зөвхөн
                # posted-ыг авбал эх бичилт унаад буцаалт нь үлдэж, дараагийн
                # үнэлгээ хиймэл олз/гарз бичдэг байв.
                JournalEntry.status != EntryStatus.draft
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
                # trial_balance-тай ИЖИЛ шүүлт: буцаагдсан бичилт нь өөрийн
                # буцаалттайгаа цуцлагддаг тул хоёуланг нь авна. Зөвхөн
                # posted-ыг авбал эх бичилт унаад буцаалт нь үлдэж, дараагийн
                # үнэлгээ хиймэл олз/гарз бичдэг байв.
                JournalEntry.status != EntryStatus.draft
            )
        )
        q_fx_cr = (
            select(func.coalesce(func.sum(JournalLine.amount_currency), 0))
            .join(JournalEntry)
            .where(
                JournalLine.account_id == acc.id,
                JournalLine.credit_minor > 0,
                JournalEntry.entry_date <= reval_date,
                # trial_balance-тай ИЖИЛ шүүлт: буцаагдсан бичилт нь өөрийн
                # буцаалттайгаа цуцлагддаг тул хоёуланг нь авна. Зөвхөн
                # posted-ыг авбал эх бичилт унаад буцаалт нь үлдэж, дараагийн
                # үнэлгээ хиймэл олз/гарз бичдэг байв.
                JournalEntry.status != EntryStatus.draft
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

        # Ханш ба эх сурвалжийг олох
        rate, src = resolve_rate(acc.currency, reval_date, session, company_id,
                                 strict=strict_rates)
        warn = " [БАТАЛГААЖААГҮЙ ХАНШ]" if src == "mock" else ""

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
                    description=f"Ханшийн дахин үнэлгээ олз /{acc.currency} {fx_balance:.2f} @ {rate}/{warn}"
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
                    description=f"Ханшийн дахин үнэлгээ гарз /{acc.currency} {fx_balance:.2f} @ {rate}/{warn}"
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
                    description=f"Ханшийн дахин үнэлгээ гарз /{acc.currency} {fx_balance:.2f} @ {rate}/{warn}"
                ))
            else:  # Ханшийн зөрүүний олз (Өр төлбөр буурсан)
                gain_val = abs(diff_minor)
                lines.append(ledger.LineInput(
                    account_code=acc.code,
                    debit_minor=gain_val,
                    description=f"Ханшийн дахин үнэлгээ олз /{acc.currency} {fx_balance:.2f} @ {rate}/{warn}"
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
