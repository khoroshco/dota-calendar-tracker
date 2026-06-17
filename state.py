"""Чтение/запись state.json — источник истины по неделям.

Файл коммитится settle-джобом (live его не трогает). На уровне модуля только
stdlib, чтобы state можно было импортировать без сторонних зависимостей.
"""
import json
import os

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state(path=STATE_PATH):
    """Возвращает состояние или {} (пустое/битое/отсутствует → первый запуск)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state, path=STATE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def is_initialized(state):
    """True, если settle уже засидил baseline и текущую неделю."""
    return "settle_baseline_forever" in state and "current_week_monday" in state
