# Dota → недельные часы в Apple-календаре

Headless-джоб на **GitHub Actions** (крутится в облаке, Мак не нужен). Ведёт all-day
событие на **воскресенье** каждой недели в **Apple/iCloud** календаре с двумя метриками
игры в Dota 2. Поломка → **Telegram-алерт**.

**Формат события:** `🎮 Dota 6:49 (10:22, 18s)`

- `6:49` — **Dota** чистое время в матчах за неделю (сумма длительностей, OpenDota).
- `10:22` — **Steam** `playtime_forever` за неделю (с idle: меню, очередь, простой —
  «сколько Dota была запущена на компе»), в скобках.
- `18s` — число матчей (`s` = sessions). Turbo учитывается.

Скобки появляются только когда есть матчи. До-трекерные недели (бэкфилл из OpenDota)
пишутся в формате `🎮 Dota (6:49)` — только матчи, без Steam (за те недели Steam-истории нет).

## Режимы

Режим берётся из cron-строки (scheduled) или из `workflow_dispatch` (вручную).

**Автоматические (cron, UTC → МСК):**

| Режим | Расписание | Что делает |
|---|---|---|
| `settle` | `0 2 * * *` → 05:00 МСК | Источник истины. `delta = forever − baseline` за игровой день `[05:00→05:00]` → `daily`, копит в неделю, ролловер при смене недели (прошлая → `history`). Обновляет baseline, коммитит `state.json`. |
| `live` | `55 17/19/20 * * *` → 20:55 / 22:55 / 23:55 МСК | Косметика: провизорно «неделя + сегодня», патчит заголовок. baseline/state **не** трогает. |

**Ручные (`workflow_dispatch` → input `mode`):**

| Режим | Что делает |
|---|---|
| `test` | Шлёт `✅` в Telegram (проверка алерт-канала). |
| `steam` | Печатает текущий `playtime_forever` (диагностика Steam). |
| `dota` | Печатает профиль OpenDota + матчи за год + за текущую неделю (диагностика Dota). |
| `dump` | Печатает сырой iCal текущего недельного события с сервера. |
| `backfill` | Разово: проставляет Dota-цифру `(матчи, Ns)` в события **отслеженных** недель (из `history`). |
| `yearfill` | Разово: создаёт Dota-only события `🎮 Dota (H:MM)` на каждую неделю года **до** старта трекера (пустые недели → `0:00`, без пропусков). |

### Почему окно `[05:00 → 05:00]`
Игровой день = `[05:00 МСК → 05:00 МСК следующего дня]`, помечается датой начала. В 05:00
юзер гарантированно не в матче → `playtime_forever` полностью дофлашен. Любая сессия целиком
попадает в одно окно → не рвётся на стыке дней/недель. Поздняя воскресная сессия уходит в
воскресенье (правильную неделю). Неделя Пн→Вс, событие на воскресенье.

## Метрика Dota (OpenDota)

Вторая цифра — чистое время в матчах, из **OpenDota** (`api.opendota.com`, без API-ключа).
`account_id = SteamID64 − 76561197960265728`. Матч относится к неделе по `start_time` в том же
окне `[Пн 05:00 → Пн 05:00]`.

- ⚠️ В Dota 2 включить **«Открыть данные публичных матчей» / Expose Public Match Data**
  (Настройки → Опции), иначе OpenDota не видит матчи.
- ⚠️ В запросе обязателен **`significant=0`**: по умолчанию OpenDota **исключает Turbo** и
  event-режимы. С `significant=0` учитываются все режимы.
- Dota-число **вторично**: если OpenDota недоступна — событие показывает только Steam, джоб
  не падает.

## Структура

```
main.py              # точка входа: --mode settle|live|test|steam|dump|dota|backfill|yearfill
steam.py             # playtime_forever (appid 570) + ретраи
dota.py              # OpenDota: матчи (significant=0, с Turbo) + ретраи
caldav_sink.py       # iCloud CalDAV: create/update all-day по UID + ретраи (VALARM вс 23:00)
alert.py             # Telegram sendMessage (режим test)
state.py             # чтение/запись state.json
state.json           # состояние; коммитит только settle
tests/test_logic.py  # детерминированные тесты truth-логики (без сети/секретов)
.github/workflows/tracker.yml
```

## Секреты (Settings → Secrets and variables → Actions)

