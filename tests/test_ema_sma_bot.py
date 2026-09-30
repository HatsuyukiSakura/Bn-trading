import hashlib
import hmac
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from ema_sma_bot.backtest import _funding_sums, run_portfolio, simulate_symbol
from ema_sma_bot.binance_client import (BinanceError, FuturesClient, UnknownExecution, floor_to_step,
                                        format_decimal)
from ema_sma_bot.config import Config, load_dotenv
from ema_sma_bot.indicators import ema, sma
from ema_sma_bot.store import Store
from ema_sma_bot.strategy import FLAT, LONG, StrategyParams, compute_signals, desired_position
from ema_sma_bot.trader import PERIOD_MS, Trader, last_closed_bar_open

H4 = PERIOD_MS


def make_df(closes, lows=None, start=0):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    lows = np.minimum(opens, closes) if lows is None else np.asarray(lows, dtype=float)
    t = start + np.arange(len(closes), dtype="int64") * H4
    return pd.DataFrame({"open_time": t, "open": opens, "high": np.maximum(opens, closes),
                         "low": lows, "close": closes, "volume": 1.0, "close_time": t + H4 - 1})


# ================================================================ 指標
def test_sma_matches_mean():
    out = sma(pd.Series(range(1, 11), dtype=float), 5)
    assert out.iloc[:4].isna().all()
    assert out.iloc[4] == 3.0 and out.iloc[-1] == 8.0


def test_ema_seeded_with_sma_and_recursive():
    out = ema(pd.Series([1, 2, 3, 4, 5, 6, 7], dtype=float), 3)
    assert out.iloc[:2].isna().all()
    expected = 2.0
    for v in [4, 5, 6, 7]:
        expected = 0.5 * v + 0.5 * expected
    assert out.iloc[-1] == pytest.approx(expected)


# ================================================================ 策略
@pytest.mark.parametrize("current, close, e, s, mode, expected", [
    (FLAT, 101, 100, 0, "ema", LONG),
    (LONG, 99, 100, 0, "ema", FLAT),
    (LONG, 100, 100, 0, "ema", LONG),          # 等於 EMA → 維持
    (FLAT, 100, 100, 0, "ema", FLAT),
    (FLAT, 110, 100, 90, "ema_sma", LONG),
    (FLAT, 110, 90, 100, "ema_sma", FLAT),     # 均線空頭排列不進場
    (LONG, 95, 100, 90, "ema_sma", LONG),      # 均線之間續抱
    (LONG, 85, 100, 90, "ema_sma", FLAT),
    (FLAT, 110, float("nan"), 90, "ema", FLAT),
])
def test_desired_position(current, close, e, s, mode, expected):
    assert desired_position(current, close, e, s, mode) == expected


def test_compute_signals_trend():
    closes = list(np.linspace(100, 200, 400)) + list(np.linspace(200, 80, 200))
    out = compute_signals(make_df(closes), StrategyParams())
    assert (out["position"].iloc[:149] == FLAT).all()
    assert LONG in out["position"].values
    assert out["position"].iloc[-1] == FLAT


# ================================================================ 回測
def test_funding_sums_window():
    open_times = np.array([0, H4, 2 * H4], dtype="int64")
    funding = pd.DataFrame({"funding_time": [0, H4, H4 + 5, 3 * H4], "rate": [0.1, 0.01, 0.02, 0.5]})
    # t=0 的結算在進場之前 → 不計；(0, 4H] 含 H4；(4H, 8H] 含 H4+5；(8H, 12H] 含 3H4
    assert _funding_sums(open_times, funding) == pytest.approx([0.01, 0.02, 0.5])


