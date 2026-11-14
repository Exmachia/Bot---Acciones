"""Etapa 3: comité de Claude (cliente falso) y jugadas rechazadas en simulación."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from pead_bot.config import Config, CostsCfg
from pead_bot.llm.committee import Argument, CommitteeAnalyzer, CommitteeVerdict
from pead_bot.llm.enrich import enrich
from pead_bot.notify.dispatch import build_messages, shadow_trades
from pead_bot.notify.format import format_alert
from pead_bot.scanner import run_scan
from pead_bot.store.db import Trade, TradeStore
from pead_bot.strategy.plan import make_plan
from pead_bot.tracking.report import committee_lines, format_weekly

from test_llm import NEWS, FakeAPNews
from test_notify import DAYS, _ev
from test_scanner import NOW, FakeFH

CFG = Config()


def _verdict(conf=4, **kw):
    base = dict(resumen="r", sorpresa_real=True, letra_chica="", guia="subio", sentimiento="positivo",
                riesgos=["Competencia"], confianza=conf,
                prediccion="Probablemente siga subiendo unos días por la guía al alza.",
                a_favor="Subió su pronóstico del año", en_contra="Ya subió 4% y el sector está caro")
    base.update(kw)
    return CommitteeVerdict(**base)


class FakeMsgs:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def parse(self, **kw):
        role = ("juez" if kw["output_format"] is CommitteeVerdict
                else "optimista" if "OPTIMISTA" in kw["system"] else "pesimista")
        self.calls.append((role, kw))
        if role in self.fail:
            return SimpleNamespace(stop_reason="max_tokens", parsed_output=None)
        out = (_verdict(conf=9) if role == "juez"
               else Argument(argumento=f"caso {role}", puntos=[f"{role} 1", f"{role} 2"]))
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out)


def _committee(fail=()):
    m = FakeMsgs(fail)
    return CommitteeAnalyzer("claude-haiku-5-5", client=SimpleNamespace(messages=m)), m


def test_committee_runs_three_roles_and_judge_sees_both():
    an, m = _committee()
    v = an.analyze(_ev().data, NEWS)
    assert [r for r, _ in m.calls] == ["optimista", "pesimista", "juez"]
    judge_prompt = m.calls[2][1]["messages"][0]["content"]
    assert "<optimista>" in judge_prompt and "caso optimista" in judge_prompt
    assert "<pesimista>" in judge_prompt and "pesimista 2" in judge_prompt
    assert all(kw["model"] == "claude-haiku-5-5" for _, kw in m.calls)
    assert v.confianza == 5                                   # 9 → 5


def test_committee_survives_one_side_failing():
    an, m = _committee(fail={"pesimista"})
    v = an.analyze(_ev().data, NEWS)
    assert v is not None and "(no disponible)" in m.calls[2][1]["messages"][0]["content"]


def test_committee_none_without_news_or_judge():
    an, m = _committee()
    assert an.analyze(_ev().data, []) is None and m.calls == []
    an2, _ = _committee(fail={"juez"})
    assert an2.analyze(_ev().data, NEWS) is None


def test_alert_shows_prediction_on_top_and_committee_block():
    ev = _ev()
    ev.analysis = _verdict(conf=4, letra_chica="Incluye venta de una planta")
    txt = format_alert(ev, make_plan(104.0, date(2026, 11, 12), CFG.plan, DAYS), NOW, cfg=CFG)
    assert txt.index("🔮") < txt.index("LA JUGADA")
    for must in ["Lo que creemos:</b> Probablemente siga subiendo", "⚖️ <b>COMITÉ</b>",
                 "🟢 A favor: Subió su pronóstico", "🔴 En contra: Ya subió 4%",
                 "Juez: confianza 4/5", "Letra chica: Incluye venta"]:
        assert must in txt, must
    assert "🤖 <b>CLAUDE</b>" not in txt


def test_enrich_caches_committee_separately(tmp_path):
    ap = FakeAPNews()
    res = run_scan(CFG, FakeFH(), ap, now=NOW)
    an, m = _committee()
    assert enrich(res, CFG, ap, an, tmp_path) == 1
    assert list(tmp_path.glob("*_GOOD_comite.json"))
    res2 = run_scan(CFG, FakeFH(), ap, now=NOW)
    assert enrich(res2, CFG, ap, an, tmp_path) == 0
    good = next(e for e in res2.evaluations if e.data.symbol == "GOOD")
    assert isinstance(good.analysis, CommitteeVerdict) and good.analysis.prediccion


def test_rejected_go_to_shadow_not_to_chat():
    res = run_scan(CFG, FakeFH(), FakeAPNews(), now=NOW)
    good = next(e for e in res.evaluations if e.data.symbol == "GOOD")
    good.analysis = _verdict(conf=2)
    assert build_messages(res, CFG, set()) == []
    [(key, t)] = shadow_trades(res, CFG, set())
    assert key == "shadow:2026-11-12:GOOD" and t.alertada == 0 and t.confidence == 2
    assert shadow_trades(res, CFG, {key}) == []
    good.analysis = _verdict(conf=4)
    assert shadow_trades(res, CFG, set()) == []


def test_open_total_ignores_shadows():
    st = TradeStore(":memory:")
    st.add(Trade("A", "A", NOW, NOW.date(), 100, 95, 108, date(2026, 11, 19), 2, 4, monto_mxn=7500))
    st.add(Trade("B", "B", NOW, NOW.date(), 100, 95, 108, date(2026, 11, 19), 2, 2, monto_mxn=5000,
                 alertada=0))
    assert st.open_total() == (1, 7500)
    assert {t.symbol: t.alertada for t in st.open_trades()} == {"A": 1, "B": 0}


def _closed(sym, exit_price, alertada=1):
    t = Trade(sym, sym, NOW, NOW.date(), 100, 95, 108, date(2026, 11, 19), 2, 3, 500, "closed",
              date(2026, 11, 16), exit_price, "objetivo", 500, monto_mxn=5000, alertada=alertada)
    return t


def test_committee_section_in_weekly_report():
    C = CostsCfg()
    appr = [_closed("A", 108)]
    rej = [_closed("B", 95, 0)]
    lines = committee_lines(appr, rej, C)
    assert "Aprobadas: 1 · promedio +6.9% neto" in lines[1]
    assert "Rechazadas (solo simuladas): 1 · promedio -6.1% neto" in lines[2]
    assert "muy pocas" in lines[3]
    txt = format_weekly(appr, appr, [], date(2026, 11, 22), C, 5000, rejected=rej)
    assert "¿El comité acierta?" in txt
    assert committee_lines(appr, [], C) == []
