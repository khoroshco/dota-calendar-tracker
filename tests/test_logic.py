#!/usr/bin/env python3
"""Детерминированные тесты truth-логики settle/live (без сети и I/O).

Запуск: python3 tests/test_logic.py   (или pytest)
Импортирует main на голом stdlib — тяжёлые зависимости там ленивые.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as m  # noqa: E402


def _week_state(monday, minutes, baseline=100, daily=None, history=None,
                last_settle="2026-06-17T06:45:00Z"):
    return {
        "settle_baseline_forever": baseline,
        "current_week_monday": monday,
        "current_week_minutes": minutes,
        "current_week_event_uid": m.uid_for(date.fromisoformat(monday)),
        "daily": daily or {},
        "history": history or [],
        "last_settle_utc": last_settle,
    }


# ---------- settle ----------

def test_init_seeds_baseline_and_creates_event():
    new, ops = m.plan_settle(404068, {}, date(2026, 6, 17))  # среда
    assert new["settle_baseline_forever"] == 404068
    assert new["current_week_monday"] == "2026-06-15"
    assert new["current_week_minutes"] == 0
    assert new["current_week_event_uid"] == "dota-week-2026-06-15@tracker"
    assert new["daily"] == {} and new["history"] == []
    assert ops == [("dota-week-2026-06-15@tracker", "🎮 Dota: 0 ч 0 м", date(2026, 6, 21))]


def test_delta_accumulates_midweek():
    new, ops = m.plan_settle(130, _week_state("2026-06-15", 60), date(2026, 6, 17))
    assert new["daily"]["2026-06-16"] == 30      # вчера = вт 16-го
    assert new["current_week_minutes"] == 90
    assert new["settle_baseline_forever"] == 130
    assert new["history"] == []                  # без ролловера
    assert ops == [("dota-week-2026-06-15@tracker", "🎮 Dota: 1 ч 30 м", date(2026, 6, 21))]


def test_delta_clamped_to_zero_on_playtime_reset():
    new, ops = m.plan_settle(150, _week_state("2026-06-15", 50, baseline=200), date(2026, 6, 17))
    assert new["daily"]["2026-06-16"] == 0       # forever упал → дельта 0, без минусов
    assert new["current_week_minutes"] == 50
    assert new["settle_baseline_forever"] == 150


def test_week_rollover_archives_old_and_starts_new():
    st = _week_state("2026-06-08", 500)          # прошлая неделя
    new, ops = m.plan_settle(160, st, date(2026, 6, 16))  # вт; вчера пн 15-го → новая неделя
    assert new["history"] == [{"week_monday": "2026-06-08", "minutes": 500}]
    assert new["current_week_monday"] == "2026-06-15"
    assert new["current_week_event_uid"] == "dota-week-2026-06-15@tracker"
    assert new["current_week_minutes"] == 60     # дельта пн → в НОВУЮ неделю
    assert new["daily"]["2026-06-15"] == 60
    assert len(ops) == 2
    assert ops[0] == ("dota-week-2026-06-15@tracker", "🎮 Dota: 0 ч 0 м", date(2026, 6, 21))
    assert ops[1] == ("dota-week-2026-06-15@tracker", "🎮 Dota: 1 ч 0 м", date(2026, 6, 21))


def test_late_sunday_session_stays_in_ending_week():
    # Пн-settle (22-е) атрибутирует вс 21-е: оно в неделе 15-го → НЕ ролловер.
    st = _week_state("2026-06-15", 400)
    new, ops = m.plan_settle(190, st, date(2026, 6, 22))  # пн; вчера вс 21-го
    assert new["history"] == []                  # ролловера НЕТ
    assert new["current_week_monday"] == "2026-06-15"
    assert new["daily"]["2026-06-21"] == 90
    assert new["current_week_minutes"] == 490
    assert ops == [("dota-week-2026-06-15@tracker", "🎮 Dota: 8 ч 10 м", date(2026, 6, 21))]


def test_multiday_gap_attributed_to_single_day():
    st = _week_state("2026-06-15", 0)
    new, ops = m.plan_settle(400, st, date(2026, 6, 18))  # разрыв: всё 300 → один день (вчера)
    assert new["daily"] == {"2026-06-17": 300}
    assert new["current_week_minutes"] == 300


def test_plan_settle_does_not_mutate_input_state():
    st = _week_state("2026-06-15", 60)
    snapshot = dict(st)
    m.plan_settle(130, st, date(2026, 6, 17))
    assert st["current_week_minutes"] == snapshot["current_week_minutes"]  # вход не тронут
    assert st["daily"] == {}


# ---------- live ----------

def test_live_midweek_includes_today():
    # последний settle — ср 17-го (та же неделя 15-го)
    st = _week_state("2026-06-15", 60, last_settle="2026-06-17T06:45:00Z")
    uid, summary, sunday, prov, delta = m.plan_live(130, st)
    assert uid == "dota-week-2026-06-15@tracker"
    assert prov == 90 and delta == 30
    assert summary == "🎮 Dota: 1 ч 30 м"
    assert sunday == date(2026, 6, 21)


def test_live_monday_boundary_writes_new_week_not_last():
    # settle прошёл в пн 22-го (атрибутировал вс, без ролловера) → открытое окно
    # уже в новой неделе; сегодня → событие вс 28-го, прошлые 490 НЕ попадают.
    st = _week_state("2026-06-15", 490, last_settle="2026-06-22T07:14:00Z")
    uid, summary, sunday, prov, delta = m.plan_live(130, st)
    assert uid == "dota-week-2026-06-22@tracker"
    assert prov == 30
    assert summary == "🎮 Dota: 0 ч 30 м"
    assert sunday == date(2026, 6, 28)


def test_live_jitter_after_midnight_stays_in_ending_week():
    # БАГ-регресс: live заехал за полночь (календарно пн 22-го), но утренний
    # settle ещё НЕ прошёл → последний settle = вс 21-го → открытое окно ещё в
    # неделе 15-го. Поздняя вс-сессия (57 м) идёт в вс-21, НЕ в новую неделю.
    st = _week_state("2026-06-15", 121, baseline=404189, last_settle="2026-06-21T06:45:13Z")
    uid, summary, sunday, prov, delta = m.plan_live(404246, st)
    assert uid == "dota-week-2026-06-15@tracker"   # прошлая неделя, НЕ june-22
    assert delta == 57 and prov == 178             # 121 + 57
    assert sunday == date(2026, 6, 21)


def test_live_clamps_negative_delta():
    st = _week_state("2026-06-15", 100, baseline=120, last_settle="2026-06-17T06:45:00Z")
    uid, summary, sunday, prov, delta = m.plan_live(80, st)
    assert delta == 0 and prov == 100              # forever<baseline → только неделя


# ---------- формат ----------

def test_fmt_hm():
    assert m.fmt_hm(0) == "0 ч 0 м"
    assert m.fmt_hm(59) == "0 ч 59 м"
    assert m.fmt_hm(630) == "10 ч 30 м"
    assert m.fmt_hm(-5) == "0 ч 0 м"
    assert m.summary_for(125) == "🎮 Dota: 2 ч 5 м"


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    failed = 0
    for t in _TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(_TESTS) - failed}/{len(_TESTS)} passed")
    sys.exit(1 if failed else 0)
