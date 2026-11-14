"""Variantes de la estrategia para comparar en el backtest (Etapa 1).

Se fijaron ANTES de ver resultados (14 nov 2026) para no acomodarlas a los datos.
Todas eligen las MISMAS acciones (mismos filtros el día de reacción); solo cambia
cuánto tiempo se mantiene, el stop, el objetivo y cuándo se entra.

Regla de elección: gana la que más le saque a SPY (neto de costos) en la PRIMERA mitad
del año; luego se revisa UNA sola vez en la segunda mitad.
"""
from __future__ import annotations

from dataclasses import dataclass

NO_TARGET = 10_000.0  # objetivo tan alto que nunca se toca = "sin tope"


@dataclass(frozen=True)
class Variant:
    key: str
    name: str
    hold_days: int
    stop_pct: float
    target_pct: float      # NO_TARGET = sin tope
    entry: str = "misma_sesion"  # "misma_sesion" | "dia_siguiente"


VARIANTS = [
    Variant("v5", "Actual: 5 días", 5, 5, 8),
    Variant("v20", "20 días", 20, 8, NO_TARGET),
    Variant("v40", "40 días", 40, 10, NO_TARGET),
    Variant("v60", "60 días", 60, 12, NO_TARGET),
    Variant("v20n", "20 días · entrar al día siguiente", 20, 8, NO_TARGET, "dia_siguiente"),
    Variant("v40n", "40 días · entrar al día siguiente", 40, 10, NO_TARGET, "dia_siguiente"),
]
