"""Tests for Superadmin / Admin Panel API endpoints."""

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


def _register(client, email, name="Test User"):
    r = client.post("/api/register", json={
        "email": email, "password": "Password123!", "name": name, "company_name": f"{name}-Co"
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_non_superadmin_blocked(client):
    """Энгийн хэрэглэгч админ панелийн API руу хандахад 403 буцах ёстой."""
    reg = _register(client, "normal@bayan.mn")
    headers = {"Authorization": f"Bearer {reg['token']}"}

    r = client.get("/api/admin/stats", headers=headers)
    assert r.status_code == 403, f"Expected 403 Forbidden, got {r.status_code}"


def test_superadmin_stats_and_companies(client):
    """Супер админ хэрэглэгч систем админы статистик болон компанийн жагсаалт харах."""
    reg = _register(client, "admin@bayan.mn")  # admin@bayan.mn is granted admin access
    headers = {"Authorization": f"Bearer {reg['token']}"}

    # 1. Stats
    r_stats = client.get("/api/admin/stats", headers=headers)
    assert r_stats.status_code == 200, f"Error: {r_stats.text}"
    st = r_stats.json()
    assert "total_companies" in st
    assert st["total_companies"] >= 1

    # 2. Companies list
    r_comps = client.get("/api/admin/companies", headers=headers)
    assert r_comps.status_code == 200, f"Error: {r_comps.text}"
    comps = r_comps.json()
    assert len(comps) >= 1

    # 3. Users list
    r_users = client.get("/api/admin/users", headers=headers)
    assert r_users.status_code == 200, f"Error: {r_users.text}"
    users = r_users.json()
    assert len(users) >= 1


def test_admin_subscription_renewal(client):
    """Супер админ компанийн SaaS багцыг сунгах."""
    reg = _register(client, "admin@bayan.mn")
    headers = {"Authorization": f"Bearer {reg['token']}"}
    c_id = reg["company_id"]

    r = client.post(f"/api/admin/companies/{c_id}/subscription", headers=headers, json={
        "plan": "ENTERPRISE",
        "days": 60,
        "price_mnt": 500000.0
    })
    assert r.status_code == 200, f"Error: {r.text}"
    data = r.json()
    assert data["plan"] == "ENTERPRISE"
    assert "60 хоногоор" in data["message"]
