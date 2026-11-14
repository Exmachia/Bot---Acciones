"""Costos de operar desde México por el SIC (funciones puras).

Por operación (compra + venta):
- Comisión de la casa de bolsa sobre lo que compras y sobre lo que vendes, más IVA.
- Tipo de cambio: lo que pierdes al pasar pesos a dólares y de regreso (ida y vuelta).
ISR: 10% sobre la GANANCIA NETA del periodo (las pérdidas restan antes de calcularlo,
como en la declaración anual). Si en el periodo perdiste, no hay ISR.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import CostsCfg


@dataclass
class TradeCost:
    gross_pct: float      # lo que se movió el precio
    commission_pct: float  # comisión + IVA, compra y venta
    fx_pct: float         # tipo de cambio
    net_pct: float        # antes de ISR

    def pnl(self, notional: float) -> float:
        return notional * self.net_pct / 100


def trade_cost(entry: float, exit_price: float, c: CostsCfg) -> TradeCost:
    """Todos los % son sobre lo invertido al comprar."""
    ratio = exit_price / entry
    gross = (ratio - 1) * 100
    comm_rate = c.commission_pct * (1 + c.vat_on_commission_pct / 100)
    commission = comm_rate + comm_rate * ratio   # compra + venta (la venta sobre lo que vale al salir)
    fx = c.fx_spread_pct
    return TradeCost(round(gross, 6), round(commission, 6), round(fx, 6),
                     round(gross - commission - fx, 6))


def isr(net_pnl_total: float, c: CostsCfg) -> float:
    """ISR del periodo: solo si hubo ganancia neta."""
    return round(max(0.0, net_pnl_total) * c.isr_pct / 100, 6)


def breakeven_move_pct(c: CostsCfg) -> float:
    """Cuánto tiene que subir una acción solo para no perder por costos (aprox.)."""
    comm_rate = c.commission_pct * (1 + c.vat_on_commission_pct / 100)
    # gross − comm·(2 + gross/100) − fx = 0  →  gross = (2·comm + fx) / (1 − comm/100)
    return round((2 * comm_rate + c.fx_spread_pct) / (1 - comm_rate / 100), 4)
