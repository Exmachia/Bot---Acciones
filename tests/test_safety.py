import pytest

from pead_bot.safety import (ALPACA_PAPER_TRADING_URL, LiveTradingRefused,
                             assert_environment_is_safe, assert_url_is_safe)


def test_paper_and_data_urls_ok():
    assert_url_is_safe(ALPACA_PAPER_TRADING_URL + "/v2/account")
    assert_url_is_safe("https://data.alpaca.markets/v2/stocks/bars")
    assert_url_is_safe("https://finnhub.io/api/v1/calendar/earnings")


@pytest.mark.parametrize("url", [
    "https://api.alpaca.markets/v2/orders",
    "https://API.alpaca.markets/v2/account",
    "https://broker-api.alpaca.markets/v1",
])
def test_live_urls_refused(url):
    with pytest.raises(LiveTradingRefused):
        assert_url_is_safe(url)


def test_env_with_live_url_refused():
    with pytest.raises(LiveTradingRefused):
        assert_environment_is_safe(environ={"APCA_API_BASE_URL": "https://api.alpaca.markets"})


def test_env_paper_ok():
    assert_environment_is_safe(environ={"APCA_API_BASE_URL": "https://paper-api.alpaca.markets",
                                        "ALPACA_API_KEY_ID": "PKABC123"})


def test_live_key_prefix_refused():
    with pytest.raises(LiveTradingRefused):
        assert_environment_is_safe(environ={"ALPACA_API_KEY_ID": "AKLIVE123"})


def test_config_file_with_live_url_refused(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("base_url: https://api.alpaca.markets\n")
    with pytest.raises(LiveTradingRefused):
        assert_environment_is_safe(p, environ={})


def test_no_order_function_exists():
    import pead_bot.data.alpaca as m
    src = open(m.__file__).read().lower()
    assert "/v2/orders" not in src and "def submit" not in src and "def order" not in src
