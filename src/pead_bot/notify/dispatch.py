"""Decide qué mensajes mandar en un escaneo, sin repetir avisos.

- Alerta (🟢): pasó TODOS los filtros, incluido el volumen relativo. Una vez por
  acción por día.
- Lista de pendientes (⏳): antes de abrir, las que pasan todo menos el volumen
  (aún no se puede medir). Cada acción aparece una sola vez por día.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..config import Config
from ..scanner import ScanResult
from ..store.db import Trade
from ..strategy.plan import make_plan
from .format import format_alert, format_pending, max_points


@dataclass
class Outgoing:
    keys: list[str]   # se marcan como enviadas solo si el mensaje sí salió
    text: str
    trade: Trade | None = field(default=None)  # operación simulada a registrar si sale


def is_pre_open(res: ScanResult) -> bool:
    """True si la sesión en juego todavía no abre."""
    if res.active_day is None:
        return False
    if res.active_day != res.now.date() or res.session is None:
        return True  # fin de semana, feriado o ya cerró hoy → la sesión en juego es otro día
    return res.now < res.session.open


def low_confidence(ev, cfg: Config) -> bool:
    """Claude la calificó por debajo del mínimo de config.yaml → no se avisa."""
    return ev.analysis is not None and ev.analysis.confianza < cfg.llm.min_confidence


def build_messages(res: ScanResult, cfg: Config, sent: set[str]) -> list[Outgoing]:
    day = res.active_day
    if day is None:
        return []
    out: list[Outgoing] = []
    pts = max_points(cfg.strategy.points_revenue_beat, cfg.strategy.points_guidance_raise)

    for ev in res.evaluations:
        if not ev.passed or ev.data.price is None or low_confidence(ev, cfg):
            continue
        key = f"alert:{day.isoformat()}:{ev.data.symbol}"
        if key in sent:
            continue
        plan = make_plan(ev.data.price, day, cfg.plan, res.trading_days)
        trade = Trade(ev.data.symbol, ev.data.name, res.now, day, plan.entry, plan.stop,
                      plan.target, plan.deadline, ev.score,
                      ev.analysis.confianza if ev.analysis is not None else None)
        out.append(Outgoing([key], format_alert(ev, plan, res.now, pts, source=ev.source), trade))

    if is_pre_open(res):
        new = [ev for ev in res.evaluations
               if ev.pending_only and not low_confidence(ev, cfg)
               and f"pending:{day.isoformat()}:{ev.data.symbol}" not in sent]
        if new:
            keys = [f"pending:{day.isoformat()}:{ev.data.symbol}" for ev in new]
            out.append(Outgoing(keys, format_pending(new, day, res.now)))
    return out


class SentStore:
    """Registro simple de avisos ya enviados (archivo JSON local).
    En la fase 4 esto se pasa a SQLite."""

    def __init__(self, path: Path, keep_days: int = 14):
        self.path = Path(path)
        self.keep_days = keep_days

    def load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def save(self, keys: set[str], today: date) -> None:
        cutoff = (today - timedelta(days=self.keep_days)).isoformat()
        kept = sorted(k for k in keys if k.split(":")[1] >= cutoff)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(kept, indent=0), encoding="utf-8")
