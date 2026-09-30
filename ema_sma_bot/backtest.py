"""
回測（USDⓈ-M 永續，純做多，1 倍有效槓桿）。

- 第 i 根 K 線收盤產生訊號，於第 i+1 根開盤價成交（避免未來函數）。
- 盤中停損：K 線最低價 ≤ 停損價即出場，成交價 = min(開盤價, 停損價)（跳空時以開盤價成交）。
  實盤預設以標記價觸發，這裡用合約成交價的最低點近似，結果會略偏保守。
- 停損後上鎖，須等策略出場訊號出現後才可再進場（與實盤一致）。
- 資金費率：持倉跨越結算時點 t（K 棒開盤 < t ≤ 下一根開盤）即扣 部位名目 × 費率。
- 多標的：資金等分成 N 份各自獨立運算後加總（實盤另有 ±25% 再平衡，這裡未模擬）。
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .strategy import FLAT, LONG, StrategyParams, add_indicators, desired_position, exit_signal

PERIOD_MS = 4 * 3600 * 1000
BARS_PER_YEAR = 6 * 365


@dataclass
class SleeveResult:
    symbol: str
    equity: pd.Series
    trades: list
    bars_in_market: int
    funding_paid: float
    fees_paid: float


@dataclass
class PortfolioResult:
    equity: pd.Series
    benchmark: pd.Series
    sleeves: list = field(default_factory=list)

    @property
    def trades(self) -> list:
        return [t for s in self.sleeves for t in s.trades]

    def metrics(self) -> dict:
        eq = self.equity
        years = max((eq.index[-1] - eq.index[0]) / (365.25 * 86400 * 1000), 1e-9)
        rets = eq.pct_change().dropna()
        sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR)) if rets.std() > 0 else 0.0
        mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
        trades = self.trades
        total_bars = sum(len(s.equity) for s in self.sleeves) or 1
        bench_mdd = float(((self.benchmark - self.benchmark.cummax()) / self.benchmark.cummax()).min())
        return {
            "total_return": eq.iloc[-1] / eq.iloc[0] - 1,
            "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1,
            "max_drawdown": mdd,
            "sharpe": sharpe,
            "trades": len(trades),
            "win_rate": sum(t["pnl_pct"] > 0 for t in trades) / len(trades) if trades else 0.0,
            "stops": sum(t["reason"] == "STOP" for t in trades),
            "time_in_market": sum(s.bars_in_market for s in self.sleeves) / total_bars,
            "funding_paid": sum(s.funding_paid for s in self.sleeves),
            "fees_paid": sum(s.fees_paid for s in self.sleeves),
            "benchmark_return": self.benchmark.iloc[-1] / self.benchmark.iloc[0] - 1,
            "benchmark_mdd": bench_mdd,
        }

    def summary(self) -> str:
        m = self.metrics()
        return (
            f"總報酬:          {m['total_return']:+.2%}\n"
            f"年化報酬 (CAGR): {m['cagr']:+.2%}\n"
            f"最大回撤:        {m['max_drawdown']:.2%}\n"
            f"Sharpe:          {m['sharpe']:.2f}\n"
            f"交易次數:        {m['trades']}（其中停損 {m['stops']} 次）\n"
            f"勝率:            {m['win_rate']:.2%}\n"
            f"在場比例:        {m['time_in_market']:.2%}\n"
            f"資金費支出:      {m['funding_paid']:,.2f}\n"
            f"手續費支出:      {m['fees_paid']:,.2f}\n"
            f"等權買入持有:    {m['benchmark_return']:+.2%}（最大回撤 {m['benchmark_mdd']:.2%}）"
        )


def _funding_sums(open_times: np.ndarray, funding: Optional[pd.DataFrame]) -> np.ndarray:
    """每根 K 棒持倉要付的資金費率合計：結算時點 t 落在 (開盤, 開盤 + 4H]。"""
    if funding is None or funding.empty:
        return np.zeros(len(open_times))
    ft = funding["funding_time"].to_numpy(dtype="int64")
    order = np.argsort(ft)
    ft = ft[order]
    cum = np.concatenate([[0.0], np.cumsum(funding["rate"].to_numpy(dtype=float)[order])])
    lo = np.searchsorted(ft, open_times, side="right")
    hi = np.searchsorted(ft, open_times + PERIOD_MS, side="right")
    return cum[hi] - cum[lo]


def simulate_symbol(df: pd.DataFrame, params: StrategyParams, capital: float = 1.0,
                    fee_rate: float = 0.0005, funding: Optional[pd.DataFrame] = None,
                    symbol: str = "") -> SleeveResult:
    data = add_indicators(df, params).reset_index(drop=True)
    t = data["open_time"].to_numpy(dtype="int64")
    o, h, l, c = (data[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    e, s = data["ema"].to_numpy(dtype=float), data["sma"].to_numpy(dtype=float)
    fund = _funding_sums(t, funding)
    sl = params.stop_loss_pct

    cash, qty, entry, entry_i = capital, 0.0, 0.0, 0
    locked, decision = False, FLAT
    trades, equity = [], np.empty(len(data))
    bars_in, funding_paid, fees_paid = 0, 0.0, 0.0

    def close_trade(i, price, reason):
        nonlocal cash, qty, fees_paid
        fee = qty * price * fee_rate
        fees_paid += fee
        cash += qty * price - fee
        trades.append({"symbol": symbol, "entry_time": int(t[entry_i]), "exit_time": int(t[i]),
                       "entry_price": entry, "exit_price": price,
                       "pnl_pct": price / entry * (1 - fee_rate) ** 2 - 1, "reason": reason})
        qty = 0.0

    for i in range(len(data)):
        # 1) 開盤執行上一根收盤的決定
        if decision == LONG and qty == 0 and cash > 0:
            fee = cash * fee_rate
            fees_paid += fee
            qty, entry, entry_i = (cash - fee) / o[i], o[i], i
            cash = 0.0
        elif decision == FLAT and qty > 0:
            close_trade(i, o[i], "SIGNAL")

        # 2) 盤中停損
        stopped = False
        if qty > 0 and sl > 0 and l[i] <= entry * (1 - sl):
            close_trade(i, min(o[i], entry * (1 - sl)), "STOP")
            stopped = True

        # 3) 資金費
        if qty > 0:
            bars_in += 1
            paid = qty * c[i] * fund[i]
            funding_paid += paid
            cash -= paid

        # 4) 收盤判斷
        is_exit = exit_signal(c[i], e[i], s[i], params.mode)
        if is_exit:
            locked = False
        elif stopped:
            locked = True
        desired = desired_position(LONG if qty > 0 else FLAT, c[i], e[i], s[i], params.mode)
        decision = FLAT if (desired == LONG and qty == 0 and locked) else desired
        equity[i] = cash + qty * c[i]

    return SleeveResult(symbol, pd.Series(equity, index=t), trades, bars_in, funding_paid, fees_paid)


def run_portfolio(data: dict, params: StrategyParams, initial_balance: float = 10_000.0,
                  fee_rate: float = 0.0005, funding: Optional[dict] = None) -> PortfolioResult:
    """data: {symbol: klines DataFrame}；funding: {symbol: funding DataFrame}。"""
    funding = funding or {}
    per = initial_balance / len(data)
    sleeves, bench = [], []
    for sym, df in data.items():
        sleeves.append(simulate_symbol(df, params, per, fee_rate, funding.get(sym), sym))
        closes = df.set_index("open_time")["close"]
        bench.append(per * closes / float(df["open"].iloc[0]))

    index = sorted(set().union(*[s.equity.index for s in sleeves]))

    def combine(series_list):
        frame = pd.concat([x.reindex(index).ffill() for x in series_list], axis=1)
        return frame.fillna(per).sum(axis=1)

    return PortfolioResult(combine([s.equity for s in sleeves]), combine(bench), sleeves)
