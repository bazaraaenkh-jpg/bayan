"""AI туслах — метрикийн каталог, чиглүүлэлт, эрх, аудит (§5.3 L3).

Гол шалгалт: AI тоо зохиохгүй, каталогоос гадуур таамаглахгүй, эрх хүрэхгүй
хэрэглэгчид цалингийн дүн ХЭЗЭЭ Ч буцахгүй.
"""

from datetime import date

import pytest
from sqlalchemy import select

from bayan import assistant, metrics, reports
from bayan.ledger import LineInput, post_entry
from bayan.models import SourceType
from bayan.salary import Employee, PayrollLine

TODAY = date(2026, 8, 6)


# ------------------------------------------------------------------ өгөгдөл

@pytest.fixture
def books(session, company):
    """Бодит бичилттэй дэвтэр: 7-р сард борлуулалт ба зардал."""
    post_entry(session, company.id, date(2026, 7, 10), [
        LineInput("1101", debit_minor=12_000_000_00, description="Борлуулалт"),
        LineInput("5101", credit_minor=12_000_000_00, description="Борлуулалт"),
    ], source_type=SourceType.manual, memo="Борлуулалт")
    post_entry(session, company.id, date(2026, 7, 15), [
        LineInput("7103", debit_minor=3_000_000_00, description="Түрээс"),
        LineInput("1101", credit_minor=3_000_000_00, description="Түрээс"),
    ], source_type=SourceType.manual, memo="Түрээс")
    post_entry(session, company.id, date(2026, 7, 20), [
        LineInput("7104", debit_minor=1_000_000_00, description="Шатахуун"),
        LineInput("1101", credit_minor=1_000_000_00, description="Шатахуун"),
    ], source_type=SourceType.manual, memo="Шатахуун")
    session.flush()
    return company


# ------------------------------------------------------------------ хугацаа

@pytest.mark.parametrize("text, expect_from, expect_to", [
    ("2026 оны 7-р сарын борлуулалт хэд вэ", date(2026, 7, 1), date(2026, 7, 31)),
    ("2026-07 борлуулалт", date(2026, 7, 1), date(2026, 7, 31)),
    ("2025 оны борлуулалт", date(2025, 1, 1), date(2025, 12, 31)),
    ("2-р улирлын зардал", date(2026, 4, 1), date(2026, 6, 30)),
    ("өнгөрсөн сарын зардал", date(2026, 7, 1), date(2026, 7, 31)),
    ("2-р сарын зардал", date(2026, 2, 1), date(2026, 2, 28)),
])
def test_parse_period(text, expect_from, expect_to):
    p = metrics.parse_period(text, TODAY)
    assert (p.date_from, p.date_to) == (expect_from, expect_to)


def test_period_defaults_to_current_month():
    p = metrics.parse_period("борлуулалт хэд вэ", TODAY)
    assert (p.date_from, p.date_to) == (date(2026, 8, 1), date(2026, 8, 31))


def test_period_this_year_is_year_to_date():
    p = metrics.parse_period("энэ жилийн борлуулалт", TODAY)
    assert p.date_from == date(2026, 1, 1) and p.date_to == TODAY


def test_period_last_year_rolls_back_over_january():
    p = metrics.parse_period("өнгөрсөн сар", date(2026, 1, 15))
    assert (p.date_from, p.date_to) == (date(2025, 12, 1), date(2025, 12, 31))


def test_period_months_enumerates_range():
    p = metrics.parse_period("2-р улирал", TODAY)
    assert p.months() == [(2026, 4), (2026, 5), (2026, 6)]


# --------------------------------------------------------------- чиглүүлэлт

@pytest.mark.parametrize("question, expected", [
    ("энэ сарын борлуулалт хэд вэ", "revenue"),
    ("зардал хэд гарсан бэ", "expenses"),
    ("цэвэр ашиг", "net_income"),
    ("мөнгө хэд байна", "cash"),
    ("авлага хэд вэ", "receivables"),
    ("нөат хэд төлөх вэ", "vat"),
    ("хамгийн их зардал юу вэ", "top_expenses"),
    ("батлагдаагүй ангилал хэд байна", "unclassified"),
])
def test_rule_router_picks_metric(question, expected):
    assert assistant.route_by_rules(question) == expected


def test_longest_keyword_wins_over_shorter_one():
    """«татварын өглөг» нь «өглөг»-ийг агуулах тул урт нь ялах ёстой."""
    assert assistant.route_by_rules("татварын өглөг хэд вэ") == "tax_payable"
    assert assistant.route_by_rules("өглөг хэд вэ") == "payables"


