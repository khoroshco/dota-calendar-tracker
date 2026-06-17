# Dota 2 → недельные часы в Apple-календаре

Headless-джоб на **GitHub Actions** (крутится в облаке, Мак не нужен). Раз в день
снимает `playtime_forever` из Steam, честно атрибутирует игровой день и ведёт
all-day событие на **воскресенье** текущей недели в **Apple/iCloud** календаре.
Поломка → **Telegram-алерт**.

## Как это работает

Два режима (выбираются по cron-строке или вручную):

- **settle (05:00 МСК)** — источник истины. `delta = forever_now − baseline` = время
  за прошедший игровой день `[05:00 → 05:00]`. Кладётся в `daily[вчера]`, копится в
  неделю, при смене понедельника — ролловер (прошлая неделя → `history`, новое
  событие). Обновляет baseline. Коммитит `state.json`.
- **live (20:55 / 22:55 / 23:55 МСК)** — косметика. Считает провизорное «неделя +
  сегодня» и патчит заголовок воскресного события. baseline **не** трогает,
  state **не** коммитит. Следующий settle перезапишет истиной.
- **test** — шлёт `✅` в Telegram (проверка алерт-канала).
- **steam** — диагностика: печатает `playtime_forever` (нужны только Steam-секреты; удобно проверить Фазу 1 до настройки Apple).

Подробности точности — в шапке `main.py` и в `dota-calendar-tracker-plan.md`.

### Почему окно `[05:00 → 05:00]`
В 05:00 юзер гарантированно не в матче → `playtime_forever` дофлашен, нет висящей
сессии. Любая сессия целиком попадает в одно окно → ни одна не рвётся на стыке
дней/недель. Поздняя воскресная сессия (Вс 23:00–01:00) уходит в воскресенье.

## Структура

```
main.py          # точка входа: --mode settle|live|test
steam.py         # playtime_forever (appid 570) + ретраи
caldav_sink.py   # iCloud CalDAV: create/update all-day по UID + ретраи
alert.py         # Telegram sendMessage (режим test)
state.py         # чтение/запись state.json
state.json       # состояние; коммитит только settle
.github/workflows/tracker.yml
```

## Секреты (Settings → Secrets and variables → Actions)

| Secret | Что | Нужен для |
|--------|-----|-----------|
| `TELEGRAM_BOT_TOKEN` | токен бота | test, алерты |
| `TELEGRAM_CHAT_ID` | твой chat_id | test, алерты |
| `STEAM_API_KEY` | https://steamcommunity.com/dev/apikey | settle, live |
| `STEAM_ID64` | SteamID64, 17 цифр (steamid.io) | settle, live |
| `APPLE_ID` | Apple ID (email) | settle, live |
| `APPLE_APP_PASSWORD` | app-specific password (appleid.apple.com → Sign-In and Security → App-Specific Passwords; нужна 2FA) | settle, live |
| `ICLOUD_CALENDAR_NAME` | имя календаря, напр. `Gaming` (создать заранее в Apple Calendar) | settle, live |
| `HEALTHCHECK_URL` *(опц.)* | URL с healthchecks.io — пинг при успешном settle | ловит тихий дроп расписания |

Добавить через CLI:
```bash
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
# ...и так далее по списку
```

### Перед стартом
1. Создать отдельный календарь **Gaming** в Apple Calendar (синхронится в iCloud).
2. Включить 2FA, сгенерить app-specific password.
3. Steam-профиль и **Game details** → **Public**.

## Запуск и тесты

Вручную: **Actions → dota-tracker → Run workflow** → выбрать `mode`.

Фазы валидации (см. план):
- **Фаза 0** — `mode=test` → пришёл Telegram. Затем сломать секрет → пришёл
  failure-алерт со ссылкой на run.
- **Фаза 1/3** — `mode=settle` дважды: появился `state.json`, посчиталась delta.
- **Фаза 2** — в календаре Gaming появилось all-day событие на воскресенье,
  заголовок `🎮 Dota 2: Xч Yм`, повтор не плодит дубли.
- **Фаза 4** — `mode=live` вечером обновляет заголовок «с учётом сегодня».

Локально (нужны env-переменные секретов):
```bash
pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python main.py --mode test
```

## Edge-кейсы
- **Первый запуск**: settle сидит baseline, неделя с 0, создаёт событие.
- **Пропущенные дни** (Actions молчал): следующий settle-delta покрывает разрыв,
  но **многодневный разрыв припишется одному дню** (`daily[вчера]`). Это осознанное
  упрощение.
- **Reset `playtime_forever`**: `delta` клампится в 0 (без минусов).
- **Смена пароля Apple ID** отзывает app-password → джоб падает → Telegram-алерт
  (ожидаемо, это фича).
- **CalDAV-флапы** (5xx/429): ретраи с бэкоффом, при исчерпании — алерт.
- **Граница недели**: live в понедельник днём пишет в событие **новой** недели, а
  не в прошлую (settle делает формальный ролловер во вторник 05:00). Прошлое
  воскресное событие остаётся с финальной суммой.
- **Джиттер крона** Actions безопасен: settle привязан к окну `[05:00→05:00]`, а не
  к точной секунде.

## Заметки по эксплуатации
- Scheduled-workflow GitHub отключает после ~60 дней без активности — ежедневный
  коммит `state.json` от settle держит репо «живым».
- `HEALTHCHECK_URL` ловит **тихий** дроп расписания (когда джоб вообще не
  стартовал — `if: failure()` такое не поймает). На healthchecks.io настрой алерт
  на отсутствие пинга дольше суток.
