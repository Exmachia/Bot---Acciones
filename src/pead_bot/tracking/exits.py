"""Cuándo y a qué precio se cierra una operación simulada (función pura).

Se revisan barras en orden (el día de entrada solo cuenta lo que pasó DESPUÉS de la
alerta). Reglas, pensadas para ser conservadoras:
1. Si una barra abre por debajo del stop (salto a la baja), se sale al precio de apertura.
2. Si una barra abre por encima del objetivo, se sale a la apertura (salto a favor).
3. Si en la misma barra se tocan stop y objetivo, se asume que primero tocó el stop
   (no sabemos el orden; suponer lo peor evita inflar resultados).
4. Si se llega a la fecha límite sin tocar nada, se sale al cierre de ese día.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class PathBar:
    day: date
    o: float
    h: float
    l: float
    c: float


@dataclass
class Exit:
    day: date
    price: float
    reason: str  # "stop" | "objetivo" | "fecha_limite"


def find_exit(stop: float, target: float, deadline: date, bars: list[PathBar]) -> Exit | None:
    for b in sorted(bars, key=lambda x: x.day):
        if b.o <= stop:
            return Exit(b.day, b.o, "stop")
        if b.o >= target:
            return Exit(b.day, b.o, "objetivo")
        if b.l <= stop:
            return Exit(b.day, stop, "stop")
        if b.h >= target:
            return Exit(b.day, target, "objetivo")
        if b.day >= deadline:
            return Exit(b.day, b.c, "fecha_limite")
    return None  # sigue abierta
