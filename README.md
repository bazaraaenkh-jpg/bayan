# Bayan AI

Claude Fable 5-д суурилсан санхүүгийн тайлангийн автомат систем (fino.mn-ийн өрсөлдөгч).

## Баримтууд

| Файл | Агуулга | Онлайн хувилбар |
|---|---|---|
| [docs/01-system-spec-v1.4.html](docs/01-system-spec-v1.4.html) | Системийн даалгавар v1.4 — зах зээлийн харьцуулалт (Fino/BAAZ дотоод судалгаа, 1C, Diamond), 10 модуль, хуулга parse аргачлал, Дуусаагүй үйлдвэрлэлийн (WIP) модуль, 5 үе шат | [Artifact](https://claude.ai/code/artifact/bd74dc19-b43b-4ac0-ab02-b51f5ab2ae42) |
| [docs/02-phase1-gl-parse-spec-v1.0.html](docs/02-phase1-gl-parse-spec-v1.0.html) | Үе 1 техникийн тодорхойлолт — Монголын банкуудын хуулгын форматын каталог (F1-F9), Format Registry (YAML descriptor), validation gate (V1-V6), GL инвариантууд (G1-G7), Fable 5 хэрэглээ, хүлээн авах шалгуур (A1-A6) | [Artifact](https://claude.ai/code/artifact/200f504a-5f7f-4444-b942-70b99d2cf6b2) |
| [docs/03-baaz-structure-research.docx](docs/03-baaz-structure-research.docx) | BAAZ.mn-ийн дотоод бүтцийн судалгаа (side panel Claude-ийн тайлан, 4 модуль route кодтой) | — |

HTML файлуудыг browser-ээр нээж үзнэ (артефакттай ижил агуулга).

## Дараагийн алхам (Үе 1-ийн эхний 2 долоо хоног)

1. 5 банкны (Хаан, Голомт, ХХБ, Хас, Төрийн) бодит хуулгын дээж цуглуулах — xlsx + PDF хоёуланг, `samples/<банк>/` хавтаст
2. Банк бүрийн format descriptor-ийн ноорог бичих (`registry/`)
3. Golden dataset-ийн эхний 10 хуулгыг гараар баталгаажуулах

## Код (Үе 1 — хэрэгжсэн)

```
bayan-ai/
├── docs/                       # даалгавар, тодорхойлолтууд
├── registry/                   # банкны форматын YAML descriptor-ууд (5 банк)
│   ├── khan_ib_xlsx.yaml       #   active — синтетик golden test-тэй
│   └── ...                     #   draft — бодит хуулгаар баталгаажина
├── src/bayan/
│   ├── models.py               # өгөгдлийн загвар (SQLAlchemy, мөнгө = minor bigint)
│   ├── ledger.py               # GL цөм: post/reverse/trial_balance, G1-G4 инвариант
│   ├── coa_seed.py             # Монголын стандарт дансны төлөвлөгөөний seed
│   ├── registry.py             # Format Registry: descriptor ачаалах, fingerprint оноо
│   ├── extract_excel.py        # Зам А: детерминистик Excel задлалт
│   ├── normalize.py            # каноник гүйлгээ + canonical_hash
│   ├── validate.py             # Validation Gate V1-V6 (LLM-гүй)
│   ├── classify.py             # дүрэм → Haiku 4.5 → Fable 5 эскалаци (G5)
│   ├── pipeline.py             # orchestrator + approve → журнал
│   └── cli.py                  # python -m bayan.cli parse <file.xlsx>
├── tests/                      # 28 тест: G/V инвариант, амount parser, e2e pipeline
└── samples/                    # банкны бодит хуулгууд (git-д ОРУУЛАХГҮЙ)
```

### Бүх системийг ажиллуулах (вэб UI)

```powershell
cd D:\mcp\bayan-ai
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m bayan.api
# Browser: http://127.0.0.1:8377
```

Таб: Самбар / Банкны хуулга (drag-drop upload) / Ангилал батлах / Тайлан (СТ-1, СТ-2) / Үйлдвэрлэл (WIP).
Өгөгдөл `bayan.db` (SQLite) файлд хадгалагдана.

### Модулиуд (v0.1 — 39 тест)

| Модуль | Файл | Агуулга |
|---|---|---|
| GL цөм | ledger.py | G1-G4 инвариант, reversal, гүйлгээ баланс, сарын түгжээ |
| Хуулга pipeline | registry/extract_*/normalize/validate/pipeline | 5 банк, xlsx+xls, V1-V6 gate, ХХБ бодит хуулгаар баталгаажсан |
| AI ангилалт | classify.py | дүрэм → Haiku 4.5 → Fable 5, 3 сагс, G5 |
| Харилцагч, нэхэмжлэх | partners.py | борлуулалт/худалдан авалт, НӨАТ, насжилт 0-30/…/120+ |
| Бараа материал | inventory.py | дундаж өртөг, орлого/зарлага, GL атом холболт |
| Үндсэн хөрөнгө | assets.py | шулуун шугам элэгдэл, сарын автомат гүйлт |
| Цалин | salary.py | НДШ/ХХОАТ тохируулгатай, нэгдсэн журнал |
| **Дуусаагүй үйлдвэрлэл** | wip.py | BOM, ажлын захиалга, М+Х+НЗ, хэсэгчилсэн хүлээлгэн өгөлт, СТ-1 тулгалт |
| Тайлан | reports.py | СТ-1 (тэнцлийн invariant-тэй), СТ-2 |
| API + UI | api.py, web/index.html | FastAPI, drag-drop upload, батлах дэлгэц |
| Дотоод шилжүүлэг | internal_transfers.py | Өөрийн данс хоорондын илрүүлэлт + толин тусгал dedupe (цөмд) |
| PDF задлалт (Зам Б) | extract_pdf.py | pdfplumber, давтагдсан толгой хасах, тасарсан утга залгах |
| Vision (Зам В) | vision.py | Fable 5 vision 2-pass, скан хуулга (API key шаардлагатай) |
| НӨАТ | vat.py, ebarimt.py | ТТ-03а гол мөрүүд, ebarimt падаан тулгалт (клиент skeleton) |
| Нэвтрэлт | auth.py | User↔Membership (олон компани), бүртгүүлэх/урих, PBKDF2+HMAC токен, 4 эрх; бүх API endpoint хамгаалалттай, tenant isolation тесттэй |
| PostgreSQL | migrations/001_pg_triggers.sql | G1-G3 DB триггер + RLS (продакшнд) |

### Тест ажиллуулах

```powershell
cd D:\mcp\bayan-ai
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe sqlalchemy pyyaml openpyxl pytest
.venv\Scripts\python.exe -m pytest tests\ -q        # 28 passed
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m bayan.cli registry       # descriptor-уудыг жагсаах
.venv\Scripts\python.exe -m bayan.cli parse хуулга.xlsx   # хуулга шалгах
```

AI ангилалт ажиллуулахад: `pip install anthropic` + `ANTHROPIC_API_KEY` орчны хувьсагч.
API key-гүйгээр дүрмийн давхарга (ClassifierRule) ганцаараа ажиллана.

### Дараагийн код даалгаврууд

- [ ] Зам Б: pdfplumber-д суурилсан текст PDF задлагч (extract_pdf.py)
- [ ] Зам В: Fable 5 vision 2-pass задлагч
- [ ] draft descriptor-уудыг samples/-ийн бодит хуулгаар баталгаажуулж active болгох
- [ ] PostgreSQL migration + G1-G3 DB trigger (одоо сервисийн давхаргад)
- [ ] FastAPI REST давхарга (§5.4-ийн endpoint-ууд)
- [ ] Review UI (хуулга батлах дэлгэц)

**Анхаар:** `samples/` доторх бодит хуулга нь нууц санхүүгийн мэдээлэл тул `.gitignore`-д орсон.
