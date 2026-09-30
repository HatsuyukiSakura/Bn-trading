"""
EMA150 / SMA150 趨勢策略（只使用已收盤的 K 線）。

規則（以收盤價判斷）：
  做多進場：close > EMA 且 close > SMA 且 EMA > SMA   （價格站上雙均線，且均線多頭排列）
  做多出場：close < EMA 且 close < SMA                （價格跌破雙均線）
  做空進場：close < EMA 且 close < SMA 且 EMA < SMA   （僅合約且 allow_short）
  做空出場：close > EMA 且 close > SMA

進場與出場條件不對稱（出場需跌破「兩條」均線），在雙均線之間的區域會維持原本
部位，減少在均線附近來回洗單。
"""
from dataclasses import dataclass

import pandas as pd

from .indicators import ema, sma

FLAT = 0
LONG = 1
SHORT = -1


@dataclass(frozen=True)
class StrategyParams:
    ema_period: int = 150
    sma_period: int = 150
    allow_short: bool = False


def add_indicators(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["ema"] = ema(out["close"], params.ema_period)
    out["sma"] = sma(out["close"], params.sma_period)
    return out


def next_position(current: int, close: float, ema_v: float, sma_v: float, allow_short: bool) -> int:
    """根據目前部位與最新一根已收盤 K 線，回傳目標部位。"""
    if pd.isna(ema_v) or pd.isna(sma_v):
        return current

    above = close > ema_v and close > sma_v
    below = close < ema_v and close < sma_v
    long_entry = above and ema_v > sma_v
    short_entry = below and ema_v < sma_v

    if current == LONG:
        if below:
            return SHORT if (allow_short and short_entry) else FLAT
        return LONG
    if current == SHORT:
        if above:
            return LONG if long_entry else FLAT
        return SHORT
    # FLAT
    if long_entry:
        return LONG
    if allow_short and short_entry:
        return SHORT
    return FLAT


def compute_positions(df: pd.DataFrame, params: StrategyParams, initial: int = FLAT) -> pd.DataFrame:
    """
    逐根計算每根 K 線收盤後的目標部位，結果放在 `position` 欄。
    df 需包含 close 欄位，且只能包含已收盤的 K 線。
    """
    out = add_indicators(df, params)
    positions = []
    pos = initial
    for close, e, s in zip(out["close"], out["ema"], out["sma"]):
        pos = next_position(pos, close, e, s, params.allow_short)
        positions.append(pos)
    out["position"] = positions
    return out
