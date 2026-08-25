"""Сверка намерения с корзиной.

Устройство сознательно разделено на две половины:

  1) ИЗВЛЕЧЕНИЕ — превратить слова человека в набор фактов.
     Здесь работает настоящая языковая модель (llm_extract.py).
     Это единственное место, где нужен ИИ.

  2) СВЕРКА — сравнить факты с корзиной и выдать заключение.
     Это обычный детерминированный код. Он воспроизводим, объясним
     и пригоден для приложения к спору: не «так решил ИИ», а
     «человек просил возврат, товар невозвратный».

Разделение важно не для красоты: заключение, которое нельзя перепроверить,
в споре не стоит ничего.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from catalog import find
from llm_extract import ExtractionError, call_model

BLOCK = "стоп"
WARN = "предупреждение"


_SOFTENABLE_FIELDS = {
    "brand", "color", "size_label", "level", "price_preference",
    "explicit_max_cents", "quantity", "needs_refundable", "deadline_days",
}


@dataclass
class IntentFacts:
    brand: str | None = None
    color: str | None = None
    size_label: str | None = None
    level: str | None = None
    price_preference: str | None = None      # cheap | premium
    explicit_max_cents: int | None = None
    quantity: int | None = None
    needs_refundable: bool = False
    deadline_days: int | None = None
    raw: str = ""
    # Имена полей выше, которые человек сам обозначил как необязательные
    # или примерные ("не принципиально", "плюс-минус"). Расхождение по
    # такому полю — предупреждение (WARN), а не блокировка (BLOCK).
    soft: frozenset = field(default_factory=frozenset)


@dataclass
class Finding:
    code: str
    severity: str
    text: str


@dataclass
class Verdict:
    coherent: bool
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]


# ---------------------------------------------------------------------------
# 1. Извлечение — настоящая языковая модель (llm_extract.py)
# ---------------------------------------------------------------------------


def _as_int(value, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ExtractionError(f"Поле {field_name}: ожидалось число, получен bool {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ExtractionError(f"Поле {field_name}: ожидалось целое число, получено {value!r}")


def _as_str(value, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ExtractionError(f"Поле {field_name}: ожидалась строка, получено {value!r}")


def _as_soft_set(value) -> frozenset:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ExtractionError(f"Поле soft_fields: ожидался список, получено {value!r}")
    unknown = [v for v in value if v not in _SOFTENABLE_FIELDS]
    if unknown:
        raise ExtractionError(f"Поле soft_fields: неизвестные имена полей {unknown!r}")
    return frozenset(value)


def extract_intent(request: str, deadline_days: int | None = None) -> IntentFacts:
    """Извлекает факты из просьбы человека настоящей языковой моделью.

    Сверка (ниже в этом файле) не знает и не должна знать, что было внутри
    этой функции — только то, что IntentFacts либо получен, либо не получен
    (тогда наружу летит ExtractionError, а не молчаливые null-поля).
    """
    data = call_model(request)

    level = _as_str(data.get("level"), "level")
    if level == "advanced":
        level = "pro"

    price_preference = _as_str(data.get("price_preference"), "price_preference")

    model_deadline = _as_int(data.get("deadline_days"), "deadline_days")

    return IntentFacts(
        raw=request,
        deadline_days=deadline_days if deadline_days is not None else model_deadline,
        brand=_as_str(data.get("brand"), "brand"),
        color=_as_str(data.get("color"), "color"),
        size_label=_as_str(data.get("size_label"), "size_label"),
        level=level,
        price_preference=price_preference,
        explicit_max_cents=_as_int(data.get("explicit_max_cents"), "explicit_max_cents"),
        quantity=_as_int(data.get("quantity"), "quantity"),
        needs_refundable=bool(data.get("needs_refundable", False)),
        soft=_as_soft_set(data.get("soft_fields")),
    )


# ---------------------------------------------------------------------------
# 2. Сверка — детерминированная
# ---------------------------------------------------------------------------


def _suits(product: dict, facts: IntentFacts) -> bool:
    """Подходит ли товар под названные человеком признаки."""
    if facts.brand and product["brand"].lower() != facts.brand.lower():
        return False
    if facts.color and product["color"] != facts.color and product["category"] != "racket":
        return False
    if facts.size_label and product["size_label"] and product["size_label"] != facts.size_label:
        return False
    if facts.level and product["level"] not in (facts.level, "any"):
        return False
    if facts.needs_refundable and not product["refundable"]:
        return False
    if facts.deadline_days is not None and product["ships_in_days"] > facts.deadline_days:
        return False
    return True


def check_coherence(facts: IntentFacts, purchase, allowed_band, allowlist=()) -> Verdict:
    """purchase: список (артикул, количество). allowed_band: (мин, макс) в центах.

    allowlist — утверждённые артикулы: нужны, чтобы понять, был ли у агента
    более подходящий выбор. Без этого «дорого» не отличить от «дешевле не было».
    """
    def sev(field_name: str) -> str:
        return WARN if field_name in facts.soft else BLOCK

    findings: list[Finding] = []
    band_min, band_max = allowed_band
    items = [(find(sku), qty) for sku, qty in purchase]
    total = sum(p["price_cents"] * q for p, q in items)

    for product, qty in items:
        name = product["name"]

        if facts.brand and product["brand"].lower() != facts.brand.lower():
            findings.append(Finding(
                "brand", sev("brand"),
                f"Человек назвал бренд {facts.brand}, куплен {product['brand']} — {name}.",
            ))

        if facts.color and product["color"] != facts.color and product["category"] != "racket":
            findings.append(Finding(
                "color", sev("color"),
                f"Просили цвет «{facts.color}», у товара «{product['color']}» — {name}.",
            ))

        if facts.size_label and product["size_label"] and product["size_label"] != facts.size_label:
            findings.append(Finding(
                "size", sev("size_label"),
                f"Просили размер {facts.size_label}, куплен {product['size_label']} — {name}.",
            ))

        if facts.level and product["level"] not in (facts.level, "any"):
            findings.append(Finding(
                "level", sev("level"),
                f"Просили уровень «{facts.level}», товар относится к «{product['level']}» — {name}.",
            ))

        if facts.needs_refundable and not product["refundable"]:
            findings.append(Finding(
                "refund", sev("needs_refundable"),
                f"Человек просил возможность вернуть, товар невозвратный — {name}.",
            ))

        if facts.deadline_days is not None and product["ships_in_days"] > facts.deadline_days:
            findings.append(Finding(
                "deadline", sev("deadline_days"),
                f"Нужно за {facts.deadline_days} дн., срок поставки {product['ships_in_days']} дн. — {name}.",
            ))

    # цена: был ли доступен более дешёвый подходящий вариант
    if facts.price_preference == "cheap" and len(items) == 1:
        chosen, _ = items[0]
        cheaper = [
            find(s) for s in allowlist
            if find(s)["category"] == chosen["category"]
            and _suits(find(s), facts)
            and find(s)["price_cents"] < chosen["price_cents"] * 0.85
        ]
        if cheaper:
            best = min(cheaper, key=lambda p: p["price_cents"])
            findings.append(Finding(
                "price", sev("price_preference"),
                f"Просили недорого. Куплено за {chosen['price_cents']/100:.0f}, "
                f"хотя в том же утверждённом списке был «{best['name']}» "
                f"за {best['price_cents']/100:.0f}.",
            ))

    if facts.explicit_max_cents and total > facts.explicit_max_cents:
        findings.append(Finding(
            "explicit-max", sev("explicit_max_cents"),
            f"Человек назвал предел {facts.explicit_max_cents/100:.0f}, "
            f"потрачено {total/100:.0f}.",
        ))

    # количество
    if facts.quantity is not None:
        bought = sum(q for _, q in items)
        if bought > facts.quantity:
            findings.append(Finding(
                "quantity", sev("quantity"),
                f"Просили {facts.quantity} шт., куплено {bought} шт.",
            ))

    return Verdict(coherent=not any(f.severity == BLOCK for f in findings), findings=findings)


# ---------------------------------------------------------------------------
# 3. Режим «до покупки» — та же сверка, другой момент вызова
# ---------------------------------------------------------------------------
#
# check_coherence выше отвечает на вопрос «эта уже сделанная покупка совпадает
# с просьбой?» — годится для аудита и споров постфактум. decide_before_purchase
# задаёт тот же вопрос до того, как агент нажал «купить»: результат — не
# вердикт для спора, а команда «можно продолжать» или «сначала спроси
# человека», с готовой причиной, которую можно ему показать.


ASK = "спросить"
PROCEED = "продолжить"


@dataclass
class Decision:
    action: str                                    # PROCEED | ASK
    reasons: list[Finding] = field(default_factory=list)   # BLOCK-находки — из-за них решили спросить
    notes: list[Finding] = field(default_factory=list)     # WARN-находки — можно продолжать, но человеку видно

    @property
    def question(self) -> str | None:
        """Готовый вопрос человеку, если решили спросить. None, если можно продолжать."""
        if self.action != ASK:
            return None
        bullets = "\n".join(f"  - {f.text}" for f in self.reasons)
        return f"Прежде чем купить, уточните у человека:\n{bullets}\nВсё равно продолжить?"


def decide_before_purchase(facts: IntentFacts, purchase, allowed_band, allowlist=()) -> Decision:
    """Решает, можно ли агенту продолжить покупку без подтверждения человека.

    Ничего нового не проверяет — использует ту же check_coherence на
    предполагаемой покупке, до того как она состоялась. BLOCK-находки
    останавливают агента и требуют подтверждения; WARN-находки не
    останавливают, но остаются видны в notes.
    """
    verdict = check_coherence(facts, purchase, allowed_band, allowlist)
    reasons = [f for f in verdict.findings if f.severity == BLOCK]
    notes = [f for f in verdict.findings if f.severity == WARN]
    return Decision(action=ASK if reasons else PROCEED, reasons=reasons, notes=notes)
