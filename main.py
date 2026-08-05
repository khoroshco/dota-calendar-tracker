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
import copy
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
    return f"{h}:{m:02d}"


def summary_for(steam_minutes, dota_minutes=None, dota_count=0):
    """'🎮 Dota 6:49 (10:22, 18s)' — дота-время впереди, Steam+матчи в скобках.

    Без матчей → '🎮 Dota 4:40' (только Steam). s = число матчей-сессий.
    """
    if dota_count:  # есть матчи → дота впереди, (Steam, N сессий) в скобках
        return f"🎮 Dota {fmt_hm(dota_minutes)} ({fmt_hm(steam_minutes)}, {dota_count}s)"
    return f"🎮 Dota {fmt_hm(steam_minutes)}"


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_settle_date_msk(state):
    """MSK-дата последнего settle = день текущего открытого игрового окна.

    Live опирается на неё, а НЕ на календарное «сейчас»: игровой день меняется
    только в момент settle. Иначе live, заехавший за полночь из-за джиттера крона
    (до утреннего settle), припишет ещё не закрытый игровой день к новой неделе.
    """
    iso = state.get("last_settle_utc")
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).date()
    except (TypeError, ValueError):
        return now_msk().date()


def _week_start_unix(week_monday):
    """Unix-время начала недельного окна: понедельник 05:00 МСК."""
    dt = datetime(week_monday.year, week_monday.month, week_monday.day, 5, 0, 0, tzinfo=MSK)
    return int(dt.timestamp())


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


def run_dump(cfg):
    """Диагностика: печатает сырой iCal текущего недельного события с сервера."""
    require(cfg, ["APPLE_ID", "APPLE_APP_PASSWORD", "ICLOUD_CALENDAR_NAME"])
    from caldav_sink import get_calendar, _find_by_uid

    st = state_mod.load_state()
    uid = st.get("current_week_event_uid")
    if not uid:
        print("[dump] нет current_week_event_uid в state — сначала прогон settle")
        return
    cal = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    ev = _find_by_uid(cal, uid)
    if ev is None:
        print(f"[dump] событие {uid} не найдено на сервере")
        return
    raw = ev.data
    raw = raw.decode() if isinstance(raw, bytes) else raw
    print(f"[dump] RAW iCal для {uid}:")
    for line in raw.splitlines():
        print(f"[dump] | {line}")


def run_dota(cfg):
    """Диагностика: профиль + история матчей (OpenDota), время за текущую неделю."""
    require(cfg, ["STEAM_ID64"])
    from dota import account_id_from_steamid64, get_matches, get_profile

    st = state_mod.load_state()
    week_monday = (date.fromisoformat(st["current_week_monday"])
                   if state_mod.is_initialized(st) else monday_of(now_msk().date()))
    since = _week_start_unix(week_monday)
    acc = account_id_from_steamid64(cfg["STEAM_ID64"])

    try:
        prof = get_profile(cfg["STEAM_ID64"]).get("profile") or {}
        print(f"[dota] account_id={acc}; профиль OpenDota: personaname={prof.get('personaname')!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[dota] account_id={acc}; профиль недоступен: {e}")

    allm = get_matches(cfg["STEAM_ID64"], days=400)
    print(f"[dota] матчей за 400 дней: {len(allm)}")
    if allm:
        latest = max((m.get("start_time") or 0) for m in allm)
        print(f"[dota] последний матч: {datetime.fromtimestamp(latest, timezone.utc).isoformat()}")

    wk = [m for m in allm if (m.get("start_time") or 0) >= since]
    wk_min = sum(m.get("duration", 0) for m in wk) // 60
    print(f"[dota] текущая неделя (с {week_monday.isoformat()} 05:00 МСК): "
          f"{len(wk)} матч(ей), {wk_min} мин = {fmt_hm(wk_min)}")
    if not allm:
        print("[dota] за год ноль матчей → скорее всего выключен 'Expose Public Match Data' "
              "(Dota 2 → Settings → Options), либо матчи скрыты.")


def run_backfill(cfg):
    """Проставить Dota-цифру в события ПРОШЛЫХ (архивных) недель. Текущую ведёт live/settle.

    Ручной разовый режим: читает history, тянет матчи (significant=0 → с Turbo),
    раскладывает по недельным окнам и обновляет каждое событие. State не трогает.
    """
    require(cfg, _GAME_SECRETS)
    from caldav_sink import get_calendar, upsert_event
    from dota import get_matches

    st = state_mod.load_state()
    weeks = [(h["week_monday"], h["minutes"]) for h in st.get("history", [])]
    if not weeks:
        print("[backfill] history пуст — нечего бэкфилить")
        return

    try:
        matches = get_matches(cfg["STEAM_ID64"], days=50)
        print(f"[backfill] матчей из OpenDota (50 дней, с Turbo): {len(matches)}")
    except Exception as e:  # noqa: BLE001 — не трогаем события, если Dota недоступна
        print(f"[backfill] OpenDota недоступен, прерываю: {e}")
        return

    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    for wm_str, steam_min in weeks:
        wm = date.fromisoformat(wm_str)
        start = _week_start_unix(wm)
        end = _week_start_unix(wm + timedelta(days=7))
        wk = [m for m in matches if start <= (m.get("start_time") or 0) < end]
        dota_min = sum(m.get("duration", 0) for m in wk) // 60
        summary = summary_for(steam_min, dota_min, len(wk))
        res = upsert_event(calendar, uid_for(wm), summary, sunday_of(wm))
        print(f"[backfill] {wm_str}: Steam={steam_min}м Dota={dota_min}м ({len(wk)} матч) → '{summary}' {res}")


