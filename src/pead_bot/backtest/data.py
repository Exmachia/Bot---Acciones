"""Junta los datos del backtest (gratis) y los guarda en data/cache/backtest/.

Fuentes:
- Alpaca: lista de acciones, calendario de bolsa, barras diarias y por minuto (SIP).
- Finnhub (plan gratis): EPS real vs. estimado de los últimos 4 trimestres por empresa
  (sin fecha de reporte) y acciones en circulación para aproximar el valor de mercado.
- SEC EDGAR: fecha y HORA exacta de cada reporte de resultados (8-K, item 2.02).

Todo se guarda en disco: si se corta, al volver a correr sigue donde se quedó.
"""
from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from ..data.alpaca import Bar
from ..schedule import NY
from ..strategy.filters import eps_surprise_pct

log = logging.getLogger(__name__)

SEC_UA = {"User-Agent": "pead-bot-backtest/0.1 (personal research)"}  # sin datos personales
EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}


@dataclass
class Event:
    symbol: str
    period: str           # fin del trimestre reportado
    eps_actual: float
    eps_estimate: float
    surprise_pct: float
    report_ts: datetime   # hora del 8-K en la SEC, en Nueva York


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, name: str):
        f = self.root / f"{name}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def put(self, name: str, obj) -> None:
        (self.root / f"{name}.json").write_text(json.dumps(obj), encoding="utf-8")


def _bars_to_rows(bars: list[Bar]) -> list[list]:
    return [[b.t.isoformat(), b.o, b.h, b.l, b.c, b.v] for b in bars]


def rows_to_bars(rows: list[list]) -> list[Bar]:
    return [Bar(datetime.fromisoformat(r[0]), *r[1:]) for r in rows]


def universe(ap, cache: Cache) -> list[str]:
    syms = cache.get("universe")
    if syms is None:
        syms = sorted(a["symbol"] for a in ap.assets()
                      if a.get("tradable") and a.get("exchange") in EXCHANGES
                      and a["symbol"].isalpha())
        cache.put("universe", syms)
    return syms


def daily_bars(ap, symbols: list[str], start: date, end: date, cache: Cache,
               min_avg_volume: float, min_price: float) -> dict[str, list[list]]:
    """Barras diarias del periodo. Para no guardar miles de acciones diminutas, se quedan
    solo las que en algún momento del año fueron líquidas (filtro amplio; el filtro
    exacto se aplica después con los datos de cada fecha)."""
    got = cache.get("daily")
    if got is not None:
        return got
    got = {}
    s = datetime.combine(start, datetime.min.time(), tzinfo=NY)
    e = datetime.combine(end, datetime.min.time(), tzinfo=NY)
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        for sym, bars in ap.bars(chunk, "1Day", s, e, feed="sip").items():
            if len(bars) < 40:
                continue
            vols = [b.v for b in bars]
            if max(b.c for b in bars) < min_price or sum(vols) / len(vols) < min_avg_volume:
                continue
            got[sym] = _bars_to_rows(bars)
        log.info("barras diarias: %d/%d acciones revisadas, %d guardadas",
                 min(i + 100, len(symbols)), len(symbols), len(got))
    cache.put("daily", got)
    return got


def earnings(fh, symbols: list[str], cache: Cache, pause: float = 1.05) -> dict[str, list]:
    """EPS real vs. estimado por empresa (1 llamada por empresa; plan gratis = 60/min)."""
    got = cache.get("earnings") or {}
    todo = [s for s in symbols if s not in got]
    for n, sym in enumerate(todo, 1):
        try:
            got[sym] = fh.earnings_history(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("earnings %s: %s", sym, e)
            got[sym] = []
        _time.sleep(pause)
        if n % 50 == 0 or n == len(todo):
            cache.put("earnings", got)
            log.info("resultados Finnhub: %d/%d", n, len(todo))
    return got


def sec_report_times(symbols: list[str], cache: Cache, pause: float = 0.15) -> dict[str, list[str]]:
    """Fechas/horas (UTC, ISO) de los 8-K con item 2.02 (resultados) de cada empresa."""
    got = cache.get("sec") or {}
    todo = [s for s in symbols if s not in got]
    if not todo:
        return got
    ciks = cache.get("sec_ciks")
    if ciks is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=30)
        r.raise_for_status()
        ciks = {v["ticker"]: v["cik_str"] for v in r.json().values()}
        cache.put("sec_ciks", ciks)
    for n, sym in enumerate(todo, 1):
        cik = ciks.get(sym)
        times = []
        if cik:
            try:
                r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                                 headers=SEC_UA, timeout=30)
                if r.ok:
                    f = r.json()["filings"]["recent"]
                    times = [f["acceptanceDateTime"][i] for i in range(len(f["form"]))
                             if f["form"][i] == "8-K" and "2.02" in (f["items"][i] or "")]
            except (requests.RequestException, KeyError, ValueError) as e:
                log.warning("SEC %s: %s", sym, e)
        got[sym] = times
        _time.sleep(pause)
        if n % 100 == 0 or n == len(todo):
            cache.put("sec", got)
            log.info("fechas SEC: %d/%d", n, len(todo))
    return got


