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

    def send(self, text: str, retries: int = 3, buttons: list[list[tuple[str, str]]] | None = None) -> int | None:
        """Manda el texto (partido si es largo). Los botones van en el último pedazo.
        Devuelve el message_id del último mensaje."""
        parts = split_message(text)
        mid = None
        for i, part in enumerate(parts):
            payload = {"chat_id": self.chat_id, "text": part, "parse_mode": "HTML",
                       "disable_web_page_preview": True}
            if buttons and i == len(parts) - 1:
                payload["reply_markup"] = {"inline_keyboard": [
                    [{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
            res = self._call("sendMessage", payload, retries)
            mid = (res or {}).get("message_id")
        return mid

    def get_updates(self, offset: int | None = None, wait: int = 0) -> list[dict]:
        """wait > 0: espera hasta `wait` segundos a que llegue algo (long polling)."""
        payload = {"timeout": wait, "allowed_updates": ["callback_query", "message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=wait + 15) or []

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text}, retries=0)
        except TelegramError:
            pass  # si el toque es muy viejo, Telegram ya no deja contestarlo; no importa

    def edit_buttons(self, message_id: int, buttons: list[list[tuple[str, str]]] | None) -> None:
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                      for row in (buttons or [])]}
        try:
            self._call("editMessageReplyMarkup",
                       {"chat_id": self.chat_id, "message_id": message_id, "reply_markup": markup}, retries=0)
        except TelegramError:
            pass  # si no se puede editar (mensaje muy viejo), el aviso de texto basta

    def _call(self, method: str, payload: dict, retries: int = 3, timeout: float = 20):
        url = self._url.rsplit("/", 1)[0] + "/" + method
        for attempt in range(retries + 1):
            try:
                r = self._s.post(url, json=payload, timeout=timeout)
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
            return data.get("result")
