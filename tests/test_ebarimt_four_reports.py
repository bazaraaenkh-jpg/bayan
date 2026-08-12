"""eBarimt-ын 4 тайланг (ААН/иргэн × орлого/зарлага) банкны хуулгатай тулгах.

Өмнөх хувилбарын гол гэмтлүүд, тус бүрд нь тест:

  * «Нийт дүн» баганыг «НӨАТ-ын дүн» дардаг байсан (сүүлд таарсан нь ялдаг)
  * толгой танигдаагүй үед дурын тоог дүн болгож ХУУРАМЧ мөр үүсгэдэг байв
  * .xls (BIFF) файлыг CSV гэж уншиж хог өгөгдөл гаргадаг байв
  * орлого/зарлагын ЧИГЛЭЛийг шалгадаггүй тул зарлагын падаан орлогын
    гүйлгээтэй тулгагддаг байв
  * нэг шилжүүлгээр төлсөн олон падааныг «мөнгө ирээгүй» гэж үздэг байв
  * API горим тохиргоогүй үед ЧИМЭЭГҮЙ mock өгөгдөл буцаадаг байв
"""

import io
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import bayan.api as apimod
from bayan import ebarimt, ebarimt_match as em
from bayan.db import make_engine
from bayan.models import BankTxn, Direction, ExtractionPath, Statement, StatementStatus


# --------------------------------------------------------------- задлагч

HEADER = ("№,Гүйлгээний огноо,ДДТД,Байгууллагын нэр,Татвар төлөгчийн дугаар,"
          "Нийт дүн,НӨАТ-ын дүн,НХАТ дүн\n")


def _csv(rows: str) -> bytes:
    return (HEADER + rows).encode("utf-8")


def test_total_column_not_stolen_by_vat_column():
    """«Нийт дүн» ба «НӨАТ-ын дүн» зэрэг байхад нийт дүнг зөв сонгоно."""
    res = ebarimt.parse_ebarimt_export(
        _csv('1,2026-07-05,DDTD-1,Хангамж ХХК,5011223344,"1,320,000.00",120000,0\n'),
        "Байгууллагын зарлага.csv")
    (item,) = res["items"]
    assert item["total_minor"] == 132_000_000
    assert item["vat_minor"] == 12_000_000
    assert item["receipt_id"] == "DDTD-1"        # ДДТД-г «регистр» дардаггүй
    assert item["party_tin"] == "5011223344"


def test_dataset_detected_from_file_name():
    assert ebarimt.detect_dataset("Байгууллагын орлого 2026-07.xlsx") == "org_income"
    assert ebarimt.detect_dataset("Байгууллагын зарлага.xls") == "org_expense"
    assert ebarimt.detect_dataset("Иргэний орлого.csv") == "citizen_income"
    assert ebarimt.detect_dataset("иргэний_зарлага_07.xlsx") == "citizen_expense"
    assert ebarimt.detect_dataset("хуулга.xlsx") is None      # таамаглахгүй


def test_direction_follows_dataset():
    inc = ebarimt.parse_ebarimt_export(
        _csv("1,2026-07-05,D1,Номин ХХК,5011,100000,9090,0\n"),
        "Байгууллагын орлого.csv")
    exp = ebarimt.parse_ebarimt_export(
        _csv("1,2026-07-05,D2,Номин ХХК,5011,100000,9090,0\n"),
        "Байгууллагын зарлага.csv")
    assert inc["items"][0]["direction"] == "in"
    assert exp["items"][0]["direction"] == "out"


def test_unreadable_file_raises_instead_of_fabricating_rows():
    """Толгой танигдаагүй бол алдаа мэдэгдэнэ — тоо түүж мөр зохиохгүй."""
    junk = "Тайлан\nЗарим текст,12345,678\nӨөр мөр,999,111\n".encode("utf-8")
    with pytest.raises(ebarimt.EbarimtParseError):
        ebarimt.parse_ebarimt_export(junk, "тодорхойгүй.csv")


def test_ole2_xls_is_not_read_as_csv():
    """OLE2 (.xls) гарын үсэгтэй файлыг CSV гэж уншихгүй — тодорхой алдаа өгнө."""
    fake_xls = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(ebarimt.EbarimtParseError):
        ebarimt.parse_ebarimt_export(fake_xls, "Иргэний зарлага.xls")


def test_summary_row_without_date_or_ddtd_is_skipped():
    res = ebarimt.parse_ebarimt_export(
        _csv("1,2026-07-05,D1,Номин ХХК,5011,100000,9090,0\n"
             ",,,,,100000,9090,0\n"),
        "Байгууллагын орлого.csv")
    assert len(res["items"]) == 1
    assert res["skipped_rows"] == 1


