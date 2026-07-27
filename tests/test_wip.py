"""WIP — өртгийн урсгал, хэсэгчилсэн хүлээлгэн өгөлт, СТ-1 тулгалт."""

from datetime import date

import pytest
from bayan import inventory, ledger, wip


def _setup(session, company):
    mat = inventory.Item(company_id=company.id, code="M1", name="Түүхий эд")
    prod = inventory.Item(company_id=company.id, code="P1", name="Бүтээгдэхүүн",
                          gl_account="2151")
    session.add_all([mat, prod]); session.flush()
    inventory.receive(session, company.id, mat, date(2026, 3, 1), 100, 10_000_000_00)
    return mat, prod


def test_wip_full_cycle(session, company):
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-001", prod, 10, date(2026, 3, 2))

    wip.issue_materials(session, order, [(mat, 40)], date(2026, 3, 3))
    assert order.material_minor == 4_000_000_00        # 40 × 100к
    wip.add_labor(session, order, 1_500_000_00, date(2026, 3, 20))
    wip.apply_overhead(session, order, 500_000_00, date(2026, 3, 31))
    assert order.accumulated_minor == 6_000_000_00

    # GL-ийн 2145 үлдэгдэл захиалгын хуримтлагдсан өртөгтэй тэнцэнэ
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2145"]["balance_minor"] == 6_000_000_00

    cost = wip.complete(session, order, 10, date(2026, 4, 5))
    assert cost == 6_000_000_00
    assert order.status == wip.OrderStatus.closed
    assert prod.qty == 10 and prod.avg_cost_minor == 60_000_000  # нэгж 600,000₮

    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2145"]["balance_minor"] == 0
    assert tb["2151"]["balance_minor"] == 6_000_000_00


def test_wip_partial_completion_and_month_span(session, company):
    """Хэсэгчилсэн хүлээлгэн өгөлт: өртөг пропорциональ, үлдэгдэл балансад."""
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-002", prod, 10, date(2026, 3, 2))
    wip.issue_materials(session, order, [(mat, 30)], date(2026, 3, 3))
    wip.add_labor(session, order, 1_000_000_00, date(2026, 3, 25))
    total = order.accumulated_minor                     # 3M + 1M = 4M

    cost1 = wip.complete(session, order, 4, date(2026, 3, 30))
    assert cost1 == total * 4 // 10
    assert order.status == wip.OrderStatus.in_progress  # сар дамжин нээлттэй!

    # Балансад ДҮ үлдэгдэл = 60% (Fino-гийн нэг алхамт баримтад байхгүй чадвар)
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2145"]["balance_minor"] == total - cost1

    # 4-р сард нэмэлт зардал + үлдсэнийг дуусгана
    wip.add_labor(session, order, 500_000_00, date(2026, 4, 10))
    cost2 = wip.complete(session, order, 6, date(2026, 4, 20))
    assert order.status == wip.OrderStatus.closed
    # Нийт шилжсэн = нийт хуримтлагдсан (мөнгө алдагдахгүй)
    assert cost1 + cost2 == order.accumulated_minor
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2145"]["balance_minor"] == 0


def test_wip_report_reconciles_with_gl(session, company):
    mat, prod = _setup(session, company)
    o1 = wip.open_order(session, company.id, "WO-A", prod, 5, date(2026, 3, 1))
    o2 = wip.open_order(session, company.id, "WO-B", prod, 5, date(2026, 3, 1))
    wip.issue_materials(session, o1, [(mat, 10)], date(2026, 3, 5))
    wip.issue_materials(session, o2, [(mat, 20)], date(2026, 3, 6))
    wip.add_labor(session, o2, 700_000_00, date(2026, 3, 28))

    report = wip.wip_balance_report(session, company.id)
    report_total = sum(r["wip_balance_minor"] for r in report)
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert report_total == tb["2145"]["balance_minor"]   # даалгаврын 6.2 шаардлага


def test_wip_errors(session, company):
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-E", prod, 5, date(2026, 3, 1))
    wip.issue_materials(session, order, [(mat, 5)], date(2026, 3, 2))
    with pytest.raises(wip.WipError):
        wip.complete(session, order, 6, date(2026, 3, 5))   # төлөвлөснөөс их
    wip.complete(session, order, 5, date(2026, 3, 5))
    with pytest.raises(wip.WipError):
        wip.add_labor(session, order, 1, date(2026, 3, 6))  # хаагдсан захиалга
