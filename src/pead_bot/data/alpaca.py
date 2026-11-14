"""Cliente de Alpaca SOLO para datos y calendario.

- Trading: únicamente paper (constante en safety.py). Aquí solo se usa para
  leer el calendario de bolsa. No existe ninguna función para mandar órdenes.
- Datos: data.alpaca.markets. En el plan gratuito:
  * feed 'iex' = tiempo real pero solo de la bolsa IEX (una fracción del volumen).
  * feed 'sip' = todo el mercado, pero solo histórico (no los últimos 15 min).
  Por eso: precio actual con IEX; volúmenes con SIP hasta hace 16 minutos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..safety import ALPACA_DATA_URL, ALPACA_PAPER_TRADING_URL
from ..schedule import NY
from .http import get_json

SIP_DELAY = timedelta(minutes=16)


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class TradingDay:
    date: date
    open: datetime
    close: datetime


@dataclass
class NewsItem:
    id: int
    headline: str
    summary: str
    url: str
    source: str
    created_at: datetime


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(NY).isoformat()


class AlpacaClient:
    def __init__(self, key_id: str, secret: str):
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}

    # ---- paper (solo lectura) ----
    def check_paper_account(self) -> dict:
        """Si las llaves fueran reales, el endpoint de paper las rechaza."""
        return get_json(f"{ALPACA_PAPER_TRADING_URL}/v2/account", headers=self.headers)

    def assets(self) -> list[dict]:
        """Acciones de EE. UU. activas (solo lectura, endpoint de paper)."""
        return get_json(f"{ALPACA_PAPER_TRADING_URL}/v2/assets", headers=self.headers,
                        params={"status": "active", "asset_class": "us_equity"})

    def calendar(self, start: date, end: date) -> list[TradingDay]:
        rows = get_json(f"{ALPACA_PAPER_TRADING_URL}/v2/calendar", headers=self.headers,
                        params={"start": start.isoformat(), "end": end.isoformat()})
        out = []
        for r in rows:
            d = date.fromisoformat(r["date"])
            o = datetime.combine(d, datetime.strptime(r["open"], "%H:%M").time(), tzinfo=NY)
            c = datetime.combine(d, datetime.strptime(r["close"], "%H:%M").time(), tzinfo=NY)
            out.append(TradingDay(d, o, c))
        return out

    # ---- datos ----
    def bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime,
             feed: str = "sip") -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {s: [] for s in symbols}
        params = {"symbols": ",".join(symbols), "timeframe": timeframe,
                  "start": _rfc3339(start), "end": _rfc3339(end), "feed": feed,
                  "adjustment": "split", "limit": 10000}
        while True:
            data = get_json(f"{ALPACA_DATA_URL}/v2/stocks/bars", headers=self.headers, params=params)
            for sym, rows in (data.get("bars") or {}).items():
                out.setdefault(sym, []).extend(
                    Bar(_parse_ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]) for b in rows)
            token = data.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        data = get_json(f"{ALPACA_DATA_URL}/v2/stocks/trades/latest", headers=self.headers,
                        params={"symbols": ",".join(symbols), "feed": "iex"})
        return {s: t["p"] for s, t in (data.get("trades") or {}).items()}

    def news(self, symbol: str, start: datetime, end: datetime, limit: int = 10) -> list[NewsItem]:
        data = get_json(f"{ALPACA_DATA_URL}/v1beta1/news", headers=self.headers,
                        params={"symbols": symbol, "start": _rfc3339(start), "end": _rfc3339(end),
                                "limit": limit, "sort": "desc", "include_content": "false"})
        return [NewsItem(n["id"], n.get("headline", ""), n.get("summary", ""), n.get("url", ""),
                         n.get("source", ""), _parse_ts(n["created_at"]))
                for n in data.get("news") or []]
