"""Operaciones SIMULADAS en SQLite (data/pead.sqlite, fuera de git).

Una fila por alerta mandada: la "compra" es el precio de la alerta y se cierra sola
con stop, objetivo o fecha límite. Nunca hay dinero real de por medio.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    name        TEXT NOT NULL,
    opened_at   TEXT NOT NULL,          -- momento de la alerta (ISO, con zona horaria)
    entry_date  TEXT NOT NULL,
    entry       REAL NOT NULL,
    stop        REAL NOT NULL,
    target      REAL NOT NULL,
    deadline    TEXT NOT NULL,
    score       INTEGER NOT NULL,
    confidence  INTEGER,                -- 1-5 de Claude; NULL si no hubo lectura
    spy_entry   REAL,                   -- precio de SPY al momento de la alerta
    status      TEXT NOT NULL DEFAULT 'open',
    exit_date   TEXT,
    exit_price  REAL,
    exit_reason TEXT,
    spy_exit    REAL,
    monto_mxn   REAL,                   -- pesos que dijo la alerta (regla de config.yaml)
    decision    TEXT,                   -- 'voy' | 'paso' | NULL (botones de Telegram)
    notices     TEXT,                   -- avisos de venta ya mandados, separados por coma
    alertada    INTEGER DEFAULT 1,      -- 0 = el comité la rechazó: solo se simula, no se avisó
    UNIQUE(symbol, entry_date)
);
"""


@dataclass
class Trade:
    symbol: str
    name: str
    opened_at: datetime
    entry_date: date
    entry: float
    stop: float
    target: float
    deadline: date
    score: int
    confidence: int | None = None
    spy_entry: float | None = None
    status: str = "open"
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    spy_exit: float | None = None
    monto_mxn: float | None = None
    decision: str | None = None
    notices: str | None = None
    alertada: int = 1
    id: int | None = None

    def has_notice(self, kind: str) -> bool:
        return kind in (self.notices or "").split(",")


_DATES = {"entry_date", "deadline", "exit_date"}


def _from_row(row: sqlite3.Row) -> Trade:
    kw = dict(row)
    for k in _DATES:
        if kw[k]:
            kw[k] = date.fromisoformat(kw[k])
    kw["opened_at"] = datetime.fromisoformat(kw["opened_at"])
    return Trade(**kw)


class TradeStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(trades)")}
        for col, typ in (("monto_mxn", "REAL"), ("decision", "TEXT"), ("notices", "TEXT"),
                         ("alertada", "INTEGER DEFAULT 1")):
            if col not in cols:  # bases creadas con una versión anterior
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        self.conn.commit()

    def get(self, symbol: str, entry_date: date) -> Trade | None:
        r = self.conn.execute("SELECT * FROM trades WHERE symbol=? AND entry_date=?",
                              (symbol, entry_date.isoformat())).fetchone()
        return _from_row(r) if r else None

    def set_decision(self, trade_id: int, decision: str) -> None:
        self.conn.execute("UPDATE trades SET decision=? WHERE id=?", (decision, trade_id))
        self.conn.commit()

    def add_notice(self, trade_id: int, kind: str) -> None:
        cur = self.conn.execute("SELECT notices FROM trades WHERE id=?", (trade_id,)).fetchone()[0]
        kinds = [k for k in (cur or "").split(",") if k] + [kind]
        self.conn.execute("UPDATE trades SET notices=? WHERE id=?", (",".join(kinds), trade_id))
        self.conn.commit()

    def taken(self) -> list[Trade]:
        """Jugadas en las que dijiste 'voy' y que aún no tienen aviso de venta."""
        rows = self.conn.execute("SELECT * FROM trades WHERE decision='voy' ORDER BY entry_date, id")
        return [t for t in map(_from_row, rows) if not (t.has_notice("stop") or t.has_notice("objetivo")
                                                        or t.has_notice("cierre"))]

    def open_total(self) -> tuple[int, float]:
        """(cuántas abiertas, pesos metidos en ellas)."""
        r = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(monto_mxn), 0) FROM trades "
            "WHERE status='open' AND alertada=1").fetchone()
        return int(r[0]), float(r[1])

    def add(self, t: Trade) -> bool:
        """False si ya existía (misma acción, mismo día)."""
        cols = [f.name for f in fields(Trade) if f.name != "id"]
        vals = []
        for c in cols:
            v = getattr(t, c)
            vals.append(v.isoformat() if isinstance(v, (date, datetime)) else v)
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        self.conn.commit()
        return cur.rowcount == 1

    def open_trades(self) -> list[Trade]:
        rows = self.conn.execute("SELECT * FROM trades WHERE status='open' ORDER BY entry_date, id")
        return [_from_row(r) for r in rows]

    def close(self, trade_id: int, exit_date: date, exit_price: float, reason: str,
              spy_exit: float | None) -> None:
        self.conn.execute(
            "UPDATE trades SET status='closed', exit_date=?, exit_price=?, exit_reason=?, spy_exit=? "
            "WHERE id=?", (exit_date.isoformat(), exit_price, reason, spy_exit, trade_id))
        self.conn.commit()

    def closed_between(self, start: date, end: date) -> list[Trade]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status='closed' AND exit_date BETWEEN ? AND ? "
            "ORDER BY exit_date, id", (start.isoformat(), end.isoformat()))
        return [_from_row(r) for r in rows]

    def all_closed(self) -> list[Trade]:
        rows = self.conn.execute("SELECT * FROM trades WHERE status='closed' ORDER BY exit_date, id")
        return [_from_row(r) for r in rows]