def test_backtest_uptrend_no_lookahead_and_fees():
    df = make_df(np.linspace(100, 300, 600))
    res = simulate_symbol(df, StrategyParams(), capital=1000, fee_rate=0.0)
    assert res.equity.iloc[-1] > 1000
    first_signal = int(np.argmax(compute_signals(df, StrategyParams())["position"].to_numpy() == LONG))
    assert res.equity.iloc[first_signal] == pytest.approx(1000)  # 訊號當根尚未成交
    with_fee = simulate_symbol(df, StrategyParams(), capital=1000, fee_rate=0.0005)
    assert with_fee.equity.iloc[-1] < res.equity.iloc[-1]
    assert with_fee.fees_paid > 0


def test_backtest_funding_reduces_equity():
    df = make_df(np.linspace(100, 300, 600))
    funding = pd.DataFrame({"funding_time": df["open_time"] + H4, "rate": 0.0001})
    base = simulate_symbol(df, StrategyParams(), capital=1000)
    paid = simulate_symbol(df, StrategyParams(), capital=1000, funding=funding)
    assert paid.funding_paid > 0
    assert paid.equity.iloc[-1] == pytest.approx(base.equity.iloc[-1] - paid.funding_paid, rel=1e-6)


def test_backtest_stop_and_reentry_lock():
    closes = np.linspace(100, 200, 400)
    lows = np.minimum(np.concatenate([[closes[0]], closes[:-1]]), closes).copy()
    lows[300] = closes[300] * 0.5          # 盤中插針到 -50%，收盤仍在 EMA 之上
    df = make_df(closes, lows=lows)
    params = StrategyParams(stop_loss_pct=0.10)
    res = simulate_symbol(df, params, capital=1000, fee_rate=0.0)
    stops = [t for t in res.trades if t["reason"] == "STOP"]
    assert len(stops) == 1
    assert stops[0]["exit_price"] == pytest.approx(stops[0]["entry_price"] * 0.9)
    # 停損後價格仍在 EMA 上方 → 上鎖不再進場，之後權益維持現金不變
    assert res.equity.iloc[-1] == pytest.approx(res.equity.iloc[301])
    assert len(res.trades) == 1
    # 無停損時插針不影響
    no_stop = simulate_symbol(df, StrategyParams(), capital=1000, fee_rate=0.0)
    assert no_stop.trades == []


def test_backtest_lock_released_after_exit_signal():
    up = np.linspace(100, 200, 400)
    closes = np.concatenate([up, np.linspace(200, 120, 100), np.linspace(120, 260, 200)])
    lows = np.minimum(np.concatenate([[closes[0]], closes[:-1]]), closes).copy()
    lows[300] = closes[300] * 0.5
    res = simulate_symbol(make_df(closes, lows=lows), StrategyParams(stop_loss_pct=0.10), 1000, 0.0)
    reasons = [t["reason"] for t in res.trades]
    assert reasons[0] == "STOP"
    assert res.equity.iloc[-1] > res.equity.iloc[450]  # 跌破 EMA 解鎖後，再次站上時重新進場


def test_portfolio_combines_sleeves():
    a = make_df(np.linspace(100, 300, 600))
    b = make_df(np.linspace(100, 50, 500), start=100 * H4)   # 較晚上市、下跌
    res = run_portfolio({"A": a, "B": b}, StrategyParams(), 10_000, 0.0)
    assert res.equity.iloc[0] == pytest.approx(10_000)
    m = res.metrics()
    assert m["total_return"] > 0 and -1 <= m["max_drawdown"] <= 0
    assert "資金費支出" in res.summary()


# ================================================================ 客戶端
def test_floor_and_format():
    assert floor_to_step(0.123456, "0.001") == Decimal("0.123")
    assert format_decimal(floor_to_step(1.99999, "0.01000000")) == "1.99"
    assert format_decimal(floor_to_step(Decimal("12.7"), Decimal("1"))) == "12"


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, params=None, timeout=None):
        self.calls.append((method, url, dict(params or {})))
        return self.responses.pop(0)

    def get(self, url, timeout=None):
        return FakeResponse(200, {"serverTime": 1_700_000_000_000})


