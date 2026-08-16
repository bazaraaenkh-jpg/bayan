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


def test_completed_goods_are_sellable(session, company):
    """Үйлдвэрлэсэн бүтээгдэхүүн FIFO багц ба картын хөдөлгөөнтэй үүсэж,
    inventory.issue-ээр борлуулагдана (өмнө нь багцгүй тул борлуулагдахгүй байв)."""
    from sqlalchemy import select

    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-S", prod, 10, date(2026, 3, 2))
    wip.issue_materials(session, order, [(mat, 40)], date(2026, 3, 3))
    wip.add_labor(session, order, 3_000_000_00, date(2026, 3, 25))
    wip.complete(session, order, 10, date(2026, 4, 5))

    batches = session.scalars(select(inventory.StockBatch).where(
        inventory.StockBatch.item_id == prod.id)).all()
    moves = session.scalars(select(inventory.StockMove).where(
        inventory.StockMove.item_id == prod.id)).all()
    assert len(batches) == 1 and batches[0].remaining_qty == 10
    assert batches[0].unit_cost_minor == 700_000_00      # 7 сая / 10 ш
    assert len(moves) == 1 and moves[0].kind == inventory.MoveKind.receipt
    assert moves[0].ref == "WO WO-S"

    # Борлуулалт: ББӨ-д 2151-ээс шилжинэ
    inventory.issue(session, company.id, prod, date(2026, 4, 20), 10,
                    target_account="6101", ref="Борлуулалт")
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2151"]["balance_minor"] == 0
    assert tb["6101"]["balance_minor"] == 7_000_000_00
    assert prod.qty == 0


def test_labor_does_not_double_credit_salary_payable(session, company):
    """Шууд хөдөлмөр 7101 зардлаас шингээгдэнэ — 3102 өглөг давхардахгүй."""
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-L", prod, 10, date(2026, 3, 2))
    wip.add_labor(session, order, 3_000_000_00, date(2026, 3, 25))

    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert "3102" not in tb                              # өглөгт огт хүрэхгүй
    assert tb["7101"]["credit_minor"] == 3_000_000_00    # зардлаас шингээв
    assert tb["2145"]["balance_minor"] == 3_000_000_00

    with pytest.raises(wip.WipError):
        wip.add_labor(session, order, 1_00, date(2026, 3, 26), credit_account="3102")


def test_absorption_status_and_strict_overhead(session, company):
    """Шингээлтийн зөрүү тайлагдаж, strict үед илүү шингээлт зогсоно."""
    mat, prod = _setup(session, company)
    ledger.post_entry(session, company.id, date(2026, 3, 15), [
        ledger.LineInput("7108", debit_minor=2_000_000_00, description="Бодит НЗ"),
        ledger.LineInput("3101", credit_minor=2_000_000_00),
    ])
    order = wip.open_order(session, company.id, "WO-O", prod, 10, date(2026, 3, 2))

    st = wip.apply_overhead(session, order, 1_500_000_00, date(2026, 3, 31))
    assert st["actual_minor"] == 2_000_000_00
    assert st["absorbed_minor"] == 1_500_000_00
    assert st["variance_minor"] == 500_000_00            # дутуу шингээсэн
    assert not st["over_absorbed"]

    # strict: бодитоос давахыг зөвшөөрөхгүй, бичилт ч үлдэхгүй
    with pytest.raises(wip.WipError):
        wip.apply_overhead(session, order, 900_000_00, date(2026, 3, 31), strict=True)
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["7108"]["credit_minor"] == 1_500_000_00

    # strict=False (анхдагч) үед зөвшөөрөх ч сэрэмжлүүлэг өгнө
    st = wip.apply_overhead(session, order, 900_000_00, date(2026, 3, 31))
    assert st["over_absorbed"] and st["variance_minor"] == -400_000_00


def test_partial_completion_unit_cost_is_even(session, company):
    """Хожим нэмэгдсэн зардал үлдсэн нэгжүүдэд ЖИГД хуваарилагдана."""
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-P", prod, 10, date(2026, 3, 2))
    wip.issue_materials(session, order, [(mat, 100)], date(2026, 3, 3))   # 10 сая

    c1 = wip.complete(session, order, 5, date(2026, 3, 30))
    assert c1 // 5 == 1_000_000_00                       # нэгжид 1 сая

    wip.add_labor(session, order, 4_000_000_00, date(2026, 4, 10))
    c2 = wip.complete(session, order, 3, date(2026, 4, 20))
    c3 = wip.complete(session, order, 2, date(2026, 4, 25))

    # Үлдсэн 5 нэгж 9 саяг тэнцүү хуваана — 1.8 сая/ш (өмнө 1.4 vs 2.4 болж зөрдөг байв)
    assert c2 // 3 == 1_800_000_00
    assert c3 // 2 == 1_800_000_00
    assert c1 + c2 + c3 == order.accumulated_minor       # мөнгө алдагдахгүй
    tb = {r["code"]: r for r in ledger.trial_balance(session, company.id)}
    assert tb["2145"]["balance_minor"] == 0


def test_wip_errors(session, company):
    mat, prod = _setup(session, company)
    order = wip.open_order(session, company.id, "WO-E", prod, 5, date(2026, 3, 1))
    wip.issue_materials(session, order, [(mat, 5)], date(2026, 3, 2))
    with pytest.raises(wip.WipError):
        wip.complete(session, order, 6, date(2026, 3, 5))   # төлөвлөснөөс их
    wip.complete(session, order, 5, date(2026, 3, 5))
    with pytest.raises(wip.WipError):
        wip.add_labor(session, order, 1, date(2026, 3, 6))  # хаагдсан захиалга

    # Өртөггүй захиалга — ойлгомжгүй UnbalancedEntryError биш, тодорхой WipError
    empty = wip.open_order(session, company.id, "WO-0", prod, 5, date(2026, 3, 1))
    with pytest.raises(wip.WipError, match="хуримтлагдсан өртөг"):
        wip.complete(session, empty, 5, date(2026, 3, 2))
