"""Corre el backtest de 1 año con datos gratis y arma el reporte."""
from __future__ import annotations

import csv
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import Config
from ..schedule import NY
from ..tracking.costs import breakeven_move_pct, isr
from . import data as D
from .sim import SimResult, simulate

log = logging.getLogger(__name__)


@dataclass
class BacktestReport:
    start: date
    end: date
    universe: int
    liquid: int
    event_stats: dict
    results: list[SimResult] = field(default_factory=list)
    spy_buy_hold_pct: float | None = None

    @property
    def trades(self) -> list[SimResult]:
        return [r for r in self.results if r.outcome == "operacion"]


def collect_and_simulate(cfg: Config, fh, ap, cache_dir: Path, end: date) -> BacktestReport:
    start = end - timedelta(days=365)
    cache = D.Cache(cache_dir)
    f, s = cfg.filters, cfg.strategy

    syms = D.universe(ap, cache)
    log.info("universo: %d acciones", len(syms))
    daily = D.daily_bars(ap, syms + ["SPY"], start - timedelta(days=45), end, cache,
                         min_avg_volume=f.min_avg_volume * 0.5, min_price=f.min_price)
    liquid = [x for x in daily if x != "SPY"]
    log.info("líquidas en algún momento del año: %d", len(liquid))

    earn = D.earnings(fh, liquid, cache)
    with_surprise = D.candidates_with_surprise(earn, s.eps_surprise_min_pct, s.min_abs_eps_estimate)
    sec = D.sec_report_times(with_surprise, cache)
    events, stats = D.build_events(earn, sec, s.eps_surprise_min_pct, s.min_abs_eps_estimate, start, end)
    log.info("eventos con sorpresa y fecha: %d", len(events))
    shares = D.shares(fh, sorted({e.symbol for e in events}), cache)

    cal = ap.calendar(start - timedelta(days=45), end + timedelta(days=30))
    sessions = {d.date: d for d in cal}
    spy_bars = D.rows_to_bars(daily["SPY"])
    spy_daily = {b.t.astimezone(NY).date(): b.c for b in spy_bars}

    from ..strategy.filters import reaction_day
    rep = BacktestReport(start, end, len(syms), len(liquid), stats)
    for n, ev in enumerate(events, 1):
        rd = reaction_day(ev.report_ts, cal)
        bars = D.rows_to_bars(daily.get(ev.symbol, []))
        if rd is None or rd not in sessions or rd > end:
            rep.results.append(SimResult(ev, "sin_calendario"))
            continue
        # Solo bajar minutos si lo que no cambia en el día ya pasa (ahorra llamadas).
        quick = simulate(ev, cfg, cal, bars, [], [], spy_daily, shares.get(ev.symbol))
        if quick.outcome == "no_paso_filtros" and set(quick.failed or []) & {
                "sorpresa_eps", "volumen_promedio", "valor_mercado"}:
            rep.results.append(quick)
            continue
        ss = sessions[rd]
        minute, spy_min = D.minute_bars(ap, ev.symbol, rd, ss.open, ss.close, cache)
        rep.results.append(simulate(ev, cfg, cal, bars, minute, spy_min, spy_daily, shares.get(ev.symbol)))
        if n % 25 == 0:
            log.info("simulados %d/%d", n, len(events))

    in_period = [b for b in spy_bars if start <= b.t.astimezone(NY).date() <= end]
    if in_period:
        rep.spy_buy_hold_pct = (in_period[-1].c / in_period[0].c - 1) * 100
    return rep


def write_csv(rep: BacktestReport, path: Path, amount: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["accion", "reporte_ny", "sorpresa_eps_pct", "resultado", "entrada_ny", "entrada",
                    "salida_dia", "salida", "motivo", "bruto_pct", "neto_pct", "mxn", "spy_pct", "fallaron"])
        for r in rep.results:
            e = r.event
            w.writerow([e.symbol, e.report_ts.isoformat(), round(e.surprise_pct, 1), r.outcome,
                        r.entry_time.isoformat() if r.entry_time else "", r.entry or "",
                        r.exit.day if r.exit else "", r.exit.price if r.exit else "",
                        r.exit.reason if r.exit else "",
                        round(r.cost.gross_pct, 2) if r.cost else "", round(r.cost.net_pct, 2) if r.cost else "",
                        round(r.cost.pnl(amount), 2) if r.cost else "",
                        round(r.spy_pct, 2) if r.spy_pct is not None else "",
                        ",".join(r.failed or [])])


@dataclass
class Stats:
    n: int
    wins: int
    avg_gross: float
    avg_net: float
    se_net: float           # error estándar del promedio neto
    total_mxn: float
    tax: float
    spy_mxn: float
    spy_avg: float
    by_reason: Counter


