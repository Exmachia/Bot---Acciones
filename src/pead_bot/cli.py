"""Línea de comandos:
  python -m pead_bot.cli scan --dry-run   # imprime candidatos y los mensajes que mandaría
  python -m pead_bot.cli scan             # además manda las alertas a Telegram
  python -m pead_bot.cli telegram-test    # manda una alerta de EJEMPLO para ver el formato
  python -m pead_bot.cli inbox            # lee tus toques de ✅ Voy / ❌ Paso
  python -m pead_bot.cli listen           # escucha y contesta tus botones al instante
  python -m pead_bot.cli guia             # manda la explicación del bot en palabras sencillas
  python -m pead_bot.cli track            # cierre diario de operaciones simuladas
  python -m pead_bot.cli report           # reporte semanal con costos y contra SPY
  python -m pead_bot.cli backtest         # prueba la estrategia con 1 año de datos pasados
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


def _inbox(tg) -> None:
    """Lee tus toques de ✅ Voy / ❌ Paso."""
    import os

    from .config import ROOT
    from .notify.decisions import process_updates
    log = process_updates(tg, _store(), os.environ["TELEGRAM_CHAT_ID"],
                          ROOT / "data" / "cache" / "telegram_state.json")
    if log:
        print("Respuestas leídas: " + ", ".join(log))


def _watch(tg, cfg, ap, session) -> None:
    """Avisos de venta para las jugadas en las que dijiste 'voy'."""
    from datetime import datetime

    from .notify.decisions import watch_taken
    from .schedule import NY
    store = _store()
    taken = store.taken()
    if not taken:
        return
    prices = {}
    open_syms = sorted({t.symbol for t in taken if t.status == "open"})
    if open_syms and ap is not None and session is not None:
        prices = ap.latest_prices(open_syms)
    for n in watch_taken(taken, prices, datetime.now(NY), session, cfg.costs):
        tg.send(n.text)
        store.add_notice(n.trade.id, n.kind)
        print(f"Aviso de venta: {n.trade.symbol} ({n.kind})")


def cmd_listen(args) -> int:
    """Escucha tus toques y contesta al instante durante N minutos (Ctrl+C para salir)."""
    import os
    import time

    from .config import ROOT
    from .notify.decisions import process_updates
    load_config(args.config)
    tg = _telegram()
    until = time.monotonic() + args.minutos * 60
    print(f"Escuchando botones {args.minutos} min… (Ctrl+C para salir)")
    try:
        while time.monotonic() < until:
            wait = int(max(1, min(50, until - time.monotonic())))
            for line in process_updates(tg, _store(), os.environ["TELEGRAM_CHAT_ID"],
                                        ROOT / "data" / "cache" / "telegram_state.json", wait=wait):
                print("Respuesta:", line)
    except KeyboardInterrupt:
        print("\nListo, dejé de escuchar.")
    return 0


def cmd_inbox(args) -> int:
    """Solo lee tus toques de botones (útil mientras el bot no corre solo)."""
    load_config(args.config)
    _inbox(_telegram())
    return 0


def cmd_scan(args) -> int:
    from .scanner import run_scan
    cfg = load_config(args.config)
    tg = None if args.dry_run else _telegram()  # falla antes de escanear si falta una llave
    if tg:
        _inbox(tg)
    fh, ap = _clients()
    res = run_scan(cfg, fh, ap)
    _read_news(res, cfg, ap)
    _print_scan(res, cfg)
    rc = _send(res, cfg, tg, ap)
    if tg:
        _watch(tg, cfg, ap, res.session)
    return rc


def _read_news(res, cfg, ap) -> None:
    """Fase 3: Claude lee las noticias de los candidatos que se van a avisar."""
    import os

    from .config import ROOT
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("(Sin ANTHROPIC_API_KEY en .env: las alertas salen sin lectura de noticias.)")
        return
    from .llm.committee import CommitteeAnalyzer
    from .llm.enrich import enrich
    from .llm.news import NewsAnalyzer
    analyzer = (CommitteeAnalyzer if cfg.llm.modo == "comite" else NewsAnalyzer)(cfg.llm.model)
    n = enrich(res, cfg, ap, analyzer, ROOT / "data" / "cache" / "analysis")
    if n:
        print(f"Claude ({cfg.llm.modo}) leyó noticias de {n} candidato(s).")


def _store():
    from .config import ROOT
    from .store.db import TradeStore
    return TradeStore(ROOT / "data" / "pead.sqlite")


def _spy_price(ap):
    if ap is None:
        return None
    try:
        return ap.latest_prices(["SPY"]).get("SPY")
    except Exception:  # noqa: BLE001 — sin SPY la operación igual se registra
        return None


def _send(res, cfg, tg, ap=None) -> int:
    from .config import ROOT
    from .notify.dispatch import SentStore, build_messages, shadow_trades
    store = SentStore(ROOT / "data" / "cache" / "sent.json")
    sent = store.load()
    shadows = shadow_trades(res, cfg, sent)
    if shadows and tg is not None:  # rechazadas por el comité: solo simulación
        db = _store()
        spy = _spy_price(ap)
        for key, t in shadows:
            t.spy_entry = spy
            db.add(t)
            sent.add(key)
        store.save(sent, res.now.date())
        print(f"El comité rechazó {len(shadows)}: " + ", ".join(t.symbol for _, t in shadows)
              + " (quedan en simulación para comparar)")
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
    spy = _spy_price(ap) if trades is not None else None
    from .notify.decisions import alert_buttons
    for m in msgs:
        tg.send(m.text, buttons=alert_buttons(m.trade.symbol, m.trade.entry_date) if m.trade else None)
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
                      price=104.0, base_close=100.0, avg_volume=3e6, market_cap=25e9, rvol=3.1,
                      guidance_raised=True)
    ev = evaluate(d, cfg.strategy, cfg.filters)
    from .llm.committee import CommitteeVerdict
    ev.analysis = CommitteeVerdict(
        resumen="Vendió más de lo esperado en todas sus divisiones y subió su pronóstico del año.",
        sorpresa_real=True, letra_chica="", guia="subio", sentimiento="positivo",
        riesgos=["Mucha competencia en su mercado"], confianza=4,
        prediccion="Es probable que siga subiendo poco a poco esta semana por el pronóstico al alza, "
                   "aunque si el mercado cae podría frenarse.",
        a_favor="Superó en ganancias y ventas, y subió su pronóstico del año.",
        en_contra="Ya subió 4% y su sector tiene mucha competencia.")
    days = [now.date() + timedelta(days=i) for i in range(1, 15)
            if (now.date() + timedelta(days=i)).weekday() < 5]
    plan = make_plan(d.price, now.date(), cfg.plan, days)
    from .notify.decisions import alert_buttons
    tg.send("🧪 <b>EJEMPLO</b> (datos inventados, no es una señal):\n\n"
            + format_alert(ev, plan, now, cfg=cfg, open_total=(1, 5000.0)),
            buttons=alert_buttons("EJEM", now.date()))
    print("Mandé la alerta de ejemplo. Toca ✅ Voy o ❌ Paso y luego corre: inbox")
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
    if tg:
        _inbox(tg)
    _, ap = _clients()
    store = _store()
    now = datetime.now(NY)
    cal = ap.calendar(now.date() - timedelta(days=30), now.date())
    closed = track(store, ap, cal, now, cfg.costs)
    print(f"Abiertas que siguen: {len(store.open_trades())} · cerradas hoy: {len(closed)}")
    for c in closed:
        txt = format_closed(c, cfg.capital.tope_por_operacion_mxn)
        print("\n" + ("" if c.trade.alertada else "(rechazada por el comité, no se avisa) ") + txt)
        if tg and c.trade.alertada:
            tg.send(txt)
    if tg:
        _watch(tg, cfg, ap, None)  # avisa de tus jugadas que terminaron con el cierre
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
    week = store.closed_between(today - timedelta(days=6), today)
    closed = store.all_closed()
    txt = format_weekly([t for t in week if t.alertada], [t for t in closed if t.alertada],
                        [t for t in store.open_trades() if t.alertada], today, cfg.costs,
                        cfg.capital.tope_por_operacion_mxn,
                        rejected=[t for t in closed if not t.alertada])
    print(txt)
    if tg:
        tg.send(txt)
        print("\nMandé el reporte a Telegram.")
    return 0


def cmd_backtest(args) -> int:
    """Fase 5: backtest de 1 año con datos gratis (tarda la primera vez; luego usa caché)."""
    from datetime import date

    from .backtest.run import collect, collect_and_simulate, format_report, variants_report, write_csv
    from .config import ROOT
    cfg = load_config(args.config)
    fh, ap = _clients()
    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.variantes:
        txt = variants_report(collect(cfg, fh, ap, ROOT / "data" / "cache" / "backtest", end), cfg, ap,
                              cfg.capital.monto(None))
        out = ROOT / "data" / "backtest" / f"variantes_{end.isoformat()}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
        print(txt)
        return 0
    rep = collect_and_simulate(cfg, fh, ap, ROOT / "data" / "cache" / "backtest", end)
    amount = cfg.capital.monto(None)
    out = ROOT / "data" / "backtest" / f"operaciones_{end.isoformat()}.csv"
    write_csv(rep, out, amount)
    txt = format_report(rep, cfg, amount)
    (out.parent / f"reporte_{end.isoformat()}.txt").write_text(txt, encoding="utf-8")
    print(txt)
    print(f"\nDetalle de cada evento: {out}")
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
    ls = sub.add_parser("listen", help="Escucha y contesta tus botones al instante")
    ls.add_argument("--minutos", type=float, default=60, help="Cuánto tiempo escuchar (default 60)")
    ls.set_defaults(func=cmd_listen)
    ib = sub.add_parser("inbox", help="Lee tus toques de ✅ Voy / ❌ Paso")
    ib.set_defaults(func=cmd_inbox)
    gd = sub.add_parser("guia", help="Manda la explicación del bot a Telegram")
    gd.add_argument("--dry-run", action="store_true", help="Solo imprime")
    gd.set_defaults(func=cmd_guide)
    bt = sub.add_parser("backtest", help="Backtest de 1 año con datos gratis")
    bt.add_argument("--end", default=None, help="Último día (AAAA-MM-DD); por defecto hoy")
    bt.add_argument("--variantes", action="store_true", help="Compara las variantes de plazo")
    bt.set_defaults(func=cmd_backtest)
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
