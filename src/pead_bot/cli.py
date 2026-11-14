"""Línea de comandos: python -m pead_bot.cli scan --dry-run"""
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


def cmd_scan(args) -> int:
    from .scanner import run_scan
    cfg = load_config(args.config)
    fh, ap = _clients()
    res = run_scan(cfg, fh, ap)
    print(f"Escaneo {fmt_mx(res.now)} · mercado hoy: {'sí' if res.session else 'no'}")
    print(f"Reportes descartados antes de filtros (sin sorpresa >"
          f"{cfg.strategy.eps_surprise_min_pct}% o fuera de ventana): {res.skipped_pre_filter}")
    if not res.evaluations:
        print("Sin candidatos.")
        return 0
    for ev in res.evaluations:
        d = ev.data
        tag = "✅ PASA" if ev.passed else ("⏳ PENDIENTE" if ev.pending_only else "❌ NO")
        print(f"\n{tag}  {d.symbol} · {d.name} · puntos={ev.score}")
        for c in ev.checks:
            mark = {True: "ok ", False: "NO ", None: "...",}[c.passed]
            print(f"   [{mark}] {c.name}: {c.detail}")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="pead-bot")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="Escanea candidatos")
    sc.add_argument("--dry-run", action="store_true", help="Solo imprime, no manda alertas")
    sc.set_defaults(func=cmd_scan)
    args = p.parse_args(argv)
    if args.config is None:
        from .config import DEFAULT_CONFIG
        args.config = DEFAULT_CONFIG
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