def test_signed_request_signature():
    sess = FakeSession([FakeResponse(200, {"ok": 1})])
    c = FuturesClient("k", "secret", session=sess)
    c.request("GET", "/fapi/v3/account", {"a": 1}, signed=True)
    params = sess.calls[0][2]
    sig = params.pop("signature")
    from urllib.parse import urlencode
    assert sig == hmac.new(b"secret", urlencode(params).encode(), hashlib.sha256).hexdigest()
    assert sess.calls[0][1] == "https://demo-fapi.binance.com/fapi/v3/account"


def test_order_503_unknown_is_not_retried():
    sess = FakeSession([FakeResponse(503, {"code": -1, "msg": "Unknown error, please check your request"})])
    c = FuturesClient("k", "s", session=sess)
    with pytest.raises(UnknownExecution):
        c.market_order("BTCUSDT", "BUY", Decimal("0.01"), "cid")
    assert len(sess.calls) == 1


def test_time_error_resyncs_and_retries():
    sess = FakeSession([FakeResponse(400, {"code": -1021, "msg": "ts"}), FakeResponse(200, {"ok": 1})])
    c = FuturesClient("k", "s", session=sess)
    assert c.request("GET", "/x", signed=True) == {"ok": 1}


def test_ok_code_on_margin_type():
    sess = FakeSession([
        FakeResponse(200, {"dualSidePosition": True}),
        FakeResponse(400, {"code": -4059, "msg": "No need"}),
        FakeResponse(200, {"multiAssetsMargin": False}),
        FakeResponse(400, {"code": -4046, "msg": "No need"}),
        FakeResponse(200, {"leverage": 2}),
    ])
    FuturesClient("k", "s", session=sess).init_account(["BTCUSDT"], 2)
    assert sess.calls[-1][2]["leverage"] == 2


def test_banned_raises():
    sess = FakeSession([FakeResponse(418, {"code": -1003, "msg": "banned"})])
    with pytest.raises(BinanceError) as info:
        FuturesClient(session=sess).request("GET", "/fapi/v1/klines")
    assert info.value.status == 418


# ================================================================ Trader
NOW = 1_800_000_000_000 - (1_800_000_000_000 % H4) + 60_000   # 某個 4H 邊界後 60 秒


class FakeFutures:
    def __init__(self, closes, equity=10_000, positions=None, mark=None):
        n = len(closes)
        start = last_closed_bar_open(NOW) - (n - 1) * H4
        df = make_df(list(closes) + [closes[-1]], start=start)   # 最後一根是未收盤 K 棒
        self.df = df
        self.equity = Decimal(str(equity))
        self.pos = positions or {}
        self.mark = Decimal(str(mark if mark is not None else closes[-1]))
        self.orders, self.algo, self.cancelled = [], [], []
        self.existing = {}
        self.unknown = False
        self._algo_id = 0

    def now_ms(self):
        return NOW

    def klines(self, symbol, interval, limit):
        return self.df.tail(limit).reset_index(drop=True)

    def exchange_filters(self, symbols):
        return {s: {"status": "TRADING", "step": Decimal("0.001"), "min_qty": Decimal("0.001"),
                    "max_qty": Decimal("1000"), "min_notional": Decimal("5"), "tick": Decimal("0.01")}
                for s in symbols}

    def account_equity(self):
        return self.equity

    def positions(self, symbols):
        return {s: p for s, p in self.pos.items() if s in symbols}

    def mark_price(self, symbol):
        return self.mark

    def query_order(self, symbol, cid):
        return self.existing.get(cid)

    def market_order(self, symbol, side, qty, cid, reduce_only=False):
        self.orders.append((symbol, side, qty, reduce_only, cid))
        if self.unknown:
            raise UnknownExecution("503")
        return {"status": "FILLED", "avgPrice": str(self.mark)}

    def open_algo_orders(self, symbol):
        return [o for o in self.algo if o["symbol"] == symbol]

    def place_stop_market(self, symbol, trigger, cid, working_type):
        self._algo_id += 1
        self.algo.append({"symbol": symbol, "algoId": self._algo_id, "clientAlgoId": cid,
                          "triggerPrice": str(trigger), "workingType": working_type})
        return self.algo[-1]

    def cancel_algo_order(self, algo_id):
        self.cancelled.append(algo_id)
        self.algo = [o for o in self.algo if o["algoId"] != algo_id]


