from uuid import uuid4
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

def test_new_report_templates_endpoints(client):
    apimod._hits.clear()
    uid = uuid4().hex[:6]
    reg_resp = client.post("/api/register", json={
        "email": f"reports_{uid}@bayan.mn",
        "name": "Тайлан Нягтлан",
        "password": "pass12345password",
        "company_name": f"Тайлан Компани {uid}"
    })
    assert reg_resp.status_code == 200
    reg = reg_resp.json()
    token = reg["token"]
    comp_id = reg["company_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test Financial Notes API
    res_notes = client.get(f"/api/companies/{comp_id}/reports/notes?date_from=2026-01-01&date_to=2026-12-31", headers=headers)
    assert res_notes.status_code == 200, res_notes.text
    notes = res_notes.json()
    assert "note_1_cash" in notes
    assert "note_4_ppe" in notes

    # 2. Test NBFI Reports API
    res_nbfi = client.get(f"/api/companies/{comp_id}/reports/nbfi?as_of_date=2026-12-31", headers=headers)
    assert res_nbfi.status_code == 200, res_nbfi.text
    nbfi = res_nbfi.json()
    assert "st1_nbfi_balance_sheet" in nbfi
    assert "representation_statement" in nbfi

    # 3. Test TT-02 Tax Return API
    res_tt02 = client.get(f"/api/companies/{comp_id}/reports/tax/tt02?year=2026", headers=headers)
    assert res_tt02.status_code == 200, res_tt02.text
    tt02 = res_tt02.json()
    assert tt02["form_code"] == "ТТ-02"

    # 4. Test TT-11 Tax Return API
    res_tt11 = client.get(f"/api/companies/{comp_id}/reports/tax/tt11?year=2026", headers=headers)
    assert res_tt11.status_code == 200, res_tt11.text
    tt11 = res_tt11.json()
    assert tt11["form_code"] == "ТТ-11"

    # 5. Test Export Single Excel for new report types
    for rtype in ["notes", "nbfi", "tt02", "tt11"]:
        res_exp = client.get(f"/api/companies/{comp_id}/reports/export-single?report_type={rtype}&date_from=2026-01-01&date_to=2026-12-31", headers=headers)
        assert res_exp.status_code == 200
