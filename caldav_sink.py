"""iCloud CalDAV: создание/обновление all-day события на воскресенье недели.

Самая капризная часть. Детерминированный UID по неделе → событие всегда
находится и обновляется без дублей. Ретраи с бэкоффом на 5xx/429/сетевые.
All-day пишется как floating DATE (без TZID) → правильный день у любого зрителя.
"""
import time

_ICLOUD_URL = "https://caldav.icloud.com/"
_RETRY_ATTEMPTS = 4


class CalDavError(RuntimeError):
    pass


def _retry(label, fn, *, attempts=_RETRY_ATTEMPTS, base_delay=2.0):
    """Ретрай с экспоненциальным бэкоффом. Auth-ошибки не ретраим (фейлим сразу)."""
    from caldav.lib import error as caldav_error

    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except caldav_error.AuthorizationError:
            raise  # неверный/отозванный app-specific password — ретрай бесполезен
        except Exception as e:  # noqa: BLE001 — iCloud отдаёт разнородные 5xx/429/сетевые
            last = e
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(f"[caldav] {label}: попытка {attempt}/{attempts} не удалась: {e}; повтор через {delay:.0f}s")
            time.sleep(delay)
    raise CalDavError(f"CalDAV {label}: исчерпаны попытки ({attempts}): {last}")


def get_calendar(apple_id, app_password, calendar_name):
    """Подключается к iCloud и находит календарь по имени. Логирует итоговый URL."""
    import caldav

    def _connect():
        client = caldav.DAVClient(url=_ICLOUD_URL, username=apple_id, password=app_password)
        calendars = client.principal().calendars()
        names = [c.name for c in calendars]
        print(f"[caldav] доступные календари: {names}")
        for c in calendars:
            if c.name == calendar_name:
                print(f"[caldav] выбран '{calendar_name}' → {c.url}")
                return c
        raise CalDavError(
            f"Календарь '{calendar_name}' не найден. Доступные: {names}. "
            f"Создай его в Apple Calendar и дождись синхронизации в iCloud."
        )

    return _retry("connect", _connect)


def upsert_event(calendar, uid, summary, sunday, *, transparent=True):
    """Создаёт или обновляет all-day событие по UID. Возвращает 'created'/'updated'."""

    def _do():
        existing = _find_by_uid(calendar, uid)
        if existing is None:
            calendar.save_event(_build_vcalendar(uid, summary, sunday, transparent, sequence=0))
            return "created"
        seq = _read_sequence(existing) + 1
        existing.data = _build_vcalendar(uid, summary, sunday, transparent, sequence=seq)
        existing.save()
        return "updated"

    return _retry(f"upsert {uid}", _do)


def _find_by_uid(calendar, uid):
    from caldav.lib import error as caldav_error

    try:
        return calendar.event_by_uid(uid)
    except caldav_error.NotFoundError:
        return None
    except Exception:  # noqa: BLE001 — не все серверы ровно поддерживают UID-query
        for ev in calendar.events():
            try:
                for comp in ev.icalendar_instance.walk("VEVENT"):
                    if str(comp.get("UID")) == uid:
                        return ev
            except Exception:  # noqa: BLE001
                continue
        return None


def _read_sequence(event):
    try:
        for comp in event.icalendar_instance.walk("VEVENT"):
            return int(comp.get("SEQUENCE", 0))
    except Exception:  # noqa: BLE001
        pass
    return 0


def _build_vcalendar(uid, summary, sunday, transparent, *, sequence):
    from datetime import datetime, timedelta, timezone
    from icalendar import Alarm, Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//dota-calendar-tracker//EN")
    cal.add("version", "2.0")

    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", sunday)                    # date → DTSTART;VALUE=DATE (floating)
    ev.add("dtend", sunday + timedelta(days=1))  # эксклюзивно: воскресенье + 1 день
    ev.add("dtstamp", datetime.now(timezone.utc))
    ev.add("transp", "TRANSPARENT" if transparent else "OPAQUE")  # free / busy
    ev.add("sequence", sequence)

    # Напоминание в воскресенье 23:00: PT23H от начала all-day дня (00:00) = 23:00
    # по локальному времени зрителя. Явный VALARM перекрывает дефолтный 09:00-алерт,
    # который Apple Calendar сам вешает на all-day события без напоминаний.
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", summary)
    alarm.add("trigger", timedelta(hours=23))
    # Помечаем как «дефолтный» алерт all-day → Apple Calendar считает слот занятым
    # и не доклеивает своё напоминание на 09:00 из глобальных настроек.
    alarm.add("x-apple-default-alarm", "TRUE")
    ev.add_component(alarm)

    cal.add_component(ev)
    return cal.to_ical()
