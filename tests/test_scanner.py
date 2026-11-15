"""Escaneo completo con clientes falsos (sin red)."""
from datetime import date, datetime, time, timedelta

from pead_bot.config import Config
from pead_bot.data.alpaca import Bar, TradingDay
from pead_bot.data.finnhub import CompanyProfile, EarningsReport
from pead_bot.scanner import run_scan
from pead_bot.schedule import NY

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=NY)  # jueves, mediodía NY


def _days():
    d, out = date(2026, 10, 1), []
    while d <= date(2026, 12, 10):
        if d.weekday() < 5 and d != date(2026, 11, 26):
            out.append(TradingDay(d, datetime.combine(d, time(9, 30), tzinfo=NY),
                                  datetime.combine(d, time(16, 0), tzinfo=NY)))
        d += timedelta(days=1)
    return out


class FakeFH:
    def earnings_calendar(self, start, end):
        return [
            EarningsReport("GOOD", date(2026, 11, 11), "amc", 1.30, 1.00, 120, 100),
            EarningsReport("CHASE", date(2026, 11, 11), "amc", 1.30, 1.00, 120, 100),
            EarningsReport("MISS", date(2026, 11, 11), "amc", 0.90, 1.00, 90, 100),
            EarningsReport("OLD", date(2026, 11, 9), "amc", 2.0, 1.0, 1, 1),
            EarningsReport("NOTYET", date(2026, 11, 12), "amc", None, 1.0, None, 1),
        ]

    def profile(self, s):
        return CompanyProfile(s, f"{s} Inc", 10e9, "NASDAQ")


class FakeAP:
    def calendar(self, start, end):
        return _days()

    def bars(self, symbols, timeframe, start, end, feed="sip"):
        out = {}
        for s in symbols:
            if timeframe == "1Day":
                out[s] = [Bar(datetime.combine(d.date, time(0), tzinfo=NY), 100, 101, 99, 100, 2_000_000)
                          for d in _days() if d.date < NOW.date()]
            else:  # 1Min: 2.5M acciones a las 11:44 (134 min) → RVOL ≈ 3.6x
                out[s] = [Bar(start, 100, 100, 100, 100, 2_500_000)]
        return out

    def latest_prices(self, symbols):
        return {s: (110.0 if s == "CHASE" else 104.0) for s in symbols}


def test_scan_end_to_end():
    res = run_scan(Config(), FakeFH(), FakeAP(), now=NOW)
    by = {e.data.symbol: e for e in res.evaluations}
    assert set(by) == {"GOOD", "CHASE"}          # MISS/OLD/NOTYET filtrados antes
    assert by["GOOD"].passed and by["GOOD"].data.rvol > 2
    assert not by["CHASE"].passed
    assert [c.name for c in by["CHASE"].checks if c.passed is False] == ["no_perseguir"]
    assert res.evaluations[0].data.symbol == "GOOD"


def test_after_close_does_not_use_todays_volume_for_tomorrows_reaction():
    """Reporte de hoy después del cierre: reacciona mañana. El volumen de hoy (antes del
    reporte) no debe confirmar nada; queda pendiente para la lista de pre-apertura."""
    class LateFH(FakeFH):
        def earnings_calendar(self, start, end):
            return [EarningsReport("LATE", date(2026, 11, 12), "amc", 1.30, 1.00, 120, 100)]

    after_close = datetime(2026, 11, 12, 16, 45, tzinfo=NY)
    res = run_scan(Config(), LateFH(), FakeAP(), now=after_close)
    ev = res.evaluations[0]
    assert res.active_day == date(2026, 11, 13)
    assert ev.data.rvol is None and not ev.passed and ev.pending_only