UP = list(np.linspace(100, 200, 500))          # 收盤在 EMA 上方 → LONG
DOWN = UP + list(np.linspace(200, 100, 120))   # 收盤在 EMA 下方 → FLAT


def make_trader(tmp_path, client, **kw):
    base = dict(api_key="k", api_secret="s", symbols=["BTCUSDT", "ETHUSDT"],
                db_path=str(tmp_path / "state.db"))
    base.update(kw)
    return Trader(Config(**base), client, Store(base["db_path"]))


def test_enters_equal_weight_and_places_stop(tmp_path):
    client = FakeFutures(UP)
    make_trader(tmp_path, client).reconcile_cycle()
    buys = [o for o in client.orders if o[1] == "BUY"]
    assert len(buys) == 2
    assert buys[0][2] == floor_to_step(Decimal(5000) / Decimal("200"), "0.001")   # 1/N 權益，1 倍
    assert buys[0][3] is False
    assert len(client.algo) == 2
    assert Decimal(client.algo[0]["triggerPrice"]) == Decimal("180.00")   # 200 × 0.9
    assert client.algo[0]["workingType"] == "MARK_PRICE"


def test_idempotent_existing_order_is_not_resent(tmp_path):
    client = FakeFutures(UP)
    bar = last_closed_bar_open(NOW)
    client.existing[f"e150-BTCUSDT-{bar // 1000}-B0"] = {"status": "FILLED"}
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders == []


def test_exit_on_flat_signal_reduce_only_and_cancel_stop(tmp_path):
    client = FakeFutures(DOWN, positions={"BTCUSDT": {"amt": Decimal("20"), "entry": Decimal("110")}})
    client.algo.append({"symbol": "BTCUSDT", "algoId": 7, "clientAlgoId": "sl-BTCUSDT-1", "triggerPrice": "99"})
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders[0][1:4] == ("SELL", Decimal("20"), True)
    assert client.cancelled == [7]


def test_keeps_correct_stop_and_replaces_wrong_one(tmp_path):
    pos = {"BTCUSDT": {"amt": Decimal("50"), "entry": Decimal("200")}}   # 單一標的目標 = 10000/200
    client = FakeFutures(UP, positions=pos)
    client.algo.append({"symbol": "BTCUSDT", "algoId": 1, "clientAlgoId": "sl-a", "triggerPrice": "180.00"})
    client.algo.append({"symbol": "BTCUSDT", "algoId": 2, "clientAlgoId": "sl-b", "triggerPrice": "150"})
    client.algo.append({"symbol": "BTCUSDT", "algoId": 3, "clientAlgoId": "manual", "triggerPrice": "1"})
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.cancelled == [2]                 # 只取消錯誤的那張，手動單不動
    assert client.orders == []                     # 部位在 ±25% 內，不再平衡


def test_software_stop_and_reentry_lock(tmp_path):
    pos = {"BTCUSDT": {"amt": Decimal("25"), "entry": Decimal("200")}}
    client = FakeFutures(UP, positions=pos, mark=179)
    trader = make_trader(tmp_path, client, symbols=["BTCUSDT"])
    trader.reconcile_cycle()
    assert client.orders[0][1:4] == ("SELL", Decimal("25"), True)
    assert trader.store.get("stop_lock:BTCUSDT")

    # 部位已平，訊號仍是 LONG → 上鎖不進場
    client.pos, client.orders, client.mark = {}, [], Decimal("200")
    trader.reconcile_cycle()
    assert client.orders == []

    # 收盤跌破 EMA 解鎖，之後再次站上才進場
    down = FakeFutures(DOWN)
    trader.client = down
    trader.reconcile_cycle()
    assert trader.store.get("stop_lock:BTCUSDT") is None
    assert down.orders == []


