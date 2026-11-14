from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pead_bot.config import PlanCfg
from pead_bot.schedule import MX, NY, add_trading_days, report_timestamp, scan_phase, to_mx
from pead_bot.strategy.plan import make_plan


def test_ny_to_mx_offset_changes_with_us_dst():
    # Julio: NY en horario de verano (UTC-4), CDMX fijo UTC-6 → 2 h de diferencia
    jul = datetime(2026, 7, 15, 9, 30, tzinfo=NY)
    assert to_mx(jul).hour == 7 and to_mx(jul).minute == 30
    # Diciembre: NY UTC-5 → 1 h de diferencia
    dec = datetime(2026, 12, 15, 9, 30, tzinfo=NY)
    assert to_mx(dec).hour == 8


def test_report_timestamp():
    assert report_timestamp(date(2026, 11, 11), "bmo").hour == 7
    assert report_timestamp(date(2026, 11, 11), "amc").hour == 16
    assert report_timestamp(date(2026, 11, 11), None).hour == 16


def _session(d):
    return (datetime.combine(d, time(9, 30), tzinfo=NY), datetime.combine(d, time(16, 0), tzinfo=NY))


def test_scan_phase():
    d = date(2026, 11, 12)
    o, c = _session(d)
    pm = time(8, 30)
    assert scan_phase(datetime(2026, 11, 12, 8, 35, tzinfo=NY), o, c, pm) == "premarket"
    assert scan_phase(datetime(2026, 11, 12, 9, 0, tzinfo=NY), o, c, pm) is None
    assert scan_phase(datetime(2026, 11, 12, 11, 0, tzinfo=NY), o, c, pm) == "intraday"
    assert scan_phase(datetime(2026, 11, 12, 16, 30, tzinfo=NY), o, c, pm) is None
    assert scan_phase(datetime(2026, 11, 12, 11, 0, tzinfo=NY), None, None, pm) is None
    # un cron en hora de CDMX se interpreta bien
    assert scan_phase(datetime(2026, 11, 12, 10, 0, tzinfo=MX), o, c, pm) == "intraday"


# Calendario con el feriado de Thanksgiving 2026 (jue 26 nov)
DAYS = [date(2026, 11, d) for d in (23, 24, 25, 27, 30)] + [date(2026, 12, d) for d in (1, 2, 3)]


def test_add_trading_days_skips_holiday_and_weekend():
    assert add_trading_days(date(2026, 11, 24), 5, DAYS) == date(2026, 12, 2)


def test_plan_levels():
    p = make_plan(100.0, date(2026, 11, 24), PlanCfg(), DAYS)
    assert p.stop == 95.0 and p.target == 108.0 and p.deadline == date(2026, 12, 2)
