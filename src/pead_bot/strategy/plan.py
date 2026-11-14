"""Plan de la operación simulada.

- Entrada: precio al que "compraríamos".
- Stop-loss: precio de salida si va en contra (limita la pérdida).
- Objetivo (take profit): precio de salida si va a favor.
- Salida forzada: si no tocó ninguno, se cierra tras N días hábiles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import PlanCfg
from ..schedule import add_trading_days


@dataclass
class TradePlan:
    entry: float
    stop: float
    target: float
    entry_date: date
    deadline: date


def make_plan(entry: float, entry_date: date, cfg: PlanCfg, trading_days: list[date]) -> TradePlan:
    return TradePlan(
        entry=round(entry, 4),
        stop=round(entry * (1 - cfg.stop_loss_pct / 100), 2),
        target=round(entry * (1 + cfg.take_profit_pct / 100), 2),
        entry_date=entry_date,
        deadline=add_trading_days(entry_date, cfg.max_holding_days, trading_days),
    )
