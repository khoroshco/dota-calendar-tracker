"""Telegram-уведомления (sendMessage). Используется режимом test.

Алерт о падении джоба шлёт сам workflow (шаг `if: failure()`), чтобы поймать
и не-Python падения тоже.
"""


def send_telegram(token, chat_id, text, *, timeout=20):
    import requests  # ленивый импорт: модуль грузится без сторонних зависимостей

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
