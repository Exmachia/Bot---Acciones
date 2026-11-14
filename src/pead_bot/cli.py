"""Línea de comandos:
  python -m pead_bot.cli scan --dry-run   # imprime candidatos y los mensajes que mandaría
  python -m pead_bot.cli scan             # además manda las alertas a Telegram
  python -m pead_bot.cli telegram-test    # manda una alerta de EJEMPLO para ver el formato
  python -m pead_bot.cli guia             # manda la explicación del bot en palabras sencillas
  python -m pead_bot.cli track            # cierre diario de operaciones simuladas
  python -m pead_bot.cli report           # reporte semanal con costos y contra SPY
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
    _read_news(res, cfg, ap)
    _print_scan(res, cfg)
    return _send(res, cfg, tg, ap)


def _read_news(res, cfg, ap) -> None:
    """Fase 3: Claude lee las noticias de los candidatos que se van a avisar."""
    import os

    from .config import ROOT
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("(Sin ANTHROPIC_API_KEY en .env: las alertas salen sin lectura de noticias.)")
        return
    from .llm.enrich import enrich
    from .llm.news import NewsAnalyzer
    n = enrich(res, cfg, ap, NewsAnalyzer(cfg.llm.model), ROOT / "data" / "cache" / "analysis")
    if n:
        print(f"Claude leyó noticias de {n} candidato(s).")


def _store():
    from .config import ROOT
    from .store.db import TradeStore
    return TradeStore(ROOT / "data" / "pead.sqlite")


def _send(res, cfg, tg, ap=None) -> int:
    from .config import ROOT
    from .notify.dispatch import SentStore, build_messages
    store = SentStore(ROOT / "data" / "cache" / "sent.json")
    sent = store.load()
    msgs = build_messages(res, cfg, sent, open_total=_store().open_total())
    if not msgs:
        print("\nMensajes a Telegram: ninguno nuevo.")
        return 0
    if tg is None:
        print(f"\n--- {len(msgs)} mensaje(s) que se mandarían (dry-run, no se envía nada) ---")
        for m in msgs:
            print("\n" + m.text)
        return 0
    trades = _store() if any(m.trade for m in msgs) else None
    spy = None
    if trades is not None and ap is not None:
        try:
            spy = ap.latest_prices(["SPY"]).get("SPY")
        except Exception:  # noqa: BLE001 — sin SPY la operación igual se registra
            spy = None
    for m in msgs:
        tg.send(m.text)
        sent.update(m.keys)
        store.save(sent, res.now.date())  # guarda después de cada uno por si el siguiente falla
        if m.trade is not None:
            m.trade.spy_entry = spy
            trades.add(m.trade)
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
    from .llm.news import NewsAnalysis
    ev.analysis = NewsAnalysis(
        resumen="Vendió más de lo esperado en todas sus divisiones.\nSubió su pronóstico del año.",
        sorpresa_real=True, letra_chica="", guia="subio", sentimiento="positivo",
        riesgos=["Mucha competencia en su mercado"], confianza=4)
    days = [now.date() + timedelta(days=i) for i in range(1, 15)
            if (now.date() + timedelta(days=i)).weekday() < 5]
    plan = make_plan(d.price, now.date(), cfg.plan, days)
    tg.send("🧪 <b>EJEMPLO</b> (datos inventados, no es una señal):\n\n"
            + format_alert(ev, plan, now, cfg=cfg, open_total=(1, 5000.0)))
    print("Mandé la alerta de ejemplo. Revisa Telegram.")
    return 0


def cmd_guide(args) -> int:
    """Manda a Telegram la explicación del bot en palabras sencillas."""
    from .notify.format import format_guide
    cfg = load_config(args.config)
    txt = format_guide(cfg)
    print(txt)
    if not args.dry_run:
        _telegram().send(txt)
        print("\nMandé la guía a Telegram.")
    return 0


def cmd_track(args) -> int:
    """Cierre diario: revisa stop/objetivo/fecha límite de las operaciones simuladas."""
    from datetime import datetime, timedelta

    from .schedule import NY
    from .tracking.daily import track
    from .tracking.report import format_closed
    cfg = load_config(args.config)
    tg = None if args.dry_run else _telegram()
    _, ap = _clients()
    store = _store()
    now = datetime.now(NY)
    cal = ap.calendar(now.date() - timedelta(days=30), now.date())
    closed = track(store, ap, cal, now, cfg.costs)
    print(f"Abiertas que siguen: {len(store.open_trades())} · cerradas hoy: {len(closed)}")
    for c in closed:
        txt = format_closed(c, cfg.capital.tope_por_operacion_mxn)
        print("\n" + txt)
        if tg:
            tg.send(txt)
    return 0


def cmd_report(args) -> int:
    """Reporte semanal (domingo) con costos y contra SPY."""
    from datetime import datetime, timedelta

    from .schedule import NY
    from .tracking.report import format_weekly
    cfg = load_config(args.config)
    tg = None if args.dry_run else _telegram()
    store = _store()
    today = datetime.now(NY).date()
    txt = format_weekly(store.closed_between(today - timedelta(days=6), today), store.all_closed(),
                        store.open_trades(), today, cfg.costs, cfg.capital.tope_por_operacion_mxn)
    print(txt)
    if tg:
        tg.send(txt)
        print("\nMandé el reporte a Telegram.")
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
        if ev.analysis is not None:
            a = ev.analysis
            print(f"   Claude: confianza {a.confianza}/5 · sorpresa real: {'sí' if a.sorpresa_real else 'no'}"
                  f" · guía: {a.guia} · {a.resumen}")


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
    gd = sub.add_parser("guia", help="Manda la explicación del bot a Telegram")
    gd.add_argument("--dry-run", action="store_true", help="Solo imprime")
    gd.set_defaults(func=cmd_guide)
    tr = sub.add_parser("track", help="Cierre diario de operaciones simuladas")
    tr.add_argument("--dry-run", action="store_true", help="No manda nada a Telegram")
    tr.set_defaults(func=cmd_track)
    rp = sub.add_parser("report", help="Reporte semanal")
    rp.add_argument("--dry-run", action="store_true", help="No manda nada a Telegram")
    rp.set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    if args.config is None:
        from .config import DEFAULT_CONFIG
        args.config = DEFAULT_CONFIG
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
