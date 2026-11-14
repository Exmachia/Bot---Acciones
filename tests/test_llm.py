"""Fase 3: lectura de noticias con Claude (cliente falso, sin red ni costo)."""
from datetime import date, datetime
from types import SimpleNamespace

from pead_bot.config import Config
from pead_bot.data.alpaca import NewsItem
from pead_bot.llm.enrich import enrich
from pead_bot.llm.news import NewsAnalysis, NewsAnalyzer, build_prompt
from pead_bot.notify.dispatch import build_messages
from pead_bot.notify.format import format_alert, format_pending
from pead_bot.scanner import run_scan
from pead_bot.schedule import NY
from pead_bot.strategy.plan import make_plan

from test_notify import DAYS, _ev, _premarket_result
from test_scanner import NOW, FakeAP, FakeFH

CFG = Config()
NEWS = [NewsItem(1, "GOOD sube tras reporte", "Ventas récord y guía al alza", "https://ej.com/n1",
                 "benzinga", datetime(2026, 11, 11, 17, 0, tzinfo=NY))]


def _analysis(conf=4, guia="subio", real=True, letra=""):
    return NewsAnalysis(resumen="Vendió más de lo esperado.\nSubió su pronóstico del año.",
                        sorpresa_real=real, letra_chica=letra, guia=guia,
                        sentimiento="positivo", riesgos=["Competencia fuerte", "", "Dólar", "Extra"],
                        confianza=conf)


# --- prompt y analizador -----------------------------------------------------
def test_prompt_wraps_news_as_data():
    p = build_prompt(_ev().data, NEWS)
    assert "<noticias>" in p and "</noticias>" in p
    assert "Título: GOOD sube tras reporte" in p
    assert "real 1.30 vs. estimado 1.00" in p


class FakeMessages:
    def __init__(self, parsed, stop="end_turn"):
        self.parsed, self.stop, self.calls = parsed, stop, []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, parsed_output=self.parsed)


def _analyzer(parsed, stop="end_turn"):
    msgs = FakeMessages(parsed, stop)
    return NewsAnalyzer("claude-haiku-5-5", client=SimpleNamespace(messages=msgs)), msgs


def test_analyzer_calls_haiku_with_schema_and_clamps():
    an, msgs = _analyzer(_analysis(conf=9))
    a = an.analyze(_ev().data, NEWS)
    kw = msgs.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and kw["output_format"] is NewsAnalysis
    assert "temperature" not in kw
    assert a.confianza == 5                                   # 9 → 5
    assert a.riesgos == ["Competencia fuerte", "Dólar", "Extra"]  # sin vacíos, máx. 3


def test_analyzer_skips_without_news_and_on_bad_stop():
    an, msgs = _analyzer(_analysis())
    assert an.analyze(_ev().data, []) is None and msgs.calls == []
    an2, _ = _analyzer(_analysis(), stop="max_tokens")
    assert an2.analyze(_ev().data, NEWS) is None


# --- enrich ------------------------------------------------------------------
class FakeAPNews(FakeAP):
    def __init__(self):
        self.news_calls = 0

    def news(self, symbol, start, end, limit=10):
        self.news_calls += 1
        return NEWS


class CountingAnalyzer:
    def __init__(self, a):
        self.a, self.calls = a, 0

    def analyze(self, d, news):
        self.calls += 1
        return self.a


def test_enrich_adds_guidance_point_and_caches(tmp_path):
    ap = FakeAPNews()
    res = run_scan(CFG, FakeFH(), ap, now=NOW)       # GOOD pasa (2 puntos), CHASE no
    an = CountingAnalyzer(_analysis(guia="subio"))
    assert enrich(res, CFG, ap, an, tmp_path) == 1   # solo GOOD; CHASE no se analiza
    good = next(e for e in res.evaluations if e.data.symbol == "GOOD")
    assert good.score == 3 and good.analysis.confianza == 4
    assert good.source == ("benzinga", "https://ej.com/n1")
    res2 = run_scan(CFG, FakeFH(), ap, now=NOW)
    assert enrich(res2, CFG, ap, an, tmp_path) == 0  # segunda vez sale del caché
    assert an.calls == 1
    good2 = next(e for e in res2.evaluations if e.data.symbol == "GOOD")
    assert good2.score == 3 and good2.source == ("benzinga", "https://ej.com/n1")


def test_enrich_premarket_analyzes_pending(tmp_path):
    res = _premarket_result([_ev("AAA", rvol=None), _ev("BAD", price=3)])
    an = CountingAnalyzer(_analysis())
    assert enrich(res, CFG, FakeAPNews(), an, tmp_path) == 1  # BAD no pasa filtros: no se gasta


# --- mensajes ----------------------------------------------------------------
def test_low_confidence_is_not_sent(tmp_path):
    ap = FakeAPNews()
    res = run_scan(CFG, FakeFH(), ap, now=NOW)
    enrich(res, CFG, ap, CountingAnalyzer(_analysis(conf=2, guia="igual")), tmp_path)
    assert build_messages(res, CFG, set()) == []      # min_confidence = 3


def test_alert_shows_claude_reading():
    ev = _ev()
    ev.analysis = _analysis(conf=4, real=False, letra="La ganancia incluye la venta de una planta")
    plan = make_plan(104.0, date(2026, 11, 12), CFG.plan, DAYS)
    txt = format_alert(ev, plan, NOW, source=("benzinga", "https://ej.com/n1"))
    for must in ["CLAUDE", "Vendió más de lo esperado.",
                 "Sorpresa parece de una sola vez ⚠️ · guía la subió ✅",
                 "Letra chica: La ganancia incluye la venta de una planta",
                 "Riesgos: Competencia fuerte; Dólar; Extra",
                 "Puntos 2/3", '<a href="https://ej.com/n1">benzinga</a>']:
        assert must in txt, must


def test_pending_shows_confidence():
    ev = _ev("AAA", rvol=None)
    ev.analysis = _analysis(conf=3)
    assert "confianza 3/5" in format_pending([ev], date(2026, 11, 12), NOW)
