"""Textos de las alertas en español (HTML de Telegram). Funciones puras, sin red."""
from __future__ import annotations

from datetime import date, datetime
from html import escape

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


def catalyst(ev: Evaluation) -> str:
    d = ev.data
    when = _HOUR.get((d.hour or "").lower(), "hora no informada")
    return f"Resultados trimestrales ({fmt_date(d.report_date)}, {when})"


def summary_lines(ev: Evaluation) -> list[str]:
    """Resumen de 2 líneas con los números del reporte."""
    d = ev.data
    l1 = f"Ganó ${d.eps_actual:.2f} por acción vs. ${d.eps_estimate:.2f} esperado"
    if ev.eps_surprise is not None:
        l1 += f" ({ev.eps_surprise:+.0f}%)"
    l1 += "."
    if d.revenue_actual is None or d.revenue_estimate is None:
        l2 = "Ventas: sin dato del estimado."
    else:
        verdict = "superó" if d.revenue_actual > d.revenue_estimate else "no superó"
        l2 = (f"Ventas: {fmt_money(d.revenue_actual)} vs. {fmt_money(d.revenue_estimate)} "
              f"esperado ({verdict}).")
    return [l1, l2]


_GUIA = {"subio": "la subió ✅", "igual": "sin cambios", "bajo": "la bajó ⚠️", "sin_dato": "no se menciona"}


def analysis_lines(a) -> list[str]:
    """Bloque con la lectura de noticias de Claude (fase 3)."""
    lines = ["<b>Lectura de noticias (Claude)</b>", escape(a.resumen.strip())]
    lines.append("Sorpresa: " + ("viene del negocio ✅" if a.sorpresa_real
                                 else "parece de una sola vez ⚠️"))
    if a.letra_chica.strip():
        lines.append(f"Letra chica: {escape(a.letra_chica.strip())}")
    lines.append(f"Pronóstico de la empresa (guía): {_GUIA.get(a.guia, a.guia)}")
    for r in a.riesgos:
        lines.append(f"• Riesgo: {escape(r)}")
    return lines


def max_points(points_revenue: int = 1, points_guidance: int = 1) -> int:
    return 1 + points_revenue + points_guidance


def format_alert(ev: Evaluation, plan: TradePlan, now: datetime, max_pts: int = 3,
                 source: tuple[str, str] | None = None) -> str:
    """Alerta cuando un candidato ya pasó todos los filtros (incluido el volumen).
    `source` = (texto, url) de la noticia principal; si no hay, se usa la SEC."""
    d = ev.data
    a = ev.analysis
    lines = [
        f"🟢 <b>{escape(d.symbol)}</b> · {escape(d.name)}",
        f"<b>Catalizador:</b> {escape(catalyst(ev))}",
        *[escape(x) for x in summary_lines(ev)],
        "",
    ]
    if a is not None:
        lines += [*analysis_lines(a), ""]
    moved = f"{ev.gain_pct:+.1f}%" if ev.gain_pct is not None else "s/d"
    lines.append(f"<b>Se ha movido:</b> {moved} desde el cierre previo al reporte")
    if d.rvol is not None:
        lines.append(f"<b>Volumen:</b> {d.rvol:.1f}x lo normal a esta hora (hasta hace 16 min)")
    lines += [
        "",
        "<b>Plan</b>",
        f"Entrada: ${plan.entry:,.2f}",
        f"Stop: ${plan.stop:,.2f} ({(plan.stop / plan.entry - 1) * 100:+.0f}%)",
        f"Objetivo: ${plan.target:,.2f} ({(plan.target / plan.entry - 1) * 100:+.0f}%)",
        f"Fecha límite: {fmt_date(plan.deadline)} (si no tocó stop ni objetivo, se sale)",
        "",
        f"<b>Confianza:</b> " + (f"{a.confianza} de 5 (Claude) · " if a is not None
                                 else "sin lectura de noticias · ") + f"{ev.score} de {max_pts} puntos",
        f'<a href="{escape(source_url(d.symbol))}">Ver reporte en la SEC</a>'
        + (f' · <a href="{escape(source[1])}">{escape(source[0])}</a>' if source else ""),
        "",
        f"⚠️ {SIC_NOTE}",
        f"<i>{DISCLAIMER} {escape(fmt_mx(now))}</i>",
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
