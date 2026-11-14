"""Fase 4: costos, reglas de salida, SQLite, cierre diario y reporte (sin red)."""
from datetime import date, datetime, time

import pytest

from pead_bot.config import Config, CostsCfg
from pead_bot.data.alpaca import Bar
from pead_bot.notify.dispatch import build_messages
from pead_bot.scanner import run_scan
from pead_bot.schedule import NY
from pead_bot.store.db import Trade, TradeStore
from pead_bot.tracking.costs import breakeven_move_pct, isr, trade_cost
from pead_bot.tracking.daily import track
from pead_bot.tracking.exits import PathBar, find_exit
from pead_bot.tracking.report import format_closed, format_weekly, summarize

from test_scanner import NOW, FakeAP, FakeFH, _days

C = CostsCfg()  # comisión 0.25% + IVA 16% = 0.29%, tipo de cambio 0.5%, ISR 10%


# --- costos (cuentas hechas a mano) -------------------------------------------
def test_cost_winner():
    k = trade_cost(100, 108, C)
    assert k.gross_pct == pytest.approx(8.0)
    assert k.commission_pct == pytest.approx(0.29 + 0.29 * 1.08)   # compra + venta
    assert k.fx_pct == pytest.approx(0.5)
    assert k.net_pct == pytest.approx(8 - 0.6032 - 0.5)            # 6.8968
    assert k.pnl(10_000) == pytest.approx(689.68)


def test_cost_loser():
    k = trade_cost(100, 95, C)
    assert k.net_pct == pytest.approx(-5 - (0.29 + 0.29 * 0.95) - 0.5)  # -6.0655


def test_breakeven_really_breaks_even():
    be = breakeven_move_pct(C)
    assert be == pytest.approx(1.0831, abs=1e-4)
    assert trade_cost(100, 100 * (1 + be / 100), C).net_pct == pytest.approx(0, abs=1e-3)


def test_isr_only_on_net_gain():
    assert isr(1000, C) == pytest.approx(100)
    assert isr(-500, C) == 0


# --- reglas de salida -------------------------------------------------------
D = [date(2026, 11, d) for d in (12, 13, 16, 17, 18, 19)]
DEADLINE = date(2026, 11, 19)


def _pb(i, o, h, l, c):
    return PathBar(D[i], o, h, l, c)


def test_exit_target():
    e = find_exit(95, 108, DEADLINE, [_pb(0, 100, 103, 99, 102), _pb(1, 102, 109, 101, 108.5)])
    assert (e.day, e.price, e.reason) == (D[1], 108, "objetivo")


def test_exit_stop():
    e = find_exit(95, 108, DEADLINE, [_pb(0, 100, 101, 94, 96)])
    assert (e.price, e.reason) == (95, "stop")


def test_gap_down_exits_at_open_not_stop():
    e = find_exit(95, 108, DEADLINE, [_pb(0, 100, 101, 99, 100), _pb(1, 90, 92, 88, 91)])
    assert (e.price, e.reason) == (90, "stop")


def test_gap_up_exits_at_open():
    e = find_exit(95, 108, DEADLINE, [_pb(0, 112, 115, 110, 114)])
    assert (e.price, e.reason) == (112, "objetivo")


def test_same_bar_both_assumes_stop():
    e = find_exit(95, 108, DEADLINE, [_pb(0, 100, 110, 94, 105)])
    assert e.reason == "stop"


def test_deadline_exits_at_close():
    bars = [_pb(i, 100, 102, 98, 100 + i * 0.1) for i in range(6)]
    e = find_exit(95, 108, DEADLINE, bars)
    assert (e.day, e.price, e.reason) == (DEADLINE, pytest.approx(100.5), "fecha_limite")


def test_still_open():
    assert find_exit(95, 108, DEADLINE, [_pb(0, 100, 102, 98, 101)]) is None


# --- SQLite -----------------------------------------------------------------
def _trade(symbol="GOOD", entry=104.0, spy=500.0):
    return Trade(symbol, f"{symbol} Inc", NOW, NOW.date(), entry, round(entry * .95, 2),
                 round(entry * 1.08, 2), DEADLINE, 2, 4, spy)


def test_store_roundtrip_and_unique():
    st = TradeStore(":memory:")
    assert st.add(_trade()) is True
    assert st.add(_trade()) is False                 # misma acción, mismo día
    t = st.open_trades()[0]
    assert t.opened_at == NOW and t.deadline == DEADLINE and t.confidence == 4
    st.close(t.id, D[2], 112.32, "objetivo", 505.0)
    assert st.open_trades() == []
    c = st.closed_between(D[0], D[5])[0]
    assert (c.exit_date, c.exit_reason, c.spy_exit) == (D[2], "objetivo", 505.0)


