"""Textos de las alertas en español (HTML de Telegram). Funciones puras, sin red."""
from __future__ import annotations

from datetime import date, datetime
from html import escape

from ..config import CapitalCfg, Config, CostsCfg
from ..schedule import fmt_mx
from ..strategy.filters import Evaluation
from ..strategy.plan import TradePlan

SIC_NOTE = "Verifica que esté disponible en el SIC de tu casa de bolsa."
DISCLAIMER = "Operación simulada. No es asesoría financiera."

_HOUR = {"bmo": "antes de la apertura", "amc": "después del cierre", "dmh": "durante el mercado"}
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def fmt_date(d: date) -> str:
    """'mié 18 nov'."""
    return f"{_DIAS[d.weekday()]} {d.day} {_MESES[d.month - 1]}"


def fmt_money(x: float | None) -> str:
    """Ventas/valor de mercado en USD legible: $1.2 mil M, $350 M."""
    if x is None:
        return "s/d"
    return f"${x / 1e9:,.1f} mil M" if abs(x) >= 1e9 else f"${x / 1e6:,.0f} M"


def source_url(symbol: str) -> str:
    """Reportes 8-K en la SEC: ahí la empresa publica oficialmente sus resultados."""
    return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={symbol}&type=8-K&dateb=&owner=include&count=10")


_GUIA = {"subio": "la subió ✅", "igual": "sin cambios", "bajo": "la bajó ⚠️", "sin_dato": "no la menciona"}
SEP = "━━━━━━━━━━━━━━━"

RISK_NOTE = "Monto según tu regla de config.yaml. Bajo tu propio riesgo; no es asesoría financiera."


def max_points(points_revenue: int = 1, points_guidance: int = 1) -> int:
    return 1 + points_revenue + points_guidance


def play_lines(ev: Evaluation, plan: TradePlan, capital: CapitalCfg, costs: CostsCfg,
               holding_days: int, open_total: tuple[int, float] | None = None) -> list[str]:
    """La jugada: cuánto (según TU regla), a qué precios y hasta cuándo."""
    from ..tracking.costs import trade_cost
    conf = ev.analysis.confianza if ev.analysis is not None else None
    amount = capital.monto(conf)
    loss = trade_cost(plan.entry, plan.stop, costs).pnl(amount)
    gain = trade_cost(plan.entry, plan.target, costs).pnl(amount)
    why = f"confianza {conf}/5" if conf is not None else "sin lectura de noticias"
    stop_pct = (plan.stop / plan.entry - 1) * 100
    tgt_pct = (plan.target / plan.entry - 1) * 100
    lines = [
        "🎯 <b>LA JUGADA</b>",
        f"💵 <b>${amount:,.0f} MXN</b> · {why}",
        f"🛒 Entrada ${plan.entry:,.2f}",
        f"🛑 Stop ${plan.stop:,.2f} ({stop_pct:+.0f}%) → pierdes ≈ ${abs(loss):,.0f}",
        f"✅ Objetivo ${plan.target:,.2f} ({tgt_pct:+.0f}%) → ganas ≈ ${gain:,.0f}",
        f"⏱️ Máximo hasta el {fmt_date(plan.deadline)} ({holding_days} días de bolsa)",
    ]
    if open_total is not None and open_total[0] > 0:
        n, total = open_total
        lines.append(f"📂 Abiertas: {n} (≈ ${total:,.0f}) → con esta ≈ ${total + amount:,.0f}")
    lines.append("<i>Montos con comisión, IVA y tipo de cambio; antes de ISR.</i>")
    return lines


def what_happened_lines(ev: Evaluation) -> list[str]:
    d = ev.data
    sp = f" ({ev.eps_surprise:+.0f}%)" if ev.eps_surprise is not None else ""
    lines = ["📰 <b>QUÉ PASÓ</b>",
             f"Ganó ${d.eps_actual:.2f} por acción vs. ${d.eps_estimate:.2f} esperado{sp}"]
    if d.revenue_actual is not None and d.revenue_estimate is not None:
        ok = "superó ✅" if d.revenue_actual > d.revenue_estimate else "no superó ⚠️"
        lines.append(f"Ventas {fmt_money(d.revenue_actual)} vs. {fmt_money(d.revenue_estimate)} · {ok}")
    when = _HOUR.get((d.hour or "").lower(), "hora no informada")
    moved = f"{ev.gain_pct:+.1f}%" if ev.gain_pct is not None else "s/d"
    vol = f" · volumen {d.rvol:.1f}x" if d.rvol is not None else ""
    lines.append(f"Reportó {fmt_date(d.report_date)} {when} · lleva {moved}{vol}")
    return lines


def analysis_lines(a) -> list[str]:
    """Lectura de noticias: comité (optimista/pesimista/juez) o lectura simple de Claude."""
    if hasattr(a, "prediccion"):
        lines = ["⚖️ <b>COMITÉ</b>",
                 f"🟢 A favor: {escape(a.a_favor.strip())}",
                 f"🔴 En contra: {escape(a.en_contra.strip())}",
                 f"Juez: confianza {a.confianza}/5 · sorpresa "
                 + ("del negocio ✅" if a.sorpresa_real else "de una sola vez ⚠️")
                 + f" · guía {_GUIA.get(a.guia, a.guia)}"]
        if a.letra_chica.strip():
            lines.append(f"🔎 Letra chica: {escape(a.letra_chica.strip())}")
        return lines
    lines = ["🤖 <b>CLAUDE</b>", escape(a.resumen.strip())]
    surprise = "viene del negocio ✅" if a.sorpresa_real else "parece de una sola vez ⚠️"
    lines.append(f"Sorpresa {surprise} · guía {_GUIA.get(a.guia, a.guia)}")
    if a.letra_chica.strip():
        lines.append(f"🔎 Letra chica: {escape(a.letra_chica.strip())}")
    risks = [r.strip() for r in a.riesgos if r.strip()]
    if risks:
        lines.append("⚠️ Riesgos: " + "; ".join(escape(r) for r in risks))
    return lines