def test_router_returns_none_for_unrelated_question():
    assert assistant.route_by_rules("маргааш бороо орох уу") is None


# ------------------------------------------------- тоо тайлантай таарах эсэх

def test_revenue_metric_matches_income_statement(session, books):
    p = metrics.parse_period("2026 оны 7-р сар", TODAY)
    result = metrics.compute(session, books.id, "revenue", p)
    inc = reports.income_statement(session, books.id, p.date_from, p.date_to)

    assert result.value_minor == inc["revenue_minor"] == 12_000_000_00
    assert result.accounts == ["5101"]


def test_expenses_metric_matches_income_statement(session, books):
    p = metrics.parse_period("2026 оны 7-р сар", TODAY)
    result = metrics.compute(session, books.id, "expenses", p)
    inc = reports.income_statement(session, books.id, p.date_from, p.date_to)

    assert result.value_minor == inc["expenses_minor"] == 4_000_000_00


def test_cash_metric_matches_trial_balance(session, books):
    from bayan.ledger import trial_balance

    p = metrics.parse_period("мөнгө", TODAY)
    result = metrics.compute(session, books.id, "cash", p)

    tb = {r["code"]: r for r in trial_balance(session, books.id, None, p.date_to)}
    expected = tb["1101"]["debit_minor"] - tb["1101"]["credit_minor"]
    assert result.value_minor == expected == 8_000_000_00


def test_top_expenses_is_sorted_descending(session, books):
    p = metrics.parse_period("2026 оны 7-р сар", TODAY)
    result = metrics.compute(session, books.id, "top_expenses", p)

    amounts = [r["amount_minor"] for r in result.rows]
    assert amounts == sorted(amounts, reverse=True)
    assert result.rows[0]["code"] == "7103"


def test_metric_carries_drilldown_accounts_and_source(session, books):
    p = metrics.parse_period("2026 оны 7-р сар", TODAY)
    result = metrics.compute(session, books.id, "expenses", p)

    assert set(result.accounts) == {"7103", "7104"}
    assert "СТ-2" in result.source


def test_stock_metric_ignores_period_start(session, books):
    """Үлдэгдэл нь эхнээс хуримтлагдана — асуултын эхлэх огноо нөлөөлөхгүй."""
    july = metrics.compute(session, books.id, "cash",
                           metrics.parse_period("2026 оны 7-р сар", TODAY))
    august = metrics.compute(session, books.id, "cash",
                             metrics.parse_period("2026 оны 8-р сар", TODAY))
    assert july.value_minor == august.value_minor == 8_000_000_00


# ------------------------------------------------------------------ хариулт

def test_answer_states_the_number_from_the_ledger(session, books):
    out = assistant.ask(session, books.id, "owner",
                        "2026 оны 7-р сарын борлуулалт хэд вэ",
                        use_ai=False, today=TODAY)

    assert out["status"] == "answered"
    assert out["metric"] == "revenue"
    assert out["data"]["value_minor"] == 12_000_000_00
    assert "12,000,000₮" in out["answer"]
    assert out["period"] == "2026 оны 7-р сар"


def test_largest_part_is_actually_the_largest_row(session, books):
    """Задаргаа кодоор эрэмбэлэгддэг — «хамгийн том» гэдэг нь эхний мөр биш."""
    out = assistant.ask(session, books.id, "owner", "2026 оны 7-р сарын зардал",
                        use_ai=False, today=TODAY)

    rows = out["data"]["rows"]
    assert [r["code"] for r in rows] == ["7103", "7104"]      # кодын дараалал
    biggest = max(rows, key=lambda r: r["amount_minor"])
    assert biggest["code"] == "7103"
    assert f"Хамгийн том хэсэг нь {biggest['name']}" in out["answer"]


def test_no_largest_part_claim_when_there_is_no_breakdown(session, books):
    out = assistant.ask(session, books.id, "owner", "2026 оны 7-р сарын цэвэр ашиг",
                        use_ai=False, today=TODAY)
    assert "Хамгийн том хэсэг" not in out["answer"]


def test_answer_includes_source_so_the_number_can_be_traced(session, books):
    out = assistant.ask(session, books.id, "owner", "2026 оны 7-р сарын зардал",
                        use_ai=False, today=TODAY)
    assert "Эх сурвалж" in out["answer"]
    assert out["data"]["accounts"] == ["7103", "7104"]


def test_unknown_question_refuses_instead_of_guessing(session, company):
    out = assistant.ask(session, company.id, "owner", "маргааш бороо орох уу",
                        use_ai=False, today=TODAY)

    assert out["status"] == "unknown"
    assert out["metric"] is None
    assert "data" not in out
    assert "мэдэхгүй" in out["answer"]