def compute_stats(trades: list[SimResult], cfg: Config, amount: float) -> Stats | None:
    if not trades:
        return None
    nets = [t.cost.net_pct for t in trades]
    n = len(nets)
    avg = sum(nets) / n
    sd = math.sqrt(sum((x - avg) ** 2 for x in nets) / (n - 1)) if n > 1 else 0.0
    total = sum(t.cost.pnl(amount) for t in trades)
    spys = [t.spy_pct for t in trades if t.spy_pct is not None]
    return Stats(n, sum(x > 0 for x in nets), sum(t.cost.gross_pct for t in trades) / n, avg,
                 sd / math.sqrt(n) if n > 1 else 0.0, total, isr(total, cfg.costs),
                 sum(amount * p / 100 for p in spys), sum(spys) / len(spys) if spys else 0.0,
                 Counter(t.exit.reason for t in trades))


def verdict(st: Stats | None) -> str:
    if st is None or st.n < 20:
        return ("NO SE PUEDE CONCLUIR: hubo muy pocas operaciones (menos de 20). "
                "Con tan pocos casos el resultado puede ser pura suerte.")
    edge = st.avg_net - st.spy_avg       # % por operación por encima de SPY en los mismos días
    low, high = edge - 2 * st.se_net, edge + 2 * st.se_net
    if low > 0:
        return (f"LE GANÓ A SPY: {edge:+.2f}% por operación después de costos, y aun en el escenario "
                f"malo (rango {low:+.2f}% a {high:+.2f}%) sigue arriba. Prometedor, pero es 1 año.")
    if high < 0:
        return (f"NO LE GANA A SPY: {edge:+.2f}% por operación después de costos (rango {low:+.2f}% a "
                f"{high:+.2f}%). Con estas reglas, te iría mejor solo comprando el índice.")
    return (f"NO HAY EVIDENCIA DE QUE LE GANE A SPY: {edge:+.2f}% por operación después de costos, "
            f"pero el rango ({low:+.2f}% a {high:+.2f}%) incluye perder. Podría ser suerte.")


def format_report(rep: BacktestReport, cfg: Config, amount: float) -> str:
    st = compute_stats(rep.trades, cfg, amount)
    out = Counter(r.outcome for r in rep.results)
    fails = Counter(x for r in rep.results if r.outcome == "no_paso_filtros" for x in (r.failed or []))
    L = [
        f"BACKTEST {rep.start} a {rep.end} (datos gratis, sin Claude)",
        f"Monto por operación: ${amount:,.0f} MXN (el de 'sin lectura' de tu regla)",
        "",
        f"Acciones revisadas: {rep.universe:,} · líquidas: {rep.liquid:,}",
        f"Trimestres con datos: {rep.event_stats.get('trimestres', 0):,} · con sorpresa >"
        f"{cfg.strategy.eps_surprise_min_pct:.0f}%: {rep.event_stats.get('con_sorpresa', 0):,} · "
        f"sin hora en la SEC: {rep.event_stats.get('sin_fecha_sec', 0):,}",
        f"Eventos simulados: {len(rep.results):,} → operaciones: {out.get('operacion', 0)} · "
        f"aún abiertas: {out.get('abierta', 0)} · no pasaron filtros: {out.get('no_paso_filtros', 0)}",
        "Filtros que más descartaron: " + ", ".join(f"{k} ({v})" for k, v in fails.most_common(5)),
        "",
    ]
    if st:
        L += [
            f"Operaciones: {st.n} · ganadoras: {st.wins} ({st.wins / st.n * 100:.0f}%)",
            f"Salidas: " + ", ".join(f"{k} {v}" for k, v in st.by_reason.most_common()),
            f"Promedio por operación: {st.avg_gross:+.2f}% bruto · {st.avg_net:+.2f}% neto de costos "
            f"(punto de equilibrio ~{breakeven_move_pct(cfg.costs):.1f}%)",
            f"Total: {st.total_mxn:+,.0f} MXN · ISR: -{st.tax:,.0f} · neto: {st.total_mxn - st.tax:+,.0f} MXN",
            f"SPY en los mismos días: {st.spy_avg:+.2f}% por operación · {st.spy_mxn:+,.0f} MXN",
        ]
    if rep.spy_buy_hold_pct is not None:
        L.append(f"Referencia: SPY comprado y guardado todo el año: {rep.spy_buy_hold_pct:+.1f}%")
    L += ["", "VEREDICTO: " + verdict(st), "",
          "Limitaciones: solo 1 año; solo empresas que existen hoy (supervivencia); valor de mercado",
          "aproximado con acciones de hoy; sin el punto por ventas ni el filtro de Claude;",
          "el volumen y precio por minuto son del feed SIP (en vivo el precio viene de IEX)."]
    return "\n".join(L)
