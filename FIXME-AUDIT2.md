# Аудит 2-р тойргийн засварын даалгавар

> Энэ файлыг бүрэн уншиж, доорх 6 бүлгийг **бүгдийг** нь зас.
> Дуусмагц §7-гийн шалгалтыг заавал ажиллуулж, бүх тест ногоон болсныг батал.
>
> **ЧУХАЛ:** `tests/test_audit_idor.py` болон `tests/test_audit_idor_round2.py`
> бол аудитын нотлох баримт. Эдгээрийг **устгаж, өөрчилж, сулруулж болохгүй**.
> Тэдгээр тест ногоон болох ёстой — кодыг засаж ногоон болго, тестийг биш.

---

## 1. Байхгүй функц: `ledger.post_journal_entry` → `ledger.post_entry`

`bayan/ledger.py`-д `post_journal_entry` гэсэн функц **байхгүй**. 6 газар дуудагдаж
байгаа бөгөөд дуудагдмагц `AttributeError` → 500 өгнө.

**Байршил:** `src/bayan/api.py` мөр `4303, 4339, 4573, 4816, 4848, 4873`

**Зөв гарын үсэг:**

```python
ledger.post_entry(
    session=db,
    company_id=company_id,
    entry_date=<date объект>,
    lines=[ledger.LineInput(...), ...],   # ← LineInput жагсаалт, dict БИШ
    source_type=SourceType.manual,
    memo="тайлбар",                        # ← 'description' биш 'memo'
    actor_id=ctx["uid"],
)
```

**`LineInput` нь `account_code` (текст код) авдаг, `account_id` авдаггүй:**

```python
# БУРУУ (одоогийн код):
lines=[{"account_id": acc_1401.id, "debit_minor": amt, "credit_minor": 0}]

# ЗӨВ:
lines=[ledger.LineInput("1401", debit_minor=amt, description="...")]
```

`LineInput` бүрэн гарын үсэг:
`LineInput(account_code, debit_minor=0, credit_minor=0, description=None, counterparty_id=None, cost_center_id=None, currency=None, amount_currency=None)`

---

## 2. Байхгүй функц: `auth.check_company_membership`

`bayan/auth.py`-д ийм функц **байхгүй**. 4 газар дуудагдаж байна.

**Байршил:** `src/bayan/api.py` мөр `483, 1594, 1920, 2026`

### ⚠️ Мөрийг зүгээр УСТГАЖ БОЛОХГҮЙ

Эдгээр 4 endpoint-д `company_guard` **байхгүй** — эрхийн шалгалт нь зөвхөн энэ
байхгүй функцээр хийгдэж байна. Мөрийг устгавал **нэвтэрсэн ямар ч хэрэглэгч
хэн бусдын компанид журналын бичилт хийж чадах** болно.

**Зөв засвар — dependency болгож солих:**

```python
# БУРУУ (одоогийн):
@app.post("/api/companies/{company_id}/entries")
def create_manual_entry(company_id: str, body: JournalEntryIn,
                        user: dict = Depends(current_user),
                        db: Session = Depends(get_db)):
    auth.check_company_membership(db, user["uid"], company_id, "post")

# ЗӨВ:
@app.post("/api/companies/{company_id}/entries")
def create_manual_entry(company_id: str, body: JournalEntryIn,
                        ctx: dict = Depends(company_guard("post")),
                        db: Session = Depends(get_db)):
    # эрх аль хэдийн шалгагдсан; хэрэглэгчийн id нь ctx["uid"]
```

Дотор нь `user["uid"]` ашиглаж байсан газруудыг `ctx["uid"]` болгож солино.

---

## 3. Дансны төлөвлөгөөнд байхгүй кодууд

Дараах кодууд `coa_seed.py`-ийн `SEED`-д **байхгүй**. Зөв кодоор солино
(шинэ данс нэмэхгүй — доорх бүгд төлөвлөгөөнд аль хэдийн байгаа):

| Одоогийн | Хаана | Юунд зориулсан | **Солих код** |
|---|---|---|---|
| `1021` | api.py:1934, 4299, 4568 | Харилцах данс (бэлэн бус) | **`1101`** Байгууллагын харилцах |
| `1501` | api.py:2078 | Татан авсан бараа | **`2105`** Худалдах бараа |
| `5401` | api.py:1657 | Ханшийн тэгшитгэлийн олз | **`5204`** Ханшийн зөрүүгийн олз |
| `6401` | api.py:1660 | Ханшийн тэгшитгэлийн гарз | **`7118`** Ханшийн зөрүүгийн гарз |
| `6105` | api.py:4812 | ҮХ дахин үнэлгээний олз | **`5204`** Ханшийн зөрүүгийн олз |
| `7401` | **assets.py:132** | ҮХ хасалтын гарз | **`7199`** Бусад зардал |

---

## 4. Утга нь буруу дансууд (нягтлан бодох алдаа)

Доорх кодууд төлөвлөгөөнд **байгаа** боловч огт өөр утгатай данс руу мөнгө
бичиж байна. Санхүүгийн тайлан буруу гарна.

**`api.py:1931-1934` — зээлийн эргэн төлөлт:**

| Одоогийн | Тухайн дансны бодит нэр | **Байх ёстой** |
|---|---|---|
| `2101` | Түүхий эд материал | **`3201`** Богино хугацаат зээл |
| `7101` | Цалингийн зардал | **`7119`** Хүүгийн зардал |
| `1021` | *(байхгүй)* | **`1101`** Байгууллагын харилцах |