def test_unknown_answer_lists_what_it_can_do(session, company):
    out = assistant.ask(session, company.id, "cashier", "юу ч биш",
                        use_ai=False, today=TODAY)
    assert "борлуулалтын орлого" in out["answer"].lower()
    # няравт цалингийн метрик санал болгохгүй
    assert "цалингийн зардал" not in out["answer"].lower()


# ---------------------------------------------------------- эрх (red team)

@pytest.fixture
def with_payroll(session, books):
    """Цалин бодогдож, журналд ч бичигдсэн дэвтэр."""
    emp = Employee(company_id=books.id, code="E01", last_name="Дорж",
                   first_name="Сүх", position=None, base_salary_minor=2_000_000_00)
    session.add(emp)
    session.flush()
    session.add(PayrollLine(
        company_id=books.id, employee_id=emp.id, year=2026, month=7,
        gross_minor=2_000_000_00, ndsh_employee_minor=230_000_00,
        ndsh_employer_minor=250_000_00, hhoat_minor=157_000_00,
        net_minor=1_613_000_00))
    post_entry(session, books.id, date(2026, 7, 25), [
        LineInput("7101", debit_minor=2_000_000_00, description="Цалин"),
        LineInput("7102", debit_minor=250_000_00, description="АО НДШ"),
        LineInput("3102", credit_minor=1_613_000_00, description="Гарт олгох"),
        LineInput("3103", credit_minor=480_000_00, description="НДШ өглөг"),
        LineInput("3104", credit_minor=157_000_00, description="ХХОАТ өглөг"),
    ], source_type=SourceType.salary, memo="Цалин 2026-07")
    session.flush()
    return books


SALARY_PROBES = [
    "ажилтнуудын цалин хэд вэ",
    "7-р сарын цалингийн зардал",
    "Дорж хэдэн төгрөгийн цалин авдаг вэ",
    "payroll",
    "хөлс хэд вэ",
]


@pytest.mark.parametrize("role", ["cashier", "warehouse_manager", "viewer", "approver"])
@pytest.mark.parametrize("question", SALARY_PROBES)
def test_salary_is_never_returned_to_unauthorised_roles(session, with_payroll,
                                                        role, question):
    out = assistant.ask(session, with_payroll.id, role, question,
                        use_ai=False, today=TODAY)

    assert out["status"] in ("denied", "unknown")
    assert "data" not in out
    assert "2,000,000" not in out["answer"]
    assert "1,613,000" not in out["answer"]


@pytest.mark.parametrize("role", ["owner", "chief_accountant", "accountant", "auditor"])
def test_salary_is_returned_to_authorised_roles(session, with_payroll, role):
    out = assistant.ask(session, with_payroll.id, role,
                        "2026 оны 7-р сарын цалин", use_ai=False, today=TODAY)

    assert out["status"] == "answered"
    assert out["data"]["value_minor"] == 2_000_000_00


def test_denied_answer_names_no_figure_at_all(session, with_payroll):
    out = assistant.ask(session, with_payroll.id, "cashier", "цалин хэд вэ",
                        use_ai=False, today=TODAY)
    assert out["status"] == "denied"
    assert not any(ch.isdigit() for ch in out["answer"])


def test_expense_breakdown_hides_the_salary_account_from_cashier(session, with_payroll):
    """Нийт зардлын дүн үлдэнэ, гэхдээ «7101 Цалин — X₮» гэсэн МӨР нуугдана."""
    out = assistant.ask(session, with_payroll.id, "cashier",
                        "2026 оны 7-р сарын хамгийн их зардал",
                        use_ai=False, today=TODAY)

    assert out["status"] == "answered"
    codes = [r["code"] for r in out["data"]["rows"]]
    assert "7101" not in codes and "7102" not in codes
    assert out["data"]["masked_rows"] == 2
    assert "2,000,000" not in out["answer"]


def test_expense_breakdown_shows_salary_account_to_owner(session, with_payroll):
    out = assistant.ask(session, with_payroll.id, "owner",
                        "2026 оны 7-р сарын хамгийн их зардал",
                        use_ai=False, today=TODAY)

    assert "7101" in [r["code"] for r in out["data"]["rows"]]
    assert out["data"]["masked_rows"] == 0


def test_total_expenses_stay_whole_for_everyone(session, with_payroll):
    """Нийт дүн бол тайлангийн ердийн үзүүлэлт — эрхээс үл хамааран ижил."""
    p = metrics.parse_period("2026 оны 7-р сар", TODAY)
    inc = reports.income_statement(session, with_payroll.id, p.date_from, p.date_to)

    for role in ("owner", "cashier"):
        out = assistant.ask(session, with_payroll.id, role,
                            "2026 оны 7-р сарын зардал", use_ai=False, today=TODAY)
        assert out["data"]["value_minor"] == inc["expenses_minor"]