def test_alert_message_carries_trade():
    res = run_scan(Config(), FakeFH(), FakeAP(), now=NOW)
    m = build_messages(res, Config(), set())[0]
    assert m.trade.symbol == "GOOD" and m.trade.entry == 104.0
    assert m.trade.stop == 98.8 and m.trade.target == 112.32
    assert m.trade.deadline == DEADLINE and m.trade.opened_at == NOW


# --- cierre diario de punta a punta -----------------------------------------
class TrackAP:
    """Alerta el jue 12 a mediodía a $104 (stop 98.80, objetivo 112.32).
    Antes de la alerta hubo un mínimo de $90 que NO debe contar.
    El lun 16 llega a $113 → cierra en el objetivo."""

    def __init__(self):
        self.calls = []

    def bars(self, symbols, timeframe, start, end, feed="sip"):
        self.calls.append((tuple(symbols), timeframe))
        out = {}
        if timeframe == "1Min":
            out["GOOD"] = [Bar(datetime.combine(D[0], time(10, 0), tzinfo=NY), 95, 96, 90, 95, 1),
                           Bar(datetime.combine(D[0], time(13, 0), tzinfo=NY), 104, 106, 102, 105, 1)]
            return out
        day = lambda d: datetime.combine(d, time(0), tzinfo=NY)  # noqa: E731
        out["GOOD"] = [Bar(day(D[0]), 95, 106, 90, 105, 1), Bar(day(D[1]), 105, 107, 103, 106, 1),
                       Bar(day(D[2]), 106, 113, 105, 112, 1)]
        out["SPY"] = [Bar(day(d), 500, 501, 499, 500 + i, 1) for i, d in enumerate(D[:3])]
        return out


def test_track_closes_on_target_and_ignores_pre_alert_prices():
    st = TradeStore(":memory:")
    st.add(_trade(spy=500.0))
    after_close_mon = datetime(2026, 11, 16, 16, 40, tzinfo=NY)
    closed = track(st, TrackAP(), _days(), after_close_mon, C)
    assert len(closed) == 1
    c = closed[0]
    assert (c.exit.day, c.exit.price, c.exit.reason) == (D[2], 112.32, "objetivo")
    assert c.spy_pct == pytest.approx(0.4)          # 500 → 502
    assert st.open_trades() == []


def test_track_waits_for_session_to_finish():
    st = TradeStore(":memory:")
    st.add(_trade())
    during_thu = datetime(2026, 11, 12, 15, 0, tzinfo=NY)   # el día de entrada aún no termina
    assert track(st, TrackAP(), _days(), during_thu, C) == []
    assert len(st.open_trades()) == 1


# --- mensajes ---------------------------------------------------------------
def test_closed_message():
    st = TradeStore(":memory:")
    st.add(_trade(spy=500.0))
    c = track(st, TrackAP(), _days(), datetime(2026, 11, 16, 16, 40, tzinfo=NY), C)[0]
    txt = format_closed(c, 10_000)
    assert "Cerré (simulada) GOOD" in txt and "llegó al objetivo" in txt
    assert "+8.0%" in txt and "SPY en esos mismos días: +0.4%" in txt
    assert "MXN" in txt


def _closed(symbol, entry, exit_price, spy_in=500.0, spy_out=500.0, day=D[3]):
    t = _trade(symbol, entry, spy_in)
    t.status, t.exit_price, t.exit_date, t.exit_reason, t.spy_exit = "closed", exit_price, day, "objetivo", spy_out
    return t


def test_summary_tax_and_spy():
    ts = [_closed("A", 100, 108, 500, 505), _closed("B", 100, 95, 500, 495)]
    s = summarize(ts, C, 10_000)
    assert s.n == 2 and s.wins == 1
    assert s.net_pnl == pytest.approx(689.68 - 606.55)
    assert s.tax == pytest.approx((689.68 - 606.55) * 0.10)
    assert s.spy_pnl == pytest.approx(100 - 100)      # +1% y −1%


def test_weekly_report_is_honest_with_few_trades():
    week = [_closed("A", 100, 108)]
    txt = format_weekly(week, week, [_trade("OPEN")], D[5], C, 10_000)
    assert "Reporte semanal" in txt and "Esta semana" in txt and "Desde el inicio" in txt
    assert "Abiertas (1)" in txt and "OPEN" in txt
    assert "~1.1%" in txt                              # punto de equilibrio por costos
    assert "todavía no se puede concluir" in txt