def run_yearfill(cfg):
    """Создать Dota-only события (по OpenDota) для недель этого года ДО старта трекера.

    Формат '🎮 Dota (6:49)' — только время в матчах, без Steam. Недели, что уже
    ведёт трекер (есть в state), не трогаем. Матч → неделя по окну [Пн 05:00 → Пн 05:00].
    State не меняется.
    """
    require(cfg, _GAME_SECRETS)
    from collections import defaultdict
    from caldav_sink import get_calendar, upsert_event
    from dota import get_matches

    st = state_mod.load_state()
    tracked = {h["week_monday"] for h in st.get("history", [])}
    if state_mod.is_initialized(st):
        tracked.add(st["current_week_monday"])
    first_tracked = min(tracked) if tracked else None
    year_start = date(now_msk().year, 1, 1)

    matches = get_matches(cfg["STEAM_ID64"], days=220)
    print(f"[yearfill] матчей из OpenDota (220 дней): {len(matches)}")

    buckets = defaultdict(lambda: [0, 0])  # week_monday_iso -> [seconds, count]
    for mm in matches:
        stime, dur = mm.get("start_time"), mm.get("duration")
        if stime is None or dur is None:
            continue
        dt = datetime.fromtimestamp(stime, MSK)
        gday = dt.date() if dt.hour >= 5 else dt.date() - timedelta(days=1)  # окно 05:00
        wm = monday_of(gday)
        if wm < year_start:
            continue
        if first_tracked and wm.isoformat() >= first_tracked:
            continue  # эти недели ведёт трекер (полный формат) — не трогаем
        b = buckets[wm.isoformat()]
        b[0] += dur
        b[1] += 1

    # Диапазон: [первый Пн года → последняя до-трекерная неделя]. Идём по ВСЕМ
    # неделям подряд, чтобы не было пропусков — пустые получают '🎮 Dota (0:00)'.
    start_monday = monday_of(year_start)
    if start_monday < year_start:
        start_monday += timedelta(days=7)
    end_monday = (date.fromisoformat(first_tracked) - timedelta(days=7)
                  if first_tracked else monday_of(now_msk().date()))
    if end_monday < start_monday:
        print("[yearfill] нет до-трекерных недель в этом году")
        return

    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    wm, n = start_monday, 0
    while wm <= end_monday:
        sec, cnt = buckets.get(wm.isoformat(), (0, 0))
        dota_min = sec // 60
        summary = f"🎮 Dota ({fmt_hm(dota_min)})"
        res = upsert_event(calendar, uid_for(wm), summary, sunday_of(wm))
        print(f"[yearfill] {wm.isoformat()}: {dota_min}м ({cnt} матч) → '{summary}' {res}")
        wm += timedelta(days=7)
        n += 1
    print(f"[yearfill] недель создано/обновлено: {n} (включая пустые 0:00)")


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


def _dota_week_minutes(cfg, week_monday):
    """(минуты, число матчей) за неделю (OpenDota, с Turbo). (None, 0) если недоступно."""
    if not cfg.get("STEAM_ID64"):
        return None, 0
    try:
        from dota import match_minutes_since
        since = _week_start_unix(week_monday)
        minutes, count = match_minutes_since(cfg["STEAM_ID64"], since)
        print(f"[dota] неделя {week_monday.isoformat()}: {count} матч(ей) → {minutes} мин")
        return minutes, count
    except Exception as e:  # noqa: BLE001 — Dota-число вторично, джоб не валим
        print(f"[dota] недоступно, показываю только Steam: {e}")
        return None, 0