def test_catalog_hides_salary_metric_from_cashier():
    names = {m.name for m in metrics.catalog_for("cashier")}
    assert "payroll" not in names and "revenue" in names
    assert "payroll" in {m.name for m in metrics.catalog_for("owner")}


# --------------------------------------------- таамаглал ба гажилт (L2/L4)

@pytest.fixture
def long_history(session, company):
    """7 сарын тогтмол борлуулалт — таамаглахад хангалттай."""
    for m in range(1, 8):
        post_entry(session, company.id, date(2026, m, 15), [
            LineInput("1101", debit_minor=10_000_000_00, description="Борлуулалт"),
            LineInput("5101", credit_minor=10_000_000_00, description="Борлуулалт"),
        ], source_type=SourceType.manual, memo="Борлуулалт")
    session.flush()
    return company


@pytest.mark.parametrize("question, expected", [
    ("борлуулалтын таамаг хэд вэ", "revenue_forecast"),
    ("зардлын таамаг", "expense_forecast"),
    ("мөнгө хүрэлцэх үү", "cash_forecast"),
    ("13 долоо хоногийн мөнгөн урсгал", "cash_forecast"),
    ("гажилт байна уу", "anomalies"),
    ("давхар төлөлт байна уу", "anomalies"),
])
def test_router_reaches_the_new_metrics(question, expected):
    assert assistant.route_by_rules(question) == expected


def test_forecast_question_does_not_hijack_the_plain_revenue_question():
    assert assistant.route_by_rules("борлуулалт хэд вэ") == "revenue"
    assert assistant.route_by_rules("борлуулалтын таамаг хэд вэ") == "revenue_forecast"


def test_forecast_answer_always_states_its_accuracy(session, long_history):
    out = assistant.ask(session, long_history.id, "owner",
                        "борлуулалтын таамаг", use_ai=False,
                        today=date(2026, 8, 15))

    assert out["status"] == "answered"
    assert "Нарийвчлал" in out["answer"]
    assert "дундаж алдаа" in out["answer"]
    assert out["data"]["compare"]["backtest_points"] > 0


def test_forecast_is_refused_when_history_is_too_short(session, books):
    """books-д зөвхөн 7-р сарын өгөгдөл — таамаглахгүй, шалтгааныг хэлнэ."""
    out = assistant.ask(session, books.id, "owner", "борлуулалтын таамаг",
                        use_ai=False, today=date(2026, 8, 15))

    assert out["status"] == "unavailable"
    assert "түүх" in out["answer"].lower()
    assert "data" not in out


def test_refused_forecast_is_still_logged(session, books):
    assistant.ask(session, books.id, "owner", "борлуулалтын таамаг",
                  use_ai=False, today=date(2026, 8, 15))

    row = session.scalar(select(assistant.AssistantQuery))
    assert row.status == "unavailable" and row.metric == "revenue_forecast"
    assert row.value_minor is None


def test_anomaly_answer_lists_the_findings(session, books):
    """books-д 7-р сард ижил дүнтэй хоёр төлөлт үүсгэж шалгана."""
    for day in (10, 12):
        post_entry(session, books.id, date(2026, 7, day), [
            LineInput("7103", debit_minor=777_000_00, description="Түрээс"),
            LineInput("1101", credit_minor=777_000_00, description="Түрээс"),
        ], source_type=SourceType.manual, memo="Түрээс")
    session.flush()

    out = assistant.ask(session, books.id, "owner",
                        "2026 оны 7-р сард гажилт байна уу", use_ai=False,
                        today=TODAY)

    assert out["status"] == "answered"
    assert out["data"]["unit"] == "count"
    assert out["data"]["value_minor"] >= 1
    assert "Давхар төлөлтийн сэжиг" in " ".join(
        r["name"] for r in out["data"]["rows"])


def test_clean_books_answer_says_nothing_was_found(session, books):
    out = assistant.ask(session, books.id, "owner",
                        "2026 оны 7-р сард гажилт байна уу", use_ai=False,
                        today=TODAY)
    assert "илрээгүй" in out["answer"]


