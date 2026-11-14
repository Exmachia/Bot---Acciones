"""Orquesta un escaneo: calendario → filtros → candidatos."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config
from .data.alpaca import SIP_DELAY, AlpacaClient, TradingDay
from .data.finnhub import FinnhubClient
from .schedule import NY, report_timestamp, session_minutes_elapsed
from .strategy.filters import (CandidateData, Evaluation, average_volume, base_close,
                               eps_surprise_pct, evaluate, is_recent, relative_volume)

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    now: datetime
    session: TradingDay | None
    trading_days: list
    evaluations: list[Evaluation]
    skipped_pre_filter: int


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def run_scan(cfg: Config, fh: FinnhubClient, ap: AlpacaClient, now: datetime | None = None) -> ScanResult:
    now = (now or datetime.now(NY)).astimezone(NY)
    s, f = cfg.strategy, cfg.filters

    cal = ap.calendar(now.date() - timedelta(days=45), now.date() + timedelta(days=30))
    trading_days = [d.date for d in cal]
    session = next((d for d in cal if d.date == now.date()), None)

    # 1) Reportes recientes con sorpresa de EPS suficiente (filtro barato, sin más llamadas)
    reports = fh.earnings_calendar(now.date() - timedelta(days=2), now.date())
    picked = []
    for r in reports:
        if r.eps_actual is None or not r.symbol or "." in r.symbol:
            continue  # aún no reporta, o no es una acción común de EE. UU.
        if not is_recent(report_timestamp(r.date, r.hour), now, s.lookback_hours):
            continue
        sp = eps_surprise_pct(r.eps_actual, r.eps_estimate, s.min_abs_eps_estimate)
        if sp is None or sp <= s.eps_surprise_min_pct:
            continue
        picked.append(r)
    skipped = len(reports) - len(picked)
    if not picked:
        return ScanResult(now, session, trading_days, [], skipped)

    symbols = sorted({r.symbol for r in picked})

    # 2) Barras diarias SIP (historia) y precio actual (IEX, tiempo real)
    daily, prices = {}, {}
    for chunk in _chunks(symbols, 100):
        daily.update(ap.bars(chunk, "1Day", now - timedelta(days=45), now - SIP_DELAY, feed="sip"))
        prices.update(ap.latest_prices(chunk))

    # 3) Volumen de hoy (SIP de minuto hasta hace 16 min) para el volumen relativo
    today_vol: dict[str, float] = {}
    minutes = 0.0
    if session and now > session.open + SIP_DELAY:
        sip_end = min(now - SIP_DELAY, session.close)
        minutes = session_minutes_elapsed(sip_end, session.open, session.close)
        for chunk in _chunks(symbols, 100):
            for sym, bars in ap.bars(chunk, "1Min", session.open, sip_end, feed="sip").items():
                today_vol[sym] = sum(b.v for b in bars)

    evals = []
    for r in picked:
        bars = daily.get(r.symbol, [])
        closes = {b.t.astimezone(NY).date(): b.c for b in bars}
        vols = {b.t.astimezone(NY).date(): b.v for b in bars}
        avg = average_volume(vols, now.date(), f.avg_volume_days)
        try:
            prof = fh.profile(r.symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("perfil %s falló: %s", r.symbol, e)
            prof = None
        rvol = (relative_volume(today_vol[r.symbol], avg, minutes, f.min_session_minutes_for_rvol)
                if r.symbol in today_vol else None)
        data = CandidateData(
            symbol=r.symbol, name=prof.name if prof else r.symbol,
            report_date=r.date, hour=r.hour,
            eps_actual=r.eps_actual, eps_estimate=r.eps_estimate,
            revenue_actual=r.revenue_actual, revenue_estimate=r.revenue_estimate,
            price=prices.get(r.symbol), base_close=base_close(closes, r.date, r.hour),
            avg_volume=avg, market_cap=prof.market_cap_usd if prof else None, rvol=rvol,
        )
        evals.append(evaluate(data, s, f))
    evals.sort(key=lambda e: (e.passed, e.score, e.eps_surprise or 0), reverse=True)
    return ScanResult(now, session, trading_days, evals, skipped)
