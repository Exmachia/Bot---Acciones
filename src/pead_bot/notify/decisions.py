"""Botones ✅ Voy / ❌ Paso en cada alerta, y avisos de venta para TUS jugadas.

- El bot no compra ni vende: solo anota tu decisión y te escribe cuándo vender.
- Los toques se leen en cada corrida del bot (getUpdates). Mientras el bot no corra
  solo (fase 6), se leen cuando lo corras tú; Telegram los guarda hasta 24 h.
- Solo se aceptan toques que vengan de TU chat (TELEGRAM_CHAT_ID).
- Avisos de venta (una sola vez cada uno):
  🛑 tocó el stop · ✅ llegó al objetivo · ⏱️ hoy es el último día · 🔔 terminó al cierre.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from pathlib import Path

from ..config import CostsCfg
from ..schedule import to_mx
from ..store.db import Trade, TradeStore
from ..tracking.costs import trade_cost

SELL_KINDS = ("stop", "objetivo", "cierre")
EXAMPLE_SYMBOL = "EJEM"
_REASON = {"stop": "tocó el stop", "objetivo": "llegó al objetivo", "fecha_limite": "llegó la fecha límite"}


def alert_buttons(symbol: str, entry_date: date) -> list[list[tuple[str, str]]]:
    k = f"{symbol}|{entry_date.isoformat()}"
    return [[("✅ Voy", f"voy|{k}"), ("❌ Paso", f"paso|{k}")]]


def parse_callback(data: str) -> tuple[str, str, date] | None:
    try:
        action, symbol, d = data.split("|")
        if action not in ("voy", "paso", "cambiar"):
            return None
        return action, symbol, date.fromisoformat(d)
    except ValueError:
        return None


def decision_reply(t: Trade, action: str) -> str:
    amount = f"${t.monto_mxn:,.0f} MXN" if t.monto_mxn else "el monto de la alerta"
    if action == "voy":
        lines = [f"✅ <b>Anotado: vas en {escape(t.symbol)}</b> con {amount}.",
                 f"Te aviso cuándo venderla: stop ${t.stop:,.2f}, objetivo ${t.target:,.2f}, "
                 f"o a más tardar el {t.deadline.day}/{t.deadline.month}.",
                 "Tip: si tu casa de bolsa lo permite, deja puestas órdenes de venta en esos precios."]
        if t.status == "closed":
            lines.append(f"⚠️ Ojo: según el bot esta jugada ya terminó ({_REASON.get(t.exit_reason, '')}, "
                         f"${t.exit_price:,.2f}).")
        return "\n".join(lines)
    return f"❌ Ok, pasas en {escape(t.symbol)}. La sigo en simulación para las estadísticas."


def _status_buttons(t: Trade, action: str) -> list[list[tuple[str, str]]]:
    label = "✅ Vas en esta" if action == "voy" else "❌ Pasaste"
    return [[(label, "noop"), ("↩️ Cambiar", f"cambiar|{t.symbol}|{t.entry_date.isoformat()}")]]


def process_updates(tg, store: TradeStore, chat_id: str, state_path: Path, wait: int = 0) -> list[str]:
    """Lee los toques de botones pendientes y los aplica. Devuelve un log corto.
    wait > 0: espera hasta esos segundos a que llegue un toque (para contestar al instante)."""
    state_path = Path(state_path)
    try:
        offset = json.loads(state_path.read_text(encoding="utf-8")).get("offset")
    except (FileNotFoundError, json.JSONDecodeError):
        offset = None
    log = []
    updates = tg.get_updates(offset, wait) if wait else tg.get_updates(offset)
    for u in updates:
        offset = max(offset or 0, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue  # no es tu chat: se ignora
        data = cq.get("data", "")
        if data == "noop":
            tg.answer_callback(cq["id"])
            continue
        parsed = parse_callback(data)
        if parsed and parsed[1] == EXAMPLE_SYMBOL:  # alerta de ejemplo (telegram-test): no se guarda
            demo = Trade(EXAMPLE_SYMBOL, "Ejemplo", datetime.now(), parsed[2], 104.0, 98.8, 112.32,
                         parsed[2], 3, 4, monto_mxn=7_500)
            tg.answer_callback(cq["id"], "Ejemplo.")
            if parsed[0] == "cambiar":
                tg.edit_buttons(msg.get("message_id"), alert_buttons(EXAMPLE_SYMBOL, parsed[2]))
            else:
                tg.edit_buttons(msg.get("message_id"), _status_buttons(demo, parsed[0]))
                tg.send("🧪 (ejemplo, no se guardó) " + decision_reply(demo, parsed[0]))
            log.append(f"{EXAMPLE_SYMBOL}: {parsed[0]} (ejemplo)")
            continue
        t = store.get(parsed[1], parsed[2]) if parsed else None
        if t is None:
            tg.answer_callback(cq["id"], "No encontré esa jugada.")
            continue
        action = parsed[0]
        if action == "cambiar":
            store.set_decision(t.id, None)
            tg.answer_callback(cq["id"], "Elige otra vez.")
            tg.edit_buttons(msg.get("message_id"), alert_buttons(t.symbol, t.entry_date))
            log.append(f"{t.symbol}: cambiar")
            continue
        store.set_decision(t.id, action)
        if action == "voy" and t.status == "closed" and not t.has_notice("cierre"):
            store.add_notice(t.id, "cierre")  # la respuesta ya avisa que terminó
        tg.answer_callback(cq["id"], "Anotado.")
        tg.edit_buttons(msg.get("message_id"), _status_buttons(t, action))
        tg.send(decision_reply(t, action))
        log.append(f"{t.symbol}: {action}")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    return log


@dataclass
class Notice:
    trade: Trade
    kind: str
    text: str


def _mxn(t: Trade, exit_price: float, costs: CostsCfg) -> str:
    if not t.monto_mxn:
        return ""
    pnl = trade_cost(t.entry, exit_price, costs).pnl(t.monto_mxn)
    word = "ganancia" if pnl > 0 else "pérdida"
    return f"\nAsí la {word} queda en ≈ ${abs(pnl):,.0f} MXN (con costos, desde el precio de la alerta)."


def watch_taken(trades: list[Trade], prices: dict[str, float], now: datetime,
                session, costs: CostsCfg) -> list[Notice]:
    """Decide qué avisos de venta mandar ahora (función pura).
    `session` = sesión de hoy (o None si hoy no hay bolsa)."""
    out = []
    market_open = session is not None and session.open <= now <= session.close
    for t in trades:
        if t.decision != "voy" or any(t.has_notice(k) for k in SELL_KINDS) or now <= t.opened_at:
            continue
        sym = escape(t.symbol)
        if t.status == "closed":
            out.append(Notice(t, "cierre",
                              f"🔔 <b>Vende {sym}</b>: con el cierre la jugada terminó "
                              f"({_REASON.get(t.exit_reason, t.exit_reason)}, ${t.exit_price:,.2f}). "
                              "Si sigues dentro, véndela." + _mxn(t, t.exit_price, costs)))
            continue
        p = prices.get(t.symbol)
        if market_open and p:
            if p <= t.stop:
                out.append(Notice(t, "stop", f"🛑 <b>Vende {sym}</b>: tocó el stop.\n"
                                  f"Ahora ${p:,.2f} (stop ${t.stop:,.2f})." + _mxn(t, p, costs)))
                continue
            if p >= t.target:
                out.append(Notice(t, "objetivo", f"✅ <b>Vende {sym}</b>: llegó al objetivo 🎉\n"
                                  f"Ahora ${p:,.2f} (objetivo ${t.target:,.2f})." + _mxn(t, p, costs)))
                continue
        if (market_open and now.date() == t.deadline and not t.has_notice("ultimo_dia")):
            close = to_mx(session.close).strftime("%H:%M")
            now_txt = f" Ahora está en ${p:,.2f} ({(p / t.entry - 1) * 100:+.1f}% desde la alerta)." if p else ""
            out.append(Notice(t, "ultimo_dia", f"⏱️ <b>Hoy es el último día de {sym}</b>: véndela antes "
                              f"del cierre ({close} CDMX).{now_txt}"))
    return out
