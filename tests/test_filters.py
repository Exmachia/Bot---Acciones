from datetime import date, datetime, timedelta

import pytest

from pead_bot.config import FiltersCfg, StrategyCfg
from pead_bot.schedule import NY
from pead_bot.strategy.filters import (CandidateData, average_volume, base_close, beat,
                                       eps_surprise_pct, evaluate, is_recent, relative_volume)


@pytest.mark.parametrize("actual,est,expected", [
    (1.10, 1.00, 10.0),
    (1.20, 1.00, 20.0),
    (-0.20, -0.50, 60.0),     # perdió menos de lo esperado → sorpresa positiva
    (-0.60, -0.50, -20.0),
    (0.90, 1.00, -10.0),
])
def test_eps_surprise(actual, est, expected):
    assert eps_surprise_pct(actual, est) == pytest.approx(expected)


def test_eps_surprise_estimate_near_zero_is_none():
    assert eps_surprise_pct(0.05, 0.001) is None
    assert eps_surprise_pct(None, 1.0) is None
    assert eps_surprise_pct(1.0, None) is None


def test_beat():
    assert beat(110, 100) is True
    assert beat(100, 100) is False
    assert beat(None, 100) is None


def test_is_recent():
    now = datetime(2026, 11, 12, 10, 0, tzinfo=NY)
    assert is_recent(now - timedelta(hours=23), now, 24)
    assert not is_recent(now - timedelta(hours=25), now, 24)
    assert not is_recent(now + timedelta(minutes=1), now, 24)  # futuro: aún no pasa


def test_base_close_amc_vs_bmo():
    closes = {date(2026, 11, 10): 100.0, date(2026, 11, 11): 102.0, date(2026, 11, 12): 110.0}
    # reportó el 11 después del cierre → base = cierre del 11
    assert base_close(closes, date(2026, 11, 11), "amc") == 102.0
    # reportó el 11 antes de abrir → base = cierre del 10
    assert base_close(closes, date(2026, 11, 11), "bmo") == 100.0
    # hora desconocida → conservador: como amc
    assert base_close(closes, date(2026, 11, 11), None) == 102.0


def test_average_volume_excludes_today_and_needs_history():
    vols = {date(2026, 10, d): 1_000_000.0 for d in range(1, 29)}
    vols[date(2026, 10, 29)] = 50_000_000.0  # "hoy" con volumen enorme
    assert average_volume(vols, date(2026, 10, 29), 20) == pytest.approx(1_000_000)
    assert average_volume({date(2026, 10, 1): 1.0}, date(2026, 10, 2), 20) is None


def test_relative_volume_prorated():
    # a la mitad de la sesión (195 min) con volumen de un día completo → 2x
    assert relative_volume(1_000_000, 1_000_000, 195, 15) == pytest.approx(2.0)
    assert relative_volume(1_000_000, 1_000_000, 390, 15) == pytest.approx(1.0)
    assert relative_volume(1_000_000, 1_000_000, 10, 15) is None   # muy temprano
    assert relative_volume(1_000_000, None, 200, 15) is None


def _cand(**kw):
    base = dict(symbol="ABC", name="ABC Corp", report_date=date(2026, 11, 11), hour="amc",
                eps_actual=1.20, eps_estimate=1.00, revenue_actual=110.0, revenue_estimate=100.0,
                price=104.0, base_close=100.0, avg_volume=2_000_000, market_cap=5e9, rvol=3.0)
    base.update(kw)
    return CandidateData(**base)


S, F = StrategyCfg(), FiltersCfg()


def test_good_candidate_passes_with_points():
    ev = evaluate(_cand(), S, F)
    assert ev.passed, ev.failed_reasons()
    assert ev.score == 2  # EPS + ingresos
    assert evaluate(_cand(guidance_raised=True), S, F).score == 3


@pytest.mark.parametrize("override,failing", [
    (dict(eps_actual=1.10), "sorpresa_eps"),            # exactamente 10% no es "más de 10%"
    (dict(price=4.99, base_close=4.80), "precio"),
    (dict(avg_volume=999_999), "volumen_promedio"),
    (dict(market_cap=9e8), "valor_mercado"),
    (dict(price=108.5), "no_perseguir"),                # +8.5% ya es perseguir
    (dict(rvol=1.9), "volumen_relativo"),
])
def test_each_filter_rejects(override, failing):
    ev = evaluate(_cand(**override), S, F)
    assert not ev.passed
    assert [c.name for c in ev.checks if c.passed is False] == [failing]


def test_exactly_8_pct_is_allowed():
    assert evaluate(_cand(price=108.0), S, F).passed


def test_rvol_pending_premarket():
    ev = evaluate(_cand(rvol=None), S, F)
    assert not ev.passed and ev.pending_only


def test_revenue_miss_no_extra_point():
    assert evaluate(_cand(revenue_actual=90.0), S, F).score == 1
