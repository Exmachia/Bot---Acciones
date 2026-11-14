"""Fase 2: textos de alertas, qué se manda y cuándo, y cliente de Telegram (sin red)."""
from datetime import date, datetime, time

import pytest
import requests

from pead_bot.config import Config
from pead_bot.notify.dispatch import SentStore, build_messages, is_pre_open
from pead_bot.notify.format import SIC_NOTE, fmt_date, fmt_money, format_alert, format_pending
from pead_bot.notify.telegram import TelegramClient, TelegramError, split_message
from pead_bot.scanner import ScanResult, run_scan
from pead_bot.schedule import NY
from pead_bot.strategy.filters import CandidateData, evaluate
from pead_bot.strategy.plan import make_plan

from test_scanner import NOW, FakeAP, FakeFH, _days

CFG = Config()
DAYS = [d.date for d in _days()]


def _ev(symbol="ACME", rvol=3.0, price=104.0, name="Acme & <Co>"):
    d = CandidateData(symbol, name, date(2026, 11, 11), "amc", 1.30, 1.00, 2.4e9, 2.2e9,
                      price=price, base_close=100.0, avg_volume=3e6, market_cap=25e9, rvol=rvol)
    return evaluate(d, CFG.strategy, CFG.filters)


# --- formato -----------------------------------------------------------------
def test_fmt_helpers():
    assert fmt_date(date(2026, 11, 18)) == "mié 18 nov"
    assert fmt_money(2.4e9) == "$2.4 mil M"
    assert fmt_money(350e6) == "$350 M"
    assert fmt_money(None) == "s/d"


def test_alert_has_everything():
    ev = _ev()
    plan = make_plan(104.0, date(2026, 11, 12), CFG.plan, DAYS)
    txt = format_alert(ev, plan, NOW)
    for must in ["<b>ACME</b>", "Acme &amp; &lt;Co&gt;",          # nombre escapado para HTML
                 "Resultados trimestrales (mié 11 nov, después del cierre)",
                 "Ganó $1.30 por acción vs. $1.00 esperado (+30%).",
                 "Ventas: $2.4 mil M vs. $2.2 mil M esperado (superó).",
                 "+4.0% desde el cierre previo", "3.0x lo normal",
                 "Entrada: $104.00", "Stop: $98.80 (-5%)", "Objetivo: $112.32 (+8%)",
                 "Fecha límite: jue 19 nov", "Confianza:</b> sin lectura de noticias · 2 de 3 puntos",
                 "sec.gov", "CIK=ACME", SIC_NOTE, "No es asesoría"]:
        assert must in txt, must


def test_pending_list():
    txt = format_pending([_ev("AAA", rvol=None), _ev("BBB", rvol=None)], date(2026, 11, 12), NOW)
    assert "Pendientes de confirmación · jue 12 nov" in txt
    assert "Todavía no es señal" in txt
    assert txt.count("•") == 2


# --- qué se manda -----------------------------------------------------------
def test_intraday_sends_alert_once():
    res = run_scan(CFG, FakeFH(), FakeAP(), now=NOW)  # jueves 12:00 NY: GOOD pasa, CHASE no
    msgs = build_messages(res, CFG, set())
    assert len(msgs) == 1 and msgs[0].keys == ["alert:2026-11-12:GOOD"]
    assert "<b>GOOD</b>" in msgs[0].text
    assert build_messages(res, CFG, set(msgs[0].keys)) == []  # no se repite


def _premarket_result(evs, now=datetime(2026, 11, 12, 8, 30, tzinfo=NY)):
    cal = _days()
    session = next(d for d in cal if d.date == now.date())
    return ScanResult(now, session, DAYS, evs, 0, active_day=now.date())


def test_premarket_sends_pending_only_new_symbols():
    res = _premarket_result([_ev("AAA", rvol=None), _ev("BBB", rvol=None), _ev("BAD", price=3)])
    assert is_pre_open(res)
    msgs = build_messages(res, CFG, set())
    assert len(msgs) == 1
    assert "AAA" in msgs[0].text and "BBB" in msgs[0].text and "BAD" not in msgs[0].text
    sent = set(msgs[0].keys)
    res2 = _premarket_result([_ev("AAA", rvol=None), _ev("BBB", rvol=None), _ev("CCC", rvol=None)])
    msgs2 = build_messages(res2, CFG, sent)
    assert len(msgs2) == 1 and "CCC" in msgs2[0].text and "AAA" not in msgs2[0].text


def test_no_pending_list_once_market_is_open():
    res = _premarket_result([_ev("AAA", rvol=None)], now=datetime(2026, 11, 12, 9, 40, tzinfo=NY))
    assert not is_pre_open(res)
    assert build_messages(res, CFG, set()) == []


def test_weekend_counts_as_pre_open_for_monday():
    sat = datetime(2026, 11, 14, 10, 0, tzinfo=NY)
    res = ScanResult(sat, None, DAYS, [_ev("AAA", rvol=None)], 0, active_day=date(2026, 11, 16))
    assert is_pre_open(res)
    assert "lun 16 nov" in build_messages(res, CFG, set())[0].text


