"""Mensajes de cierre y reporte semanal (domingo), con costos y contra SPY.

Cada operación se mide con el monto que dijo su alerta (`monto_mxn`); si no lo
tiene (operaciones viejas), con el monto por defecto que se pase.
SPY se mide en los MISMOS días que cada operación y sin costos: es la vara
"¿me habría ido mejor solo teniendo el índice?".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

from ..config import CostsCfg
from ..notify.format import fmt_date
from ..store.db import Trade
from .costs import breakeven_move_pct, isr, trade_cost
from .daily import Closed, spy_window_pct

_REASON = {"stop": "tocó el stop", "objetivo": "llegó al objetivo", "fecha_limite": "fecha límite"}
MIN_TRADES_TO_JUDGE = 20


def mxn(x: float) -> str:
    sign = "-" if x < 0 else "+"
    return f"{sign}${abs(x):,.0f} MXN"


def amount_of(t: Trade, default: float) -> float:
    return t.monto_mxn if t.monto_mxn else default


def format_closed(c: Closed, default_amount: float) -> str:
    t, e, k = c.trade, c.exit, c.cost
    notional = amount_of(t, default_amount)
    icon = "✅" if k.net_pct > 0 else "🔻"
    lines = [
        f"{icon} <b>Cerré (simulada) {escape(t.symbol)}</b> · {_REASON.get(e.reason, e.reason)}"
        + (" · tú ibas en esta" if t.decision == "voy" else ""),
        f"Entrada ${t.entry:,.2f} ({fmt_date(t.entry_date)}) → salida ${e.price:,.2f} ({fmt_date(e.day)})",
        f"Movimiento: {k.gross_pct:+.1f}% · después de comisión, IVA y tipo de cambio: {k.net_pct:+.1f}%",
        f"Con ${notional:,.0f} MXN: {mxn(k.pnl(notional))} (antes de ISR)",
    ]
    if c.spy_pct is not None:
        lines.append(f"SPY en esos mismos días: {c.spy_pct:+.1f}%")
    return "\n".join(lines)


@dataclass
class Summary:
    n: int
    wins: int
    net_pnl: float      # MXN antes de ISR
    tax: float          # ISR
    spy_pnl: float      # MXN si ese dinero hubiera estado en SPY esos días
    spy_known: int      # operaciones con dato de SPY

    @property
    def after_tax(self) -> float:
        return self.net_pnl - self.tax


def summarize(trades: list[Trade], costs: CostsCfg, default_amount: float) -> Summary:
    net = spy = 0.0
    wins = known = 0
    for t in trades:
        k = trade_cost(t.entry, t.exit_price, costs)
        notional = amount_of(t, default_amount)
        net += k.pnl(notional)
        wins += k.net_pct > 0
        sp = spy_window_pct(t.spy_entry, t.spy_exit)
        if sp is not None:
            spy += notional * sp / 100
            known += 1
    return Summary(len(trades), wins, round(net, 2), isr(net, costs), round(spy, 2), known)


def _summary_lines(title: str, s: Summary) -> list[str]:
    if s.n == 0:
        return [f"<b>{title}</b>", "Sin operaciones cerradas."]
    lines = [
        f"<b>{title}</b>",
        f"Operaciones: {s.n} · ganadoras: {s.wins} ({s.wins / s.n * 100:.0f}%)",
        f"Resultado: {mxn(s.net_pnl)} · ISR: -${s.tax:,.0f} · neto: <b>{mxn(s.after_tax)}</b>",
    ]
    if s.spy_known:
        diff = s.after_tax - s.spy_pnl
        verdict = "le ganó" if diff > 0 else "perdió contra"
        lines.append(f"SPY en los mismos días: {mxn(s.spy_pnl)} → la estrategia {verdict} SPY por "
                     f"{mxn(abs(diff))[1:]}")
    return lines


def format_weekly(week: list[Trade], all_closed: list[Trade], open_: list[Trade],
                  today: date, costs: CostsCfg, default_amount: float) -> str:
    lines = [
        f"📊 <b>Reporte semanal · {fmt_date(today - timedelta(days=6))} a {fmt_date(today)}</b>",
        "<i>Simulado, con el monto que dio cada alerta según tu regla.</i>",
        "",
    ]
    for t in week:
        k = trade_cost(t.entry, t.exit_price, costs)
        lines.append(f"• {escape(t.symbol)}: {k.net_pct:+.1f}% neto = "
                     f"{mxn(k.pnl(amount_of(t, default_amount)))} ({_REASON.get(t.exit_reason, '')})")
    if week:
        lines.append("")
    lines += _summary_lines("Esta semana", summarize(week, costs, default_amount)) + [""]
    total = summarize(all_closed, costs, default_amount)
    lines += _summary_lines("Desde el inicio", total) + [""]
    if open_:
        lines.append(f"<b>Abiertas ({len(open_)})</b>")
        for t in open_:
            lines.append(f"• {escape(t.symbol)} (${amount_of(t, default_amount):,.0f}): entrada ${t.entry:,.2f} · stop ${t.stop:,.2f} · "
                         f"objetivo ${t.target:,.2f} · límite {fmt_date(t.deadline)}")
        lines.append("")
    lines.append(f"Costos: cada operación necesita subir ~{breakeven_move_pct(costs):.1f}% solo para "
                 "no perder (comisión, IVA y tipo de cambio).")
    if total.n < MIN_TRADES_TO_JUDGE:
        lines.append(f"⚠️ Con {total.n} operación(es) todavía no se puede concluir nada; "
                     f"hacen falta al menos {MIN_TRADES_TO_JUDGE}.")
    return "\n".join(lines)