def match_report_time(period: str, sec_times: list[str], max_days: int = 75) -> datetime | None:
    """Primer 8-K de resultados después del fin del trimestre (normalmente 2 a 8 semanas)."""
    end = date.fromisoformat(period)
    best = None
    for t in sec_times:
        ts = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(NY)
        if end < ts.date() <= end + timedelta(days=max_days) and (best is None or ts < best):
            best = ts
    return best


def build_events(earn: dict[str, list], sec: dict[str, list[str]], min_surprise: float,
                 min_abs_estimate: float, start: date, end: date) -> tuple[list[Event], dict]:
    """Eventos con sorpresa suficiente y hora de reporte conocida dentro del periodo."""
    stats = {"trimestres": 0, "con_sorpresa": 0, "sin_fecha_sec": 0, "fuera_de_periodo": 0}
    out = []
    for sym, rows in earn.items():
        for r in rows:
            stats["trimestres"] += 1
            sp = eps_surprise_pct(r.get("actual"), r.get("estimate"), min_abs_estimate)
            if sp is None or sp <= min_surprise:
                continue
            stats["con_sorpresa"] += 1
            ts = match_report_time(r["period"], sec.get(sym, []))
            if ts is None:
                stats["sin_fecha_sec"] += 1
                continue
            if not (start <= ts.date() <= end):
                stats["fuera_de_periodo"] += 1
                continue
            out.append(Event(sym, r["period"], r["actual"], r["estimate"], sp, ts))
    out.sort(key=lambda e: e.report_ts)
    return out, stats


def candidates_with_surprise(earn: dict[str, list], min_surprise: float, min_abs: float) -> list[str]:
    return sorted(s for s, rows in earn.items()
                  if any((sp := eps_surprise_pct(r.get("actual"), r.get("estimate"), min_abs))
                         is not None and sp > min_surprise for r in rows))


def shares(fh, symbols: list[str], cache: Cache, pause: float = 1.05) -> dict[str, float | None]:
    got = cache.get("shares") or {}
    todo = [s for s in symbols if s not in got]
    for n, sym in enumerate(todo, 1):
        try:
            p = fh.profile(sym)
            got[sym] = p.shares_outstanding if p else None
        except Exception as e:  # noqa: BLE001
            log.warning("perfil %s: %s", sym, e)
            got[sym] = None
        _time.sleep(pause)
        if n % 50 == 0 or n == len(todo):
            cache.put("shares", got)
            log.info("acciones en circulación: %d/%d", n, len(todo))
    return got


def minute_bars(ap, symbol: str, day: date, open_dt: datetime, close_dt: datetime,
                cache: Cache) -> tuple[list[Bar], list[Bar]]:
    """Minutos del día de reacción para la acción y para SPY (una sola llamada)."""
    name = f"min_{symbol}_{day.isoformat()}"
    got = cache.get(name)
    if got is None:
        bars = ap.bars([symbol, "SPY"], "1Min", open_dt, close_dt, feed="sip")
        got = {k: _bars_to_rows(v) for k, v in bars.items()}
        cache.put(name, got)
    return rows_to_bars(got.get(symbol, [])), rows_to_bars(got.get("SPY", []))


def event_to_dict(e: Event) -> dict:
    d = asdict(e)
    d["report_ts"] = e.report_ts.isoformat()
    return d
