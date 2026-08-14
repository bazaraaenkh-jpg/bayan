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


# ============================================================================
#  НӨАТУС-ын БОДИТ экспортын формат (2026-07-ны файлууд дээр баталгаажсан)
# ============================================================================
#
# Хоёр өөр экспорт байдаг:
#   1. «Байгууллага хоорондын гүйлгээ (борлуулалт / худалдан авалт)» — ААН-ы
#      баримтууд. Файлын нэрэнд төрөл нь бий.
#   2. «Баримтын задаргаа» (barimtiin_zadargaa.xlsx) — ПОС/бүх баримт. Орлого,
#      зарлагынх нь ЯГ ижил нэртэй буудаг тул зөвхөн толгойн баганаас ялгана.

B2B_HEADER = ("ДДТД,Огноо,Харилцагчийн нэр,Харилцагчийн ТТД,НӨАТ,Нийт дүн,"
              "Татварын төрөл,Хаанаас үүссэн,Төлөв\n")
B2B_ROW = ("0000070184730002607310000015,2026-07-31,Мандал даатгал,5473489,"
           "13181.818182,145000,Энгийн,ИБАРИМТ,Илгээгдсэн баримт\n")

# «Х/А» = худалдан авагч → бид борлуулсан → орлого
POS_SALE_HEADER = ("Пос дугаар,ДДТД,Огноо,Нийт дүн,НХАТ,НӨАТ,Цэвэр дүн,"
                   "Х/А регистр,Х/А нэр,Хаанаас,НӨАТ төлөгч эсэх,Пос дугаар,"
                   "Систем нийлүүлэгч,Байршлын алба\n")
POS_SALE_ROW = ("7529057,000007018473000260701000001224,2026-07-01 10:05:39.0,"
                "66000.00,,6000.00,60000.00,,,ebarimt,,001,ebarimt,Баянгол\n")

# «Борлуулагч» = худалдагч → бид авсан → зарлага
POS_BUY_HEADER = ("Пос дугаар,ДДТД,Огноо,Нийт дүн,НХАТ,НӨАТ,Цэвэр дүн,"
                  "Борлуулагчийн регистр,Борлуулагчийн нэр,Хаанаас,"
                  "НӨАТ төлөгч эсэх,Пос дугаар,Систем нийлүүлэгч,Байршлын алба\n")
POS_BUY_ROW = ("7885419,026100238379001096720006310026,2026-07-01 15:00:35.0,"
               "22000.00,,2000.00,20000.00,6011616,Дата бэйнк,POS,"
               "НӨАТ төлөгч,012383,Дата Бэйнк ХХК,Хан-Уул\n")


def test_real_b2b_export_columns_and_dataset():
    res = ebarimt.parse_ebarimt_export(
        (B2B_HEADER + B2B_ROW).encode("utf-8"),
        "Байгууллага хоорондын гүйлгээ (борлуулалт) [7-р сар]_export_178.xlsx")
    assert res["dataset"] == "org_income"
    assert res["direction"] == "in"
    (it,) = res["items"]
    assert it["total_minor"] == 145_000_00        # «Нийт дүн», НӨАТ биш
    assert it["vat_minor"] == 1_318_182           # 13181.818182₮ → мөнгө болгож бөөрөнхийлнө
    assert it["party"] == "Мандал даатгал"
    assert it["party_tin"] == "5473489"
    assert it["receipt_id"].startswith("00000701847300026073")


def test_real_b2b_purchase_export_is_outflow():
    res = ebarimt.parse_ebarimt_export(
        (B2B_HEADER + B2B_ROW).encode("utf-8"),
        "Байгууллага хоорондын гүйлгээ (худалдан авалт) [7-р сар]_export.xlsx")
    assert res["dataset"] == "org_expense"
    assert res["items"][0]["direction"] == "out"


