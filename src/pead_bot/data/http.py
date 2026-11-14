"""GET con reintentos simples (límites de tasa de los planes gratuitos)."""
from __future__ import annotations

import time

import requests

from ..safety import assert_url_is_safe


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             retries: int = 4, timeout: float = 20.0):
    assert_url_is_safe(url)
    delay = 2.0
    for attempt in range(retries + 1):
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                r.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code in (401, 403):
            raise PermissionError(
                f"{r.status_code} en {url.split('?')[0]}: llave inválida o sin acceso a este endpoint "
                f"en tu plan. Respuesta: {r.text[:200]}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("inalcanzable")
