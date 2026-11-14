"""Cierre diario de operaciones simuladas (corre después del cierre, 16:30 NY).

Para cada operación abierta arma el "camino" del precio desde la alerta:
- Día de entrada: solo los minutos DESPUÉS de la alerta (no vale lo que pasó antes).
- Días siguientes: barra diaria completa.
Solo se usan sesiones ya terminadas (y con el retraso de 16 min del feed SIP).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..config import CostsCfg
from ..data.alpaca import SIP_DELAY, Bar
from ..schedule import NY
from ..store.db import Trade, TradeStore
from .costs import TradeCost, trade_cost
from .exits import Exit, PathBar, find_exit


@dataclass
class Closed:
    trade: Trade
    exit: Exit
    cost: TradeCost
    spy_pct: float | None  # lo que hizo SPY en la misma ventana


def entry_day_bar(minute_bars: list[Bar], opened_at: datetime) -> PathBar | None:
    after = sorted((b for b in minute_bars if b.t > opened_at), key=lambda b: b.t)
    if not after:
        return None
    return PathBar(opened_at.astimezone(NY).date(), after[0].o, max(b.h for b in after),
                   min(b.l for b in after), after[-1].c)


def build_path(t: Trade, minute_bars: list[Bar], daily_bars: list[Bar], done_days: set) -> list[PathBar]:
    path = []
    if t.entry_date in done_days:
        eb = entry_day_bar(minute_bars, t.opened_at)
        if eb:
            path.append(eb)
    for b in daily_bars:
        d = b.t.astimezone(NY).date()
        if d > t.entry_date and d in done_days:
            path.append(PathBar(d, b.o, b.h, b.l, b.c))
    return path


def spy_window_pct(spy_entry: float | None, spy_exit: float | None) -> float | None:
    if not spy_entry or not spy_exit:
        return None
    return round((spy_exit / spy_entry - 1) * 100, 6)


def track(store: TradeStore, ap, calendar, now: datetime, costs: CostsCfg) -> list[Closed]:
    now = now.astimezone(NY)
    open_ = store.open_trades()
    if not open_:
        return []
    done_days = {s.date for s in calendar if s.close + SIP_DELAY <= now}
    if not done_days:
        return []
    start = datetime.combine(min(t.entry_date for t in open_), datetime.min.time(), tzinfo=NY)
    end = now - SIP_DELAY
    symbols = sorted({t.symbol for t in open_} | {"SPY"})
    daily = ap.bars(symbols, "1Day", start, end, feed="sip")
    spy_close = {b.t.astimezone(NY).date(): b.c for b in daily.get("SPY", [])}
    sessions = {s.date: s for s in calendar}

    closed = []
    for t in open_:
        minute = []
        if t.entry_date in done_days:
            s = sessions[t.entry_date]
            minute = ap.bars([t.symbol], "1Min", t.opened_at, s.close, feed="sip").get(t.symbol, [])
        ex = find_exit(t.stop, t.target, t.deadline,
                       build_path(t, minute, daily.get(t.symbol, []), done_days))
        if ex is None:
            continue
        spy_exit = spy_close.get(ex.day)
        store.close(t.id, ex.day, ex.price, ex.reason, spy_exit)
        closed.append(Closed(t, ex, trade_cost(t.entry, ex.price, costs),
                             spy_window_pct(t.spy_entry, spy_exit)))
    return closed
