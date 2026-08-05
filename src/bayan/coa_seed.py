"""Дансны төлөвлөгөөний seed — Сангийн яамны ЖДБ-ийн загварын хураангуй хэсэг.

Үе 1-д хэрэглэгдэх гол данснууд. Бүрэн төлөвлөгөө нь seed_full.csv-ээс
(Үе 1-ийн даалгавар) импортлогдоно. parent данс is_postable=False (G4).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from datetime import datetime, timedelta
from .models import Account, Company, NormalSide, Subscription

# (code, name, normal_side, is_postable, parent_code)
SEED = [
    # ------------------ ХӨРӨНГӨ (10-29)
    ("10",   "Мөнгөн хөрөнгө",              "debit",  False, None),
    ("1001", "Байгууллагын касс (төгрөг)",   "debit",  True,  "10"),
    ("1002", "Байгууллагын касс (валют)",    "debit",  True,  "10"),
    
    ("11",   "Банкин дахь мөнгө",           "debit",  False, None),
    ("1101", "Байгууллагын харилцах (төгрөг)", "debit",  True,  "11"),
    ("1102", "Байгууллагын харилцах (валют)",  "debit",  True,  "11"),
    ("1103", "Банкин дахь бусад мөнгө",     "debit",  True,  "11"),
    
    ("12",   "Дансны авлага",               "debit",  False, None),
    ("1201", "Худалдан авагчаас авах авлага","debit",  True,  "12"),
    ("1202", "Охин компаниас авах авлага",  "debit",  True,  "12"),
    ("1203", "НӨАТ-ын авлага",              "debit",  True,  "12"),
    ("1204", "ХХОАТ-ын авлага",             "debit",  True,  "12"),
    ("1205", "Татвар, НДШ-ийн авлага",      "debit",  True,  "12"),
    ("1206", "Ажилтнуудаас авах авлага",     "debit",  True,  "12"),
    ("1207", "Бусад авлага",                "debit",  True,  "12"),
    ("1209", "Найдваргүй авлагын хасалт",    "credit", True,  "12"),
    
    ("14",   "Урьдчилж төлсөн зардал",      "debit",  False, None),
    ("1401", "Урьдчилж төлсөн зардал",      "debit",  True,  "14"),
    ("1402", "Урьдчилж төлсөн даатгал",     "debit",  True,  "14"),
    ("1403", "Урьдчилж төлсөн түрээс",      "debit",  True,  "14"),
    
    ("21",   "Бараа материал",              "debit",  False, None),
    ("2101", "Түүхий эд материал",          "debit",  True,  "21"),
    ("2102", "Туслах материал",            "debit",  True,  "21"),
    ("2103", "Хангамжийн материал",          "debit",  True,  "21"),
    ("2104", "Сав баглаа боодол",           "debit",  True,  "21"),
    ("2105", "Худалдах бараа",              "debit",  True,  "21"),
    ("2145", "Дуусаагүй үйлдвэрлэл",        "debit",  True,  "21"),
    ("2151", "Бэлэн бүтээгдэхүүн",          "debit",  True,  "21"),
    ("2199", "Бараа материалын хорогдол",    "credit", True,  "21"),
    
    ("25",   "Үндсэн хөрөнгө",              "debit",  False, None),
    ("2501", "Барилга байгууламж",          "debit",  True,  "25"),
    ("2502", "Машин тоног төхөөрөмж",       "debit",  True,  "25"),
    ("2503", "Тээврийн хэрэгсэл",           "debit",  True,  "25"),
    ("2504", "Тавилга эд хогшил",           "debit",  True,  "25"),
    ("2505", "Компьютер, техник хэрэгсэл",  "debit",  True,  "25"),
    ("2509", "Хуримтлагдсан элэгдэл",       "credit", True,  "25"),
    
    ("26",   "Биет бус хөрөнгө",            "debit",  False, None),
    ("2601", "Програм хангамж",             "debit",  True,  "26"),
    ("2602", "Патент, лиценз",              "debit",  True,  "26"),
    ("2609", "Хуримтлагдсан элэгдэл (биет бус)", "credit", True,  "26"),
    
    # ------------------ ӨР ТӨЛБӨР (30-39)
    ("31",   "Богино хугацаат өр төлбөр",   "credit", False, None),
    ("3101", "Дансны өглөг",                "credit", True,  "31"),
    ("3102", "Цалингийн өглөг",             "credit", True,  "31"),
    ("3103", "НДШ-ийн өглөг",               "credit", True,  "31"),
    ("3104", "Татварын өглөг",              "credit", True,  "31"),
    ("3105", "НӨАТ-ын өглөг",               "credit", True,  "31"),
    ("3106", "ХХОАТ-ын өглөг",              "credit", True,  "31"),
    ("3107", "Ногдол ашгийн өглөг",          "credit", True,  "31"),
    ("3108", "Урьдчилж орсон орлого",       "credit", True,  "31"),
    ("3109", "Бусад богино хугацаат өглөг",  "credit", True,  "31"),
    
    ("32",   "Богино хугацаат зээл",        "credit", False, None),
    ("3201", "Богино хугацаат зээл",        "credit", True,  "32"),
    ("3202", "Урт хугацаат зээлийн б/х хэсэг", "credit", True,  "32"),
    
    ("35",   "Урт хугацаат өр төлбөр",      "credit", False, None),
    ("3501", "Урт хугацаат зээл",           "credit", True,  "35"),
    ("3502", "Бусад урт хугацаат өглөг",    "credit", True,  "35"),
    
    # ------------------ ЭЗДИЙН ӨМЧ (40-49)
    ("41",   "Эздийн өмч",                  "credit", False, None),
    ("4101", "Хувь нийлүүлсэн хөрөнгө",     "credit", True,  "41"),
    ("4102", "Давуу эрхтэй хувьцаа",        "credit", True,  "41"),
    ("4103", "Нэмж төлөгдсөн капитал",      "credit", True,  "41"),
    ("4104", "Хөрөнгийн дахин үнэлгээний нэмэгдэл", "credit", True,  "41"),
    ("45",   "Хуримтлагдсан ашиг",          "credit", False, None),
    ("4501", "Хуримтлагдсан ашиг",          "credit", True,  "45"),
    ("4502", "Тайлант үеийн цэвэр ашиг/алдагдал", "credit", True,  "45"),
    ("4503", "Тайлант үеийн ногдол ашиг",    "debit",  True,  "45"),
    
    # ------------------ ОРЛОГО (50-59)
    ("51",   "Борлуулалтын орлого",         "credit", False, None),
    ("5101", "Борлуулалтын орлого",         "credit", True,  "51"),
    ("5102", "Үйлчилгээний орлого",         "credit", True,  "51"),
    ("5103", "Бараа борлуулалтын орлого",   "credit", True,  "51"),
    ("5104", "Борлуулалтын буцаалт, хөнгөлөлт", "debit",  True,  "51"),
    
    ("52",   "Бусад орлого",                "credit", False, None),
    ("5201", "Бусад орлого",                "credit", True,  "52"),
    ("5202", "Хүүгийн орлого",              "credit", True,  "52"),
    ("5203", "Ногдол ашгийн орлого",        "credit", True,  "52"),
    ("5204", "Ханшийн зөрүүгийн олз",       "credit", True,  "52"),
    
    # ------------------ БӨӨ & ЗАРДАЛ (60-79)
    ("61",   "Борлуулалтын өртөг",          "debit",  False, None),
    ("6101", "Борлуулсан бүтээгдэхүүний өртөг","debit", True,  "61"),
    ("6102", "Борлуулсан барааны өртөг",    "debit",  True,  "61"),
    ("6103", "Үйлчилгээний өртөг",          "debit",  True,  "61"),
    ("6104", "Бараа материалын өртгийн залруулга", "debit", True, "61"),
    
    ("71",   "Үйл ажиллагааны зардал",      "debit",  False, None),
    ("7101", "Цалингийн зардал",            "debit",  True,  "71"),
    ("7102", "НДШ-ийн зардал",              "debit",  True,  "71"),
    ("7103", "Түрээсийн зардал",            "debit",  True,  "71"),
    ("7104", "Тээвэр, шатахууны зардал",    "debit",  True,  "71"),
    ("7105", "Харилцаа холбооны зардал",    "debit",  True,  "71"),
    ("7106", "Банкны шимтгэлийн зардал",    "debit",  True,  "71"),
    ("7107", "Элэгдлийн зардал",            "debit",  True,  "71"),
    ("7108", "Үйлдвэрлэлийн нэмэгдэл зардал","debit",  True,  "71"),
    ("7109", "Бичиг хэргийн зардал",        "debit",  True,  "71"),
    ("7110", "Сургалт хөгжлийн зардал",     "debit",  True,  "71"),
    ("7111", "Зар сурталчилгааны зардал",   "debit",  True,  "71"),
    ("7112", "Томилолтын зардал",           "debit",  True,  "71"),
    ("7113", "Засвар үйлчилгээний зардал",   "debit",  True,  "71"),
    ("7114", "Цэвэрлэгээ, ариутгалын зардал", "debit",  True,  "71"),
    ("7115", "Шуудан, тээврийн зардал",     "debit",  True,  "71"),
    ("7116", "Даатгалын зардал",            "debit",  True,  "71"),
    ("7117", "Мэргэжлийн үйлчилгээний зардал (аудит, хууль)", "debit", True, "71"),
    ("7118", "Ханшийн зөрүүгийн гарз",      "debit",  True,  "71"),
    ("7119", "Хүүгийн зардал",              "debit",  True,  "71"),
    ("7120", "Татвар, хураамжийн зардал",   "debit",  True,  "71"),
    ("7121", "Найдваргүй авлагын зардал",    "debit",  True,  "71"),
    ("7122", "Ашиглалтын зардал (цахилгаан, дулаан, ус)", "debit", True, "71"),
    ("7199", "Бусад үйл ажиллагааны зардал", "debit",  True,  "71"),
]


# Монголын банкны түгээмэл гүйлгээний утгын үндсэн дүрмүүд —
# компани бүр өөрийнхөө дүрмээр баяжуулна (AI-аас ҮРГЭЛЖ дээгүүр)
# (түлхүүр үг, дансны код[, чиглэл]) — ДАРААЛАЛ НЬ ЧУХАЛ.
# Дүрмүүд жагсаалтын дарааллаар priority авч, эхний таарсан нь хүчинтэй болно.
# Тиймээс тодорхой (нарийн) түлхүүр үгс ерөнхий үгсийн ӨМНӨ байрлана:
# "нийгмийн даатгал" → "даатгал"-аас өмнө, "зээлийн хүү" → "зээл"-ээс өмнө.
# Чиглэл өгвөл тухайн чиглэлийн гүйлгээнд л хэрэглэгдэнэ (орлого/зарлага
# ижил түлхүүр үгтэй байх тохиолдлыг зааглана).
# Монгол хэлэнд нөхцөл залгахад эгшиг гээгддэг (хэрэг→хэргийн, тээвэр→тээврийн,
# суваг→сувгийн) тул түлхүүр үгийг гээгдэхээс өмнөх үндсээр нь богиносгов.
DEFAULT_RULES = [
    # --- Нарийн тодорхой: илүү ерөнхий үгсээс өмнө байх ёстой
    ("нийгмийн даатгал", "3103"),
    ("эрүүл мэндийн даатгал", "3103"),
    ("social insurance", "3103"),
    ("ндш",              "3103"),
    ("зээлийн хүү",      "7119"),
    ("хүүгийн орлого",   "5202", "credit"),
    ("хадгаламжийн хүү", "5202", "credit"),
    ("нөат",             "3105"),
    ("ххоат",            "3106"),
    ("ааноат",           "3104"),
    ("данс хөтөлсний",   "7106"),
    ("гүйлгээний шимтгэл", "7106"),
    ("картын хураамж",   "7106"),

    # --- Цалин, хүний нөөц
    ("цалин",            "3102"),
    ("salary",           "3102"),
    ("тэтгэмж",          "3102"),

    # --- Татвар, хураамж
    ("татвар",           "3104"),
    ("улсын тэмдэгтийн", "7120"),

    # --- Банк
    ("шимтгэл",          "7106"),
    ("хураамж",          "7106"),
    ("sms үйлчилгээ",    "7106"),

    # --- Түрээс, ашиглалт
    ("түрээс",           "7103"),
    ("rent",             "7103"),
    ("цахилгаан",        "7122"),
    ("дулаан",           "7122"),
    ("ус сув",           "7122"),
    ("усуг",             "7122"),
    ("убцтс",            "7122"),
    ("хогны",            "7122"),

    # --- Тээвэр, шатахуун
    ("шатахуун",         "7104"),
    ("бензин",           "7104"),
    ("дизель",           "7104"),
    ("петровис",         "7104"),
    ("шунхлай",          "7104"),
    ("магнай трейд",     "7104"),
    ("такси",            "7104"),
    ("тээв",             "7104"),

    # --- Холбоо
    ("интернэт",         "7105"),
    ("юнител",           "7105"),
    ("унител",           "7105"),
    ("мобиком",          "7105"),
    ("skytel",           "7105"),
    ("g-mobile",         "7105"),
    ("жи мобайл",        "7105"),
    ("холбооны",         "7105"),

    # --- Үйл ажиллагааны бусад зардал
    ("бичиг хэр",        "7109"),
    ("канц",             "7109"),
    ("сургалт",          "7110"),
    ("семинар",          "7110"),
    ("сурталчилгаа",     "7111"),
    ("реклам",           "7111"),
    ("томилолт",         "7112"),
    ("нисэх тийз",       "7112"),
    ("зочид буудал",     "7112"),
    ("засвар",           "7113"),
    ("сэлбэг",           "7113"),
    ("цэвэрлэгээ",       "7114"),
    ("ариутгал",         "7114"),
    ("шуудан",           "7115"),
    ("хүргэлт",          "7115"),
    ("карго",            "7115"),
    ("даатгал",          "7116"),
    ("аудит",            "7117"),
    ("нотариат",         "7117"),
    ("хууль",            "7117"),
    ("зөвлөх үйлчилгээ", "7117"),

    # --- Санхүүжилт
    ("зээл",             "3201"),
    ("хүү",              "7119"),

    # --- Орлого (зөвхөн орлогын гүйлгээнд)
    ("борлуулалт",       "5101", "credit"),
    ("үйлчилгээний орлого", "5102", "credit"),
]


def setup_company(session: Session, name: str, industry: str, is_vat_payer: bool, inventory_method: str = "average", reg_no: str | None = None, director: str | None = None, address: str | None = None) -> Company:
    """Бизнесийн салбар болон НӨАТ-ын төлөвт тохируулан дансны төлөвлөгөө үүсгэнэ."""
    company = Company(name=name, vat_payer=is_vat_payer, inventory_method=inventory_method, reg_no=reg_no, director=director, address=address)
    session.add(company)
    session.flush()

    # Идэвхтэй TRIAL багц үүсгэнэ (SaaS хамгаалалтад)
    sub = Subscription(
        company_id=company.id,
        plan="TRIAL",
        starts_at=datetime.utcnow() - timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=30),
        status="ACTIVE"
    )
    session.add(sub)
    session.flush()

    # Сурвалж данснуудыг шүүнэ
    accounts_to_seed = []
    for code, acc_name, side, postable, parent in SEED:
        # Салбараар шүүх
        if code in ("2145", "2151", "7108") and industry != "manufacturing":
            continue
        if code == "2101" and industry == "service":
            continue
            
        # НӨАТ-ын данс шүүх (3105 НӨАТ-ын өглөг, 1203 НӨАТ-ын авлага)
        if code in ("3105", "1203") and not is_vat_payer:
            continue
            
        accounts_to_seed.append((code, acc_name, side, postable, parent))

    existing_codes = {a[0] for a in accounts_to_seed}
    # Салбарын нэмэлт дансууд
    if industry == "service" and "6102" not in existing_codes:
        accounts_to_seed.append(("6102", "Үйлчилгээний өртөг", "debit", True, "61"))

    # НӨАТ-ын авлагын данс
    if is_vat_payer and "1203" not in existing_codes:
        accounts_to_seed.append(("1203", "НӨАТ-ын авлага", "debit", True, "12"))

    # Кодоор нь эрэмбэлж эцэг дансуудыг түрүүлж үүсгэнэ
    accounts_to_seed.sort(key=lambda x: x[0])

    by_code: dict[str, Account] = {}
    for code, acc_name, side, postable, parent in accounts_to_seed:
        acc = Account(
            company_id=company.id, code=code, name=acc_name,
            normal_side=NormalSide(side), is_postable=postable,
            parent_id=by_code[parent].id if parent and parent in by_code else None,
        )
        session.add(acc)
        session.flush()
        by_code[code] = acc

    from .models import ClassifierRule, Direction
    # Жагсаалтын дараалал = priority. Ингэснээр нарийн түлхүүр үг ерөнхийгөөс
    # ӨМНӨ шалгагдана ("нийгмийн даатгал" → "даатгал"-аас түрүүлнэ).
    for idx, rule in enumerate(DEFAULT_RULES):
        kw, code = rule[0], rule[1]
        direction = Direction(rule[2]) if len(rule) > 2 else None
        if code in by_code:
            session.add(ClassifierRule(company_id=company.id, keyword=kw,
                                       account_code=code, direction=direction,
                                       priority=idx))
    session.flush()
    return company


def seed_company(session: Session, name: str = "Demo Company") -> Company:
    """Шинэ компани + стандарт дансны төлөвлөгөө + үндсэн ангилалтын дүрэм."""
    return setup_company(session, name, industry="manufacturing", is_vat_payer=True)


def add_bank_gl_account(session: Session, company_id: str,
                        bank: str, account_no: str) -> Account:
    """Банкны данс бүрд 1101-ийн доор тусдаа GL данс (BAAZ-ийн загвар)."""
    from sqlalchemy import select
    parent = session.scalar(select(Account).where(
        Account.company_id == company_id, Account.code == "1101"))
    existing = session.scalars(select(Account).where(
        Account.company_id == company_id,
        Account.code.like("1101%"), Account.is_postable == True)).all()  # noqa: E712
    code = f"1101{len(existing) + 1:02d}"
    acc = Account(company_id=company_id, code=code,
                  name=f"Харилцах /{bank.upper()} {account_no}/",
                  normal_side=NormalSide.debit, is_postable=True,
                  parent_id=parent.id if parent else None)
    session.add(acc)
    session.flush()
    return acc


def enable_vat_for_company(session: Session, company_id: str):
    """НӨАТ төлөгч болоход шаардлагатай НӨАТ-ын дансуудыг үүсгэж идэвхжүүлнэ."""
    from sqlalchemy import select
    from .models import NormalSide
    
    # 1. НӨАТ-ын өглөгийн данс (3105)
    has_3105 = session.scalar(
        select(Account).where(Account.company_id == company_id, Account.code == "3105")
    )
    if not has_3105:
        parent_31 = session.scalar(select(Account).where(Account.company_id == company_id, Account.code == "31"))
        if not parent_31:
            parent_31 = Account(company_id=company_id, code="31", name="Татварын өглөг", normal_side=NormalSide.credit, is_postable=False)
            session.add(parent_31)
            session.flush()
        
        vat_payable = Account(company_id=company_id, code="3105", name="НӨАТ-ын өглөг", parent_id=parent_31.id, normal_side=NormalSide.credit, is_postable=True)
        session.add(vat_payable)

    # 2. НӨАТ-ын авлагын данс (1203)
    has_1203 = session.scalar(
        select(Account).where(Account.company_id == company_id, Account.code == "1203")
    )
    if not has_1203:
        parent_12 = session.scalar(select(Account).where(Account.company_id == company_id, Account.code == "12"))
        if not parent_12:
            parent_12 = Account(company_id=company_id, code="12", name="Дансны авлага", normal_side=NormalSide.debit, is_postable=False)
            session.add(parent_12)
            session.flush()
        
        vat_receivable = Account(company_id=company_id, code="1203", name="НӨАТ-ын авлага", parent_id=parent_12.id, normal_side=NormalSide.debit, is_postable=True)
        session.add(vat_receivable)
        
    session.flush()
