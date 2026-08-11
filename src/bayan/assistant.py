"""AI туслах — удирдлагын асуулт-хариулт (§5.3 L3).

Гол зарчим: **LLM хэзээ ч тоо зохиохгүй.** Асуултыг бүртгэгдсэн метрикийн
дуудлага болгож хөрвүүлээд, тоог metrics.py ledger-ээс гаргана. LLM зөвхөн
гарсан тоог үгээр тайлбарлана. Каталогт байхгүй асуултад таамаглахгүй,
«мэдэхгүй» гэж хэлнэ.

Дөрвөн хатуу дүрэм кодоор хэрэгжсэн:
  1. Метрик олдохгүй бол хариулт БАЙХГҮЙ (status="unknown") — таамаглахгүй
  2. Эрх хүрэхгүй бол тоо огт тооцоологдохгүй (status="denied") — няравт
     цалингийн дүн ХЭЗЭЭ Ч буцахгүй
  3. Асуулт бүр `assistant_query`-д бичигдэнэ — хэн, юу асуусан, ямар
     метрик дуудагдсан, юу буцсан
  4. Өгөгдөл гадагш явах эсэхийг компани бүр тохируулна (egress):
     off = LLM огт дуудахгүй · aggregate = зөвхөн нийт дүн явна (өгөгдмөл)
     · full = задаргаа (ажилтны нэр гэх мэт) ч явна

ANTHROPIC_API_KEY байхгүй бол дүрмийн чиглүүлэгч ба загварчилсан хариулт
ганцаараа ажиллана — систем AI-гүйгээр ч бүрэн ажиллана.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import metrics
from .audit import monotonic_utcnow
from .models import Base

ROUTER_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL = "claude-fable-5"

EGRESS_MODES = ("off", "aggregate", "full")
DEFAULT_EGRESS = "aggregate"

UNKNOWN = "unknown"


def _uuid() -> str:
    return str(uuid.uuid4())


class AssistantQuery(Base):
    """AI туслахаас асуусан асуулт бүрийн бүртгэл (§5.3 хатуу дүрэм)."""
    __tablename__ = "assistant_query"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(24))
    asked_at: Mapped[datetime] = mapped_column(DateTime, default=monotonic_utcnow)
    question: Mapped[str] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(String(32))
    period_label: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))      # answered|unknown|denied
    route_source: Mapped[str] = mapped_column(String(16))  # rule|haiku
    value_minor: Mapped[int | None] = mapped_column(Integer)
    answer: Mapped[str | None] = mapped_column(Text)


class AssistantSetting(Base):
    """Компанийн AI тохиргоо — өгөгдөл гадагш явах хэмжээ ил байна."""
    __tablename__ = "assistant_setting"

    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"),
                                            primary_key=True)
    egress: Mapped[str] = mapped_column(String(16), default=DEFAULT_EGRESS)


# ------------------------------------------------------------------ тохиргоо

def get_egress(session: Session, company_id: str) -> str:
    row = session.get(AssistantSetting, company_id)
    return row.egress if row else DEFAULT_EGRESS


def set_egress(session: Session, company_id: str, mode: str) -> str:
    if mode not in EGRESS_MODES:
        raise ValueError(f"egress нь {EGRESS_MODES} байх ёстой")
    row = session.get(AssistantSetting, company_id)
    if row is None:
        row = AssistantSetting(company_id=company_id, egress=mode)
        session.add(row)
    else:
        row.egress = mode
    session.flush()
    return mode


# ---------------------------------------------------------------- чиглүүлэлт

def route_by_rules(question: str) -> str | None:
    """Түлхүүр үгээр метрик сонгоно (LLM байхгүй үеийн зам).

    Хамгийн УРТ таарсан түлхүүр ялна — «татварын өглөг» нь «өглөг»-өөс
    урт тул зөв метрик рүү очно."""
    text = (question or "").lower()
    best: tuple[int, str | None] = (0, None)
    for m in metrics.CATALOG.values():
        for kw in m.keywords:
            if kw in text and len(kw) > best[0]:
                best = (len(kw), m.name)
    return best[1]


def _router_tool(names: list[str]) -> dict:
    return {
        "name": "select_metric",
        "description": "Хэрэглэгчийн асуултад тохирох метрикийг сонгоно",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": names + [UNKNOWN],
                    "description": "Тохирох метрик. Аль нь ч тохирохгүй бол "
                                   f"'{UNKNOWN}' гэж хариул — ХЭЗЭЭ Ч таамаглаж "
                                   "ойролцоо метрик сонгож болохгүй.",
                },
            },
            "required": ["metric"],
        },
    }


def route_by_llm(question: str) -> str | None:
    """Асуултыг каталогийн метрик рүү буулгана. Схемийн enum нь LLM-ийг
    каталогоос гадуур гарахыг техникийн түвшинд хориглоно."""
    import anthropic

    names = list(metrics.CATALOG)
    catalog_text = "\n".join(
        f"- {m.name}: {m.title} — {m.description}" for m in metrics.CATALOG.values())
    msg = anthropic.Anthropic().messages.create(
        model=ROUTER_MODEL,
        max_tokens=256,
        system=[{
            "type": "text",
            "text": "Чи Монголын нягтлан бодох бүртгэлийн системийн асуулт "
                    "чиглүүлэгч. Хэрэглэгчийн асуултад тохирох метрикийг "
                    "сонго. Тохирохгүй бол " + UNKNOWN + " гэж хариул.\n\n"
                    "Метрикийн каталог:\n" + catalog_text,
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[_router_tool(names)],
        tool_choice={"type": "tool", "name": "select_metric"},
        messages=[{"role": "user", "content": question}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            name = block.input.get("metric")
            return None if name == UNKNOWN else name
    return None


# ------------------------------------------------------------------ тайлбар

def fmt_mnt(minor: int) -> str:
    return f"{minor / 100:,.0f}₮"


def _accuracy_sentence(cmp_: dict) -> str:
    """§5.2-ийн заавал биелэх дүрэм: таамаг нарийвчлалгүйгээр гарахгүй."""
    mape = cmp_.get("mape_pct")
    n = cmp_.get("backtest_points") or 0
    if mape is None or not n:
        return (" Энэ таамгийн нарийвчлалыг хэмжих хангалттай түүх алга — "
                "болгоомжтой хандана уу.")
    verdict = "шийдвэрт ашиглаж болно" if mape < 30 else "зөвхөн чиг хандлага харах зорилгоор"
    return (f" Нарийвчлал: сүүлийн {n} үеэр шалгахад дундаж алдаа "
            f"{mape}% — {verdict}.")


def _template_answer(result: metrics.MetricResult) -> str:
    """LLM-гүй үеийн хариулт — тоо нь адилхан, зөвхөн үг нь энгийн."""
    cmp_all = result.compare or {}

    # Таамаглал — дүн + нарийвчлал + арга
    if "mape_pct" in cmp_all and result.metric != "cash_forecast":
        text = (f"{result.period_phrase} {result.title.lower()} "
                f"{fmt_mnt(result.value_minor)}.")
        if cmp_all.get("low_minor") is not None:
            text += (f" Магадлалт хүрээ {fmt_mnt(cmp_all['low_minor'])} – "
                     f"{fmt_mnt(cmp_all['high_minor'])}.")
        return text + _accuracy_sentence(cmp_all) + f" Арга: {result.source}."

    if result.metric == "cash_forecast":
        text = (f"{result.period_phrase} мөнгөн үлдэгдлийн хамгийн доод цэг "
                f"{fmt_mnt(result.value_minor)} "
                f"({cmp_all.get('lowest_week')}-р долоо хоногт).")
        if cmp_all.get("goes_negative"):
            text += " АНХААР: үлдэгдэл сөрөг рүү орж байна."
        return text + _accuracy_sentence(cmp_all) + f" Таамаглалын үндэслэл: {result.source}."

    if result.metric == "balance_check":
        c = cmp_all
        if c.get("balanced"):
            return (f"{result.period_phrase} дэвтэр ТЭНЦСЭН байна. "
                    f"{c.get('entry_count', 0)} бичилтийн нийт дебит "
                    f"{fmt_mnt(c.get('total_debit_minor', 0))} = нийт кредит "
                    f"{fmt_mnt(c.get('total_credit_minor', 0))}.")
        text = (f"{result.period_phrase} дэвтэр ТЭНЦЭХГҮЙ байна — зөрүү "
                f"{fmt_mnt(result.value_minor)}.")
        for chk in c.get("checks", []):
            if not chk["ok"]:
                text += f"\n• {chk['title']}: {chk['detail']}"
        if c.get("unbalanced_count"):
            text += f"\nТэнцэхгүй {c['unbalanced_count']} бичилт доор жагсав."
        return text

    if result.metric == "anomalies":
        by = cmp_all.get("by_severity", {})
        if not result.value_minor:
            return (f"{result.period_phrase} шалгах шаардлагатай гажилт "
                    "илрээгүй. Шалгасан зүйлс: " + result.source + ".")
        text = (f"{result.period_phrase} {result.value_minor} гажилт илэрлээ "
                f"(ноцтой {by.get('high', 0)}, дунд {by.get('medium', 0)}, "
                f"сул {by.get('low', 0)}).")
        for d in cmp_all.get("details", [])[:3]:
            text += f"\n• {d}"
        return text

    if result.unit == "count":
        return (f"{result.period_phrase} {result.title.lower()} "
                f"{result.value_minor} байна. Эх сурвалж: {result.source}.")

    text = (f"{result.period_phrase} {result.title.lower()} "
            f"{fmt_mnt(result.value_minor)} байна.")

    cmp_ = result.compare or {}
    prior = cmp_.get("prior_value_minor")
    if prior:
        diff = result.value_minor - prior
        pct = abs(diff) / abs(prior) * 100
        word = "өссөн" if diff > 0 else "буурсан"
        text += (f" {cmp_.get('prior_label', 'өмнөх үе')}-тэй харьцуулахад "
                 f"{fmt_mnt(abs(diff))} буюу {pct:.1f}% {word}.")

    # Задаргаа нь дансны кодоор эрэмбэлэгддэг тул эхний мөр нь хамгийн том
    # гэсэн үг БИШ — үнэхээр хамгийн томыг нь олж хэлнэ.
    if result.rows:
        top = max(result.rows, key=lambda r: r.get("amount_minor", 0))
        if top.get("amount_minor"):
            text += (f" Хамгийн том хэсэг нь {top.get('name') or top.get('code')} "
                     f"— {fmt_mnt(top['amount_minor'])}.")

    return text + f" Эх сурвалж: {result.source}."


_WRITER_SYSTEM = """Чи Монголын нягтлан бодох бүртгэлийн туслах. Чамд ledger-ээс
гарсан ГОТОВ тоо өгөгдөнө. Даалгавар: тэр тоог 1-3 өгүүлбэрээр монголоор
тайлбарлах.