Зөв бичилт: `Дт 3201 (үндсэн) + Дт 7119 (хүү) / Кт 1101 (нийт төлбөр)`

**`api.py:2082` — татан авалтын өглөг:**

| Одоогийн | Бодит нэр | **Байх ёстой** |
|---|---|---|
| `2102` | Туслах материал | **`3101`** Дансны өглөг |

**`api.py:4569` — суутган татварын өглөг:**

| Одоогийн | Бодит нэр | **Байх ёстой** |
|---|---|---|
| `2103` | Хангамжийн материал | **`3104`** Татварын өглөг |

**`api.py:4813` — ханшийн гарз:**

| Одоогийн | Бодит нэр | **Байх ёстой** |
|---|---|---|
| `7105` | Харилцаа холбооны зардал | **`7118`** Ханшийн зөрүүгийн гарз |

---

## 5. Чимээгүй алгасалтыг хориглох (хамгийн чухал)

Одоо олон газар ийм хэв маяг байна:

```python
acc_a = _get_account_by_code(db, company_id, "1401")
acc_b = _get_account_by_code(db, company_id, "1021")
if acc_a and acc_b:              # ← данс олдохгүй бол ЧИМЭЭГҮЙ алгасна
    ledger.post_journal_entry(...)
# ...
return {"id": adv.id, "status": "pending"}   # ← 200 OK буцаана
```

Данс олдохгүй бол журналын бичилт хийгдэхгүй мөртлөө endpoint **амжилттай**
гэж хариулна. Нябо мөнгөө бүртгэгдсэн гэж итгээд цаашаа явна — санхүүгийн
системд хамгийн муу төрлийн алдаа. Энэ нь системийн **G6 инвариант**
(«дэд модулийн баримт журналаа атомоор үүсгэнэ») -ыг зөрчиж байна.

**Зас:** туслах функц нэмээд бүх `if acc_a and acc_b:` хэв маягийг солино:

```python
def _require_account(db: Session, company_id: str, code: str) -> Account:
    """Данс байхгүй бол чимээгүй өнгөрөхгүй — 422 алдаа шидэнэ."""
    acc = _get_account_by_code(db, company_id, code)
    if not acc:
        raise HTTPException(422, f"Дансны төлөвлөгөөнд {code} данс алга")
    return acc
```

Ингэснээр «журнал бичигдээгүй мөртлөө амжилттай» гэсэн төлөв оршихгүй болно.

---

## 6. Үлдсэн IDOR — `get_owned()` хэрэглэх

`get_owned()` туслах функц зөв бичигдсэн боловч 3 газарт л хэрэглэгдсэн.
Дараах 2 газарт `db.get(Model, id_from_url)` хэвээр байна:

```python
# src/bayan/api.py — /purchase-orders/{po_id}/receive
po = db.get(PurchaseOrder, po_id)          # → get_owned(db, PurchaseOrder, po_id, company_id)

# src/bayan/api.py — /employee-advances/{advance_id}/clear
adv = db.get(EmployeeAdvance, advance_id)  # → get_owned(db, EmployeeAdvance, advance_id, company_id)
```

`get_owned()` нь өөрөө 404/403 шиддэг тул дараагийн `if not po: raise ...`
мөрүүдийг хасаж болно.

**Нэмээд:** URL эсвэл body-оос ID авдаг бусад бүх endpoint-ыг шалгаж,
`db.get(...)` шууд дуудаж байвал бүгдийг `get_owned()` болго.

---

## 7. Шалгалт (заавал ажиллуулах)

```bash
cd D:\mcp\bayan-ai
.venv\Scripts\python.exe -m pytest tests/ -q
```

**Хүлээгдэж буй үр дүн: бүх тест ногоон, үүнд:**

- `tests/test_audit_idor.py` — 2 тест (одоо ногоон, хэвээр байх ёстой)
- `tests/test_audit_idor_round2.py` — 2 тест (**одоо улаан → ногоон болох ёстой**)

Дараа нь байхгүй функц/данс дахин үлдээгүйг шалга:

```bash
.venv\Scripts\python.exe -X utf8 -c "import sys; sys.path.insert(0,'src'); \
import re, pathlib; from bayan import ledger, auth; from bayan.coa_seed import SEED; \
t = pathlib.Path('src/bayan/api.py').read_text(encoding='utf-8'); \
codes = {s[0] for s in SEED}; \
bad_fn = re.findall(r'(?:ledger|auth)\.(\w+)\(', t); \
print('байхгүй функц:', sorted({f for f in bad_fn if f not in dir(ledger)+dir(auth)})); \
print('байхгүй данс:', sorted({m for m in re.findall(r'LineInput\(\s*[\"\'](\d{4,})', t) if m not in codes}))"
```

Хоёулаа хоосон жагсаалт буцаах ёстой.

---

## 8. Нэмэлт (боломжтой бол)

- `POST /entries`, `/fx-revalue`, `/loans/.../post-payment`, `/employee-advances`,
  `/withholding-taxes`, `/assets/{id}/revalue`, `/assets/{id}/dispose` — эдгээр
  endpoint бүрд дор хаяж **нэг тест**: дуудахад 200 буцаж, гүйлгээ баланст
  тухайн дүн зөв данс дээр гарч ирэхийг шалгах. Эдгээр тестгүй байсан учраас
  л дээрх бүх алдаа анзаарагдаагүй.
- `git init` + эхний commit (одоо хувилбарын хяналт огт байхгүй).
