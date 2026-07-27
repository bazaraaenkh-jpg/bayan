# -*- coding: utf-8 -*-
"""Пиннакл Экспертс — ХХБ-ны БОДИТ хуулгаар бүрэн мөчлөг:
parse → gate → дотоод шилжүүлэг илрүүлэх → дүрмийн ангилалт → журнал → тулгалт.

Ажиллуулах:  .venv\\Scripts\\python.exe scripts\\demo_tdb.py
"""
import io
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from bayan import ledger
from bayan.amounts import format_minor
from bayan.classify import bucket, classify_batch
from bayan.coa_seed import add_bank_gl_account, seed_company
from bayan.db import make_session
from bayan.models import (
    BankTxn, ClassificationSuggestion, ClassifierRule, Direction, Statement,
    StatementStatus,
)
from bayan.pipeline import approve_suggestions, process_file

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "tdb"

# Өөрийн данснууд (5 хуулгын данс)
OWN_ACCOUNTS = ["411123311", "411123312", "413090433", "413108777", "413108778"]

RULES = [
    # (түлхүүр үг, данс, тайлбар)
    ("шимтгэл",            "7106", "банкны шимтгэл"),
    ("данс хөтөлсний",     "7106", "банкны шимтгэл"),
    ("salary",             "3102", "цалин олголт"),
    ("цалин",              "3102", "цалин олголт"),
    ("нийгмийн даатгал",   "3103", "НДШ"),
    ("niigmiin daatgal",   "3103", "НДШ"),
    ("нөхөн төлбөр",       "5101", "даатгалын нөхөн төлбөрийн орлого"),
    ("хохирол үнэлгээ",    "5101", "үнэлгээний үйлчилгээний орлого"),
]


