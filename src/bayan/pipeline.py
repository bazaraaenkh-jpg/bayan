"""Pipeline orchestrator: файл → таних → задлах → нормчлох → validation gate
→ хадгалах → (сонголтоор) ангилах. §4-ийн бүх шатыг холбоно.

Мөн approve_suggestions(): хүний баталсан саналыг журналын бичилт болгодог
цорын ганц зам (G5).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger, storage
from .amounts import parse_date
from .classify import classify_batch
from .extract_excel import extract_xlsx, scan_cells
from .models import (
    BankAccount, BankTxn, ClassificationSuggestion, Direction,
    ExtractionPath, SourceType, Statement, StatementStatus,
)
from .normalize import normalize
from .registry import load_registry, select_descriptor
from .validate import run_gate


class PipelineError(Exception):
    pass


@dataclass
class PipelineReport:
    statement_id: str | None = None
    descriptor_id: str | None = None
    txn_count: int = 0
    gate_ok: bool = False
    issues: list[dict] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)
    fingerprint_log: list[tuple[str, int]] = field(default_factory=list)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_period(meta: dict) -> tuple[datetime | None, datetime | None]:
    """metadata-гийн period ("YYYY.MM.DD - YYYY.MM.DD" маягийн) мужийг задлана."""
    period = meta.get("period")
    if not period:
        return None, None
    import re
    dates = re.findall(r"\d{4}[.\-/]\d{2}[.\-/]\d{2}", str(period))
    fmts = "%Y.%m.%d|%Y-%m-%d|%Y/%m/%d"
    if len(dates) == 2:
        f = parse_date(dates[0], fmts)
        t = parse_date(dates[1], fmts).replace(hour=23, minute=59, second=59)
        return f, t
    return None, None


def process_file(
    session: Session,
    company_id: str,
    path: Path,
    bank_account: BankAccount | None = None,
    classify: bool = False,
) -> PipelineReport:
    """Хуулгын файлыг бүрэн боловсруулна. Gate давбал л bank_txn хадгалагдана."""
    path = Path(path)
    report = PipelineReport()

    # --- Шат 0: файлын давхардал
    sha = _sha256(path)
    dup = session.scalar(select(Statement).where(
        Statement.company_id == company_id, Statement.file_sha256 == sha))
    if dup:
        if dup.status != StatementStatus.failed_validation:
            raise PipelineError("Энэ файл өмнө нь оруулсан байна (SHA-256 давхардал)")
        # Gate даваагүй хуулгад гүйлгээ хадгалагддаггүй тул зөвхөн толгой мөр
        # үлддэг. Задлагч/шалгуур сайжирсан үед дахин оролдох боломжтой байх
        # ёстой — хуучин уналтын бичлэгийг сольж дарж бичнэ.
        session.delete(dup)
        session.flush()

    # --- Шат 1: descriptor сонголт
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        cells = scan_cells(path)
    elif suffix == ".xls":
        from .extract_xls import scan_cells_xls
        cells = scan_cells_xls(path)
    elif suffix == ".pdf":
        from .extract_pdf import scan_pdf_text
        cells = scan_pdf_text(path)
        if not cells:
            raise PipelineError(
                "PDF-ээс текст олдсонгүй — скан байж магадгүй (Зам В vision, "
                "ANTHROPIC_API_KEY шаардлагатай)")
    else:
        raise PipelineError(f"Дэмжигдэхгүй өргөтгөл: {suffix}")
    desc, fp_log = select_descriptor(load_registry(), filename=path.name, cell_texts=cells)
    report.fingerprint_log = fp_log
    if desc is None:
        raise PipelineError(
            "Формат танигдсангүй — Registry-д descriptor нэмэх шаардлагатай. "
            f"Оноонууд: {fp_log[:3]}")
    report.descriptor_id = desc.id

    # --- Шат 2: задлалт (Зам А: Excel, Зам Б: текст PDF)
    if suffix == ".xls":
        from .extract_xls import extract_xls
        raw = extract_xls(path, desc)
    elif suffix == ".pdf":
        from .extract_pdf import extract_pdf
        raw = extract_pdf(path, desc)
    else:
        raw = extract_xlsx(path, desc)

    # --- Шат 3: нормчлол
    account_key = (bank_account.id if bank_account
                   else raw.metadata.get("account_no", "unknown"))
    txns = normalize(raw, desc, account_key=account_key)
    report.txn_count = len(txns)

    # --- Шат 4: validation gate
    period_from, period_to = _parse_period(raw.metadata)
    declared = raw.metadata.get("txn_count")
    prev_closing = None
    if bank_account is not None:
        prev_stmt = session.scalars(
            select(Statement)
            .where(Statement.bank_account_id == bank_account.id,
                   Statement.status.in_([StatementStatus.parsed_verified,
                                         StatementStatus.posted]))
            .order_by(Statement.period_to.desc())
        ).first()
        if prev_stmt and prev_stmt.closing_minor is not None:
            prev_closing = prev_stmt.closing_minor

    gate = run_gate(
        txns,
        opening_minor=raw.metadata.get("opening_balance_minor"),
        closing_minor=raw.metadata.get("closing_balance_minor"),
        declared_row_count=int(float(declared)) if declared else None,
        period_from=period_from, period_to=period_to,
        prev_closing_minor=prev_closing,
        expected_account=raw.metadata.get("account_no") or account_key,
    )
    report.gate_ok = gate.ok
    report.issues = gate.to_dict()["issues"]
    report.diagnosis = gate.diagnosis

    # --- хадгалалт
    stmt = Statement(
        company_id=company_id,
        bank_account_id=bank_account.id if bank_account else None,
        file_name=path.name, file_sha256=sha, descriptor_id=desc.id,
        period_from=period_from.date() if period_from else None,
        period_to=period_to.date() if period_to else None,
        opening_minor=raw.metadata.get("opening_balance_minor"),
        closing_minor=raw.metadata.get("closing_balance_minor"),
        status=(StatementStatus.parsed_verified if gate.ok
                else StatementStatus.failed_validation),
        validation_report=gate.to_dict(),
    )
    session.add(stmt)
    session.flush()
    report.statement_id = stmt.id

    # Файлыг үүлэн эсвэл локал санд байнгын хадгалалтад оруулна
    try:
        storage.save_file(company_id, f"statements/{stmt.id}{path.suffix}", path)
    except Exception as e:
        raise PipelineError(f"Файлыг хадгалахад алдаа гарлаа: {e}")

    if not gate.ok:
        return report   # gate даваагүй хуулгын гүйлгээ систем рүү орохгүй

    saved: list[BankTxn] = []
    for t in txns:
        saved.append(BankTxn(
            statement_id=stmt.id, company_id=company_id,
            bank_account_key=account_key, seq_no=t.seq_no,
            posted_at=t.posted_at, direction=Direction(t.direction),
            amount_minor=t.amount_minor,
            balance_after_minor=t.balance_after_minor,
            counterparty_account=t.counterparty_account,
            counterparty_name=t.counterparty_name,
            description_raw=t.description_raw,
            description_norm=t.description_norm,
            channel_ref=t.channel_ref, canonical_hash=t.canonical_hash,
            extraction_path=ExtractionPath.excel,
        ))
    session.add_all(saved)
    session.flush()

    # --- Шат 5: ангилалт (сонголтоор)
    if classify:
        classify_batch(session, company_id, saved)

    return report


def approve_suggestions(
    session: Session,
    company_id: str,
    suggestion_ids: list[str],
    bank_gl_code: str | dict[str, str],
    actor_id: str | None = None,
) -> list[str]:
    """Баталсан санал → журналын бичилт (G5-ийн ганц гарц).

    bank_gl_code: банкны дансны GL код (жишээ: 110101). Олон данстай бол
    {bank_account_key → GL код} dict өгнө.
    Кредит (орлого): Дт банк / Кт харьцах данс. Дебит (зарлага): эсрэгээр.
    """
    entry_ids = []
    for sid in suggestion_ids:
        sug = session.get(ClassificationSuggestion, sid)
        if sug is None or sug.status != "pending":
            continue
        txn = session.get(BankTxn, sug.bank_txn_id)
        gl = (bank_gl_code if isinstance(bank_gl_code, str)
              else bank_gl_code[txn.bank_account_key])
        if txn.direction == Direction.credit:
            lines = [
                ledger.LineInput(gl, debit_minor=txn.amount_minor,
                                 description=txn.description_norm),
                ledger.LineInput(sug.account_code, credit_minor=txn.amount_minor,
                                 description=txn.description_norm),
            ]
        else:
            lines = [
                ledger.LineInput(sug.account_code, debit_minor=txn.amount_minor,
                                 description=txn.description_norm),
                ledger.LineInput(gl, credit_minor=txn.amount_minor,
                                 description=txn.description_norm),
            ]
        entry = ledger.post_entry(
            session, company_id, txn.posted_at.date(), lines,
            source_type=SourceType.bank_txn, source_id=txn.id,
            memo=txn.description_norm, actor_id=actor_id,
        )
        sug.status = "approved"
        sug.journal_entry_id = entry.id
        entry_ids.append(entry.id)
    session.flush()
    return entry_ids
