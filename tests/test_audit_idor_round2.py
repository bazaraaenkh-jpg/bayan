"""АУДИТ 2-р тойрог: get_owned() хэрэглэгдээгүй үлдсэн endpoint-ууд.

Эхний тойрогт нотлогдсон IDOR-ууд засагдсан боловч дараах хоёрт
`db.get(Model, id_from_url)` хэвээр байна.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import bayan.api as apimod
from bayan.db import make_engine


@pytest.fixture
def client():
    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    return TestClient(apimod.app)


def _register(client, email, company):
    r = client.post("/api/register", json={
        "email": email, "name": "Т", "password": "нууц12345",
        "company_name": company})
    assert r.status_code == 200, r.text
    return r.json()


def test_cross_tenant_purchase_order_receive_blocked(client):
    """Б компани А компанийн худалдан авалтын захиалгыг хүлээж авч БОЛОХГҮЙ."""
    from bayan.models import PurchaseOrder
    from bayan.partners import Counterparty

    ja = _register(client, "a@x.mn", "Альфа ХХК")
    jb = _register(client, "b@x.mn", "Бета ХХК")

    db = apimod.SessionLocal()
    cp = Counterparty(company_id=ja["company_id"], name="А-гийн нийлүүлэгч")
    db.add(cp); db.flush()
    po = PurchaseOrder(company_id=ja["company_id"], po_number="PO-A-1",
                       counterparty_id=cp.id, po_date=date(2026, 3, 1),
                       total_amount_minor=100_000, status="draft")
    db.add(po); db.flush()
    po_id = po.id
    db.commit(); db.close()

    hb = {"Authorization": f"Bearer {jb['token']}"}
    r = client.post(
        f"/api/companies/{jb['company_id']}/purchase-orders/{po_id}/receive",
        headers=hb)
    assert r.status_code in (403, 404), (
        f"IDOR: Б компани А-гийн PO-г хүлээн авлаа ({r.status_code})")


def test_cross_tenant_employee_advance_clear_blocked(client):
    """Б компани А компанийн ажилтны урьдчилгааг хаагаж БОЛОХГҮЙ."""
    from bayan.models import EmployeeAdvance
    from bayan.salary import Employee

    ja = _register(client, "a@x.mn", "Альфа ХХК")
    jb = _register(client, "b@x.mn", "Бета ХХК")

    db = apimod.SessionLocal()
    emp = Employee(company_id=ja["company_id"], code="E1", last_name="Бат",
                   first_name="Дорж", base_salary_minor=1_000_000)
    db.add(emp); db.flush()
    adv = EmployeeAdvance(company_id=ja["company_id"], employee_id=emp.id,
                          advance_date=date(2026, 3, 1), amount_minor=100_000,
                          purpose="Томилолт", status="pending")
    db.add(adv); db.flush()
    adv_id = adv.id
    db.commit(); db.close()

    hb = {"Authorization": f"Bearer {jb['token']}"}
    r = client.post(
        f"/api/companies/{jb['company_id']}/employee-advances/{adv_id}/clear",
        headers=hb, json={"cleared_amount": "50000", "expense_account": "7199"})
    assert r.status_code in (403, 404), (
        f"IDOR: Б компани А-гийн урьдчилгааг хаалаа ({r.status_code})")