def test_cash_forecast_warns_when_the_balance_goes_negative(session, long_history):
    from bayan.partners import Counterparty, Invoice, InvoiceKind

    cp = Counterparty(company_id=long_history.id, name="Нийлүүлэгч", reg_no="9")
    session.add(cp)
    session.flush()
    session.add(Invoice(
        company_id=long_history.id, counterparty_id=cp.id,
        kind=InvoiceKind.purchase, number="B-1", issue_date=date(2026, 8, 1),
        due_date=date(2026, 9, 10), net_minor=900_000_000_00, vat_minor=0,
        paid_minor=0))
    session.flush()

    out = assistant.ask(session, long_history.id, "owner",
                        "мөнгө хүрэлцэх үү", use_ai=False, today=date(2026, 8, 15))

    assert "АНХААР" in out["answer"]
    assert out["data"]["compare"]["goes_negative"] is True


# ------------------------------------------------------------------ аудит

def test_every_question_is_logged(session, books):
    assistant.ask(session, books.id, "owner", "борлуулалт хэд вэ",
                  actor_id="u1", use_ai=False, today=TODAY)
    assistant.ask(session, books.id, "cashier", "цалин хэд вэ",
                  actor_id="u2", use_ai=False, today=TODAY)
    assistant.ask(session, books.id, "owner", "маргааш бороо орох уу",
                  actor_id="u1", use_ai=False, today=TODAY)

    rows = session.scalars(select(assistant.AssistantQuery)).all()
    assert len(rows) == 3
    assert {r.status for r in rows} == {"answered", "denied", "unknown"}

    denied = next(r for r in rows if r.status == "denied")
    assert denied.role == "cashier" and denied.metric == "payroll"
    assert denied.value_minor is None      # эрхгүй үед тоо огт бичигдэхгүй


def test_history_returns_newest_first(session, books):
    for q in ["борлуулалт", "зардал", "мөнгө"]:
        assistant.ask(session, books.id, "owner", q, use_ai=False, today=TODAY)

    hist = assistant.history(session, books.id)
    assert len(hist) == 3
    assert hist[0]["question"] == "мөнгө"


# ------------------------------------------------------- өгөгдлийн гаралт

def test_egress_defaults_to_aggregate(session, company):
    assert assistant.get_egress(session, company.id) == "aggregate"


def test_egress_can_be_changed_and_persists(session, company):
    assistant.set_egress(session, company.id, "off")
    assert assistant.get_egress(session, company.id) == "off"
    assistant.set_egress(session, company.id, "full")
    assert assistant.get_egress(session, company.id) == "full"


def test_egress_rejects_unknown_mode(session, company):
    with pytest.raises(ValueError):
        assistant.set_egress(session, company.id, "хаа сайгүй")


def test_egress_off_keeps_ai_disabled(session, books, monkeypatch):
    """egress=off үед API key байсан ч LLM дуудагдахгүй."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def explode(*a, **k):
        raise AssertionError("LLM дуудагдсан — egress=off үед дуудагдах ёсгүй")

    monkeypatch.setattr(assistant, "route_by_llm", explode)
    monkeypatch.setattr(assistant, "_write_answer", explode)
    assistant.set_egress(session, books.id, "off")

    out = assistant.ask(session, books.id, "owner", "борлуулалт хэд вэ",
                        today=TODAY)
    assert out["status"] == "answered" and out["route_source"] == "rule"


def test_falls_back_to_rules_when_the_model_call_fails(session, books, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(assistant, "route_by_llm",
                        lambda q: (_ for _ in ()).throw(RuntimeError("сүлжээ алга")))
    monkeypatch.setattr(assistant, "_write_answer",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сүлжээ алга")))

    out = assistant.ask(session, books.id, "owner",
                        "2026 оны 7-р сарын борлуулалт", today=TODAY)

    assert out["status"] == "answered"
    assert out["data"]["value_minor"] == 12_000_000_00
    assert "12,000,000₮" in out["answer"]


# ------------------------------------------- бүртгэлийн дарааллын тогтвортой байдал

def test_timestamps_never_repeat_even_in_a_tight_loop():
    """Windows-ийн цагийн нарийвчлал бүдүүн — тэмдэг хатуу өсөх ёстой."""
    from bayan.audit import monotonic_utcnow

    stamps = [monotonic_utcnow() for _ in range(500)]
    assert len(set(stamps)) == 500
    assert stamps == sorted(stamps)


def test_history_order_holds_for_questions_asked_in_one_instant(session, books):
    """Нэг зуурт асуусан ч түүх зөв дарааллаар гарна."""
    for q in ["борлуулалт", "зардал", "мөнгө", "авлага", "нөат"]:
        assistant.ask(session, books.id, "owner", q, use_ai=False, today=TODAY)

    hist = assistant.history(session, books.id)
    assert [h["question"] for h in hist] == ["нөат", "авлага", "мөнгө",
                                             "зардал", "борлуулалт"]