def main() -> None:
    session = make_session("sqlite:///:memory:")
    company = seed_company(session, "Пиннакл Экспертс ХХК")

    gl_by_acct: dict[str, str] = {}
    for acct in OWN_ACCOUNTS:
        gl = add_bank_gl_account(session, company.id, "tdb", acct)
        gl_by_acct[acct] = gl.code

    for kw, code, _ in RULES:
        session.add(ClassifierRule(company_id=company.id, keyword=kw,
                                   account_code=code, priority=10))
    session.flush()

    print("=" * 78)
    print("ПИННАКЛ ЭКСПЕРТС — ХХБ-ны бодит хуулгын бүрэн боловсруулалт")
    print("=" * 78)

    # ---- 1. Parse + gate
    print("\n[1] PARSE + VALIDATION GATE")
    for f in sorted(SAMPLES.glob("*.XLS")):
        r = process_file(session, company.id, f)
        mark = "✓" if r.gate_ok else "✗ (дутуу export — журналд орохгүй)"
        print(f"    {f.name}: {r.txn_count} гүйлгээ {mark}")

    txns = session.query(BankTxn).order_by(BankTxn.posted_at).all()
    print(f"    Баталгаажсан гүйлгээ: {len(txns)}")

    # ---- 2. Дотоод шилжүүлэг: харьцсан данс нь өөрийн данс бол
    #      GL нь тухайн дансны 1101xx; толин тусгалын давхардлыг хасна.
    print("\n[2] ДОТООД ШИЛЖҮҮЛГИЙН ИЛРҮҮЛЭЛТ (Үе 2-т цөм рүү орох логик)")
    internal, external = [], []
    for t in txns:
        # ХХБ гадагш гүйлгээнд cp талбарт ИЛГЭЭГЧИЙН өөрийн дансыг бичдэг
        # (бодит өгөгдлөөр илэрсэн) — жинхэнэ дотоод шилжүүлэг гэдэг нь
        # cp өөрийн ӨӨР данс байх тохиолдол.
        if (t.counterparty_account in gl_by_acct
                and t.counterparty_account != t.bank_account_key):
            internal.append(t)
        else:
            external.append(t)

    # Толин тусгал: дебит тал нь эх бичилт; кредит талын ижил (хос данс, дүн,
    # өдөр) гүйлгээ = давхардал → алгасна
    debit_keys = set()
    for t in internal:
        if t.direction == Direction.debit:
            key = (frozenset({t.bank_account_key, t.counterparty_account}),
                   t.amount_minor, t.posted_at.date())
            debit_keys.add(key)

    posted_internal, skipped_mirror = [], []
    for t in internal:
        if t.direction == Direction.credit:
            key = (frozenset({t.bank_account_key, t.counterparty_account}),
                   t.amount_minor, t.posted_at.date())
            if key in debit_keys:
                skipped_mirror.append(t)
                continue
        posted_internal.append(t)

    for t in posted_internal:
        session.add(ClassificationSuggestion(
            bank_txn_id=t.id, company_id=company.id,
            account_code=gl_by_acct[t.counterparty_account],
            confidence=1.0, source="rule",
            rationale=f"Дотоод шилжүүлэг ↔ {t.counterparty_account}"))
    session.flush()
    print(f"    Дотоод шилжүүлэг: {len(internal)} гүйлгээ — "
          f"{len(posted_internal)} бүртгэнэ, {len(skipped_mirror)} нь толин тусгал (алгассан)")

    # ---- 3. Бусад гүйлгээний ангилалт (дүрэм; AI key-тэй бол Haiku→Fable)
    print("\n[3] АНГИЛАЛТ (дүрмийн давхарга)")
    classify_batch(session, company.id, external, use_ai=False)
    sugs = session.query(ClassificationSuggestion).all()
    buckets = defaultdict(list)
    for s in sugs:
        buckets[bucket(float(s.confidence))].append(s)
    print(f"    АВТО сагс (итгэлцэл ≥0.95): {len(buckets['auto']):>4} гүйлгээ")
    print(f"    ШАЛГАХ сагс (0.70-0.95):   {len(buckets['review']):>4} гүйлгээ")
    print(f"    ГАРААР сагс (<0.70):       {len(buckets['manual']):>4} гүйлгээ"
          f"  ← ANTHROPIC_API_KEY өгвөл Fable 5 ангилна")

    by_code = defaultdict(lambda: [0, 0])
    txn_by_id = {t.id: t for t in txns}
    for s in buckets["auto"]:
        t = txn_by_id[s.bank_txn_id]
        by_code[s.account_code][0] += 1
        by_code[s.account_code][1] += t.amount_minor
    print("\n    АВТО сагсны задаргаа:")
    for code, (n, amt) in sorted(by_code.items()):
        print(f"      {code}: {n:>4} гүйлгээ  {format_minor(amt):>16}₮")

    # ---- 4. Батлах → журнал (demo-д авто сагсыг бөөнөөр батална)
    print("\n[4] БАТЛАХ → ЖУРНАЛ")
    auto_ids = [s.id for s in buckets["auto"]]
    entries = approve_suggestions(session, company.id, auto_ids, gl_by_acct)
    print(f"    Журналын бичилт: {len(entries)}")
    print(f"    (Гараар сагсны {len(buckets['manual'])} гүйлгээ Review UI-д хүлээгдэнэ)")

    # ---- 5. Тулгалт: банкны GL data == хуулгын хаалт − нээлт
    print("\n[5] ТУЛГАЛТ — банкны GL үлдэгдэл vs хуулгын цэвэр өөрчлөлт")
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    stmts = session.query(Statement).filter(
        Statement.status == StatementStatus.parsed_verified).all()
    all_ok = True
    for st in stmts:
        import re
        acct = st.file_name.split("_")[1]
        gl_code = gl_by_acct[acct]
        gl_bal = tb.get(gl_code, {"balance_minor": 0})["balance_minor"]
        stmt_net = (st.closing_minor or 0) - (st.opening_minor or 0)
        # Гараар сагсны батлагдаагүй гүйлгээний дүнг нөхөж тооцно
        pending = session.query(BankTxn).join(
            ClassificationSuggestion,
            ClassificationSuggestion.bank_txn_id == BankTxn.id
        ).filter(BankTxn.bank_account_key == acct,
                 ClassificationSuggestion.status == "pending").all()
        pending_net = sum(t.amount_minor if t.direction == Direction.credit
                          else -t.amount_minor for t in pending)
        # Толин тусгалаар алгассан кредит нөхөлт шаардахгүй: дебит талын
        # бичилт GL-ийн хоёр талыг нэг дор бүртгэсэн.
        reconciled = gl_bal + pending_net
        ok = reconciled == stmt_net
        all_ok &= ok
        print(f"    {acct}: GL {format_minor(gl_bal):>15}₮ + хүлээгдэж буй "
              f"{format_minor(pending_net):>14}₮ = {format_minor(reconciled):>15}₮ "
              f"| хуулга {format_minor(stmt_net):>15}₮ {'✓' if ok else '✗'}")

    td = sum(r["debit_minor"] for r in tb.values())
    tc = sum(r["credit_minor"] for r in tb.values())
    print(f"\n    Журналын Σдебит = {format_minor(td)}₮, Σкредит = {format_minor(tc)}₮ "
          f"{'✓' if td == tc else '✗'}")
    print("\n" + "=" * 78)
    print("БОДИТ ӨГӨГДЛИЙН БҮРЭН МӨЧЛӨГ " +
          ("✓ АМЖИЛТТАЙ" if all_ok and td == tc else "— тулгалтыг шалгана уу"))
    print("=" * 78)


if __name__ == "__main__":
    main()
