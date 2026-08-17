"""anomalies.py — худал дохио өгөхгүй, бодит гажилтыг барина."""

from datetime import date

import pytest

from bayan import anomalies, ledger, partners, salary

M = 100


def _cash(session, company):
    ledger.post_entry(session, company.id, date(2026, 2, 1), [
        ledger.LineInput("1101", debit_minor=500_000_000 * M),
        ledger.LineInput("4101", credit_minor=500_000_000 * M)])


def _pay(session, company, d, amount, memo, cp_id=None):
    return ledger.post_entry(session, company.id, d, [
        ledger.LineInput("7103", debit_minor=amount),
        ledger.LineInput("1101", credit_minor=amount, counterparty_id=cp_id),
    ], memo=memo)


def _dups(session, company):
    return anomalies.duplicate_payments(session, company.id,
                                        date(2026, 3, 1), date(2026, 3, 31))


# ------------------------------------------------------------ худал дохио

def test_corrected_payment_is_not_a_duplicate(session, company):
    """Буцаагаад дахин бичсэн төлбөрийг давхардал гэж дуудахгүй."""
    _cash(session, company)
    e = _pay(session, company, date(2026, 3, 5), 2_000_000 * M, "Түрээс (алдаатай)")
    ledger.reverse_entry(session, e.id, reversal_date=date(2026, 3, 6))
    _pay(session, company, date(2026, 3, 6), 2_000_000 * M, "Түрээс (зассан)")

    assert _dups(session, company) == []


def test_same_amount_to_different_counterparties_is_not_a_duplicate(session, company):
    """Хоёр өөр түрээслүүлэгчид ижил дүн төлөх нь давхардал биш."""
    _cash(session, company)
    a = partners.Counterparty(company_id=company.id, name="Түрээслүүлэгч А")
    b = partners.Counterparty(company_id=company.id, name="Түрээслүүлэгч Б")
    session.add_all([a, b]); session.flush()
    _pay(session, company, date(2026, 3, 3), 1_500_000 * M, "Түрээс А", a.id)
    _pay(session, company, date(2026, 3, 5), 1_500_000 * M, "Түрээс Б", b.id)

    assert _dups(session, company) == []


# ------------------------------------------------------------ бодит гажилт

def test_real_duplicate_to_same_counterparty_is_caught(session, company):
    _cash(session, company)
    cp = partners.Counterparty(company_id=company.id, name="Нийлүүлэгч")
    session.add(cp); session.flush()
    _pay(session, company, date(2026, 3, 3), 4_000_000 * M, "P-77 төлбөр", cp.id)
    _pay(session, company, date(2026, 3, 4), 4_000_000 * M, "P-77 төлбөр", cp.id)

    found = _dups(session, company)
    assert len(found) == 1
    assert found[0].code == "DUPLICATE_PAYMENT" and found[0].severity == "high"
    assert found[0].amount_minor == 4_000_000 * M


def test_duplicate_without_counterparty_still_flagged(session, company):
    """Харилцагч нь мэдэгдэхгүй бол дүн/огнооны шинжээр сэжиглэсэн хэвээр."""
    _cash(session, company)
    _pay(session, company, date(2026, 3, 3), 900_000 * M, "Төлбөр")
    _pay(session, company, date(2026, 3, 4), 900_000 * M, "Төлбөр")
    assert len(_dups(session, company)) == 1


def test_duplicate_window_is_respected(session, company):
    _cash(session, company)
    cp = partners.Counterparty(company_id=company.id, name="Н")
    session.add(cp); session.flush()
    _pay(session, company, date(2026, 3, 1), 700_000 * M, "Төлбөр", cp.id)
    _pay(session, company, date(2026, 3, 20), 700_000 * M, "Төлбөр", cp.id)
    assert _dups(session, company) == []          # 7 хоногоос хол


# ------------------------------------------------------------ бусад илрүүлэгч

def test_vat_tolerance_accepts_ebarimt_rounding(session, company):
    """eBarimt-ын бутархай НӨАТ худал дохио өгөхгүй, бодит алдаа баригдана."""
    cp = partners.Counterparty(company_id=company.id, name="Х")
    session.add(cp); session.flush()
    # 145,000₮ баримт: цэвэр 131,818.18 / НӨАТ 13,181.82
    partners.post_invoice(session, company.id, cp.id,
                          partners.InvoiceKind.purchase, "EB-1",
                          date(2026, 3, 10), date(2026, 3, 31),
                          net_minor=13_181_818, vat_minor=1_318_182,
                          expense_account="7103")
    partners.post_invoice(session, company.id, cp.id,
                          partners.InvoiceKind.purchase, "BAD-1",
                          date(2026, 3, 11), date(2026, 3, 31),
                          net_minor=10_000_000 * M, vat_minor=2_000_000 * M,
                          expense_account="7103")

    found = anomalies.vat_anomalies(session, company.id,
                                    date(2026, 3, 1), date(2026, 3, 31))
    assert [a.refs[0] for a in found] == [
        next(i.id for i in session.query(partners.Invoice)
             if i.number == "BAD-1")]


def test_payroll_jump_and_duplicate(session, company):
    e = salary.Employee(company_id=company.id, code="E1", last_name="А",
                        first_name="А", base_salary_minor=2_000_000 * M)
    session.add(e); session.flush()
    for m, gross in ((1, 2_000_000 * M), (2, 2_000_000 * M), (3, 5_000_000 * M)):
        session.add(salary.PayrollLine(
            company_id=company.id, employee_id=e.id, year=2026, month=m,
            gross_minor=gross, ndsh_employee_minor=0, ndsh_employer_minor=0,
            hhoat_minor=0, net_minor=gross))
    session.flush()

    found = anomalies.payroll_anomalies(session, company.id, 2026, 3)
    assert [a.code for a in found] == ["PAYROLL_JUMP"]
    assert found[0].severity == "high"            # 2× босгоос давсан

    # Нэг сард хоёр мөр — давхар олголт
    session.add(salary.PayrollLine(
        company_id=company.id, employee_id=e.id, year=2026, month=3,
        gross_minor=5_000_000 * M, ndsh_employee_minor=0,
        ndsh_employer_minor=0, hhoat_minor=0, net_minor=5_000_000 * M))
    session.flush()
    codes = [a.code for a in anomalies.payroll_anomalies(session, company.id, 2026, 3)]
    assert "PAYROLL_DUPLICATE" in codes


def test_scan_aggregates_and_sorts_by_severity(session, company):
    _cash(session, company)
    cp = partners.Counterparty(company_id=company.id, name="Н")
    session.add(cp); session.flush()
    _pay(session, company, date(2026, 3, 3), 4_000_000 * M, "P-1", cp.id)
    _pay(session, company, date(2026, 3, 4), 4_000_000 * M, "P-1", cp.id)
    ledger.post_entry(session, company.id, date(2027, 6, 1), [
        ledger.LineInput("7103", debit_minor=100_000 * M),
        ledger.LineInput("1101", credit_minor=100_000 * M)], memo="ирээдүй")

    res = anomalies.scan(session, company.id, 2026, 3, as_of=date(2026, 8, 17))
    codes = [a["code"] for a in res["anomalies"]]
    assert codes[0] == "DUPLICATE_PAYMENT"        # high нь эхэнд
    assert "FUTURE_ENTRY" in codes
    assert res["by_severity"]["high"] == 1
    assert res["total"] == len(res["anomalies"])
