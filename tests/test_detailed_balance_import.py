from pathlib import Path
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

def test_import_detailed_balance_sheet(client):
    apimod._hits.clear()
    uid = uuid4().hex[:6]
    reg_resp = client.post("/api/register", json={
        "email": f"pinnacle_{uid}@bayan.mn",
        "name": "Мөнх-Эрдэнэ",
        "password": "pass12345password",
        "company_name": f"Пиннакл Экспертс {uid}"
    })
    assert reg_resp.status_code == 200, reg_resp.text
    reg = reg_resp.json()
    token = reg["token"]
    comp_id = reg["company_id"]
    headers = {"Authorization": f"Bearer {token}"}

    file_path = Path(r"C:\Users\DELL\Downloads\Баланс_дэлгэрэнгүй_1785831961948.xlsx")
    assert file_path.exists()

    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/companies/{comp_id}/import/opening-balances",
            headers=headers,
            files={"file": ("Баланс_дэлгэрэнгүй_1785831961948.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["lines_count"] > 0
    print(f"\nSUCCESS! Imported {data['lines_count']} lines from detailed balance sheet! Entry ID: {data['entry_id']}")
