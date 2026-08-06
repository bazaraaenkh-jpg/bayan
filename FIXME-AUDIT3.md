# Аудит 3-р тойрог — үлдсэн ганц алдаа

> 2-р тойргийн 6 бүлэг **бүгд зөв зассан**. Тест 94/94 ногоон, IDOR 0,
> байхгүй функц 0, байхгүй данс 0. Доор зөвхөн шинээр илэрсэн 1 алдаа байна.
>
> `tests/test_audit_idor*.py` — өмнөх шигээ хэвээр үлдээ (устгаж/сулруулж болохгүй).

---

## 1. ҮХ-ийн дахин үнэлгээний өсөлт өр төлбөрт бичигдэж байна

**Байршил:** `src/bayan/api.py` — `POST /api/companies/{company_id}/assets/{asset_id}/revalue`

Одоогийн бичилт:

```
Дт 2501 Барилга байгууламж      50,000
    Кт 3101 Дансны өглөг            50,000   ← БУРУУ
```

Хөрөнгөө дээшлүүлж үнэлэхэд компани нийлүүлэгчид өртэй болж байна.
Бодит гүйлтээр баталгаажсан: 50,000₮-өөр дахин үнэлэхэд `3101` дансанд
50,000₮ өглөг үүсэв.

**НББОУС 16-гийн дагуу** дахин үнэлгээний өсөлт нь өр төлбөр биш,
**эздийн өмчийн «Дахин үнэлгээний нэмэгдэл»** (OCI) руу орно.

### Засвар — 2 алхам

**1.1. Дансны төлөвлөгөөнд шинэ данс нэмэх.** `src/bayan/coa_seed.py`-ийн
`SEED` жагсаалтад `4103`-ын дараа:

```python
("4104", "Хөрөнгийн дахин үнэлгээний нэмэгдэл", "credit", True,  "41"),
```

(Энэ мөр нь Сангийн сайдын 361-р тушаалын СТ-1 маягтын 2.3.6 «Хөрөнгийн
дахин үнэлгээний нэмэгдэл» мөртэй тохирно.)

**1.2. `revalue_asset` дотор `3101` → `4104` болгох:**

```python
    asset_code = asset.gl_account or "2502"     # ← 2101 биш! (доорх 1.3)
    _require_account(db, company_id, asset_code)
    _require_account(db, company_id, "4104")    # ← 3101 биш

    if diff_minor > 0:
        ledger.post_entry(
            db, company_id, r_date,
            lines=[
                ledger.LineInput(asset_code, debit_minor=diff_minor,
                                 description=f"ҮХ дахин үнэлгээний өсөлт: {asset.name}"),
                ledger.LineInput("4104", credit_minor=diff_minor,
                                 description=f"ҮХ дахин үнэлгээний нэмэгдэл: {asset.name}"),
            ],
            source_type=SourceType.manual, memo=..., actor_id=ctx["uid"],
        )
```

**1.3. Мөн адил мөрөнд:** `asset.gl_account or "2101"` гэсэн fallback байна.
`2101` бол **Түүхий эд материал** — үндсэн хөрөнгийн данс биш.
`"2502"` (Машин тоног төхөөрөмж) болгож солино.

**1.4. Буурах тохиолдол.** Одоо `if diff_minor > 0:` үед л бичилт хийгддэг —
хөрөнгө **буурч** үнэлэгдвэл `asset.cost_minor` өөрчлөгдөнө мөртлөө журналд
юу ч бичигдэхгүй (дахин чимээгүй алгасалт). Нэмэх:

```python
    elif diff_minor < 0:
        # Бууралт: эхлээд 4104-ийн үлдэгдлийг хаана, илүүг нь зардалд
        ledger.post_entry(
            db, company_id, r_date,
            lines=[
                ledger.LineInput("4104", debit_minor=abs(diff_minor), ...),
                ledger.LineInput(asset_code, credit_minor=abs(diff_minor), ...),
            ], ...)
```

