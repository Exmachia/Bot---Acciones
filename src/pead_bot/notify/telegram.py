"""Envío de mensajes a Telegram. El token nunca aparece en errores ni logs."""
from __future__ import annotations

import time

import requests

API = "https://api.telegram.org"
MAX_LEN = 4096  # límite de Telegram por mensaje


class TelegramError(RuntimeError):
    pass


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Parte por líneas para no cortar etiquetas HTML a la mitad."""
    parts, cur = [], ""
    for line in text.split("\n"):
        add = line if not cur else "\n" + line
        if len(cur) + len(add) > limit and cur:
            parts.append(cur)
            cur = line
        else:
            cur += add
    if cur:
        parts.append(cur)
    return parts


class TelegramClient:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None):
        self._url = f"{API}/bot{token}/sendMessage"
        self.chat_id = chat_id
        self._s = session or requests.Session()

    def send(self, text: str, retries: int = 3) -> None:
        for part in split_message(text):
            self._send_one(part, retries)

    def _send_one(self, text: str, retries: int) -> None:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        for attempt in range(retries + 1):
            try:
                r = self._s.post(self._url, json=payload, timeout=20)
            except requests.RequestException as e:  # el mensaje de requests trae la URL con el token
                if attempt == retries:
                    raise TelegramError(f"No pude conectar con Telegram ({type(e).__name__}).") from None
                time.sleep(2 * (attempt + 1))
                continue
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 429 and attempt < retries:
                time.sleep(float(data.get("parameters", {}).get("retry_after", 3)))
                continue
            if not data.get("ok"):
                hint = {401: " (revisa TELEGRAM_BOT_TOKEN)",
                        400: " (revisa TELEGRAM_CHAT_ID o el formato)",
                        403: " (¿bloqueaste al bot o no le diste Iniciar?)"}.get(r.status_code, "")
                raise TelegramError(f"Telegram respondió {r.status_code}: "
                                    f"{data.get('description', r.text[:200])}{hint}")
            return
