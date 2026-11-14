"""Candados de seguridad: este bot NUNCA opera con dinero real.

- La URL de trading de Alpaca está fija en PAPER y no se lee de ningún lado.
- Al arrancar se revisan las variables de entorno y config.yaml; si aparece
  el host de trading real, el programa se detiene.
- Las llaves de Alpaca deben ser de paper (empiezan con "PK").
- El bot no implementa ninguna función para mandar órdenes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Constantes, no configurables a propósito.
ALPACA_PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"  # solo datos; no permite operar

# Coincide con "api.alpaca.markets" que NO sea "paper-api." ni "data.".
_LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets", re.IGNORECASE)


class LiveTradingRefused(RuntimeError):
    """Se lanzó porque se detectó algo que apunta a la cuenta real."""


def _mentions_live_host(text: str) -> bool:
    return bool(_LIVE_HOST.search(text or ""))


def assert_url_is_safe(url: str) -> None:
    if _mentions_live_host(url):
        raise LiveTradingRefused(f"URL de trading REAL detectada: {url!r}. Me niego a correr.")
    if url.startswith("https://") and "alpaca.markets" in url:
        if not (url.startswith(ALPACA_PAPER_TRADING_URL) or url.startswith(ALPACA_DATA_URL)):
            raise LiveTradingRefused(f"Host de Alpaca no permitido: {url!r}")


def assert_environment_is_safe(config_path: str | Path | None = None,
                               environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    for name, value in env.items():
        if _mentions_live_host(value):
            raise LiveTradingRefused(
                f"La variable {name} apunta al endpoint REAL de Alpaca. Me niego a correr.")
    if config_path and Path(config_path).exists():
        if _mentions_live_host(Path(config_path).read_text(encoding="utf-8")):
            raise LiveTradingRefused(f"{config_path} menciona el endpoint REAL de Alpaca.")
    key_id = env.get("ALPACA_API_KEY_ID", "")
    if key_id and not key_id.startswith("PK"):
        raise LiveTradingRefused(
            "ALPACA_API_KEY_ID no parece de paper (las de paper empiezan con 'PK'). "
            "Usa las llaves de tu cuenta paper.")
