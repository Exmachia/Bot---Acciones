"""Horarios. Todo se calcula en hora de Nueva York (que sí cambia de horario
de verano) y solo se convierte a Ciudad de México para mostrarlo
(México ya no tiene horario de verano desde 2022)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MX = ZoneInfo("America/Mexico_City")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


def now_ny() -> datetime:
    return datetime.now(NY)


def to_mx(dt: datetime) -> datetime:
    return dt.astimezone(MX)


def fmt_mx(dt: datetime) -> str:
    return to_mx(dt).strftime("%d/%m/%Y %H:%M") + " (CDMX)"


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def report_timestamp(report_date: date, hour: str | None) -> datetime:
    """Momento aproximado en que se publicó el reporte (hora NY).

    Finnhub da 'bmo' (antes de la apertura), 'amc' (después del cierre),
    'dmh' (durante el mercado) o vacío. Si es desconocido asumimos después
    del cierre: es lo conservador (evita usar información antes de tiempo).
    """
    h = (hour or "").lower()
    t = {"bmo": time(7, 0), "dmh": time(12, 0)}.get(h, time(16, 5))
    return datetime.combine(report_date, t, tzinfo=NY)


def reported_after_close(hour: str | None) -> bool:
    return (hour or "").lower() not in ("bmo", "dmh")


def add_trading_days(start: date, n: int, trading_days: list[date]) -> date:
    """Fecha que está n días hábiles de bolsa después de `start`."""
    future = sorted(d for d in trading_days if d > start)
    if len(future) < n:
        raise ValueError("Calendario de bolsa insuficiente para calcular la fecha límite")
    return future[n - 1]


def session_minutes_elapsed(now: datetime, open_dt: datetime, close_dt: datetime) -> float:
    """Minutos transcurridos de la sesión regular (0 antes de abrir)."""
    if now <= open_dt:
        return 0.0
    end = min(now, close_dt)
    return (end - open_dt).total_seconds() / 60.0


def scan_phase(now: datetime, open_dt: datetime | None, close_dt: datetime | None,
               premarket: time, window_min: int = 15) -> str | None:
    """Devuelve 'premarket', 'intraday' o None según la hora NY.

    La cadencia de 15 min la pone el cron; esto solo decide si, en este
    momento, toca escaneo de pre-apertura, escaneo intradía o nada.
    """
    if open_dt is None or close_dt is None:
        return None  # hoy no hay mercado (fin de semana o feriado)
    now = now.astimezone(NY)
    pm = datetime.combine(now.date(), premarket, tzinfo=NY)
    if pm <= now < min(pm + timedelta(minutes=window_min), open_dt):
        return "premarket"
    if open_dt <= now <= close_dt:
        return "intraday"
    return None