| Secret | Что | Нужен для |
|--------|-----|-----------|
| `TELEGRAM_BOT_TOKEN` | токен бота | test, алерты |
| `TELEGRAM_CHAT_ID` | твой chat_id | test, алерты |
| `STEAM_API_KEY` | https://steamcommunity.com/dev/apikey | settle, live, steam |
| `STEAM_ID64` | SteamID64, 17 цифр (steamid.io) | settle, live, steam, dota, backfill, yearfill |
| `APPLE_ID` | Apple ID (email) | settle, live, dump, backfill, yearfill |
| `APPLE_APP_PASSWORD` | app-specific password (appleid.apple.com → Sign-In and Security → App-Specific Passwords; нужна 2FA) | ↑ |
| `ICLOUD_CALENDAR_NAME` | имя календаря, напр. `Gaming` (создать заранее в Apple Calendar) | ↑ |
| `HEALTHCHECK_URL` *(опц.)* | URL с healthchecks.io — пинг при успешном settle | ловит тихий дроп расписания |

OpenDota (Dota-число) ключа **не требует** — достаточно `STEAM_ID64`.

Добавить через CLI: `gh secret set <NAME>`.

### Перед стартом
1. Отдельный календарь **Gaming** в Apple Calendar (синкается в iCloud).
2. Включить 2FA, сгенерить app-specific password.
3. Steam-профиль и **Game details** → **Public**.
4. Для второй цифры — в Dota 2 включить **«Expose Public Match Data»**.

## Запуск и тесты

Вручную: **Actions → dota-tracker → Run workflow** → выбрать `mode`.

Детерминированные тесты truth-логики (дельта, кламп при сбросе, ролловер недели, граница
пн/вт, джиттер за полночь, многодневный разрыв, формат заголовка) — без сети и секретов;
гоняются и в CI перед каждой записью в календарь:
```bash
python3 tests/test_logic.py
```

Локально одиночный режим (нужны env-переменные секретов):
```bash
pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python main.py --mode test
```

## Edge-кейсы
- **Первый запуск**: settle сидит baseline, неделя с 0, создаёт событие.
- **Неполная первая неделя**: трекер стартовал в середине недели → Steam за ту неделю не
  досчитан (нет baseline до старта), а Dota (OpenDota) видит матчи за всю неделю. Единичный
  случай — возможно `Dota > Steam` только там.
- **Пропущенные дни** (Actions молчал): следующий settle-delta покрывает разрыв, но
  **многодневный разрыв припишется одному дню** (`daily[вчера]`). Осознанное упрощение.
- **Reset `playtime_forever`**: `delta` клампится в 0 (без минусов).
- **Смена пароля Apple ID** отзывает app-password → джоб падает → Telegram-алерт (ожидаемо).
- **CalDAV / OpenDota флапы** (5xx/429): ретраи с бэкоффом; для Dota при исчерпании —
  просто нет второй цифры (Steam остаётся), джоб не падает.
- **Джиттер крона + граница недели**: live определяет неделю по дате **последнего settle**
  (день открытого игрового окна), а не по `now()`. Поэтому вечерний live, заехавший за
  полночь из-за задержки Actions, не приписывает воскресную игру к новой неделе. Недельная
  сумма всегда точна; дневная разбивка приблизительна под сильным джиттером.

## Заметки по эксплуатации
- Scheduled-workflow GitHub отключает после ~60 дней без активности — ежедневный коммит
  `state.json` от settle держит репо «живым».
- `HEALTHCHECK_URL` ловит **тихий** дроп расписания (когда джоб вообще не стартовал —
  `if: failure()` такое не поймает). На healthchecks.io настрой алерт на отсутствие пинга.
- **Напоминание события** — в iCal зашит VALARM на **воскресенье 23:00** (PT23H от начала
  all-day дня). ВАЖНО: Apple Calendar доклеивает свой дефолтный алерт для all-day, если в
  настройках Календаря (вкладка **Alerts**, аккаунт **iCloud** — не «On My Mac»!)
  «All Day Events» ≠ None. Выстави **None**, иначе будет два напоминания. Это настройка
  устройства, не сервера.
- `backfill` / `yearfill` — разовые ручные операции (правят только события, `state` не
  трогают). Идемпотентны: детерминированный UID `dota-week-{monday}@tracker` → без дублей.
