"""Сверка намерения с корзиной.

Устройство сознательно разделено на две половины:

  1) ИЗВЛЕЧЕНИЕ — превратить слова человека в набор фактов.
     Здесь это ЗАГЛУШКА на ключевых словах. В настоящем продукте на этом
     месте языковая модель. Это единственное место, где нужен ИИ.

  2) СВЕРКА — сравнить факты с корзиной и выдать заключение.
     Это обычный детерминированный код. Он воспроизводим, объясним
     и пригоден для приложения к спору: не «так решил ИИ», а
     «человек просил возврат, товар невозвратный».

Разделение важно не для красоты: заключение, которое нельзя перепроверить,
в споре не стоит ничего.
"""

from dataclasses import dataclass, field
import re

from catalog import find

BLOCK = "стоп"
WARN = "предупреждение"


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
# 1. Извлечение — ЗАГЛУШКА
# ---------------------------------------------------------------------------

_BRANDS = ["Babolat", "HEAD", "Wilson", "ASICS"]
_COLORS = {
    "чёрн": "black", "черн": "black", "бел": "white",
    "син": "blue", "жёлт": "yellow", "желт": "yellow",
}
_NUMERALS = {
    "пару": 2, "пара": 2, "две": 2, "два": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6,
}
_CHEAP = ["недорог", "дешев", "дешёв", "бюджет", "подешевле", "не дорог"]
_PREMIUM = ["професс", "топов", "лучш", "флагман"]
_BEGINNER = ["новичк", "начинающ", "только начина", "начина"]
_REFUND = ["вернуть", "возврат", "не подойд", "обмен"]
_SIZES = ["42", "43", "44", "45", "4 1/4", "4 3/8"]
_WEEKDAY_DEADLINE = ["к субботе", "к пятнице", "к выходным", "в выходные"]


def extract_intent(request: str, deadline_days: int | None = None) -> IntentFacts:
    """ЗАГЛУШКА. В продукте здесь работает языковая модель.

    Правила на ключевых словах достаточны, чтобы показать устройство,
    и заведомо недостаточны для реальной эксплуатации.
    """
    low = request.lower()
    f = IntentFacts(raw=request, deadline_days=deadline_days)

    for b in _BRANDS:
        if b.lower() in low:
            f.brand = b
            break
    for key, val in _COLORS.items():
        if key in low:
            f.color = val
            break
    for s in _SIZES:
        if re.search(rf"\b{re.escape(s)}\b", request) or f"{s} размер" in low:
            f.size_label = s
            break
    if "сорок втор" in low:
        f.size_label = "42"

    if any(k in low for k in _CHEAP):
        f.price_preference = "cheap"
    elif any(k in low for k in _PREMIUM):
        f.price_preference = "premium"

    if any(k in low for k in _BEGINNER):
        f.level = "beginner"

    m = re.search(r"до\s+(\d[\d\s]{2,})", request)
    if m:
        f.explicit_max_cents = int(m.group(1).replace(" ", "")) * 100

    for word, n in _NUMERALS.items():
        if re.search(rf"\b{word}\b", low):
            f.quantity = n
            break

    if any(k in low for k in _REFUND):
        f.needs_refundable = True

    if f.deadline_days is None and any(k in low for k in _WEEKDAY_DEADLINE):
        f.deadline_days = 5

    return f


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
    findings: list[Finding] = []
    band_min, band_max = allowed_band
    items = [(find(sku), qty) for sku, qty in purchase]
    total = sum(p["price_cents"] * q for p, q in items)

    for product, qty in items:
        name = product["name"]

        if facts.brand and product["brand"].lower() != facts.brand.lower():
            findings.append(Finding(
                "brand", BLOCK,
                f"Человек назвал бренд {facts.brand}, куплен {product['brand']} — {name}.",
            ))

        if facts.color and product["color"] != facts.color and product["category"] != "racket":
            findings.append(Finding(
                "color", BLOCK,
                f"Просили цвет «{facts.color}», у товара «{product['color']}» — {name}.",
            ))

        if facts.size_label and product["size_label"] and product["size_label"] != facts.size_label:
            findings.append(Finding(
                "size", BLOCK,
                f"Просили размер {facts.size_label}, куплен {product['size_label']} — {name}.",
            ))

        if facts.level and product["level"] not in (facts.level, "any"):
            findings.append(Finding(
                "level", BLOCK,
                f"Просили уровень «{facts.level}», товар относится к «{product['level']}» — {name}.",
            ))

        if facts.needs_refundable and not product["refundable"]:
            findings.append(Finding(
                "refund", BLOCK,
                f"Человек просил возможность вернуть, товар невозвратный — {name}.",
            ))

        if facts.deadline_days is not None and product["ships_in_days"] > facts.deadline_days:
            findings.append(Finding(
                "deadline", BLOCK,
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
                "price", BLOCK,
                f"Просили недорого. Куплено за {chosen['price_cents']/100:.0f}, "
                f"хотя в том же утверждённом списке был «{best['name']}» "
                f"за {best['price_cents']/100:.0f}.",
            ))

    if facts.explicit_max_cents and total > facts.explicit_max_cents:
        findings.append(Finding(
            "explicit-max", BLOCK,
            f"Человек назвал предел {facts.explicit_max_cents/100:.0f}, "
            f"потрачено {total/100:.0f}.",
        ))

    # количество
    if facts.quantity is not None:
        bought = sum(q for _, q in items)
        if bought > facts.quantity:
            findings.append(Finding(
                "quantity", BLOCK,
                f"Просили {facts.quantity} шт., куплено {bought} шт.",
            ))

    return Verdict(coherent=not any(f.severity == BLOCK for f in findings), findings=findings)
