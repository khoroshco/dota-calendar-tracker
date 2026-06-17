#!/usr/bin/env python3
"""Dota 2 → недельные часы в Apple-календаре.

Режимы:
  settle (05:00 МСК) — истина: дельта playtime_forever за прошедший игровой день
                       [05:00→05:00], накопление в неделю, ролловер недели.
  live   (вечер)     — провизорный показ «неделя + сегодня». Не трогает baseline,
                       не коммитит state.
  test               — проверка алерт-канала (Telegram).

Режим: --mode | INPUT_MODE (workflow_dispatch) | GITHUB_SCHEDULE (cron).
Тяжёлые импорты (steam/caldav/alert) — ленивые внутри функций: модуль грузится
на голом stdlib, а test-режим не требует caldav.
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

import state as state_mod

# Europe/Moscow — постоянный UTC+3, DST нет. Фиксируем оффсет, без tz-базы.
MSK = timezone(timedelta(hours=3))

SECRET_KEYS = [
    "STEAM_API_KEY", "STEAM_ID64", "APPLE_ID", "APPLE_APP_PASSWORD",
    "ICLOUD_CALENDAR_NAME", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "HEALTHCHECK_URL",
]
_GAME_SECRETS = ["STEAM_API_KEY", "STEAM_ID64", "APPLE_ID", "APPLE_APP_PASSWORD", "ICLOUD_CALENDAR_NAME"]


# ---------- время / неделя ----------

def now_msk():
    return datetime.now(timezone.utc).astimezone(MSK)


def monday_of(d):
    """Понедельник недели для даты d (неделя Пн→Вс)."""
    return d - timedelta(days=d.weekday())


def sunday_of(monday):
    return monday + timedelta(days=6)


def uid_for(monday):
    return f"dota-week-{monday.isoformat()}@tracker"


def fmt_hm(total_minutes):
    h, m = divmod(max(0, int(total_minutes)), 60)
    return f"{h}ч {m}м"


def summary_for(minutes):
    return f"🎮 Dota 2: {fmt_hm(minutes)}"


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- конфиг / секреты ----------

def load_config():
    return {k: os.environ.get(k, "").strip() for k in SECRET_KEYS}


def _mask(value):
    if not value:
        return "—(не задан)"
    return f"set (len={len(value)}, ••••{value[-2:] if len(value) >= 2 else ''})"


def print_config_masks(cfg):
    print("[config] секреты (маски, не значения):")
    for k in SECRET_KEYS:
        print(f"  {k}: {_mask(cfg.get(k, ''))}")


def require(cfg, keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise SystemExit(f"[fatal] не заданы обязательные секреты: {', '.join(missing)}")


# ---------- выбор режима ----------

def resolve_mode(cli_mode=None):
    if cli_mode:
        return cli_mode
    input_mode = os.environ.get("INPUT_MODE", "").strip()
    if input_mode:
        return input_mode
    schedule = os.environ.get("GITHUB_SCHEDULE", "").strip()
    if schedule == "0 2 * * *":
        return "settle"
    if schedule:
        return "live"
    return "test"  # дефолт для локального запуска без аргументов


def run_link():
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return "(локальный запуск)"


# ---------- режимы ----------

def run_test(cfg):
    require(cfg, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
    from alert import send_telegram

    ts = now_msk().strftime("%Y-%m-%d %H:%M:%S МСК")
    text = f"✅ Тест: алерт-канал Dota-трекера жив.\nВремя: {ts}\nRun: {run_link()}"
    send_telegram(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"], text)
    print("[test] сообщение отправлено в Telegram")


def run_steam(cfg):
    """Диагностика: тянет playtime_forever и печатает. Нужны только Steam-секреты."""
    require(cfg, ["STEAM_API_KEY", "STEAM_ID64"])
    from steam import get_playtime_forever

    forever = get_playtime_forever(cfg["STEAM_API_KEY"], cfg["STEAM_ID64"])
    print(f"[steam] playtime_forever = {forever} мин = {fmt_hm(forever)} (Dota 2, с idle)")


def _healthcheck_ping(cfg):
    url = cfg.get("HEALTHCHECK_URL")
    if not url:
        return
    try:
        import requests
        requests.get(url, timeout=10)
        print("[healthcheck] ping ok")
    except Exception as e:  # noqa: BLE001 — пинг не критичен для истины
        print(f"[healthcheck] ping не удался (не критично): {e}")


def run_settle(cfg):
    require(cfg, _GAME_SECRETS)
    from steam import get_playtime_forever
    from caldav_sink import get_calendar, upsert_event

    forever = get_playtime_forever(cfg["STEAM_API_KEY"], cfg["STEAM_ID64"])
    print(f"[settle] playtime_forever = {forever} мин")

    st = state_mod.load_state()
    today = now_msk().date()
    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])

    if not state_mod.is_initialized(st):
        # Первый запуск: сидируем baseline, неделя с нуля, создаём событие.
        mon = monday_of(today)
        st = {
            "settle_baseline_forever": forever,
            "current_week_monday": mon.isoformat(),
            "current_week_minutes": 0,
            "current_week_event_uid": uid_for(mon),
            "daily": {},
            "history": [],
            "last_settle_utc": utcnow_iso(),
        }
        res = upsert_event(calendar, st["current_week_event_uid"], summary_for(0), sunday_of(mon))
        print(f"[settle] init: baseline={forever}, неделя={mon.isoformat()}, событие={res}")
        state_mod.save_state(st)
        _healthcheck_ping(cfg)
        return

    # Игровой день, который только что закрылся в 05:00.
    yesterday = today - timedelta(days=1)
    delta = max(0, forever - st["settle_baseline_forever"])
    st.setdefault("daily", {})[yesterday.isoformat()] = delta
    print(f"[settle] игровой день {yesterday.isoformat()}: delta={delta} мин")

    week_of_yesterday = monday_of(yesterday)
    cur_monday = date.fromisoformat(st["current_week_monday"])
    if week_of_yesterday != cur_monday:
        # Ролловер: прошлая неделя уже закрыта своей финальной суммой → в history,
        # начинаем новую неделю (понедельник = сегодня).
        st.setdefault("history", []).append({
            "week_monday": st["current_week_monday"],
            "minutes": st["current_week_minutes"],
        })
        new_mon = monday_of(today)
        st["current_week_monday"] = new_mon.isoformat()
        st["current_week_minutes"] = 0
        st["current_week_event_uid"] = uid_for(new_mon)
        res = upsert_event(calendar, st["current_week_event_uid"], summary_for(0), sunday_of(new_mon))
        print(f"[settle] ролловер: новая неделя {new_mon.isoformat()} (событие={res}); "
              f"закрыта {cur_monday.isoformat()} с {st['history'][-1]['minutes']} мин")

    st["current_week_minutes"] += delta
    cur_mon = date.fromisoformat(st["current_week_monday"])
    res = upsert_event(calendar, st["current_week_event_uid"],
                       summary_for(st["current_week_minutes"]), sunday_of(cur_mon))
    print(f"[settle] неделя {cur_mon.isoformat()}: {st['current_week_minutes']} мин (событие={res})")

    st["settle_baseline_forever"] = forever
    st["last_settle_utc"] = utcnow_iso()
    state_mod.save_state(st)
    _healthcheck_ping(cfg)


def run_live(cfg):
    require(cfg, _GAME_SECRETS)
    from steam import get_playtime_forever
    from caldav_sink import get_calendar, upsert_event

    st = state_mod.load_state()
    if not state_mod.is_initialized(st):
        print("[live] state ещё не инициализирован settle — нечего показывать, выходим")
        return

    forever = get_playtime_forever(cfg["STEAM_API_KEY"], cfg["STEAM_ID64"])
    delta = max(0, forever - st["settle_baseline_forever"])
    today = now_msk().date()
    today_monday = monday_of(today)

    if today_monday.isoformat() == st["current_week_monday"]:
        # Обычный день: провизорно «неделя + сегодня».
        provisional = st["current_week_minutes"] + delta
        uid = st["current_week_event_uid"]
        sunday = sunday_of(today_monday)
    else:
        # Граница недели: settle сделает ролловер только завтра в 05:00, но
        # сегодняшняя игра принадлежит уже НОВОЙ неделе. Пишем в её событие
        # (создастся при необходимости), прошлую неделю не трогаем.
        provisional = delta
        uid = uid_for(today_monday)
        sunday = sunday_of(today_monday)

    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    res = upsert_event(calendar, uid, summary_for(provisional), sunday)
    print(f"[live] провизорно {provisional} мин (сегодня delta={delta}); событие={res}")
    # baseline и state НЕ трогаем, не коммитим.


def parse_args(argv):
    p = argparse.ArgumentParser(description="Dota 2 → Apple Calendar tracker")
    p.add_argument("--mode", choices=["settle", "live", "test", "steam"], default=None,
                   help="Принудительный режим. Иначе: INPUT_MODE / GITHUB_SCHEDULE.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config()
    print_config_masks(cfg)
    mode = resolve_mode(args.mode)
    print(f"[main] mode={mode}; now_msk={now_msk().isoformat()}; run={run_link()}")

    if mode == "test":
        run_test(cfg)
    elif mode == "settle":
        run_settle(cfg)
    elif mode == "live":
        run_live(cfg)
    elif mode == "steam":
        run_steam(cfg)
    else:
        raise SystemExit(f"[fatal] неизвестный режим: {mode}")


if __name__ == "__main__":
    main()