def test_sent_store_roundtrip_and_prune(tmp_path):
    st = SentStore(tmp_path / "x" / "sent.json", keep_days=14)
    assert st.load() == set()
    st.save({"alert:2026-11-12:A", "alert:2026-10-01:OLD"}, date(2026, 11, 14))
    assert st.load() == {"alert:2026-11-12:A"}


# --- cliente de Telegram -----------------------------------------------------
def test_split_message_respects_limit():
    text = "\n".join(f"línea {i} " + "x" * 50 for i in range(200))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data
        self.headers = {"content-type": "application/json"}
        self.text = str(data)

    def json(self):
        return self._d


class _Sess:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_send_ok_uses_html():
    s = _Sess([_Resp(200, {"ok": True})])
    TelegramClient("123:SECRETO", "99", session=s).send("<b>hola</b>")
    assert s.calls[0][1] == {"chat_id": "99", "text": "<b>hola</b>", "parse_mode": "HTML",
                             "disable_web_page_preview": True}


def test_error_never_leaks_token(monkeypatch):
    monkeypatch.setattr("pead_bot.notify.telegram.time.sleep", lambda s: None)
    err = requests.ConnectionError("https://api.telegram.org/bot123:SECRETO/sendMessage")
    with pytest.raises(TelegramError) as e:
        TelegramClient("123:SECRETO", "99", session=_Sess([err] * 4)).send("x")
    assert "SECRETO" not in str(e.value)
    with pytest.raises(TelegramError) as e:
        TelegramClient("123:SECRETO", "99",
                       session=_Sess([_Resp(401, {"ok": False, "description": "Unauthorized"})])).send("x")
    assert "SECRETO" not in str(e.value) and "TELEGRAM_BOT_TOKEN" in str(e.value)


def test_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr("pead_bot.notify.telegram.time.sleep", lambda s: None)
    s = _Sess([_Resp(429, {"ok": False, "parameters": {"retry_after": 1}}), _Resp(200, {"ok": True})])
    TelegramClient("t", "1", session=s).send("x")
    assert len(s.calls) == 2


# --- en palabras sencillas (monto según confianza, tope 10,000) ---------------
from pead_bot.llm.news import NewsAnalysis  # noqa: E402
from pead_bot.notify.format import format_guide  # noqa: E402


def _with_conf(ev, conf):
    ev.analysis = NewsAnalysis(resumen="r", sorpresa_real=True, letra_chica="", guia="igual",
                               sentimiento="positivo", riesgos=[], confianza=conf)
    return ev


def test_amount_rule():
    c = CFG.capital
    assert [c.monto(k) for k in (5, 4, 3, None)] == [10_000, 7_500, 5_000, 5_000]


def test_alert_plain_block_confidence_4():
    plan = make_plan(104.0, date(2026, 11, 12), CFG.plan, DAYS)
    txt = format_alert(_with_conf(_ev(), 4), plan, NOW, cfg=CFG, open_total=(1, 5_000))
    for must in ["En palabras sencillas", "ganó 30% más de lo que esperaban",
                 "Monto: $7,500 MXN</b> (confianza 4 de 5 → 75% de tu tope de $10,000)",
                 "como máximo hasta el jue 19 nov (5 días de bolsa)",
                 "sales perdiendo ≈ $455 MXN",      # 7,500 × −6.0655%
                 "sales ganando ≈ $517 MXN",        # 7,500 × +6.8968%
                 "Ya tienes 1 jugada(s) abierta(s) con ≈ $5,000 MXN; con esta serían ≈ $12,500",
                 "bajo tu propio riesgo", "Los detalles"]:
        assert must in txt, must
    assert txt.index("En palabras sencillas") < txt.index("Catalizador")


def test_alert_without_claude_uses_lowest_amount():
    plan = make_plan(104.0, date(2026, 11, 12), CFG.plan, DAYS)
    txt = format_alert(_ev(), plan, NOW, cfg=CFG, open_total=(0, 0))
    assert "Monto: $5,000 MXN</b> (sin lectura de noticias → 50%" in txt
    assert "jugada(s) abierta(s)" not in txt


def test_trade_records_amount_and_running_total():
    res = _premarket_result([])
    res.now = datetime(2026, 11, 12, 12, 0, tzinfo=NY)
    res.evaluations = [_with_conf(_ev("AAA"), 5), _with_conf(_ev("BBB"), 3)]
    msgs = build_messages(res, CFG, set(), open_total=(0, 0.0))
    assert msgs[0].trade.monto_mxn == 10_000 and msgs[1].trade.monto_mxn == 5_000
    assert "con esta serían ≈ $15,000" in msgs[1].text


def test_guide_explains_in_plain_words():
    txt = format_guide(CFG)
    for must in ["Cómo funciona este bot", "más de 10% por encima", "Tope por jugada: $10,000 MXN",
                 "5 → $10,000, 4 → $7,500, 3 → $5,000", "sin lectura → $5,000",
                 "si baja 5%, si sube 8%", "5 días de bolsa", "no compra ni vende",
                 "bajo tu propio riesgo", SIC_NOTE]:
        assert must in txt, must
