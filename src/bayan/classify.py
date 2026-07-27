"""Шат 5 — Ангилалт: дүрэм эхэлж (нябо-гийн дүрэм AI-аас дээгүүр),
дараа нь Claude (Haiku 4.5 → итгэлцэл багатайг Fable 5 руу эскалаци).

AI-ийн гаралт ЗӨВХӨН ClassificationSuggestion болно — журналд шууд орохгүй (G5).
ANTHROPIC_API_KEY байхгүй орчинд дүрмийн давхарга ганцаараа ажиллана.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, ClassifierRule, ClassificationSuggestion, BankTxn, Direction

HAIKU = "claude-haiku-4-5-20251001"
FABLE = "claude-fable-5"
BATCH_SIZE = 50
ESCALATION_THRESHOLD = 0.9   # Haiku-гийн итгэлцэл үүнээс бага бол Fable 5 руу

# Итгэлцлийн 3 сагс (§4.5)
BUCKET_AUTO = 0.95
BUCKET_REVIEW = 0.70


@dataclass
class Suggestion:
    account_code: str
    vat_flag: bool
    confidence: float
    rationale: str
    source: str  # rule | haiku | fable


def bucket(confidence: float) -> str:
    if confidence >= BUCKET_AUTO:
        return "auto"
    if confidence >= BUCKET_REVIEW:
        return "review"
    return "manual"


# ------------------------------------------------------------- и-баримт тулгах давхарга

def apply_ebarimt_match(session: Session, company_id: str, txn: BankTxn) -> Suggestion | None:
    from .partners import Invoice, InvoiceKind
    from .models import Direction
    
    # Гүйлгээний чиглэлээс хамаарч и-баримтын төрлийг сонгоно
    inv_kind = InvoiceKind.purchase if txn.direction == Direction.debit else InvoiceKind.sales
    
    # Нийт дүн таарч буй, гүйцэд төлөгдөөгүй и-баримт хайна
    invoice = session.scalar(
        select(Invoice)
        .where(
            Invoice.company_id == company_id,
            Invoice.kind == inv_kind,
            (Invoice.net_minor + Invoice.vat_minor) == txn.amount_minor,
            Invoice.paid_minor < (Invoice.net_minor + Invoice.vat_minor)
        )
        .order_by(Invoice.issue_date.desc())
    )
    
    if invoice:
        # Худалдан авалтын өглөг (2101) эсвэл Авлагын данс (1201)
        acc_code = "2101" if inv_kind == InvoiceKind.purchase else "1201"
        vat_flag = invoice.vat_minor > 0
        return Suggestion(
            account_code=acc_code,
            vat_flag=vat_flag,
            confidence=0.98,
            rationale=f"И-Баримтын дүнтэй таарлаа: №{invoice.number}",
            source="ebarimt_match"
        )
    return None


# ------------------------------------------------------------- дүрмийн давхарга

def apply_rules(session: Session, company_id: str, txn: BankTxn) -> Suggestion | None:
    rules = session.scalars(
        select(ClassifierRule)
        .where(ClassifierRule.company_id == company_id, ClassifierRule.active == True)  # noqa: E712
        .order_by(ClassifierRule.priority)
    ).all()
    text = txn.description_norm.lower()
    for r in rules:
        if r.direction is not None and r.direction != txn.direction:
            continue
        if r.keyword.lower() in text:
            return Suggestion(
                account_code=r.account_code, vat_flag=r.vat_flag,
                confidence=1.0, rationale=f"Дүрэм: '{r.keyword}'", source="rule",
            )
    return None


# ------------------------------------------------------------- Claude давхарга

_SYSTEM = """Чи Монголын нягтлан бодох бүртгэлийн туслах. Банкны гүйлгээ бүрд
дансны төлөвлөгөөнөөс хамгийн тохирох харьцах дансыг сонго.
Гүйлгээ бүрд: account_code (заавал жагсаалтаас), vat_flag (НӨАТ-тай худалдан
авалт/борлуулалт мөн эсэх), confidence (0-1, эргэлзэж байвал бага өг),
rationale (нэг өгүүлбэр, монголоор)."""


def _tool_schema(codes: list[str]) -> dict:
    return {
        "name": "classify_transactions",
        "description": "Гүйлгээнүүдийн ангилалтын үр дүн",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "seq": {"type": "integer"},
                            "account_code": {"type": "string", "enum": codes},
                            "vat_flag": {"type": "boolean"},
                            "confidence": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["seq", "account_code", "confidence", "rationale"],
                    },
                }
            },
            "required": ["results"],
        },
    }


def _call_claude(model: str, coa_text: str, txn_lines: list[str],
                 codes: list[str]) -> list[dict]:
    import anthropic  # optional dependency
    client = anthropic.Anthropic()
    tool = _tool_schema(codes)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": _SYSTEM + "\n\nДансны төлөвлөгөө:\n" + coa_text,
            "cache_control": {"type": "ephemeral"},   # prompt caching (§6)
        }],
        tools=[tool],
        tool_choice={"type": "tool", "name": "classify_transactions"},
        messages=[{"role": "user", "content": "\n".join(txn_lines)}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input["results"]
    return []


def classify_batch(session: Session, company_id: str,
                   txns: list[BankTxn], use_ai: bool | None = None) -> list[ClassificationSuggestion]:
    """Гүйлгээнүүдийг ангилж suggestion болгож хадгална (журналд бичихгүй!)."""
    if use_ai is None:
        use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))

    accounts = session.scalars(
        select(Account).where(Account.company_id == company_id,
                              Account.is_postable == True)  # noqa: E712
    ).all()
    codes = [a.code for a in accounts]
    coa_text = "\n".join(f"{a.code} — {a.name}" for a in accounts)

    suggestions: list[ClassificationSuggestion] = []
    pending_ai: list[BankTxn] = []

    for txn in txns:
        s = apply_ebarimt_match(session, company_id, txn)
        if not s:
            s = apply_rules(session, company_id, txn)
            
        if s:
            suggestions.append(_persist(session, txn, s))
        elif use_ai:
            pending_ai.append(txn)
        else:
            default_code = "5101" if txn.direction == Direction.credit else "7199"
            default_rationale = "AI идэвхгүй — орлогоор ангилна" if txn.direction == Direction.credit else "AI идэвхгүй — гараар ангилна"
            suggestions.append(_persist(session, txn, Suggestion(
                account_code=default_code, vat_flag=False, confidence=0.0,
                rationale=default_rationale, source="rule",
            )))

    # --- Claude batch (Haiku → Fable эскалаци)
    for i in range(0, len(pending_ai), BATCH_SIZE):
        chunk = pending_ai[i:i + BATCH_SIZE]
        lines = [
            f"[{j}] {t.posted_at:%Y-%m-%d} | {t.direction.value} | "
            f"{t.amount_minor / 100:,.2f}₮ | {t.description_norm}"
            for j, t in enumerate(chunk)
        ]
        results = {r["seq"]: r for r in _call_claude(HAIKU, coa_text, lines, codes)}

        escalate = [j for j, r in results.items()
                    if r["confidence"] < ESCALATION_THRESHOLD]
        if escalate:
            esc_lines = [lines[j] for j in escalate]
            for r in _call_claude(FABLE, coa_text, esc_lines, codes):
                # esc_lines доторх индексийг эхийнхэд буцааж буулгана
                results[escalate[r["seq"]]] = {**r, "_model": "fable"}

        for j, t in enumerate(chunk):
            r = results.get(j)
            if r is None:
                s = Suggestion("7199", False, 0.0, "AI хариу ирсэнгүй", "haiku")
            else:
                s = Suggestion(
                    account_code=r["account_code"],
                    vat_flag=r.get("vat_flag", False),
                    confidence=min(max(float(r["confidence"]), 0.0), 1.0),
                    rationale=r["rationale"],
                    source="fable" if r.get("_model") == "fable" else "haiku",
                )
            suggestions.append(_persist(session, t, s))

    session.flush()
    return suggestions


def _persist(session: Session, txn: BankTxn, s: Suggestion) -> ClassificationSuggestion:
    row = ClassificationSuggestion(
        bank_txn_id=txn.id, company_id=txn.company_id,
        account_code=s.account_code, vat_flag=s.vat_flag,
        confidence=s.confidence, rationale=s.rationale, source=s.source,
    )
    session.add(row)
    return row
