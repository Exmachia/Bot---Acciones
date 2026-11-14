"""Simula UN evento como lo habría vivido el bot (funciones puras, sin red).

Para no "ver el futuro":
- El día que cuenta es el primer día en que el mercado pudo reaccionar al reporte
  (según la hora exacta del 8-K en la SEC).
- Se revisa cada 15 minutos (10:15, 10:30, ... NY), como el bot en vivo. En cada revisión:
  precio = último minuto ANTES de esa hora; volumen = solo hasta hace 16 minutos (el retraso
  del feed gratis). La primera revisión en que pasan todos los filtros es la entrada.
- El valor de mercado se aproxima con las acciones en circulación de HOY × el precio de
  entonces (no hay historia gratis de esto; es una pequeña aproximación).
- La salida usa las mismas reglas conservadoras del cierre diario (fase 4).
- Claude NO se usa: el modelo pudo aprender qué pasó después con estas acciones.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..config import Config
from ..data.alpaca import SIP_DELAY, Bar, TradingDay
from ..schedule import NY, session_minutes_elapsed
from ..strategy.filters import (CandidateData, average_volume, base_close, evaluate,
                                reaction_day, relative_volume)
from ..strategy.plan import make_plan
from ..tracking.costs import TradeCost, trade_cost
from ..tracking.daily import entry_day_bar
from ..tracking.exits import Exit, PathBar, find_exit
from .data import Event

CHECK_EVERY = timedelta(minutes=15)


@dataclass
class SimResult:
    event: Event
    outcome: str                    # "operacion" | "abierta" | motivo por el que no entró
    entry_time: datetime | None = None
    entry: float | None = None
    exit: Exit | None = None
    cost: TradeCost | None = None
    spy_pct: float | None = None
    failed: list[str] | None = None  # filtros que fallaron (si no entró)


def hour_code(ts: datetime, session: TradingDay | None) -> str:
    """bmo / dmh / amc según la hora del reporte respecto a la sesión de ese día."""
    if session is None or ts >= session.close:
        return "amc"
    return "bmo" if ts < session.open else "dmh"


def checkpoints(session: TradingDay) -> list[datetime]:
    t = session.open + timedelta(minutes=30)  # 10:00; antes el volumen relativo aún no se mide
    out = []
    while t <= session.close:
        out.append(t)
        t += CHECK_EVERY
    return out


def _price_before(bars: list[Bar], t: datetime) -> float | None:
    """Cierre del último minuto que ya terminó antes de t (barra de 1 min que empieza en b.t)."""
    last = None
    for b in bars:
        if b.t + timedelta(minutes=1) <= t:
            last = b.c
        else:
            break
    return last


def _volume_until(bars: list[Bar], t: datetime) -> float:
    return sum(b.v for b in bars if b.t + timedelta(minutes=1) <= t)


def simulate(ev: Event, cfg: Config, calendar: list[TradingDay], daily: list[Bar],
             minute: list[Bar], spy_minute: list[Bar], spy_daily: dict[date, float],
             shares: float | None) -> SimResult:
    s, f = cfg.strategy, cfg.filters
    sessions = {d.date: d for d in calendar}
    rd = reaction_day(ev.report_ts, calendar)
    if rd is None or rd not in sessions:
        return SimResult(ev, "sin_calendario")
    session = sessions[rd]
    hour = hour_code(ev.report_ts, sessions.get(ev.report_ts.date()))
    closes = {b.t.astimezone(NY).date(): b.c for b in daily}
    vols = {b.t.astimezone(NY).date(): b.v for b in daily}
    base = base_close(closes, ev.report_ts.date(), hour)
    avg = average_volume(vols, rd, f.avg_volume_days)
    minute = sorted(minute, key=lambda b: b.t)
    spy_minute = sorted(spy_minute, key=lambda b: b.t)

    last_eval = None
    for t in checkpoints(session):
        price = _price_before(minute, t)
        sip_end = t - SIP_DELAY
        mins = session_minutes_elapsed(sip_end, session.open, session.close)
        rvol = (relative_volume(_volume_until(minute, sip_end), avg, mins, f.min_session_minutes_for_rvol)
                if minute else None)
        mcap = shares * base if (shares and base) else None
        d = CandidateData(ev.symbol, ev.symbol, ev.report_ts.date(), hour, ev.eps_actual,
                          ev.eps_estimate, None, None, price=price, base_close=base,
                          avg_volume=avg, market_cap=mcap, rvol=rvol)
        e = evaluate(d, s, f)
        last_eval = e
        # Lo que no cambia en el día (sorpresa, volumen promedio, tamaño) descarta de una vez.
        static_fail = [c.name for c in e.checks if c.passed is False
                       and c.name in ("sorpresa_eps", "volumen_promedio", "valor_mercado")]
        if static_fail:
            return SimResult(ev, "no_paso_filtros", failed=static_fail)
        if e.passed:
            return _trade(ev, cfg, calendar, session, t, price, minute, daily, spy_minute, spy_daily)
    failed = [c.name for c in last_eval.checks if c.passed is not True] if last_eval else ["sin_datos"]
    return SimResult(ev, "no_paso_filtros", failed=failed)


def _trade(ev, cfg, calendar, session, t, price, minute, daily, spy_minute, spy_daily) -> SimResult:
    days = [d.date for d in calendar]
    plan = make_plan(price, session.date, cfg.plan, days)
    path = []
    eb = entry_day_bar(minute, t - timedelta(seconds=1))  # minutos que empiezan desde t
    if eb:
        path.append(eb)
    for b in daily:
        d = b.t.astimezone(NY).date()
        if d > session.date:
            path.append(PathBar(d, b.o, b.h, b.l, b.c))
    ex = find_exit(plan.stop, plan.target, plan.deadline, path)
    spy_in = _price_before(spy_minute, t)
    if ex is None:
        return SimResult(ev, "abierta", t, price)
    spy_out = spy_daily.get(ex.day)
    spy_pct = (spy_out / spy_in - 1) * 100 if spy_in and spy_out else None
    return SimResult(ev, "operacion", t, price, ex, trade_cost(price, ex.price, cfg.costs), spy_pct)
