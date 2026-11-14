"""Fase 5: simulador del backtest con datos inventados (sin red)."""
from datetime import date, datetime, time, timedelta

import pytest

from pead_bot.backtest.data import Event, build_events, match_report_time
from pead_bot.backtest.run import Stats, compute_stats, verdict
from pead_bot.backtest.sim import checkpoints, hour_code, simulate
from pead_bot.config import Config
from pead_bot.data.alpaca import Bar
from pead_bot.schedule import NY

from test_scanner import _days

CFG = Config()
CAL = _days()
S = {d.date: d for d in CAL}
REPORT = datetime(2026, 11, 11, 16, 5, tzinfo=NY)   # miércoles, después del cierre
RD = date(2026, 11, 12)                              # jueves = día de reacción


def _ev(ts=REPORT, act=1.30, est=1.00):
    return Event("ACME", "2026-09-30", act, est, (act - est) / abs(est) * 100, ts)


def _daily(after=None, avg_vol=2_000_000):
    """Cierre 100 todos los días previos; después del día de reacción, lo que se pida."""
    out = [Bar(datetime.combine(d.date, time(0), tzinfo=NY), 100, 101, 99, 100, avg_vol)
           for d in CAL if d.date < RD]
    for d, (o, h, l, c) in (after or {}).items():
        out.append(Bar(datetime.combine(d, time(0), tzinfo=NY), o, h, l, c, avg_vol))
    return out


def _minutes(price=104.0, per_min=15_000, high=105.0, low=103.0):
    s = S[RD]
    t, out = s.open, []
    while t < s.close:
        out.append(Bar(t, price, high, low, price, per_min))
        t += timedelta(minutes=1)
    return out


SPY_MIN = [Bar(S[RD].open + timedelta(minutes=i), 500, 500, 500, 500, 1) for i in range(390)]
SPY_D = {date(2026, 11, 13): 505.0, date(2026, 11, 16): 505.0}
SHARES = 1e9   # × $100 = $100 mil M de valor de mercado


def test_hour_code_and_checkpoints():
    s = S[RD]
    assert hour_code(datetime(2026, 11, 12, 7, 0, tzinfo=NY), s) == "bmo"
    assert hour_code(datetime(2026, 11, 12, 12, 0, tzinfo=NY), s) == "dmh"
    assert hour_code(datetime(2026, 11, 12, 16, 5, tzinfo=NY), s) == "amc"
    cps = checkpoints(s)
    assert cps[0].time() == time(10, 0) and cps[1].time() == time(10, 15) and cps[-1].time() == time(16, 0)


def test_enters_at_first_confirmed_checkpoint_and_hits_target():
    # A las 10:00 el volumen aún no se mide (14 min); a las 10:15 RVOL ≈ 2.9 → entra a $104.
    r = simulate(_ev(), CFG, CAL, _daily({date(2026, 11, 13): (105, 113, 104, 112)}),
                 _minutes(), SPY_MIN, SPY_D, SHARES)
    assert r.outcome == "operacion"
    assert r.entry_time.time() == time(10, 15) and r.entry == 104.0
    assert (r.exit.day, r.exit.price, r.exit.reason) == (date(2026, 11, 13), 112.32, "objetivo")
    assert r.cost.net_pct == pytest.approx(6.8968, abs=1e-3)
    assert r.spy_pct == pytest.approx(1.0)


def test_never_uses_prices_before_entry():
    # Un mínimo de $90 a las 9:31 (antes de la entrada) no debe disparar el stop.
    mins = _minutes()
    mins[1] = Bar(mins[1].t, 104, 104, 90, 104, 15_000)
    r = simulate(_ev(), CFG, CAL, _daily({date(2026, 11, 13): (105, 113, 104, 112)}),
                 mins, SPY_MIN, SPY_D, SHARES)
    assert r.exit.reason == "objetivo"


def test_chasing_is_rejected_all_day():
    r = simulate(_ev(), CFG, CAL, _daily(), _minutes(price=110, high=111, low=109), SPY_MIN, SPY_D, SHARES)
    assert r.outcome == "no_paso_filtros" and "no_perseguir" in r.failed