def test_voided_receipt_is_excluded():
    data = ("Огноо,ДДТД,Нийт дүн,Төлөв\n"
            "2026-07-05,D1,100000,Хэвийн\n"
            "2026-07-06,D2,250000,Хүчингүй болсон\n").encode("utf-8")
    res = ebarimt.parse_ebarimt_export(data, "Байгууллагын орлого.csv")
    assert [i["receipt_id"] for i in res["items"]] == ["D1"]
    assert res["voided_rows"] == 1


# --------------------------------------------------------------- тулгагч

class _Txn:
    def __init__(self, id, amount_minor, day, direction, name=None):
        self.id = id
        self.amount_minor = amount_minor
        self.posted_at = datetime(2026, 7, day, 10, 0)
        self.direction = direction
        self.counterparty_name = name
        self.description_raw = ""


def _item(total_minor, day, direction, party="НОМИН ГЭГЭЭ ХХК", rid="EB-1"):
    return {"total_minor": total_minor, "date": f"2026-07-{day:02d}",
            "party": party, "receipt_id": rid, "direction": direction}


def test_expense_receipt_does_not_match_inflow_txn():
    """Зарлагын падаан мөнгө ОРСОН гүйлгээтэй хэзээ ч тулгагдахгүй."""
    res = em.match([_item(120_000_00, 15, "out")],
                   [_Txn("t1", 120_000_00, 15, Direction.credit, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id is None


def test_expense_receipt_matches_outflow_txn():
    res = em.match([_item(120_000_00, 15, "out")],
                   [_Txn("t1", 120_000_00, 15, Direction.debit, "НОМИН ГЭГЭЭ ХХК")])
    assert res[0].txn_id == "t1"
    assert res[0].auto


def test_direction_unknown_still_matches_either_way():
    """Төрөл нь танигдаагүй файл (чиглэлгүй) хуучин байдлаараа тулгагдана."""
    item = _item(50_000_00, 15, None)
    res = em.match([item], [_Txn("t1", 50_000_00, 15, Direction.credit)])
    assert res[0].txn_id == "t1"


def test_several_receipts_paid_by_one_transfer_are_grouped():
    """Нэг шилжүүлгээр 3 баримт төлсөн тохиолдол — бүлгээр тулгана."""
    items = [_item(40_000_00, 14, "out", rid="EB-1"),
             _item(50_000_00, 15, "out", rid="EB-2"),
             _item(30_000_00, 16, "out", rid="EB-3")]
    res = em.match(items, [_Txn("t1", 120_000_00, 16, Direction.debit,
                                "НОМИН ГЭГЭЭ ХХК")])
    assert all(r.txn_id == "t1" for r in res)
    assert {r.group_size for r in res} == {3}
    assert len({r.group_id for r in res}) == 1


def test_group_respects_direction():
    """Бүлэглэх үед ч чиглэл шалгагдана."""
    items = [_item(40_000_00, 14, "out"), _item(80_000_00, 15, "out")]
    res = em.match(items, [_Txn("t1", 120_000_00, 16, Direction.credit)])
    assert all(r.txn_id is None for r in res)


# --------------------------------------------------------------- endpoint

@pytest.fixture
def client():
    engine = make_engine("sqlite:///:memory:")
    apimod.SessionLocal = sessionmaker(bind=engine, future=True)
    apimod._hits.clear()
    return TestClient(apimod.app)


def _register(client):
    uid = uuid4().hex[:6]
    r = client.post("/api/register", json={
        "email": f"eb4_{uid}@bayan.mn", "name": "Нягтлан",
        "password": "pass12345password", "company_name": f"Тулгалт ХХК {uid}",
    })
    assert r.status_code == 200, r.text
    j = r.json()
    return j["company_id"], {"Authorization": f"Bearer {j['token']}"}


def _add_txn(company_id, *, amount_minor, day=15, direction=Direction.credit,
             name=None, month=7):
    s = apimod.SessionLocal()
    stmt = Statement(company_id=company_id, file_name="st.xls",
                     file_sha256=uuid4().hex, descriptor_id="tdb.internet_bank.xls.v1",
                     status=StatementStatus.parsed_verified)
    s.add(stmt); s.flush()
    s.add(BankTxn(
        statement_id=stmt.id, company_id=company_id, bank_account_key="413108778",
        seq_no=1, posted_at=datetime(2026, month, day, 10, 0),
        direction=direction, amount_minor=amount_minor,
        balance_after_minor=amount_minor, counterparty_account=None,
        counterparty_name=name, description_raw="УТГА", description_norm="утга",
        channel_ref=None, canonical_hash=uuid4().hex,
        extraction_path=ExtractionPath.excel,
    ))
    s.commit(); s.close()


def _upload(client, company_id, headers, files):
    return client.post(
        f"/api/companies/{company_id}/ebarimt/reconcile-bank",
        headers=headers, data={"mode": "excel"}, files=files)


def test_four_reports_are_summarised_separately(client):
    company_id, headers = _register(client)
    _add_txn(company_id, amount_minor=100_000_00, day=15, direction=Direction.credit,
             name="НОМИН ХХК")
    _add_txn(company_id, amount_minor=220_000_00, day=16, direction=Direction.debit,
             name="ХАНГАМЖ ХХК")

    files = [
        ("files", ("Байгууллагын орлого.csv",
                   _csv("1,2026-07-15,D1,НОМИН ХХК,5011,100000,9090,0\n"), "text/csv")),
        ("files", ("Байгууллагын зарлага.csv",
                   _csv("1,2026-07-16,D2,ХАНГАМЖ ХХК,5012,220000,20000,0\n"), "text/csv")),
        ("files", ("Иргэний орлого.csv",
                   _csv("1,2026-07-17,D3,Иргэн,,35000,3181,0\n"), "text/csv")),
        ("files", ("Иргэний зарлага.csv",
                   _csv("1,2026-07-18,D4,Иргэн,,12000,1090,0\n"), "text/csv")),
    ]
    r = _upload(client, company_id, headers, files)
    assert r.status_code == 200, r.text
    j = r.json()

    assert {d["dataset"] for d in j["datasets"]} == {
        "org_income", "org_expense", "citizen_income", "citizen_expense"}
    assert len(j["files"]) == 4
    assert all(f.get("dataset") for f in j["files"])

    by_ds = {d["dataset"]: d for d in j["datasets"]}
    assert by_ds["org_income"]["matched_count"] == 1
    assert by_ds["org_expense"]["matched_count"] == 1
    assert by_ds["citizen_income"]["unmatched_count"] == 1
    assert j["total_ebarimt_count"] == 4
    assert j["matched_count"] == 2


def test_inflow_receipt_not_matched_to_outflow_txn_via_endpoint(client):
    """Дүн, огноо нь яг таарсан ч эсрэг чиглэлтэй бол тулгагдахгүй."""
    company_id, headers = _register(client)
    _add_txn(company_id, amount_minor=100_000_00, day=15, direction=Direction.debit)

    r = _upload(client, company_id, headers, [
        ("files", ("Байгууллагын орлого.csv",
                   _csv("1,2026-07-15,D1,НОМИН ХХК,5011,100000,9090,0\n"), "text/csv"))])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["matched_count"] == 0
    assert {i["status"] for i in j["items"]} == {"NO_BANK_PAYMENT", "NO_EBARIMT"}


def test_bank_txns_outside_period_are_not_reported(client):
    """Тухайн сарын гадуурх гүйлгээ «eBarimt дутуу» болж гарахгүй."""
    company_id, headers = _register(client)
    _add_txn(company_id, amount_minor=100_000_00, day=15, direction=Direction.credit)
    _add_txn(company_id, amount_minor=777_000_00, day=15, direction=Direction.credit,
             month=3)

    r = _upload(client, company_id, headers, [
        ("files", ("Байгууллагын орлого.csv",
                   _csv("1,2026-07-15,D1,НОМИН ХХК,5011,100000,9090,0\n"), "text/csv"))])
    j = r.json()
    assert j["total_bank_count"] == 1
    assert j["unmatched_bank_count"] == 0
    assert j["period"]["from"] and j["period"]["to"]


def test_bad_file_reports_error_but_good_file_still_processed(client):
    company_id, headers = _register(client)
    _add_txn(company_id, amount_minor=100_000_00, day=15)

    r = _upload(client, company_id, headers, [
        ("files", ("эвдэрсэн.csv", b"aaa,bbb\n1,2\n", "text/csv")),
        ("files", ("Байгууллагын орлого.csv",
                   _csv("1,2026-07-15,D1,НОМИН ХХК,5011,100000,9090,0\n"), "text/csv")),
    ])
    assert r.status_code == 200, r.text
    j = r.json()
    errors = [f for f in j["files"] if f.get("error")]
    assert len(errors) == 1 and "эвдэрсэн.csv" == errors[0]["file"]
    assert j["total_ebarimt_count"] == 1


def test_all_files_unreadable_returns_422(client):
    company_id, headers = _register(client)
    r = _upload(client, company_id, headers, [
        ("files", ("эвдэрсэн.csv", b"aaa,bbb\n1,2\n", "text/csv"))])
    assert r.status_code == 422
    assert "эвдэрсэн.csv" in r.json()["detail"]


def test_api_mode_without_credentials_does_not_return_mock(client, monkeypatch):
    """Тохиргоогүй үед хуурамч (mock) өгөгдөл биш, ойлгомжтой алдаа буцаана."""
    monkeypatch.delenv("EBARIMT_TOKEN", raising=False)
    monkeypatch.delenv("EBARIMT_TIN", raising=False)
    company_id, headers = _register(client)
    r = client.post(f"/api/companies/{company_id}/ebarimt/reconcile-bank",
                    headers=headers, data={"mode": "api", "year": 2026, "month": 7})
    assert r.status_code == 422
    assert "EBARIMT" in r.json()["detail"]