def format_alert(ev: Evaluation, plan: TradePlan, now: datetime, max_pts: int = 3,
                 source: tuple[str, str] | None = None, cfg: Config | None = None,
                 open_total: tuple[int, float] | None = None) -> str:
    """Alerta cuando un candidato ya pasó todos los filtros (incluido el volumen).
    `source` = (texto, url) de la noticia principal; siempre va también la SEC."""
    cfg = cfg or Config()
    d = ev.data
    links = f'<a href="{escape(source_url(d.symbol))}">SEC</a>'
    if source:
        links += f' · <a href="{escape(source[1])}">{escape(source[0])}</a>'
    lines = [f"🟢 <b>{escape(d.symbol)}</b> · {escape(d.name)}"]
    if ev.analysis is not None and hasattr(ev.analysis, "prediccion"):
        lines.append(f"🔮 <b>Lo que creemos:</b> {escape(ev.analysis.prediccion.strip())}")
    lines += [
        SEP,
        *play_lines(ev, plan, cfg.capital, cfg.costs, cfg.plan.max_holding_days, open_total),
        SEP,
        *what_happened_lines(ev),
    ]
    if ev.analysis is not None:
        lines += [SEP, *analysis_lines(ev.analysis)]
    lines += [
        SEP,
        f"Puntos {ev.score}/{max_pts} · {links}",
        f"📌 {SIC_NOTE}",
        f"<i>{RISK_NOTE} {escape(fmt_mx(now))}</i>",
    ]
    return "\n".join(lines)


def format_pending(evs: list[Evaluation], session_day: date, now: datetime) -> str:
    """Lista de pre-apertura: pasan todo menos el volumen, que aún no se puede medir."""
    lines = [
        f"⏳ <b>Pendientes de confirmación · {fmt_date(session_day)}</b>",
        "Pasan todos los filtros menos el volumen, que se mide ya con el mercado abierto.",
        "Te aviso si alguno se confirma. <b>Todavía no es señal.</b>",
        "",
    ]
    for ev in evs:
        d = ev.data
        sp = f"{ev.eps_surprise:+.0f}%" if ev.eps_surprise is not None else "s/d"
        mv = f"{ev.gain_pct:+.1f}%" if ev.gain_pct is not None else "s/d"
        conf = f" · confianza {ev.analysis.confianza}/5" if ev.analysis is not None else ""
        lines.append(f"• <b>{escape(d.symbol)}</b> · {escape(d.name)} · "
                     f"ganancia {sp} vs. esperado · movido {mv}{conf}")
    lines += ["", f"<i>{escape(fmt_mx(now))}</i>"]
    return "\n".join(lines)


def format_guide(cfg: Config) -> str:
    """Explicación del bot en palabras sencillas (para fijarla en el chat)."""
    c, p, f = cfg.capital, cfg.plan, cfg.filters
    return "\n".join([
        "📘 <b>Cómo funciona este bot</b>",
        "",
        "<b>La idea</b>",
        "Cuando una empresa de EE. UU. reporta ganancias mucho mejores de lo esperado, su acción a "
        "veces sigue subiendo durante unos días. El bot busca esos casos y te avisa.",
        "",
        "<b>Qué revisa antes de avisarte</b>",
        f"1. Que haya ganado más de {cfg.strategy.eps_surprise_min_pct:.0f}% por encima de lo esperado.",
        f"2. Que sea una empresa grande y fácil de comprar y vender (precio mayor a ${f.min_price:.0f}, "
        "mucho volumen).",
        f"3. Que no haya subido ya más de {f.max_gain_since_prev_close_pct:.0f}% (para no llegar tarde).",
        f"4. Que hoy se esté negociando al menos {f.min_relative_volume:.0f} veces lo normal.",
        "5. Que el comité de Claude (optimista, pesimista y juez) le dé confianza 3 de 5 o más.",
        "",
        "<b>Los mensajes</b>",
        "⏳ Pendientes: acciones a vigilar antes de abrir. No es señal.",
        "🟢 Alerta: pasó todo. Trae cuánto meter (con tu regla), hasta cuándo, y cuánto ganarías o perderías.",
        "   Toca ✅ Voy o ❌ Paso. Si vas, te escribo cuándo venderla: 🛑 stop, ✅ objetivo o ⏱️ último día.",
        "✅/🔻 Cerré (simulada): cómo terminó una alerta.",
        "📊 Reporte del domingo: cuánto habrías ganado o perdido, contra solo tener el S&P 500 (SPY).",
        "",
        "<b>Tu regla de monto (config.yaml)</b>",
        f"Tope por jugada: ${c.tope_por_operacion_mxn:,.0f} MXN. Según la confianza de Claude: "
        + ", ".join(f"{k} → ${c.monto(k):,.0f}" for k in sorted(c.pct_por_confianza, reverse=True))
        + f"; sin lectura → ${c.monto(None):,.0f}.",
        f"Salida: si baja {p.stop_loss_pct:.0f}%, si sube {p.take_profit_pct:.0f}%, "
        f"o a los {p.max_holding_days} días de bolsa, lo que pase primero.",
        "",
        "<b>Importante</b>",
        "• El bot no compra ni vende. Todo lo que registra es simulado.",
        "• Todavía no está probado con datos pasados: úsalo para observar y aprender.",
        f"• {SIC_NOTE}",
        "• Todo es bajo tu propio riesgo. No es asesoría financiera.",
    ])
