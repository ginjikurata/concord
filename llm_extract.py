"""Вызов языковой модели для превращения слов человека в факты.

Единственное место в проекте, где вызывается ИИ. Всё остальное в
coherence.py — обычный детерминированный код, который об этом файле
ничего не знает: он получает готовый набор фактов и с ним работает.

Если модель не смогла вернуть корректный ответ — поднимается
ExtractionError с сырым текстом ответа, чтобы это было видно на прогоне,
а не пряталось за null-полями.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

_ENV_LOADED = False

# .env лежит рядом с этим файлом. Искать его относительно текущей папки
# нельзя: скрипт, запущенный не из корня проекта, молча не нашёл бы ключ
# и упал бы с «LLM_API_KEY не задан», хотя ключ на месте.
_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env_file(path: str | Path = _ENV_PATH) -> None:
    global _ENV_LOADED
    if _ENV_LOADED or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    _ENV_LOADED = True


_load_env_file()

API_KEY = os.environ.get("LLM_API_KEY")
MODEL = os.environ.get("LLM_MODEL", "gemini-flash-lite-latest")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

SYSTEM_PROMPT = """Ты извлекаешь факты о покупке из просьбы человека на русском.
Человек просит купить спортивный товар (ракетка, мячи, кроссовки), иногда
путано, с оговорками или противоречиями. Твоя единственная задача — вернуть
факты, которые он назвал явно. Не додумывай то, чего не было в тексте.

Ответь СТРОГО одним JSON-объектом, без markdown-разметки, без пояснений,
со следующими ключами (используй null, если признак не назван):

- "brand": строка — канонические название бренда латиницей, если названо
  (например "Babolat", "HEAD", "Wilson", "ASICS"). Приводи разговорные и
  транслитерированные варианты к каноническому виду (например "бабблат"
  и "Бабблат" → "Babolat", "асиксы" → "ASICS"). Если бренда нет — null.
- "color": строка на английском — один из "black", "white", "blue", "yellow",
  "silver", "red", "green", "grey", "pink" — если цвет назван явно, иначе null.
- "size_label": строка — размер как он назван (например "42" или "4 3/8"), иначе null.
- "level": "beginner", если человек говорит, что новичок/только начинает;
  "pro"/"advanced", если явно просит профессиональный/топовый уровень; иначе null.
- "price_preference": "cheap", если просит недорого/дешевле/бюджетный вариант;
  "premium", если просит топовый/лучший/дорогой вариант; иначе null.
- "explicit_max_cents": целое число — явно названный предел суммы в копейках
  (рубли × 100), если человек назвал число, иначе null.
- "quantity": целое число штук, если названо явно (в т.ч. словом: "пару" = 2), иначе null.
- "needs_refundable": true, если человек просит возможность вернуть/обменять
  на случай, если не подойдёт; иначе false.
- "deadline_days": целое число дней, если человек назвал срок или дедлайн
  (например "к субботе", "срочно", "за два дня") — оцени разумное число дней;
  иначе null.
- "soft_fields": массив строк — имена полей выше (только тех, что не null),
  которые сам человек явно обозначил как необязательные, примерные или
  не принципиальные. Признаки: «если что, ладно», «не принципиально»,
  «плюс-минус», «где-то», «не страшно если не так», человек сам себя
  поправляет или снимает требование. НЕ добавляй поле сюда просто потому,
  что ты сам не уверен в извлечении — только когда это исходит из слов
  человека. Если таких полей нет — пустой массив [].

Если в просьбе есть противоречие (например «недорого, но топовую модель») —
верни оба сигнала как есть (price_preference по последнему явному указанию)
и не пытайся сам разрешить противоречие."""


class ExtractionError(RuntimeError):
    """Модель не вернула корректный ответ по схеме IntentFacts."""


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_RETRY_DELAY = re.compile(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"')


def _extract_retry_delay(error_body: str) -> float | None:
    match = _RETRY_DELAY.search(error_body)
    return float(match.group(1)) + 0.5 if match else None


def call_model(request: str) -> dict:
    """Возвращает сырой dict с полями схемы. Бросает ExtractionError при сбое."""
    if not API_KEY:
        raise ExtractionError("LLM_API_KEY не задан (см. .env)")

    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": request}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")

    payload = None
    last_error = None
    for attempt in range(4):
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": API_KEY,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", "replace")
            last_error = f"HTTP {e.code} от модели: {error_body}"
            if e.code in (429, 503) and attempt < 3:
                delay = _extract_retry_delay(error_body) or (2 ** attempt)
                time.sleep(delay)
                continue
            raise ExtractionError(last_error) from e
        except urllib.error.URLError as e:
            raise ExtractionError(f"Сеть недоступна: {e}") from e
    else:
        raise ExtractionError(last_error or "Не удалось получить ответ от модели")

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise ExtractionError(f"Неожиданный формат ответа: {payload}") from e

    match = _JSON_BLOCK.search(text)
    if not match:
        raise ExtractionError(f"В ответе модели нет JSON: {text!r}")

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Модель вернула невалидный JSON: {text!r}") from e
