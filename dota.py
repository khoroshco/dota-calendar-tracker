"""OpenDota: время в реальных матчах Dota 2 (сумма duration), без меню/очереди.

Вторая, «честная игровая» метрика в дополнение к Steam playtime_forever (с idle).
Ключ не нужен. Требует включённого в Dota «Expose Public Match Data».
"""
import time

_STEAMID64_BASE = 76561197960265728  # SteamID64 = account_id + это смещение
_BASE = "https://api.opendota.com/api"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class DotaError(RuntimeError):
    """Постоянная ошибка OpenDota (ретраем не лечится)."""


def account_id_from_steamid64(steam_id64):
    return int(str(steam_id64).strip()) - _STEAMID64_BASE


def get_matches(steam_id64, *, days=8, timeout=30, attempts=4):
    """Матчи игрока за последние `days` дней. Каждый: start_time (unix), duration (сек)."""
    import requests  # ленивый импорт

    account_id = account_id_from_steamid64(steam_id64)
    url = f"{_BASE}/players/{account_id}/matches"
    params = {"date": days}  # OpenDota: матчи за последние N дней

    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last_err = e  # сеть/таймаут — временная, ретраим
        else:
            if resp.status_code in _RETRY_STATUS:
                last_err = DotaError(f"OpenDota HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise DotaError(f"OpenDota HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                data = resp.json()
                if not isinstance(data, list):
                    raise DotaError(f"OpenDota вернул не список: {str(data)[:200]}")
                return data

        if attempt < attempts:
            delay = 2.0 * (2 ** (attempt - 1))
            print(f"[dota] попытка {attempt}/{attempts}: {last_err}; повтор через {delay:.0f}s")
            time.sleep(delay)

    raise DotaError(f"OpenDota недоступен после {attempts} попыток: {last_err}")


def get_profile(steam_id64, *, timeout=30):
    """Профиль игрока в OpenDota (personaname и пр.). Пустой 'profile' → нет данных."""
    import requests

    account_id = account_id_from_steamid64(steam_id64)
    resp = requests.get(f"{_BASE}/players/{account_id}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def match_minutes_since(steam_id64, since_unix, *, days=8):
    """Сумма длительностей матчей (мин) и их число, начавшихся в [since_unix, ∞)."""
    matches = get_matches(steam_id64, days=days)
    picked = [m for m in matches if (m.get("start_time") or 0) >= since_unix]
    total_min = sum(m.get("duration", 0) for m in picked) // 60
    return total_min, len(picked)
