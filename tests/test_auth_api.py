"""API түвшний хамгаалалтын тестүүд: нэвтрэлт, олон түрээслэгчийн
тусгаарлалт (403), эрхийн шалгалт, rate limit."""

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


def test_register_login_me(client):
    j = _register(client, "a@x.mn", "Альфа ХХК")
    h = {"Authorization": f"Bearer {j['token']}"}
    me = client.get("/api/me", headers=h).json()
    assert me["email"] == "a@x.mn"
    assert me["companies"][0]["role"] == "owner"

    # login дахин
    r = client.post("/api/login", json={"email": "a@x.mn", "password": "нууц12345"})
    assert r.status_code == 200
    r = client.post("/api/login", json={"email": "a@x.mn", "password": "буруу1234"})
    assert r.status_code == 401


def test_no_token_is_401(client):
    _register(client, "a@x.mn", "Альфа")
    r = client.get("/api/me")
    assert r.status_code == 401
    r = client.post("/api/companies", json={"name": "X"})
    assert r.status_code == 401


def test_tenant_isolation_403(client):
    """Б хэрэглэгч А-гийн компанийн өгөгдөлд хандаж чадахгүй."""
    ja = _register(client, "a@x.mn", "Альфа")
    jb = _register(client, "b@x.mn", "Бета")
    hb = {"Authorization": f"Bearer {jb['token']}"}
    r = client.get(f"/api/companies/{ja['company_id']}/trial-balance", headers=hb)
    assert r.status_code == 403
    r = client.post(f"/api/companies/{ja['company_id']}/items", headers=hb,
                    json={"code": "M1", "name": "x"})
    assert r.status_code == 403


def test_viewer_cannot_post(client):
    ja = _register(client, "a@x.mn", "Альфа")
    ha = {"Authorization": f"Bearer {ja['token']}"}
    cid = ja["company_id"]
    # owner нь viewer урина
    r = client.post(f"/api/companies/{cid}/invite", headers=ha, json={
        "email": "v@x.mn", "name": "Харагч", "role": "viewer",
        "temp_password": "нууц12345"})
    assert r.status_code == 200
    jv = client.post("/api/login", json={"email": "v@x.mn",
                                         "password": "нууц12345"}).json()
    hv = {"Authorization": f"Bearer {jv['token']}"}
    # viewer уншиж чадна
    assert client.get(f"/api/companies/{cid}/trial-balance",
                      headers=hv).status_code == 200
    # бичиж чадахгүй
    r = client.post(f"/api/companies/{cid}/items", headers=hv,
                    json={"code": "M1", "name": "x"})
    assert r.status_code == 403
    # viewer хэрэглэгч урьж чадахгүй (admin биш)
    r = client.post(f"/api/companies/{cid}/invite", headers=hv, json={
        "email": "z@x.mn", "role": "owner", "temp_password": "нууц12345"})
    assert r.status_code == 403


def test_accountant_full_flow(client):
    """Уригдсан нябо бараа үүсгэж, орлого авч, тайлан харна."""
    ja = _register(client, "a@x.mn", "Альфа")
    ha = {"Authorization": f"Bearer {ja['token']}"}
    cid = ja["company_id"]
    client.post(f"/api/companies/{cid}/invite", headers=ha, json={
        "email": "acc@x.mn", "role": "accountant", "temp_password": "нууц12345"})
    jacc = client.post("/api/login", json={"email": "acc@x.mn",
                                           "password": "нууц12345"}).json()
    h = {"Authorization": f"Bearer {jacc['token']}"}
    assert client.post(f"/api/companies/{cid}/items", headers=h,
                       json={"code": "M1", "name": "Материал"}).status_code == 200
    assert client.post(f"/api/companies/{cid}/receive", headers=h, json={
        "item_code": "M1", "qty": 10, "total_cost_minor": 100000,
        "move_date": "2026-07-01"}).status_code == 200
    stock = client.get(f"/api/companies/{cid}/stock", headers=h).json()
    assert stock[0]["qty"] == 10
    bs = client.get(f"/api/companies/{cid}/balance-sheet", headers=h).json()
    assert bs["balanced"]


def test_upload_rejects_bad_ext(client):
    ja = _register(client, "a@x.mn", "Альфа")
    ha = {"Authorization": f"Bearer {ja['token']}"}
    r = client.post(f"/api/companies/{ja['company_id']}/statements", headers=ha,
                    files={"file": ("hack.exe", b"MZ...")})
    assert r.status_code == 422


def test_rate_limit(client):
    for i in range(10):
        client.post("/api/login", json={"email": "x@x.mn", "password": "буруу1234"})
    r = client.post("/api/login", json={"email": "x@x.mn", "password": "буруу1234"})
    assert r.status_code == 429
