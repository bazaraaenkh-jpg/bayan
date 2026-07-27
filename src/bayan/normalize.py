"""Шат 3 — Нормчлол: RawStatement → каноник гүйлгээнүүд (§3, §4.3)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from .amounts import parse_amount, parse_date
from .extract_excel import RawStatement
from .registry import Descriptor


class NormalizeError(Exception):
    def __init__(self, row_index: int, message: str):
        self.row_index = row_index
        super().__init__(f"Мөр {row_index}: {message}")


@dataclass
class CanonTxn:
    seq_no: int
    row_index: int
    posted_at: datetime
    direction: str                    # debit | credit
    amount_minor: int
    balance_after_minor: int | None
    counterparty_account: str | None
    description_raw: str
    description_norm: str
    channel_ref: str | None
    canonical_hash: str
    counterparty_name: str | None = None


_ACCOUNT_RE = re.compile(r"\b(\d{9,16})\b")          # утга доторх дансны дугаар
_REF_RE = re.compile(r"\b([A-Z0-9]{10,20})\b")


def normalize_description(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    return text


def _hash(account_key: str, posted_at: datetime, direction: str,
          amount_minor: int, cp_account: str | None, desc_norm: str,
          repeat_idx: int) -> str:
    base = "\x1f".join([
        account_key,
        posted_at.strftime("%Y-%m-%d %H:%M"),
        direction,
        str(amount_minor),
        cp_account or "",
        desc_norm.lower(),
    ])
    if repeat_idx > 1:
        base += f"\x1f{repeat_idx}"   # жинхэнэ давхар гүйлгээний ялгагч (§3)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def normalize(stmt: RawStatement, desc: Descriptor,
              account_key: str) -> list[CanonTxn]:
    locale = desc.amount_locale
    date_fmt = desc.columns["posted_at"].format or "%Y.%m.%d"
    mode = desc.direction_mode
    seen: dict[str, int] = {}
    txns: list[CanonTxn] = []

    for i, row in enumerate(stmt.rows, start=1):
        v = row.values
        try:
            posted_at = parse_date(v.get("posted_at"), date_fmt)
        except Exception as e:
            raise NormalizeError(row.row_index, str(e))

        thousands = locale.get("thousands", ",")
        decimal = locale.get("decimal", ".")

        if mode == "separate":
            debit = parse_amount(v.get("debit"), thousands, decimal)
            credit = parse_amount(v.get("credit"), thousands, decimal)
            if debit and credit:
                raise NormalizeError(row.row_index, "Дебит, кредит хоёулаа утгатай")
            if not debit and not credit:
                raise NormalizeError(row.row_index, "Дүнгүй мөр")
            direction = "debit" if debit else "credit"
            amount = debit or credit
        else:  # signed — нэг баганад +/- (F5)
            signed = parse_amount(v.get("amount"), thousands, decimal)
            if signed is None or signed == 0:
                raise NormalizeError(row.row_index, "Дүнгүй мөр")
            direction = "credit" if signed > 0 else "debit"
            amount = abs(signed)

        balance = parse_amount(v.get("balance_after"), thousands, decimal)

        raw_desc = str(v.get("description") or "")
        desc_norm = normalize_description(raw_desc)

        cp_account = v.get("counterparty_account")
        cp_name = None
        if cp_account:
            # «415020440 БАТХИШИГ НОРОВ» маягийн холимог нүднээс данс/нэрийг салгана
            raw_cp = str(cp_account).strip()
            m = re.match(r"^(\d{6,16})\s*(.*)$", raw_cp)
            if m:
                cp_account, cp_name = m.group(1), (m.group(2).strip() or None)
            elif re.fullmatch(r"\d{6,16}", raw_cp):
                cp_account = raw_cp
            else:
                cp_account, cp_name = None, (raw_cp or None)
        if not cp_account:
            m = _ACCOUNT_RE.search(desc_norm)
            cp_account = m.group(1) if m else None

        ref = v.get("channel_ref")
        ref = str(ref).strip() if ref else None

        key = f"{posted_at:%Y%m%d%H%M}|{direction}|{amount}|{cp_account}|{desc_norm.lower()}"
        seen[key] = seen.get(key, 0) + 1

        txns.append(CanonTxn(
            seq_no=i, row_index=row.row_index, posted_at=posted_at,
            direction=direction, amount_minor=amount,
            balance_after_minor=balance,
            counterparty_account=cp_account,
            description_raw=raw_desc, description_norm=desc_norm,
            channel_ref=ref,
            canonical_hash=_hash(account_key, posted_at, direction, amount,
                                 cp_account, desc_norm, seen[key]),
            counterparty_name=cp_name,
        ))
    return txns
