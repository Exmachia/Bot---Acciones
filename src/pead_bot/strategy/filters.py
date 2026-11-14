"""Filtros de la estrategia. Funciones puras (sin red) para poder probarlas.

Glosario rápido:
- EPS (utilidad por acción): ganancia de la empresa dividida entre sus acciones.
- Sorpresa de EPS: cuánto superó (o no) el EPS real al que esperaban los analistas.
- Volumen relativo (RVOL): volumen de hoy comparado con el de un día normal.
- Valor de mercado (market cap): precio × número de acciones; tamaño de la empresa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..config import FiltersCfg, StrategyCfg
from ..schedule import NY, reported_after_close

REGULAR_SESSION_MINUTES = 390.0  # 9:30–16:00


def eps_surprise_pct(actual: float | None, estimate: float | None,
                     min_abs_estimate: float = 0.01) -> float | None:
    """(real − estimado) / |estimado| × 100. None si no se puede calcular.

    Usamos |estimado| para que funcione con estimados negativos: si esperaban
    −0.50 y salió −0.20, la sorpresa es +60% (perdió menos de lo esperado).
    Si el estimado es casi 0 el % explota (0.01 → 0.05 sería +400%), así que se descarta.
    """
    if actual is None or estimate is None:
        return None
    if abs(estimate) < min_abs_estimate:
        return None
    return round((actual - estimate) / abs(estimate) * 100.0, 6)  # evita 10.0000000001


def beat(actual: float | None, estimate: float | None) -> bool | None:
    if actual is None or estimate is None:
        return None
    return actual > estimate


def reaction_day(report_ts: datetime, sessions) -> date | None:
    """Primer día de bolsa en que el mercado puede reaccionar al reporte:
    la primera sesión que cierra después de publicado (bmo/dmh → ese día;
    amc → el siguiente día hábil, aunque haya fin de semana o feriado)."""
    for s in sorted(sessions, key=lambda x: x.close):
        if s.close > report_ts:
            return s.date
    return None


def active_session(now: datetime, sessions) -> date | None:
    """Sesión "en juego": hoy si el mercado no ha cerrado; si ya cerró
    (o es fin de semana/feriado), el siguiente día hábil."""
    return reaction_day(now, sessions)


def is_fresh(report_ts: datetime, now: datetime, sessions) -> bool:
    """El reporte es "nuevo" si ya se publicó y su día de reacción es la
    sesión en juego. Así un reporte del viernes en la tarde sigue vigente
    el lunes antes y durante la apertura, pero no el martes."""
    if report_ts > now:
        return False
    rd = reaction_day(report_ts, sessions)
    return rd is not None and rd == active_session(now, sessions)


def base_close(daily_closes: dict[date, float], report_date: date, hour: str | None) -> float | None:
    """Cierre "previo" contra el cual medimos cuánto ya se movió.

    - Reportó después del cierre (amc): base = cierre de ese mismo día.
    - Antes de la apertura o durante (bmo/dmh): base = cierre del día hábil anterior.
    """
    if reported_after_close(hour):
        eligible = [d for d in daily_closes if d <= report_date]
    else:
        eligible = [d for d in daily_closes if d < report_date]
    if not eligible:
        return None
    return daily_closes[max(eligible)]


def average_volume(daily_volumes: dict[date, float], before: date, days: int) -> float | None:
    """Promedio de los últimos `days` días ANTES de `before` (no incluye hoy)."""
    prior = sorted(d for d in daily_volumes if d < before)[-days:]
    if len(prior) < max(5, days // 2):  # muy poca historia → no confiable
        return None
    return sum(daily_volumes[d] for d in prior) / len(prior)


def relative_volume(today_volume: float, avg_daily_volume: float | None,
                    minutes_elapsed: float, min_minutes: float) -> float | None:
    """Volumen de hoy contra lo "normal a esta hora".

    Se prorratea linealmente: a la mitad de la sesión esperamos medio día de
    volumen. Ojo: el volumen real se concentra al abrir y al cerrar, así que
    al inicio esto exagera un poco el RVOL; por eso se exige un mínimo de minutos.
    """
    if not avg_daily_volume or minutes_elapsed < min_minutes:
        return None
    expected = avg_daily_volume * min(minutes_elapsed, REGULAR_SESSION_MINUTES) / REGULAR_SESSION_MINUTES
    return today_volume / expected if expected > 0 else None


def _money(x: float) -> str:
    return f"${x/1e9:,.1f} mil M" if x >= 1e9 else f"${x/1e6:,.0f} M"


def pct_change(price: float, base: float) -> float:
    return round((price / base - 1.0) * 100.0, 6)


# ---------------------------------------------------------------------------
@dataclass
class CandidateData:
    symbol: str
    name: str
    report_date: date
    hour: str | None
    eps_actual: float | None
    eps_estimate: float | None
    revenue_actual: float | None
    revenue_estimate: float | None
    price: float | None = None
    base_close: float | None = None
    avg_volume: float | None = None
    market_cap: float | None = None
    rvol: float | None = None
    guidance_raised: bool | None = None  # se llena en fase 3 (noticias)


@dataclass
class Check:
    name: str
    passed: bool | None          # None = pendiente (p. ej. RVOL antes de abrir)
    detail: str


@dataclass
class Evaluation:
    data: CandidateData
    checks: list[Check] = field(default_factory=list)
    eps_surprise: float | None = None
    gain_pct: float | None = None
    score: int = 0

    @property
    def passed(self) -> bool:
        return all(c.passed is True for c in self.checks)

    @property
    def pending_only(self) -> bool:
        """Pasa todo salvo checks pendientes (útil en pre-apertura)."""
        return all(c.passed is not False for c in self.checks) and not self.passed

    def failed_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.passed is False]


def evaluate(d: CandidateData, s: StrategyCfg, f: FiltersCfg) -> Evaluation:
    ev = Evaluation(d)
    add = ev.checks.append

    # 1. Sorpresa de EPS
    sp = eps_surprise_pct(d.eps_actual, d.eps_estimate, s.min_abs_eps_estimate)
    ev.eps_surprise = sp
    if sp is None:
        add(Check("sorpresa_eps", False, "sin datos o estimado ~0"))
    else:
        add(Check("sorpresa_eps", sp > s.eps_surprise_min_pct,
                  f"{sp:+.1f}% (mín. >{s.eps_surprise_min_pct}%)"))

    # 2. Calidad
    if d.price is None:
        add(Check("precio", False, "sin precio"))
    else:
        add(Check("precio", d.price > f.min_price, f"${d.price:.2f} (mín. >${f.min_price})"))
    if d.avg_volume is None:
        add(Check("volumen_promedio", False, "sin historia suficiente"))
    else:
        add(Check("volumen_promedio", d.avg_volume > f.min_avg_volume,
                  f"{d.avg_volume:,.0f}/día (mín. >{f.min_avg_volume:,.0f})"))
    if d.market_cap is None:
        add(Check("valor_mercado", False, "sin dato"))
    else:
        add(Check("valor_mercado", d.market_cap > f.min_market_cap,
                  f"{_money(d.market_cap)} (mín. >{_money(f.min_market_cap)})"))

    # 3. No perseguir
    if d.price is None or not d.base_close:
        add(Check("no_perseguir", False, "sin cierre previo"))
    else:
        g = pct_change(d.price, d.base_close)
        ev.gain_pct = g
        add(Check("no_perseguir", g <= f.max_gain_since_prev_close_pct,
                  f"{g:+.1f}% desde cierre previo (máx. {f.max_gain_since_prev_close_pct}%)"))

    # 4. Confirmación por volumen relativo
    if d.rvol is None:
        add(Check("volumen_relativo", None, "pendiente (mercado cerrado o muy temprano)"))
    else:
        add(Check("volumen_relativo", d.rvol > f.min_relative_volume,
                  f"{d.rvol:.1f}x (mín. >{f.min_relative_volume}x)"))

    # Puntos
    score = 1 if (sp is not None and sp > s.eps_surprise_min_pct) else 0
    if beat(d.revenue_actual, d.revenue_estimate):
        score += s.points_revenue_beat
    if d.guidance_raised:
        score += s.points_guidance_raise
    ev.score = score
    return ev