def test_position_vanished_sets_lock(tmp_path):
    client = FakeFutures(UP)
    trader = make_trader(tmp_path, client, symbols=["BTCUSDT"])
    trader.store.set("was_long:BTCUSDT", "1")
    trader.reconcile_cycle()
    assert client.orders == []
    assert trader.store.get("stop_lock:BTCUSDT")


def test_missing_bar_never_trades(tmp_path):
    client = FakeFutures(DOWN, positions={"BTCUSDT": {"amt": Decimal("20"), "entry": Decimal("110")}})
    client.df = client.df.iloc[:-2]   # 最新已收盤 K 棒缺漏
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders == []


def test_unexpected_short_is_closed(tmp_path):
    client = FakeFutures(DOWN, positions={"BTCUSDT": {"amt": Decimal("-3"), "entry": Decimal("100")}})
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders[0][1:4] == ("BUY", Decimal("3"), True)


def test_mark_deviation_delays_entry(tmp_path):
    client = FakeFutures(UP, mark=210)   # 偏離 5%
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders == []


def test_rebalance_outside_band(tmp_path):
    pos = {"BTCUSDT": {"amt": Decimal("10"), "entry": Decimal("200")}}   # 目標 50，偏離 80%
    client = FakeFutures(UP, positions=pos)
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert client.orders[0][1:4] == ("BUY", Decimal("40"), False)


def test_kill_switch_flattens_and_halts(tmp_path):
    pos = {"BTCUSDT": {"amt": Decimal("5"), "entry": Decimal("200")}}
    client = FakeFutures(UP, positions=pos, equity=5000)
    trader = make_trader(tmp_path, client, symbols=["BTCUSDT"])
    trader.store.set("peak_equity", "10000")
    trader.reconcile_cycle()
    assert client.orders[0][1:4] == ("SELL", Decimal("5"), True)
    assert trader.store.get("halted") == "1"
    client.orders = []
    trader.reconcile_cycle()
    assert client.orders == []


def test_daily_loss_pauses_new_entries(tmp_path):
    client = FakeFutures(UP, equity=7000)
    trader = make_trader(tmp_path, client, symbols=["BTCUSDT"])
    trader.store.set("peak_equity", "10000")
    trader.store.record_equity(NOW - 3600_000, "10000")
    trader.reconcile_cycle()
    assert client.orders == []
    assert trader.store.get("entries_paused") == "1"


def test_unknown_execution_is_queried_not_resent(tmp_path, monkeypatch):
    monkeypatch.setattr("ema_sma_bot.trader.time.sleep", lambda s: None)
    client = FakeFutures(UP)
    client.unknown = True
    make_trader(tmp_path, client, symbols=["BTCUSDT"]).reconcile_cycle()
    assert len(client.orders) == 1


def test_dry_run_sends_nothing(tmp_path):
    client = FakeFutures(UP)
    make_trader(tmp_path, client, dry_run=True).reconcile_cycle()
    assert client.orders == [] and client.algo == []


# ================================================================ 設定
def test_config_defaults_and_validation(tmp_path, monkeypatch):
    cfg = Config(api_key="k", api_secret="s")
    cfg.validate()
    assert len(cfg.symbols) == 10 and cfg.max_gross_exposure == 1.0 and cfg.exchange_leverage == 2
    assert cfg.margin_type == "CROSSED" and cfg.stop_loss_pct == 0.10
    with pytest.raises(ValueError):
        Config(api_key="k", api_secret="s", max_gross_exposure=3).validate()
    with pytest.raises(ValueError):
        Config().validate()
    Config().validate(need_keys=False)

    env = tmp_path / ".env"
    env.write_text("FOO_TEST_KEY=bar  # comment\n# x\n")
    monkeypatch.delenv("FOO_TEST_KEY", raising=False)
    load_dotenv(str(env))
    import os
    assert os.environ["FOO_TEST_KEY"] == "bar"
    monkeypatch.delenv("FOO_TEST_KEY")