def plan_settle(forever, state, today):
    """Чистая truth-логика settle: (forever, state, today) → (new_state, ops).

    ops = [(uid, summary, sunday_date), ...] в порядке применения. Без I/O —
    отсюда детерминированные тесты (дельта, кламп, ролловер, граница, разрыв).
    """
    if not state_mod.is_initialized(state):
        # Первый запуск: сидируем baseline, неделя с нуля, создаём событие.
        mon = monday_of(today)
        new = {
            "settle_baseline_forever": forever,
            "current_week_monday": mon.isoformat(),
            "current_week_minutes": 0,
            "current_week_event_uid": uid_for(mon),
            "daily": {},
            "history": [],
            "last_settle_utc": utcnow_iso(),
        }
        return new, [(new["current_week_event_uid"], 0, sunday_of(mon))]

    st = copy.deepcopy(state)
    ops = []

    # Игровой день, который только что закрылся в 05:00.
    yesterday = today - timedelta(days=1)
    delta = max(0, forever - st["settle_baseline_forever"])
    st.setdefault("daily", {})[yesterday.isoformat()] = delta

    week_of_yesterday = monday_of(yesterday)
    cur_monday = date.fromisoformat(st["current_week_monday"])
    if week_of_yesterday != cur_monday:
        # Ролловер: прошлая неделя уже закрыта финальной суммой → в history,
        # начинаем новую (понедельник = сегодня). Дельта вчера уйдёт в новую неделю.
        st.setdefault("history", []).append({
            "week_monday": st["current_week_monday"],
            "minutes": st["current_week_minutes"],
        })
        new_mon = monday_of(today)
        st["current_week_monday"] = new_mon.isoformat()
        st["current_week_minutes"] = 0
        st["current_week_event_uid"] = uid_for(new_mon)
        ops.append((st["current_week_event_uid"], 0, sunday_of(new_mon)))

    st["current_week_minutes"] += delta
    cur_mon = date.fromisoformat(st["current_week_monday"])
    ops.append((st["current_week_event_uid"], st["current_week_minutes"], sunday_of(cur_mon)))
    st["settle_baseline_forever"] = forever
    st["last_settle_utc"] = utcnow_iso()
    return st, ops


def run_settle(cfg):
    require(cfg, _GAME_SECRETS)
    from steam import get_playtime_forever
    from caldav_sink import get_calendar, upsert_event

    forever = get_playtime_forever(cfg["STEAM_API_KEY"], cfg["STEAM_ID64"])
    print(f"[settle] playtime_forever = {forever} мин")
    st = state_mod.load_state()
    today = now_msk().date()
    new_state, ops = plan_settle(forever, st, today)

    week_monday = date.fromisoformat(new_state["current_week_monday"])
    dota_min, dota_cnt = _dota_week_minutes(cfg, week_monday)

    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    for uid, steam_min, sunday in ops:
        summary = summary_for(steam_min, dota_min, dota_cnt)
        res = upsert_event(calendar, uid, summary, sunday)
        print(f"[settle] {uid}: '{summary}' -> {res}")

    state_mod.save_state(new_state)
    print(f"[settle] неделя {new_state['current_week_monday']} = {new_state['current_week_minutes']} мин; "
          f"baseline={new_state['settle_baseline_forever']}; daily={new_state.get('daily')}")
    _healthcheck_ping(cfg)


def plan_live(forever, state):
    """Чистая логика live → (uid, steam_minutes, sunday, delta). Без I/O.

    Якорь недели — дата ПОСЛЕДНЕГО settle (день открытого окна), а не календарное
    «сегодня»: устойчиво к джиттеру крона (см. _last_settle_date_msk).
    """
    delta = max(0, forever - state["settle_baseline_forever"])
    open_monday = monday_of(_last_settle_date_msk(state))
    if open_monday.isoformat() == state["current_week_monday"]:
        # Обычный случай: открытое окно в текущей неделе → «неделя + сегодня».
        provisional = state["current_week_minutes"] + delta
        uid = state["current_week_event_uid"]
    else:
        # settle ещё не сделал ролловер, но открытое окно уже в новой неделе →
        # пишем в её событие (создастся), прошлую неделю не трогаем.
        provisional = delta
        uid = uid_for(open_monday)
    return uid, provisional, sunday_of(open_monday), delta


def run_live(cfg):
    require(cfg, _GAME_SECRETS)
    from steam import get_playtime_forever
    from caldav_sink import get_calendar, upsert_event

    st = state_mod.load_state()
    if not state_mod.is_initialized(st):
        print("[live] state ещё не инициализирован settle — нечего показывать, выходим")
        return

    forever = get_playtime_forever(cfg["STEAM_API_KEY"], cfg["STEAM_ID64"])
    uid, steam_min, sunday, delta = plan_live(forever, st)
    week_monday = sunday - timedelta(days=6)
    dota_min, dota_cnt = _dota_week_minutes(cfg, week_monday)

    calendar = get_calendar(cfg["APPLE_ID"], cfg["APPLE_APP_PASSWORD"], cfg["ICLOUD_CALENDAR_NAME"])
    summary = summary_for(steam_min, dota_min, dota_cnt)
    res = upsert_event(calendar, uid, summary, sunday)
    print(f"[live] {uid}: '{summary}' (delta={delta}); событие={res}")
    # baseline и state НЕ трогаем, не коммитим.


def parse_args(argv):
    p = argparse.ArgumentParser(description="Dota 2 → Apple Calendar tracker")
    p.add_argument("--mode",
                   choices=["settle", "live", "test", "steam", "dump", "dota", "backfill", "yearfill"],
                   default=None,
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
    elif mode == "dump":
        run_dump(cfg)
    elif mode == "dota":
        run_dota(cfg)
    elif mode == "backfill":
        run_backfill(cfg)
    elif mode == "yearfill":
        run_yearfill(cfg)
    else:
        raise SystemExit(f"[fatal] неизвестный режим: {mode}")


if __name__ == "__main__":
    main()
