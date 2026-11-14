"""Botones Voy/Paso y avisos de venta (sin red)."""
from datetime import date, datetime, time

from pead_bot.config import CostsCfg
from pead_bot.data.alpaca import TradingDay
from pead_bot.notify.decisions import (alert_buttons, parse_callback, process_updates,
                                       watch_taken)
from pead_bot.schedule import NY
from pead_bot.store.db import Trade, TradeStore

C = CostsCfg()
D0, DEADLINE = date(2026, 11, 12), date(2026, 11, 19)
OPENED = datetime(2026, 11, 12, 11, 0, tzinfo=NY)


def _session(d):
    return TradingDay(d, datetime.combine(d, time(9, 30), tzinfo=NY), datetime.combine(d, time(16, 0), tzinfo=NY))


def _trade(decision="voy", **kw):
    t = Trade("ACME", "Acme Inc", OPENED, D0, 104.0, 98.8, 112.32, DEADLINE, 2, 4, 500.0,
              monto_mxn=7_500, decision=decision)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_buttons_roundtrip():
    [[voy, paso]] = alert_buttons("ACME", D0)
    assert voy == ("✅ Voy", "voy|ACME|2026-11-12") and paso[0] == "❌ Paso"
    assert parse_callback(voy[1]) == ("voy", "ACME", D0)
    assert parse_callback("borrar|ACME|2026-11-12") is None and parse_callback("basura") is None


class FakeTG:
    def __init__(self, updates):
        self.updates, self.sent, self.edits, self.answers, self.offsets = updates, [], [], [], []

    def get_updates(self, offset=None):
        self.offsets.append(offset)
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def answer_callback(self, cid, text=""):
        self.answers.append(text)

    def edit_buttons(self, mid, buttons):
        self.edits.append((mid, buttons))

    def send(self, text, buttons=None):
        self.sent.append(text)


def _tap(uid, data, chat="99"):
    return {"update_id": uid, "callback_query": {"id": f"c{uid}", "data": data,
                                                 "message": {"message_id": 7, "chat": {"id": int(chat)}}}}


def test_tap_voy_records_decision_and_confirms(tmp_path):
    st = TradeStore(":memory:")
    st.add(_trade(decision=None))
    tg = FakeTG([{"update_id": 1, "message": {"text": "hola"}}, _tap(2, "voy|ACME|2026-11-12")])
    log = process_updates(tg, st, "99", tmp_path / "s.json")
    assert log == ["ACME: voy"] and st.get("ACME", D0).decision == "voy"
    assert "Anotado: vas en ACME" in tg.sent[0] and "$7,500 MXN" in tg.sent[0]
    assert tg.edits[0][1][0][0][0] == "✅ Vas en esta"
    # la siguiente corrida no vuelve a procesar lo mismo
    tg2 = FakeTG(tg.updates)
    assert process_updates(tg2, st, "99", tmp_path / "s.json") == [] and tg2.offsets == [3]


def test_taps_from_other_chats_are_ignored(tmp_path):
    st = TradeStore(":memory:")
    st.add(_trade(decision=None))
    process_updates(FakeTG([_tap(1, "voy|ACME|2026-11-12", chat="666")]), st, "99", tmp_path / "s.json")
    assert st.get("ACME", D0).decision is None


def test_change_mind_and_paso(tmp_path):
    st = TradeStore(":memory:")
    st.add(_trade(decision=None))
    tg = FakeTG([_tap(1, "voy|ACME|2026-11-12"), _tap(2, "cambiar|ACME|2026-11-12"),
                 _tap(3, "paso|ACME|2026-11-12")])
    process_updates(tg, st, "99", tmp_path / "s.json")
    assert st.get("ACME", D0).decision == "paso"
    assert "pasas en ACME" in tg.sent[-1]


# --- avisos de venta --------------------------------------------------------
def test_stop_notice_once_with_mxn():
    now = datetime(2026, 11, 13, 11, 0, tzinfo=NY)
    [n] = watch_taken([_trade()], {"ACME": 98.5}, now, _session(now.date()), C)
    assert n.kind == "stop" and "Vende ACME" in n.text and "pérdida queda en ≈ $" in n.text
    assert watch_taken([_trade(notices="stop")], {"ACME": 97}, now, _session(now.date()), C) == []


def test_target_notice():
    now = datetime(2026, 11, 13, 11, 0, tzinfo=NY)
    [n] = watch_taken([_trade()], {"ACME": 113.0}, now, _session(now.date()), C)
    assert n.kind == "objetivo" and "llegó al objetivo" in n.text and "ganancia" in n.text


def test_last_day_notice_once():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=NY)
    [n] = watch_taken([_trade()], {"ACME": 106.0}, now, _session(DEADLINE), C)
    assert n.kind == "ultimo_dia" and "15:00 CDMX" in n.text and "+1.9%" in n.text
    assert watch_taken([_trade(notices="ultimo_dia")], {"ACME": 106.0}, now, _session(DEADLINE), C) == []


def test_no_notices_when_market_closed_or_paso():
    sat = datetime(2026, 11, 14, 11, 0, tzinfo=NY)
    assert watch_taken([_trade()], {"ACME": 90}, sat, None, C) == []
    now = datetime(2026, 11, 13, 11, 0, tzinfo=NY)
    assert watch_taken([_trade(decision="paso")], {"ACME": 90}, now, _session(now.date()), C) == []


def test_closed_at_eod_sends_cierre():
    t = _trade(status="closed", exit_price=98.8, exit_reason="stop", exit_date=date(2026, 11, 13))
    [n] = watch_taken([t], {}, datetime(2026, 11, 13, 16, 40, tzinfo=NY), None, C)
    assert n.kind == "cierre" and "tocó el stop" in n.text


def test_store_taken_excludes_already_sold():
    st = TradeStore(":memory:")
    st.add(_trade())
    t = st.get("ACME", D0)
    assert [x.symbol for x in st.taken()] == ["ACME"]
    st.add_notice(t.id, "ultimo_dia")
    assert len(st.taken()) == 1
    st.add_notice(t.id, "stop")
    assert st.taken() == [] and st.get("ACME", D0).has_notice("ultimo_dia")


def test_listen_mode_passes_wait(tmp_path):
    class WaitTG(FakeTG):
        def get_updates(self, offset=None, wait=0):
            self.waits = getattr(self, "waits", []) + [wait]
            return super().get_updates(offset)
    st = TradeStore(":memory:")
    st.add(_trade(decision=None))
    tg = WaitTG([_tap(1, "paso|ACME|2026-11-12")])
    assert process_updates(tg, st, "99", tmp_path / "s.json", wait=30) == ["ACME: paso"]
    assert tg.waits == [30]