def test_pos_export_direction_comes_from_header_not_file_name():
    """Хоёр задаргаа ижил нэртэй буудаг тул толгойн баганаас ялгана."""
    sale = ebarimt.parse_ebarimt_export(
        (POS_SALE_HEADER + POS_SALE_ROW).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    buy = ebarimt.parse_ebarimt_export(
        (POS_BUY_HEADER + POS_BUY_ROW).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    assert sale["dataset"] == "citizen_income" and sale["direction"] == "in"
    assert buy["dataset"] == "citizen_expense" and buy["direction"] == "out"


def test_system_supplier_column_is_not_taken_as_counterparty():
    """«Систем нийлүүлэгч» бол ПОС-ын программын компани — харилцагч БИШ."""
    res = ebarimt.parse_ebarimt_export(
        (POS_BUY_HEADER + POS_BUY_ROW).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    (it,) = res["items"]
    assert it["party"] == "Дата бэйнк"            # «Дата Бэйнк ХХК» биш
    assert it["party_tin"] == "6011616"
    assert it["total_minor"] == 22_000_00
    assert it["vat_minor"] == 2_000_00            # «НӨАТ төлөгч эсэх» биш
    assert it["date"] == "2026-07-01"             # цагийг таслана


def _xlsx_with_broken_dimension(header: list[str], rows: list[list]) -> bytes:
    """dimension нь «A1:A1» гэж худал бичигдсэн xlsx — eBarimt-ын экспорт ийм."""
    import zipfile
    import re as _re
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)

    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.startswith("xl/worksheets/sheet"):
                data = _re.sub(rb'<dimension ref="[^"]*"/>',
                               b'<dimension ref="A1:A1"/>', data)
            dst.writestr(info, data)
    return out.getvalue()


def test_xlsx_with_lying_dimension_still_reads_every_row():
    """read_only горим хуудасны хэмжээнд итгэдэг тул 1 мөр уншаад зогсдог байв."""
    data = _xlsx_with_broken_dimension(
        ["ДДТД", "Огноо", "Харилцагчийн нэр", "Харилцагчийн ТТД", "НӨАТ", "Нийт дүн"],
        [[f"DDTD-{i}", "2026-07-05", "Номин ХХК", "5011", 909, 10000 + i]
         for i in range(30)])
    res = ebarimt.parse_ebarimt_export(data, "Байгууллагын орлого.xlsx")
    assert len(res["items"]) == 30


def test_pos_report_and_b2b_report_do_not_double_count(client):
    """ААН-ы тайлан нь задаргааны дэд олонлог — давхардлыг ДДТД-ээр цэвэрлэнэ."""
    company_id, headers = _register(client)
    ddtd = "026100238379001096720006310026"
    pos = (POS_BUY_HEADER +
           f"7885419,{ddtd},2026-07-01 15:00:35.0,22000.00,,2000.00,20000.00,"
           f"6011616,Дата бэйнк,POS,НӨАТ төлөгч,012383,Дата Бэйнк ХХК,Хан-Уул\n"
           f"7885420,{ddtd[:-1]}7,2026-07-02 15:00:35.0,5000.00,,0,5000.00,"
           f"6011616,Дата бэйнк,POS,НӨАТ төлөгч,012383,Дата Бэйнк ХХК,Хан-Уул\n")
    b2b = (B2B_HEADER +
           f"{ddtd},2026-07-01,Дата бэйнк,6011616,2000,22000,Энгийн,ПОС,"
           f"Илгээгдсэн баримт\n")

    r = _upload(client, company_id, headers, [
        ("files", ("barimtiin_zadargaa.xlsx", pos.encode("utf-8"), "text/csv")),
        ("files", ("Байгууллага хоорондын гүйлгээ (худалдан авалт).xlsx",
                   b2b.encode("utf-8"), "text/csv")),
    ])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["duplicate_count"] == 1
    assert j["total_ebarimt_count"] == 2               # 3 биш
    assert j["total_ebarimt_amount_mnt"] == 27_000.00  # 49,000 биш
    # Давхардсаныг ААН-ы тайлангийн нэрээр үлдээнэ (харилцагч тодорхой)
    by_ds = {d["dataset"]: d for d in j["datasets"]}
    assert by_ds["org_expense"]["count"] == 1
    assert by_ds["citizen_expense"]["count"] == 1
    pos_report = [f for f in j["files"] if f["file"].startswith("barimtiin")][0]
    assert pos_report["duplicates_removed"] == 1


# ============================================================================
#  Өдрийн нэгдсэн орлого (ПОС / бэлэн борлуулалт)
# ============================================================================
#
# ПОС-ын борлуулалт банкинд баримт бүрээр ордоггүй — өдрийн эцэст (эсвэл
# маргааш нь) нэг дүнгээр буудаг. Нэг бүрчлэн ч, 2-4-өөр бүлэглэж ч барихгүй.

def _pos_item(total_minor, day, rid, pos_no=None, party="", direction="in"):
    it = {"total_minor": total_minor, "date": f"2026-07-{day:02d}",
          "party": party, "receipt_id": rid, "direction": direction}
    if pos_no:
        it["pos_no"] = pos_no
    return it


def test_daily_settlement_matches_sum_of_all_receipts_that_day():
    """Нэг өдрийн 25 баримт нэг нэгдсэн орлогод суусан."""
    items = [_pos_item(10_000_00 + i, 1, f"R{i}") for i in range(25)]
    total = sum(i["total_minor"] for i in items)
    res = em.match(items, [_Txn("t1", total, 1, Direction.credit)])
    assert all(r.txn_id == "t1" for r in res)
    assert {r.group_size for r in res} == {25}
    assert "өдрийн нийлбэр" in res[0].reasons[0]
    assert res[0].auto


def test_next_day_settlement_still_matches():
    """Т+1-ээр буусан нэгдсэн орлого."""
    items = [_pos_item(50_000_00, 10, "R1"), _pos_item(70_000_00, 10, "R2"),
             _pos_item(30_000_00, 10, "R3")]
    res = em.match(items, [_Txn("t1", 150_000_00, 11, Direction.credit)])
    assert all(r.txn_id == "t1" for r in res)


def test_daily_settlement_respects_direction():
    items = [_pos_item(50_000_00, 10, "R1"), _pos_item(70_000_00, 10, "R2")]
    res = em.match(items, [_Txn("t1", 120_000_00, 10, Direction.debit)])
    assert all(r.txn_id is None for r in res)


def test_each_pos_terminal_settles_separately():
    """Хоёр терминал тус тусдаа суудаг — өдрийн бүтэн нийлбэр таарахгүй."""
    # Дүнгүүд нь ганц ч гүйлгээтэй 1:1 тэнцэхгүй байхаар сонгосон — эс тэгвэл
    # нэг бүрчлэн тулгалт (илүү хүчтэй дохио) түрүүлж авна
    items = [_pos_item(45_000_00, 5, "A1", pos_no="7529057"),
             _pos_item(55_000_00, 5, "A2", pos_no="7529057"),
             _pos_item(17_000_00, 5, "B1", pos_no="8101727"),
             _pos_item(23_000_00, 5, "B2", pos_no="8101727")]
    res = em.match(items, [_Txn("t1", 100_000_00, 5, Direction.credit),
                           _Txn("t2", 40_000_00, 5, Direction.credit)])
    by_rid = {items[i]["receipt_id"]: r for i, r in enumerate(res)}
    assert by_rid["A1"].txn_id == by_rid["A2"].txn_id == "t1"
    assert by_rid["B1"].txn_id == by_rid["B2"].txn_id == "t2"
    assert "ПОС" in by_rid["A1"].reasons[0]


def test_separate_days_do_not_get_merged():
    """Өөр өдрийн баримтууд нэг нэгдсэн орлогод хамаарахгүй."""
    items = [_pos_item(50_000_00, 5, "R1"), _pos_item(50_000_00, 6, "R2")]
    res = em.match(items, [_Txn("t1", 100_000_00, 6, Direction.credit)],
                   allow_groups=False)
    assert all(r.txn_id is None for r in res)


def test_exact_single_match_wins_over_daily_bucket():
    """Нэг бүрчлэн яг таарсан гүйлгээг өдрийн багц булаахгүй."""
    items = [_pos_item(40_000_00, 5, "R1"), _pos_item(60_000_00, 5, "R2")]
    res = em.match(items, [_Txn("t1", 40_000_00, 5, Direction.credit),
                           _Txn("t2", 60_000_00, 5, Direction.credit)])
    assert {r.txn_id for r in res} == {"t1", "t2"}
    assert {r.group_size for r in res} == {1}


def test_daily_aggregate_can_be_switched_off():
    items = [_pos_item(50_000_00, 10, "R1"), _pos_item(70_000_00, 10, "R2")]
    txns = [_Txn("t1", 120_000_00, 10, Direction.credit)]
    assert all(r.txn_id == "t1" for r in em.match(items, txns))
    off = em.match(items, txns, allow_groups=False, allow_daily=False)
    assert all(r.txn_id is None for r in off)


def test_pos_number_is_parsed_from_real_export():
    res = ebarimt.parse_ebarimt_export(
        (POS_SALE_HEADER + POS_SALE_ROW).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    assert res["items"][0]["pos_no"] == "7529057"


def test_daily_settlement_end_to_end(client):
    """145 ПОС баримт → өдрийн нэгдсэн орлого endpoint-оор."""
    company_id, headers = _register(client)
    rows = "".join(
        f"7529057,00000701847300026070100000{1000 + i},2026-07-01 10:0{i % 10}:39.0,"
        f"{10000 + i}.00,,{(10000 + i) / 11:.2f},0,,,ebarimt,,001,ebarimt,Баянгол\n"
        for i in range(20))
    total = sum(10000 + i for i in range(20))
    _add_txn(company_id, amount_minor=total * 100, day=1, direction=Direction.credit,
             name="ПОС ОРЛОГО")

    r = _upload(client, company_id, headers, [
        ("files", ("barimtiin_zadargaa.xlsx",
                   (POS_SALE_HEADER + rows).encode("utf-8"), "text/csv"))])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["matched_count"] == 20
    assert j["unmatched_bank_count"] == 0
    assert j["tolerance"]["daily_aggregate"] is True
    assert "өдрийн нийлбэр" in j["items"][0]["match_reason"]


# ============================================================================
#  НӨАТ — татварын төрөл ба дэвтэртэй зэрэгцүүлэлт
# ============================================================================

def test_tax_type_is_parsed_from_real_column():
    data = (B2B_HEADER +
            "D1,2026-07-31,Мандал,5473489,13181.82,145000,Энгийн,ИБАРИМТ,Илгээгдсэн\n"
            "D2,2026-07-31,ХХБ,2635534,0,20600,Чөлөөлөгдөх,ПОС,Илгээгдсэн\n"
            "D3,2026-07-31,Экспорт,111,0,50000,Тэг хувь,ПОС,Илгээгдсэн\n").encode("utf-8")
    res = ebarimt.parse_ebarimt_export(
        data, "Байгууллага хоорондын гүйлгээ (борлуулалт).xlsx")
    assert [i["tax_type"] for i in res["items"]] == ["VAT_ABLE", "VAT_FREE", "VAT_ZERO"]


def test_tax_type_falls_back_to_vat_amount_when_column_missing():
    """«Баримтын задаргаа»-д татварын төрлийн багана байхгүй."""
    res = ebarimt.parse_ebarimt_export(
        (POS_SALE_HEADER + POS_SALE_ROW).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    assert res["items"][0]["tax_type"] == "VAT_ABLE"      # НӨАТ 6000₮ бий

    no_vat = POS_SALE_ROW.replace(",6000.00,", ",,")
    res2 = ebarimt.parse_ebarimt_export(
        (POS_SALE_HEADER + no_vat).encode("utf-8"), "barimtiin_zadargaa.xlsx")
    assert res2["items"][0]["tax_type"] == "NOT_VAT"


def test_summarize_ebarimt_splits_net_and_vat():
    from bayan import vat as vatmod
    items = [
        {"direction": "in", "total_minor": 110_000_00, "vat_minor": 10_000_00,
         "tax_type": "VAT_ABLE"},
        {"direction": "in", "total_minor": 50_000_00, "vat_minor": 0,
         "tax_type": "VAT_FREE"},
        {"direction": "out", "total_minor": 22_000_00, "vat_minor": 2_000_00,
         "tax_type": "VAT_ABLE"},
    ]
    s = vatmod.summarize_ebarimt(items)
    assert s["sales"]["gross_minor"] == 160_000_00
    assert s["sales"]["net_minor"] == 150_000_00       # НӨАТ хассан
    assert s["sales"]["vat_minor"] == 10_000_00
    assert s["sales"]["vatable_count"] == 1
    assert s["sales"]["exempt_gross_minor"] == 50_000_00
    assert s["net_payable_minor"] == 8_000_00          # 10,000 - 2,000


def test_vat_comparison_shows_gap_against_empty_book(client):
    """Дэвтэрт нэхэмжлэх огт байхгүй бол бүх дүн зөрүү болж харагдана."""
    company_id, headers = _register(client)
    r = _upload(client, company_id, headers, [
        ("files", ("Байгууллага хоорондын гүйлгээ (борлуулалт).xlsx",
                   (B2B_HEADER +
                    "D1,2026-07-31,Мандал,5473489,10000,110000,Энгийн,ПОС,Илгээгдсэн\n"
                    ).encode("utf-8"), "text/csv"))])
    assert r.status_code == 200, r.text
    v = r.json()["vat"]
    assert v["period"] == "2026-07"
    by_label = {l["label"]: l for l in v["lines"]}
    assert by_label["Ногдуулсан НӨАТ"]["ebarimt_minor"] == 10_000_00
    assert by_label["Ногдуулсан НӨАТ"]["book_minor"] == 0
    assert by_label["Ногдуулсан НӨАТ"]["diff_minor"] == 10_000_00
    assert by_label["Нийт борлуулалт (цэвэр)"]["ebarimt_minor"] == 100_000_00
    assert v["net_payable_minor"] == 10_000_00


def test_vat_block_does_not_break_reconciliation_response(client):
    """НӨАТ-ын тооцоо унасан ч тулгалтын хариу бүрэн үлдэнэ."""
    company_id, headers = _register(client)
    r = _upload(client, company_id, headers, [
        ("files", ("Байгууллагын орлого.csv",
                   _csv("1,2026-07-15,D1,НОМИН ХХК,5011,100000,9090,0\n"), "text/csv"))])
    j = r.json()
    assert j["ok"] is True and j["vat"] is not None
    assert j["total_ebarimt_count"] == 1


# ============================================================================
#  eBarimt → нэхэмжлэх + журнал → ТТ-03а
# ============================================================================

def _sales_csv(rows):
    """(ДДТД, огноо, харилцагч, ТТД, НӨАТ, нийт) → ААН-ы борлуулалтын экспорт."""
    body = "".join(f"{d},{dt},{p},{tin},{v},{t},Энгийн,ПОС,Илгээгдсэн\n"
                   for d, dt, p, tin, v, t in rows)
    return (B2B_HEADER + body).encode("utf-8")


def _purchase_csv(rows):
    body = "".join(f"{d},{dt},{p},{tin},{v},{t},Энгийн,ПОС,Илгээгдсэн\n"
                   for d, dt, p, tin, v, t in rows)
    return (B2B_HEADER + body).encode("utf-8")


def _post_docs(client, company_id, headers, files, **form):
    data = {"mode": "excel"}
    data.update({k: str(v).lower() if isinstance(v, bool) else v
                 for k, v in form.items()})
    return client.post(f"/api/companies/{company_id}/ebarimt/post-documents",
                       headers=headers, data=data, files=files)


def _files(sales=None, purchases=None):
    out = []
    if sales:
        out.append(("files", ("Байгууллага хоорондын гүйлгээ (борлуулалт).xlsx",
                              _sales_csv(sales), "text/csv")))
    if purchases:
        out.append(("files", ("Байгууллага хоорондын гүйлгээ (худалдан авалт).xlsx",
                              _purchase_csv(purchases), "text/csv")))
    return out


SALES = [("S1", "2026-07-05", "Мандал даатгал", "5473489", "13181.82", "145000"),
         ("S2", "2026-07-09", "ОБА транс", "6296327", "36727.27", "404000")]
PURCH = [("P1", "2026-07-08", "Хангамж ХХК", "2117932", "67363.64", "741000")]


def test_dry_run_creates_nothing(client):
    company_id, headers = _register(client)
    r = _post_docs(client, company_id, headers, _files(SALES, PURCH))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dry_run"] is True
    assert len(j["to_create"]) == 3
    assert j["totals"]["sales_count"] == 2
    assert j["totals"]["sales_vat_minor"] == 1_318_182 + 3_672_727
    assert j["period"] == "2026-07"
    # Дэвтэрт юу ч бичигдээгүй
    tb = client.get(f"/api/companies/{company_id}/vat/tt03a?year=2026&month=7",
                    headers=headers).json()
    assert tb["rows"]["26_nogduulsan_tatvar"] == 0


def test_posting_makes_tt03a_match_ebarimt(client):
    """Бичсэний дараа ТТ-03а нь баримтуудын дүнтэй ЯГ тэнцэнэ."""
    company_id, headers = _register(client)
    r = _post_docs(client, company_id, headers, _files(SALES, PURCH), dry_run=False)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["created_count"] == 3
    assert j["failed"] == []

    tt = client.get(f"/api/companies/{company_id}/vat/tt03a?year=2026&month=7",
                    headers=headers).json()["rows"]
    # Ногдуулсан НӨАТ = баримтууд дээрх дүн (10%-иар дахин тооцоогүй)
    assert tt["26_nogduulsan_tatvar"] == 1_318_182 + 3_672_727
    assert tt["42_tolson_noat"] == 6_736_364
    assert tt["1_niit_borluulalt"] == (145_000_00 - 1_318_182) + (404_000_00 - 3_672_727)


def test_posted_vat_ties_to_general_ledger(client):
    """3105 кредит ба 1203 дебит нь маягттай тэнцэж, etax-ийн шалгуур давна."""
    company_id, headers = _register(client)
    _post_docs(client, company_id, headers, _files(SALES, PURCH), dry_run=False)

    from bayan import etax
    s = apimod.SessionLocal()
    try:
        pkg = etax.build_tt03a(s, company_id, 2026, 7)
    finally:
        s.close()
    ties = {c.code: c for c in pkg.checks if c.code in ("VAT_OUTPUT", "VAT_INPUT")}
    assert ties["VAT_OUTPUT"].ok, ties["VAT_OUTPUT"].detail
    assert ties["VAT_INPUT"].ok, ties["VAT_INPUT"].detail


def test_rerun_is_idempotent(client):
    """Дахин ажиллуулахад ДДТД-ээр таньж давхар бүртгэхгүй."""
    company_id, headers = _register(client)
    _post_docs(client, company_id, headers, _files(SALES), dry_run=False)
    r = _post_docs(client, company_id, headers, _files(SALES), dry_run=False, force=True)
    j = r.json()
    assert j["created_count"] == 0
    assert j["already_exists_count"] == 2


def test_second_posting_blocked_without_force(client):
    """Тухайн сард НӨАТ-ын бичилт байвал давхар бүртгэлээс хамгаална."""
    company_id, headers = _register(client)
    _post_docs(client, company_id, headers, _files(SALES), dry_run=False)
    r = _post_docs(client, company_id, headers,
                   _files([("S9", "2026-07-20", "Шинэ ХХК", "111", "909.09", "10000")]),
                   dry_run=False)
    assert r.status_code == 409
    assert "давхар" in r.json()["detail"].lower() or "3105" in r.json()["detail"]


def test_dry_run_warns_about_existing_vat_movement(client):
    company_id, headers = _register(client)
    _post_docs(client, company_id, headers, _files(SALES), dry_run=False)
    j = _post_docs(client, company_id, headers,
                   _files([("S9", "2026-07-20", "Шинэ ХХК", "111", "909.09", "10000")])
                   ).json()
    assert j["existing_vat_movement"]["3105_credit"] > 0
    assert any("ХОЁР ДАХИН" in w for w in j["warnings"])


def test_counterparty_created_once_per_tin(client):
    company_id, headers = _register(client)
    rows = [("S1", "2026-07-05", "Мандал даатгал", "5473489", "909.09", "10000"),
            ("S2", "2026-07-06", "Мандал даатгал", "5473489", "909.09", "10000")]
    _post_docs(client, company_id, headers, _files(rows), dry_run=False)
    cps = client.get(f"/api/companies/{company_id}/counterparties",
                     headers=headers).json()
    mandal = [c for c in cps if c.get("reg_no") == "5473489"]
    assert len(mandal) == 1


def test_receipt_without_ddtd_is_skipped_not_posted(client):
    company_id, headers = _register(client)
    data = ("Огноо,Нийт дүн\n2026-07-05,100000\n").encode("utf-8")
    r = _post_docs(client, company_id, headers,
                   [("files", ("Байгууллагын орлого.csv", data, "text/csv"))])
    j = r.json()
    assert j["to_create"] == []
    assert j["skipped"] and j["skipped"][0]["reason"] == "ДДТД байхгүй"


def test_vat_comparison_is_zero_after_posting(client):
    """Бичсэний дараа eBarimt ↔ дэвтрийн зөрүү тэглэгдэнэ."""
    company_id, headers = _register(client)
    r = _post_docs(client, company_id, headers, _files(SALES, PURCH), dry_run=False)
    v = r.json()["vat"]
    assert v is not None
    for line in v["lines"]:
        assert line["diff_minor"] == 0, line
    assert v["net_payable_minor"] == v["book_net_payable_minor"]
