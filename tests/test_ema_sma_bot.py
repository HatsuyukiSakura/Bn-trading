import hashlib
import hmac
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from ema_sma_bot.backtest import run_backtest
from ema_sma_bot.binance_client import BinanceClient, floor_to_step, format_decimal
from ema_sma_bot.config import Config
from ema_sma_bot.indicators import ema, sma
from ema_sma_bot.strategy import FLAT, LONG, SHORT, StrategyParams, compute_positions, next_position
from ema_sma_bot.trader import Trader

H4 = 14_400_000


def make_df(closes):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    t = np.arange(len(closes), dtype="int64") * H4
    return pd.DataFrame({"open_time": t, "open": opens, "high": np.maximum(opens, closes),
                         "low": np.minimum(opens, closes), "close": closes,
                         "volume": 1.0, "close_time": t + H4 - 1})


# ---------------------------------------------------------------- 指標
def test_sma_matches_mean():
    s = pd.Series(range(1, 11), dtype=float)
    out = sma(s, 5)
    assert out.iloc[:4].isna().all()
    assert out.iloc[4] == 3.0
    assert out.iloc[-1] == 8.0


def test_ema_seeded_with_sma_and_recursive():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7], dtype=float)
    out = ema(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(2.0)
    alpha = 0.5
    expected = 2.0
    for v in [4, 5, 6, 7]:
        expected = alpha * v + (1 - alpha) * expected
    assert out.iloc[-1] == pytest.approx(expected)


def test_ema_short_series_all_nan():
    assert ema(pd.Series([1.0, 2.0]), 5).isna().all()


# ---------------------------------------------------------------- 策略
@pytest.mark.parametrize("current, close, e, s, short, expected", [
    (FLAT, 110, 100, 90, False, LONG),     # 站上雙均線 + 多頭排列
    (FLAT, 110, 90, 100, False, FLAT),     # 站上但均線空頭排列 → 不進場
    (FLAT, 95, 100, 90, False, FLAT),      # 在雙均線之間
    (LONG, 95, 100, 90, False, LONG),      # 在均線之間 → 續抱
    (LONG, 85, 100, 90, False, FLAT),      # 跌破雙均線 → 出場
    (LONG, 85, 90, 100, True, SHORT),      # 跌破且空頭排列 → 反手做空
    (LONG, 85, 100, 90, True, FLAT),       # 跌破但仍多頭排列 → 只平倉
    (FLAT, 80, 90, 100, False, FLAT),      # 不允許做空
    (FLAT, 80, 90, 100, True, SHORT),
    (SHORT, 95, 90, 100, True, SHORT),
    (SHORT, 110, 90, 100, True, FLAT),
    (SHORT, 110, 100, 90, True, LONG),
    (FLAT, 110, float("nan"), 90, False, FLAT),
])
def test_next_position(current, close, e, s, short, expected):
    assert next_position(current, close, e, s, short) == expected


def test_compute_positions_uptrend_then_crash():
    closes = list(np.linspace(100, 200, 400)) + list(np.linspace(200, 80, 200))
    out = compute_positions(make_df(closes), StrategyParams())
    assert (out["position"].iloc[:149] == FLAT).all()
    assert LONG in out["position"].values
    assert out["position"].iloc[-1] == FLAT


# ---------------------------------------------------------------- 回測
def test_backtest_profits_on_trend_and_no_lookahead():
    closes = list(np.linspace(100, 300, 600))
    res = run_backtest(make_df(closes), StrategyParams(), fee_rate=0.0)
    assert res.total_return > 0
    assert res.num_trades == 0  # 持倉到最後，尚未平倉
    # 進場在訊號下一根開盤
    data = compute_positions(make_df(closes), StrategyParams())
    first_signal = int(np.argmax(data["position"].to_numpy() == LONG))
    assert res.equity_curve.iloc[first_signal] == pytest.approx(10_000.0)


def test_backtest_fees_reduce_equity():
    closes = list(np.linspace(100, 200, 400)) + list(np.linspace(200, 80, 200))
    df = make_df(closes)
    no_fee = run_backtest(df, StrategyParams(), fee_rate=0.0)
    fee = run_backtest(df, StrategyParams(), fee_rate=0.001)
    assert no_fee.num_trades == fee.num_trades >= 1
    assert fee.final_equity < no_fee.final_equity
    assert 0 <= fee.max_drawdown <= 1


# ---------------------------------------------------------------- 客戶端工具
def test_floor_to_step():
    assert floor_to_step(0.123456, "0.001") == Decimal("0.123")
    assert format_decimal(floor_to_step(1.99999, "0.01000000")) == "1.99"
    assert format_decimal(floor_to_step(12.7, "1.00000000")) == "12"


def test_signature():
    c = BinanceClient(api_key="k", api_secret="secret")
    signed = c._sign({"symbol": "BTCUSDT", "timestamp": 1})
    query = "symbol=BTCUSDT&timestamp=1"
    expected = hmac.new(b"secret", query.encode(), hashlib.sha256).hexdigest()
    assert signed == f"{query}&signature={expected}"


# ---------------------------------------------------------------- Trader
class FakeClient:
    def __init__(self, df, market="spot", base_balance=0.0, quote_balance=1000.0, futures_amt=0.0):
        self.df = df
        self.market = market
        self.base_balance = base_balance
        self.quote_balance = quote_balance
        self.futures_amt = futures_amt
        self.orders = []

    def closed_klines(self, symbol, interval, limit):
        return self.df.tail(limit).reset_index(drop=True)

    def symbol_filters(self, symbol):
        return {"step": "0.00001", "min_qty": 0.00001, "min_notional": 5.0,
                "base_asset": "BTC", "quote_asset": "USDT"}

    def asset_balance(self, asset):
        return self.base_balance if asset == "BTC" else self.quote_balance

    def futures_position_amt(self, symbol):
        return self.futures_amt

    def market_order(self, symbol, side, **kwargs):
        self.orders.append((side, kwargs))
        return {"orderId": len(self.orders), "status": "FILLED"}


def _cfg(tmp_path, **kw):
    base = dict(dry_run=False, api_key="k", api_secret="s", state_file=str(tmp_path / "state.json"))
    base.update(kw)
    return Config(**base)


def uptrend_df():
    return make_df(np.linspace(100, 200, 400))


def downtrend_df():
    return make_df(list(np.linspace(100, 200, 400)) + list(np.linspace(200, 80, 200)))


def test_trader_spot_buys_with_quote_qty_and_is_idempotent(tmp_path):
    client = FakeClient(uptrend_df())
    trader = Trader(_cfg(tmp_path, position_pct=0.5), client)
    assert trader.step() == LONG
    assert client.orders == [("BUY", {"quote_qty": "500.00"})]
    # 同一根 K 線不重複處理
    assert trader.step() is None
    assert len(client.orders) == 1
    # 重啟後讀取狀態檔，也不重複處理
    assert Trader(_cfg(tmp_path), client).step() is None


def test_trader_spot_sells_full_balance_on_exit(tmp_path):
    client = FakeClient(downtrend_df(), base_balance=0.123456789, quote_balance=0.0)
    trader = Trader(_cfg(tmp_path), client)
    assert trader.step() == FLAT
    assert client.orders == [("SELL", {"quantity": "0.12345"})]


def test_trader_spot_holding_no_action(tmp_path):
    client = FakeClient(uptrend_df(), base_balance=1.0)
    assert Trader(_cfg(tmp_path), client).step() == LONG
    assert client.orders == []


def test_trader_futures_reverse_to_short(tmp_path):
    client = FakeClient(downtrend_df(), market="futures", futures_amt=0.5, quote_balance=1000.0)
    cfg = _cfg(tmp_path, market="futures", allow_short=True, leverage=2, position_pct=1.0)
    assert Trader(cfg, client).step() == SHORT
    assert client.orders[0] == ("SELL", {"quantity": "0.5", "reduce_only": True})
    side, kwargs = client.orders[1]
    price = float(downtrend_df()["close"].iloc[-1])
    assert side == "SELL"
    assert float(kwargs["quantity"]) == pytest.approx(2000 / price, abs=1e-5)


def test_trader_dry_run_places_no_orders(tmp_path):
    client = FakeClient(uptrend_df())
    trader = Trader(_cfg(tmp_path, dry_run=True), client)
    assert trader.step() == LONG
    assert client.orders == []
    assert trader.state["paper_position"] == LONG


def test_config_validation():
    with pytest.raises(ValueError):
        Config(dry_run=False).validate()
    with pytest.raises(ValueError):
        Config(position_pct=1.5).validate()
    Config().validate()
    assert Config(market="spot", allow_short=True).strategy_params.allow_short is False
