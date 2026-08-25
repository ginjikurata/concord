"""Прогон живых формулировок через extract_intent.

Для фраз с expect — автоматическая сверка по названным полям.
Для фраз без expect — печатаем сырой результат, сверяем глазами: там,
где у просьбы нет единственно верного ответа, автоматическая проверка
только создала бы иллюзию точности.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coherence import extract_intent               # noqa: E402
from llm_extract import ExtractionError             # noqa: E402
from stress_phrases import CASES                    # noqa: E402

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"


def main() -> int:
    errors = []
    checked_pass = []
    checked_fail = []
    review_only = []

    for i, case in enumerate(CASES):
        if i > 0:
            time.sleep(1.5)
        print(f"\n{B}[{case.id}]{X} {D}{case.category}{X}")
        print(f'  «{case.phrase}»')

        try:
            facts = extract_intent(case.phrase, deadline_days=None)
        except ExtractionError as e:
            print(f"  {R}ExtractionError:{X} {e}")
            errors.append((case, str(e)))
            continue

        mismatches = []
        for key, expected_val in (case.expect or {}).items():
            actual_val = getattr(facts, key)
            norm_actual = actual_val.lower() if isinstance(actual_val, str) else actual_val
            norm_expected = expected_val.lower() if isinstance(expected_val, str) else expected_val
            if norm_actual != norm_expected:
                mismatches.append((key, expected_val, actual_val))

        missing_soft = sorted(case.expect_soft - facts.soft)
        if missing_soft:
            mismatches.append(("soft_fields", sorted(case.expect_soft), sorted(facts.soft)))

        if case.expect is None and not case.expect_soft:
            print(f"  {D}(без автосверки — читаем глазами){X}")
            print(f"  {facts}")
            review_only.append((case, facts))
            if case.note:
                print(f"  {D}{case.note}{X}")
            continue

        if mismatches:
            print(f"  {R}расхождение:{X}")
            for key, exp, act in mismatches:
                print(f"    {key}: ожидали {exp!r}, получили {act!r}")
            checked_fail.append((case, facts, mismatches))
        else:
            checked_keys = list(case.expect or {}) + (["soft:" + ",".join(sorted(case.expect_soft))] if case.expect_soft else [])
            print(f"  {G}совпало{X} ({', '.join(checked_keys)})")
            checked_pass.append((case, facts))

        if case.note:
            print(f"  {D}{case.note}{X}")

    total = len(CASES)
    print(f"\n{B}{'=' * 78}{X}")
    print(f"{B}Итог:{X} всего {total}, "
          f"{G}прошли проверку{X} {len(checked_pass)}, "
          f"{R}разошлись с ожиданием{X} {len(checked_fail)}, "
          f"{R}ExtractionError{X} {len(errors)}, "
          f"{D}без автосверки (на ручной разбор){X} {len(review_only)}")

    if checked_fail:
        print(f"\n{B}Разошлись с ожиданием:{X}")
        for case, facts, mismatches in checked_fail:
            print(f"  [{case.id}] {', '.join(f'{k}: {exp!r}!={act!r}' for k, exp, act in mismatches)}")

    if errors:
        print(f"\n{B}Упали с ExtractionError:{X}")
        for case, err in errors:
            print(f"  [{case.id}] {err}")

    return 0 if not checked_fail and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