def test_low_volume_never_confirms():
    r = simulate(_ev(), CFG, CAL, _daily(), _minutes(per_min=3_000), SPY_MIN, SPY_D, SHARES)
    assert r.outcome == "no_paso_filtros" and "volumen_relativo" in r.failed


def test_static_filters_fail_without_minute_data():
    r = simulate(_ev(), CFG, CAL, _daily(avg_vol=200_000), [], [], SPY_D, SHARES)
    assert r.outcome == "no_paso_filtros" and "volumen_promedio" in r.failed
    r2 = simulate(_ev(), CFG, CAL, _daily(), [], [], SPY_D, shares=None)   # sin valor de mercado
    assert "valor_mercado" in r2.failed


def test_bmo_report_uses_previous_close_as_base():
    # Reporte jueves 7:00 → reacción ese jueves; base = cierre del miércoles (100).
    bmo = _ev(ts=datetime(2026, 11, 12, 7, 0, tzinfo=NY))
    r = simulate(bmo, CFG, CAL, _daily({date(2026, 11, 13): (105, 113, 104, 112)}),
                 _minutes(), SPY_MIN, SPY_D, SHARES)
    assert r.outcome == "operacion" and r.entry == 104.0


def test_open_trade_when_data_ends():
    r = simulate(_ev(), CFG, CAL, _daily(), _minutes(), SPY_MIN, SPY_D, SHARES)
    assert r.outcome == "abierta"


# --- datos -------------------------------------------------------------------
def test_match_report_time_picks_first_after_quarter():
    times = ["2026-08-26T20:21:19.000Z", "2026-11-19T21:20:44.000Z", "2026-10-20T11:05:00.000Z"]
    ts = match_report_time("2026-09-30", times)
    assert ts == datetime(2026, 10, 20, 7, 5, tzinfo=NY)       # UTC → hora de Nueva York
    assert match_report_time("2026-12-31", times) is None


def test_build_events_filters_surprise_and_period():
    earn = {"A": [{"period": "2026-09-30", "actual": 1.3, "estimate": 1.0},
                  {"period": "2026-06-30", "actual": 1.05, "estimate": 1.0}],     # sorpresa 5%
            "B": [{"period": "2026-09-30", "actual": 2.0, "estimate": 1.0}]}      # sin fecha SEC
    sec = {"A": ["2026-10-28T20:10:00.000Z"]}
    evs, st = build_events(earn, sec, 10, 0.01, date(2025, 11, 14), date(2026, 11, 14))
    assert [e.symbol for e in evs] == ["A"] and evs[0].surprise_pct == pytest.approx(30)
    assert st == {"trimestres": 3, "con_sorpresa": 2, "sin_fecha_sec": 1, "fuera_de_periodo": 0}


# --- veredicto honesto -------------------------------------------------------
def _stats(n, avg_net, se, spy_avg):
    from collections import Counter
    return Stats(n, n // 2, avg_net + 1, avg_net, se, 0, 0, 0, spy_avg, Counter())


def test_verdict_is_honest():
    assert "NO SE PUEDE CONCLUIR" in verdict(None)
    assert "NO SE PUEDE CONCLUIR" in verdict(_stats(10, 5, 0.1, 0))
    assert "LE GANÓ A SPY" in verdict(_stats(50, 1.5, 0.3, 0.2))
    assert "NO LE GANA A SPY" in verdict(_stats(50, -1.0, 0.2, 0.3))
    assert "NO HAY EVIDENCIA" in verdict(_stats(50, 0.6, 0.5, 0.2))


def test_compute_stats():
    r = simulate(_ev(), CFG, CAL, _daily({date(2026, 11, 13): (105, 113, 104, 112)}),
                 _minutes(), SPY_MIN, SPY_D, SHARES)
    st = compute_stats([r, r], CFG, 5_000)
    assert st.n == 2 and st.wins == 2 and st.total_mxn == pytest.approx(2 * 344.84, abs=0.01)
    assert st.spy_avg == pytest.approx(1.0) and st.se_net == 0
