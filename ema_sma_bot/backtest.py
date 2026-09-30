"""
回測：第 i 根 K 線收盤產生訊號，於第 i+1 根開盤價成交（避免未來函數）。
"""
from dataclasses import dataclass, field

import pandas as pd

from .strategy import FLAT, StrategyParams, compute_positions


@dataclass
class BacktestResult:
    initial_balance: float
    final_equity: float
    total_return: float
    buy_and_hold_return: float
    max_drawdown: float
    num_trades: int
    win_rate: float
    trades: list = field(default_factory=list)
    equity_curve: pd.Series = None

    def summary(self) -> str:
        return (
            f"初始資金:      {self.initial_balance:,.2f}\n"
            f"最終權益:      {self.final_equity:,.2f}\n"
            f"策略報酬:      {self.total_return:+.2%}\n"
            f"買入持有報酬:  {self.buy_and_hold_return:+.2%}\n"
            f"最大回撤:      {self.max_drawdown:.2%}\n"
            f"交易次數:      {self.num_trades}\n"
            f"勝率:          {self.win_rate:.2%}"
        )


def run_backtest(df: pd.DataFrame, params: StrategyParams, initial_balance: float = 10_000.0,
                 fee_rate: float = 0.001, leverage: float = 1.0) -> BacktestResult:
    """
    df 需包含 open_time, open, close 欄位（已收盤的 K 線，依時間排序）。
    fee_rate 為單邊手續費率（現貨預設 0.1%）。
    """
    data = compute_positions(df, params).reset_index(drop=True)
    opens = data["open"].to_numpy()
    closes = data["close"].to_numpy()
    signals = data["position"].to_numpy()

    cash = initial_balance      # 未進場時的權益
    pos = FLAT
    entry_price = 0.0
    entry_equity = 0.0
    entry_idx = 0
    trades = []
    equity = []

    def mark(price: float) -> float:
        if pos == FLAT:
            return cash
        ret = (price / entry_price - 1) * pos * leverage
        return max(entry_equity * (1 + ret), 0.0)

    for i in range(len(data)):
        # 上一根收盤的訊號在這一根開盤成交
        target = signals[i - 1] if i > 0 else FLAT
        if target != pos:
            price = opens[i]
            if pos != FLAT:
                exit_equity = mark(price) * (1 - fee_rate * leverage)
                trades.append({
                    "side": "LONG" if pos > 0 else "SHORT",
                    "entry_time": int(data["open_time"].iloc[entry_idx]),
                    "exit_time": int(data["open_time"].iloc[i]),
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl_pct": exit_equity / entry_equity - 1 if entry_equity else 0.0,
                })
                cash = exit_equity
                pos = FLAT
            if target != FLAT:
                entry_equity = cash * (1 - fee_rate * leverage)
                entry_price = price
                entry_idx = i
                pos = target
        equity.append(mark(closes[i]))

    equity_curve = pd.Series(equity, index=data["open_time"])
    final_equity = equity[-1] if equity else initial_balance
    peak = equity_curve.cummax()
    drawdown = ((equity_curve - peak) / peak).min() if len(equity_curve) else 0.0
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)

    return BacktestResult(
        initial_balance=initial_balance,
        final_equity=final_equity,
        total_return=final_equity / initial_balance - 1,
        buy_and_hold_return=(closes[-1] / opens[0] - 1) if len(data) else 0.0,
        max_drawdown=abs(float(drawdown)),
        num_trades=len(trades),
        win_rate=wins / len(trades) if trades else 0.0,
        trades=trades,
        equity_curve=equity_curve,
    )