*(Хатуу НББОУС-аар бол 4104-д хуримтлагдсан үлдэгдлээс илүү гарсан хэсгийг
`7199` зардалд бичих ёстой. Эхний хувилбарт 4104 руу бүтнээр нь бичээд,
дараа нь нарийвчилж болно — гол нь журналд ЯМАР НЭГ бичилт орох явдал.)*

---

## 2. Шалгалт

```bash
cd D:\mcp\bayan-ai
.venv\Scripts\python.exe -m pytest tests\ -q
```

Дараа нь дахин үнэлгээ өмчид буусныг батал:

```bash
.venv\Scripts\python.exe -X utf8 -c "import sys; sys.path.insert(0,'src'); ^
from fastapi.testclient import TestClient; from sqlalchemy.orm import sessionmaker; ^
import bayan.api as a; from bayan.db import make_engine; ^
a.SessionLocal = sessionmaker(bind=make_engine('sqlite:///:memory:'), future=True); a._hits.clear(); ^
c = TestClient(a.app); ^
j = c.post('/api/register', json={'email':'t@t.mn','name':'T','password':'нууц12345','company_name':'X'}).json(); ^
H={'Authorization':'Bearer '+j['token']}; cid=j['company_id']; ^
asst = c.post(f'/api/companies/{cid}/assets', headers=H, json={'code':'F1','name':'M','cost_minor':1000000,'life_months':12,'in_service_from':'2026-01-01'}).json(); ^
c.post(f'/api/companies/{cid}/assets/'+asst['id']+'/revalue', headers=H, json={'new_value':'15000','revalue_date':'2026-03-01'}); ^
tb={r['code']:r['balance_minor'] for r in c.get(f'/api/companies/{cid}/trial-balance', headers=H).json()}; ^
print('4104 (өмч):', tb.get('4104',0), ' 3101 (өглөг):', tb.get('3101',0)); ^
print('ЗӨВ' if tb.get('4104',0)>0 and tb.get('3101',0)==0 else 'БУРУУ ХЭВЭЭР')"
```

`4104` эерэг, `3101` тэг байх ёстой.

---

## 3. Дараагийн алхам (яаралтай биш ч чухал)

Энэ алдаа болон өмнөх тойргийн бүх алдаа нэг л шалтгаантай: **Phase-3-ийн
шинэ endpoint-ууд тестгүй.** 204 endpoint дээр 94 тест байгаа ч зээл, татан
авалт, урьдчилгаа, суутган татвар, ҮХ дахин үнэлгээ зэрэг журнал бичдэг
endpoint-ууд нэг ч тестгүй.

**Дүрэм болгох:** журналын бичилт үүсгэдэг endpoint бүрд нэг тест —
«дуудахад 200 буцаж, гүйлгээ баланст **тухайн дүн зөв дансан дээр** гарч
ирнэ». Загвар:

```python
def test_asset_revaluation_hits_equity(client):
    ...
    before = trial_balance()
    r = client.post(f"/api/companies/{cid}/assets/{aid}/revalue", ...)
    after = trial_balance()
    assert r.status_code == 200
    assert after["4104"] - before.get("4104", 0) == 50_000_00   # өмчид
    assert after.get("3101", 0) == before.get("3101", 0)        # өглөг хөдлөөгүй
```

Ийм 8-10 тест бичвэл цаашид энэ ангиллын алдаа огт гарахгүй болно.

---

## 4. Хойшлуулж болох (өмнөх тойргоос үлдсэн)

- `JournalLine.amount_currency` — мөнгө `Float` хэвээр, `fx.py`-д `SUM()` хийгддэг.
  `amount_currency_minor` (BigInteger) болгох.
- Endpoint дотор `db.commit()` — 42 газар. `get_db()` аль хэдийн commit хийдэг тул
  давхардаж байна; алдаа гарвал rollback бүрэн ажиллахгүй.
- `api.py` — 5,865 мөр, 204 endpoint нэг файлд. Домэйноор нь router болгон салгах.
- **`git init` хараахан хийгээгүй.** Код одоо сайн байдалд байгаа энэ мөчид
  эхний commit хийвэл цаашид юу эвдэрснийг шууд харах боломжтой болно.