ХАТУУ ХОРИГ:
- Өгөгдсөнөөс ӨӨР тоо бичихгүй. Тооцоолол хийхгүй, хувь хэмжээ зохиохгүй.
- Өгөгдөөгүй үзүүлэлтийн талаар таамаглахгүй.
- Зөвлөгөө өгөх бол тооноос үүдэлтэй, болгоомжтой, нэг өгүүлбэрээр."""


def _write_answer(result: metrics.MetricResult, question: str,
                  egress: str) -> str:
    """LLM-ээр үгээр тайлбарлуулна. Тоо нь аль хэдийн бэлэн — LLM зөвхөн үг."""
    import anthropic

    facts = [
        f"Метрик: {result.title}",
        f"Хугацаа: {result.period_label}",
        f"Утга: {fmt_mnt(result.value_minor) if result.unit == 'MNT' else result.value_minor}",
        f"Эх сурвалж: {result.source}",
    ]
    cmp_ = result.compare or {}
    if cmp_.get("prior_value_minor"):
        facts.append(f"Өмнөх үе: {fmt_mnt(cmp_['prior_value_minor'])}")

    # aggregate горимд задаргаа (ажилтны нэр, харилцагч) гадагш ГАРАХГҮЙ
    if egress == "full" and result.rows:
        facts.append("Задаргаа: " + "; ".join(
            f"{r.get('name') or r.get('code')} {fmt_mnt(r.get('amount_minor', 0))}"
            for r in result.rows[:10]))

    msg = anthropic.Anthropic().messages.create(
        model=WRITER_MODEL,
        max_tokens=512,
        system=[{"type": "text", "text": _WRITER_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content":
                   f"Асуулт: {question}\n\nLedger-ээс гарсан тоо:\n"
                   + "\n".join(facts)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# ------------------------------------------------------------------ гол урсгал

def _unknown_text(role: str) -> str:
    titles = ", ".join(m.title.lower() for m in metrics.catalog_for(role))
    return ("Би энэ тайланг мэдэхгүй. Тоо таамаглахаас татгалзаж байна.\n\n"
            f"Одоогоор хариулж чадах зүйлс: {titles}.")


def ask(session: Session, company_id: str, role: str, question: str,
        actor_id: str | None = None, use_ai: bool | None = None,
        today: date | None = None) -> dict:
    """Асуултад хариулна. Тоо ЗӨВХӨН метрикийн каталогоос гарна."""
    egress = get_egress(session, company_id)
    if use_ai is None:
        use_ai = bool(os.environ.get("ANTHROPIC_API_KEY")) and egress != "off"

    period = metrics.parse_period(question, today)

    name = None
    route_source = "rule"
    if use_ai:
        try:
            name = route_by_llm(question)
            route_source = "haiku"
        except Exception:
            name = None           # AI унасан ч систем үргэлжилнэ
            route_source = "rule"
    if name is None and route_source == "rule":
        name = route_by_rules(question)

    def _log(status: str, answer: str, metric_name: str | None,
             value: int | None) -> dict:
        session.add(AssistantQuery(
            company_id=company_id, actor_id=actor_id, role=role,
            question=question[:2000], metric=metric_name,
            period_label=period.label, status=status, route_source=route_source,
            value_minor=value, answer=answer[:2000]))
        session.flush()
        return {"status": status, "answer": answer, "metric": metric_name,
                "period": period.label, "route_source": route_source}

    # 1. Каталогт байхгүй → таамаглахгүй
    if name is None or name not in metrics.CATALOG:
        return _log(UNKNOWN, _unknown_text(role), None, None)

    metric = metrics.CATALOG[name]

    # 2. Эрх хүрэхгүй → тоо ОГТ тооцоологдохгүй
    if not metrics.allowed(metric, role):
        return _log("denied",
                    f"«{metric.title}» мэдээллийг харах эрх танд алга. "
                    "Шаардлагатай бол компанийн эзэн эсвэл ерөнхий нягтлангаас "
                    "эрх хүсээрэй.", name, None)

    # 3. Тоог ledger-ээс гаргана (задаргаа нь эрхээр шүүгдэнэ)
    from .forecast import NotEnoughHistory
    try:
        result = metrics.compute(session, company_id, name, period, role)
    except NotEnoughHistory as e:
        # Түүх хангалтгүй бол ТААМАГЛАХГҮЙ — шалтгааныг нь хэлнэ
        return _log("unavailable", str(e), name, None)

    # 4. LLM зөвхөн ҮГЭЭР тайлбарлана
    if use_ai:
        try:
            text = _write_answer(result, question, egress)
        except Exception:
            text = _template_answer(result)
    else:
        text = _template_answer(result)

    out = _log("answered", text, name, result.value_minor)
    out["data"] = result.to_dict()
    return out


def history(session: Session, company_id: str, limit: int = 50) -> list[dict]:
    rows = session.scalars(
        select(AssistantQuery)
        .where(AssistantQuery.company_id == company_id)
        .order_by(AssistantQuery.asked_at.desc()).limit(limit)).all()
    return [{
        "id": r.id, "asked_at": r.asked_at.isoformat() if r.asked_at else None,
        "role": r.role, "question": r.question, "metric": r.metric,
        "period": r.period_label, "status": r.status,
        "route_source": r.route_source, "value_minor": r.value_minor,
        "answer": r.answer,
    } for r in rows]
