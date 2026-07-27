"""Дотоод шилжүүлэг — өөрийн данс хоорондын гүйлгээний илрүүлэлт ба
толин тусгалын dedupe (ХХБ-ны бодит өгөгдлөөр баталгаажсан логик, цөмд).

Дүрэм:
  * Дотоод = харьцсан данс нь өөрийн ӨӨР данс (ХХБ гадагш гүйлгээнд
    cp талбарт илгээгчийн өөрийнх нь данс бичигддэг тул cp==өөрөө биш!)
  * Дебит тал эх бичилт: Дт GL(хүлээн авагч)/Кт GL(илгээгч) — хоёр талыг
    нэг бичилтээр бүртгэнэ.
  * Кредит талын ижил (хос данс, дүн, өдөр) гүйлгээ = толин тусгал → suggestion
    нь 'mirror' статустай үүсч, батлагдахгүй (журналд орохгүй).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import BankTxn, ClassificationSuggestion, Direction


@dataclass
class InternalResult:
    internal: list[BankTxn]
    mirrors: list[BankTxn]
    external: list[BankTxn]


def detect(txns: list[BankTxn], own_gl: dict[str, str]) -> InternalResult:
    """own_gl: өөрийн дансны дугаар → GL код."""
    internal, external = [], []
    for t in txns:
        if (t.counterparty_account in own_gl
                and t.counterparty_account != t.bank_account_key):
            internal.append(t)
        else:
            external.append(t)

    debit_keys = {
        (frozenset({t.bank_account_key, t.counterparty_account}),
         t.amount_minor, t.posted_at.date())
        for t in internal if t.direction == Direction.debit
    }
    keep, mirrors = [], []
    for t in internal:
        if t.direction == Direction.credit:
            key = (frozenset({t.bank_account_key, t.counterparty_account}),
                   t.amount_minor, t.posted_at.date())
            if key in debit_keys:
                mirrors.append(t)
                continue
        keep.append(t)
    return InternalResult(internal=keep, mirrors=mirrors, external=external)


def suggest_internal(session: Session, company_id: str,
                     txns: list[BankTxn], own_gl: dict[str, str]) -> InternalResult:
    """Дотоод шилжүүлгүүдэд suggestion үүсгэнэ; толин тусгалыг 'mirror'-оор
    тэмдэглэнэ (Review UI-д харагдана, батлагдахгүй). external-ийг буцаана —
    цаашид classify_batch-д өгнө."""
    res = detect(txns, own_gl)
    for t in res.internal:
        session.add(ClassificationSuggestion(
            bank_txn_id=t.id, company_id=company_id,
            account_code=own_gl[t.counterparty_account],
            confidence=1.0, source="rule",
            rationale=f"Дотоод шилжүүлэг ↔ {t.counterparty_account}"))
    for t in res.mirrors:
        session.add(ClassificationSuggestion(
            bank_txn_id=t.id, company_id=company_id,
            account_code=own_gl[t.counterparty_account],
            confidence=1.0, source="rule", status="mirror",
            rationale="Толин тусгал — эсрэг талын дебитээр бүртгэгдсэн"))
    session.flush()
    return res
