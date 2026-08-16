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

def _register(client, email="t3@x.mn", company_name="Test Company 3"):
    r = client.post("/api/register", json={
        "email": email,
        "name": "Tester",
        "password": "pass12345password",
        "company_name": company_name
    })
    return r.json()

def test_asset_revaluation_hits_equity(client):
    j = _register(client, "asset_reval@x.mn", "Asset Reval Co")
    cid = j["company_id"]
    headers = {"Authorization": f"Bearer {j['token']}"}

    asst = client.post(f"/api/companies/{cid}/assets", headers=headers, json={
        "code": "F1", "name": "Equipment", "cost_minor": 1000000,
        "life_months": 12, "in_service_from": "2026-01-01"
    }).json()

    tb_before = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}

    r = client.post(f"/api/companies/{cid}/assets/{asst['id']}/revalue", headers=headers, json={
        "new_value": "15000", "revalue_date": "2026-03-01"
    })
    assert r.status_code == 200

    tb_after = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}

    assert tb_after.get("4104", 0) - tb_before.get("4104", 0) == 500000
    assert tb_after.get("3101", 0) == tb_before.get("3101", 0)

def test_asset_revaluation_decrease(client):
    j = _register(client, "asset_dec@x.mn", "Asset Dec Co")
    cid = j["company_id"]
    headers = {"Authorization": f"Bearer {j['token']}"}

    asst = client.post(f"/api/companies/{cid}/assets", headers=headers, json={
        "code": "F2", "name": "Vehicle", "cost_minor": 1000000,
        "life_months": 12, "in_service_from": "2026-01-01"
    }).json()

    # Дахин үнэлгээний нэмэгдэл (4104) байхгүй тул бууралт бүхэлдээ П/А-д
    # гарз болно — өмнө нь 4104-ийг шалгалтгүй дебитэлж өмч сөрөг болгодог байв
    r = client.post(f"/api/companies/{cid}/assets/{asst['id']}/revalue", headers=headers, json={
        "new_value": "8000", "revalue_date": "2026-03-01"
    })
    assert r.status_code == 200

    tb = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}
    assert tb.get("4104", 0) == 0
    assert tb["7199"] == 200000          # 10,000 → 8,000 бууралт
    assert tb["2501"] == 800000

    # Эхлээд өсгөж нэмэгдэл үүсгэвэл дараагийн бууралт ЭХЛЭЭД түүнийг шавхана
    client.post(f"/api/companies/{cid}/assets/{asst['id']}/revalue", headers=headers, json={
        "new_value": "12000", "revalue_date": "2026-04-01"})
    client.post(f"/api/companies/{cid}/assets/{asst['id']}/revalue", headers=headers, json={
        "new_value": "9000", "revalue_date": "2026-05-01"})
    tb = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}
    assert tb.get("4104", 0) == 100000   # 400,000 нэмэгдлээс 300,000 шавхагдав
    assert tb["7199"] == 200000          # нэмэлт гарз үүсээгүй

def test_landed_costs_allocation_hits_inventory_and_payables(client):
    j = _register(client, "landed@x.mn", "Landed Co")
    cid = j["company_id"]
    headers = {"Authorization": f"Bearer {j['token']}"}

    itm_res = client.post(f"/api/companies/{cid}/items", headers=headers, json={
        "code": "ITM1", "name": "Imported Goods", "unit": "sh"
    }).json()
    item_id = itm_res["id"]

    r = client.post(f"/api/companies/{cid}/landed-costs/allocate", headers=headers, json={
        "shipment_name": "SHIP-001",
        "additional_cost_minor": 20000,
        "allocation_basis": "value",
        "items": [{"item_id": item_id, "qty": 10, "price_minor": 5000}]
    })
    assert r.status_code == 200

    tb = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}
    assert tb.get("2105", 0) == 70000
    assert tb.get("3101", 0) == 70000

def test_withholding_tax_hits_correct_accounts(client):
    j = _register(client, "wht@x.mn", "WHT Co")
    cid = j["company_id"]
    headers = {"Authorization": f"Bearer {j['token']}"}

    r = client.post(f"/api/companies/{cid}/withholding-taxes", headers=headers, json={
        "tax_type": "ХҮЙТ",
        "gross_amount": "100000",
        "wht_rate": 10.0,
        "tax_date": "2026-03-01"
    })
    assert r.status_code == 200

    tb = {r["code"]: r["balance_minor"] for r in client.get(f"/api/companies/{cid}/trial-balance", headers=headers).json()}
    assert tb.get("7199", 0) == 10000000
    assert tb.get("3104", 0) == 1000000
    assert tb.get("1101", 0) == -9000000
