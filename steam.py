"""Steam Web API: метрика playtime_forever (минуты) для Dota 2 (appid 570).

Метрика зафиксирована: playtime_forever с idle. Не 2weeks, не OpenDota.
"""
import time

DOTA_APPID = 570
_ENDPOINT = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class SteamError(RuntimeError):
    """Постоянная ошибка Steam (ретраем не лечится)."""


def get_playtime_forever(api_key, steam_id, *, timeout=30, attempts=4):
    """playtime_forever Dota 2 в минутах.

    Ретраит 429/5xx и сетевые сбои с экспоненциальным бэкоффом. Бросает
    SteamError на 4xx (ключ/ID) и если Dota 2 нет в ответе (приватный профиль).
    """
    import requests  # ленивый импорт

    params = {
        "key": api_key,
        "steamid": steam_id,
        "include_played_free_games": 1,  # Dota 2 — free-to-play
        "appids_filter[0]": DOTA_APPID,
        "format": "json",
    }

    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(_ENDPOINT, params=params, timeout=timeout)
        except requests.RequestException as e:
            last_err = e  # сеть/таймаут — временная ошибка, ретраим
        else:
            if resp.status_code in _RETRY_STATUS:
                last_err = SteamError(f"Steam HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise SteamError(
                    f"Steam HTTP {resp.status_code}: проверь STEAM_API_KEY и STEAM_ID64. "
                    f"Тело: {resp.text[:200]}"
                )
            else:
                return _extract_dota_minutes(resp.json())

        if attempt < attempts:
            delay = 2.0 * (2 ** (attempt - 1))
            print(f"[steam] попытка {attempt}/{attempts}: {last_err}; повтор через {delay:.0f}s")
            time.sleep(delay)

    raise SteamError(f"Steam недоступен после {attempts} попыток: {last_err}")


def _extract_dota_minutes(payload):
    games = (payload.get("response") or {}).get("games") or []
    for g in games:
        if g.get("appid") == DOTA_APPID:
            return int(g.get("playtime_forever", 0))
    raise SteamError(
        "Dota 2 (appid 570) не найдена в ответе GetOwnedGames. Проверь: профиль и "
        "«Game details» = Public, SteamID64 верный, игра есть в библиотеке."
    )
