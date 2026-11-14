"""Línea de comandos:
  python -m pead_bot.cli scan --dry-run   # imprime candidatos y los mensajes que mandaría
  python -m pead_bot.cli scan             # además manda las alertas a Telegram
  python -m pead_bot.cli telegram-test    # manda una alerta de EJEMPLO para ver el formato
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config, require_env
from .schedule import fmt_mx


def _clients():
    from .data.alpaca import AlpacaClient
    from .data.finnhub import FinnhubClient
    fh = FinnhubClient(require_env("FINNHUB_API_KEY"))
    ap = AlpacaClient(require_env("ALPACA_API_KEY_ID"), require_env("ALPACA_API_SECRET_KEY"))
    ap.check_paper_account()  # si las llaves fueran reales, esto falla
    return fh, ap


def _telegram():
    from .notify.telegram import TelegramClient
    return TelegramClient(require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID"))


def cmd_scan(args) -> int:
    from .scanner import run_scan
    cfg = load_config(args.config)
    tg = None if args.dry_run else _telegram()  # falla antes de escanear si falta una llave
    fh, ap = _clients()
    res = run_scan(cfg, fh, ap)
    _print_scan(res, cfg)
    return _send(res, cfg, tg)


def _send(res, cfg, tg) -> int:
    from .config import ROOT
    from .notify.dispatch import SentStore, build_messages
    store = SentStore(ROOT / "data" / "cache" / "sent.json")
    sent = store.load()
    msgs = build_messages(res, cfg, sent)
    if not msgs:
        print("\nMensajes a Telegram: ninguno nuevo.")
        return 0
    if tg is None:
        print(f"\n--- {len(msgs)} mensaje(s) que se mandarían (dry-run, no se envía nada) ---")
        for m in msgs:
            print("\n" + m.text)
        return 0
    for m in msgs:
        tg.send(m.text)
        sent.update(m.keys)
        store.save(sent, res.now.date())  # guarda después de cada uno por si el siguiente falla
    print(f"\nMandé {len(msgs)} mensaje(s) a Telegram.")
    return 0


def cmd_telegram_test(args) -> int:
    from datetime import datetime, timedelta

    from .notify.format import format_alert
    from .schedule import NY
    from .strategy.filters import CandidateData, evaluate
    from .strategy.plan import make_plan
    cfg = load_config(args.config)
    tg = _telegram()
    now = datetime.now(NY)
    d = CandidateData("EJEM", "Empresa de Ejemplo Inc", now.date() - timedelta(days=1), "amc",
                      eps_actual=1.30, eps_estimate=1.00, revenue_actual=2.4e9, revenue_estimate=2.2e9,
                      price=104.0, base_close=100.0, avg_volume=3e6, market_cap=25e9, rvol=3.1)
    ev = evaluate(d, cfg.strategy, cfg.filters)
    days = [now.date() + timedelta(days=i) for i in range(1, 15)
            if (now.date() + timedelta(days=i)).weekday() < 5]
    plan = make_plan(d.price, now.date(), cfg.plan, days)
    tg.send("🧪 <b>EJEMPLO</b> (datos inventados, no es una señal):\n\n" + format_alert(ev, plan, now))
    print("Mandé la alerta de ejemplo. Revisa Telegram.")
    return 0


def _print_scan(res, cfg) -> None:
    print(f"Escaneo {fmt_mx(res.now)} · mercado hoy: {'sí' if res.session else 'no'}")
    print(f"Reportes descartados antes de filtros (sin sorpresa >"
          f"{cfg.strategy.eps_surprise_min_pct}%, viejos o sin publicar): {res.skipped_pre_filter}")
    if not res.evaluations:
        print("Sin candidatos.")
        return
    for ev in res.evaluations:
        d = ev.data
        tag = "✅ PASA" if ev.passed else ("⏳ PENDIENTE" if ev.pending_only else "❌ NO")
        print(f"\n{tag}  {d.symbol} · {d.name} · puntos={ev.score}")
        for c in ev.checks:
            mark = {True: "ok ", False: "NO ", None: "...",}[c.passed]
            print(f"   [{mark}] {c.name}: {c.detail}")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="pead-bot")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="Escanea candidatos")
    sc.add_argument("--dry-run", action="store_true", help="Solo imprime, no manda alertas")
    sc.set_defaults(func=cmd_scan)
    tt = sub.add_parser("telegram-test", help="Manda una alerta de ejemplo a Telegram")
    tt.set_defaults(func=cmd_telegram_test)
    args = p.parse_args(argv)
    if args.config is None:
        from .config import DEFAULT_CONFIG
        args.config = DEFAULT_CONFIG
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
