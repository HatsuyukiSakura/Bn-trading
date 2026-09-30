"""
4H EMA150 趨勢策略（純做多，只使用已收盤的 K 線）。

SIGNAL_MODE=ema（預設，研究報告回測的規則）：
  close > EMA150 → LONG
  close < EMA150 → FLAT
  close == EMA150 → 維持原部位

SIGNAL_MODE=ema_sma（選用，加上 SMA150 濾網）：
  進場：close > EMA 且 close > SMA 且 EMA > SMA
  出場：close < EMA 且 close < SMA
  其餘維持原部位

盤中停損觸發後會「鎖住」該標的：必須先出現一次策略本身的出場訊號（例如收盤跌破
EMA150），之後再次出現進場訊號才重新進場，避免停損後立刻在同一段行情追回。
"""
from dataclasses import dataclass

import pandas as pd

from .indicators import ema, sma

FLAT = 0
LONG = 1


@dataclass(frozen=True)
class StrategyParams:
    ema_period: int = 150
    sma_period: int = 150
    mode: str = "ema"           # ema | ema_sma
    stop_loss_pct: float = 0.0  # 0 = 不設停損


def add_indicators(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["ema"] = ema(out["close"], params.ema_period)
    out["sma"] = sma(out["close"], params.sma_period)
    return out


def desired_position(current: int, close: float, ema_v: float, sma_v: float, mode: str) -> int:
    """依最新一根已收盤 K 線與目前部位回傳目標部位（LONG / FLAT）。"""
    if pd.isna(ema_v) or (mode == "ema_sma" and pd.isna(sma_v)):
        return current
    if mode == "ema":
        if close > ema_v:
            return LONG
        if close < ema_v:
            return FLAT
        return current
    if mode == "ema_sma":
        if current == LONG:
            return FLAT if (close < ema_v and close < sma_v) else LONG
        return LONG if (close > ema_v and close > sma_v and ema_v > sma_v) else FLAT
    raise ValueError(f"未知的 SIGNAL_MODE：{mode}")


def exit_signal(close: float, ema_v: float, sma_v: float, mode: str) -> bool:
    """策略本身是否會出場（用來解除停損鎖）。"""
    return desired_position(LONG, close, ema_v, sma_v, mode) == FLAT


def stop_price(entry_price: float, stop_loss_pct: float) -> float:
    return entry_price * (1 - stop_loss_pct)


def compute_signals(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """逐根計算「不考慮停損」的策略部位，放在 `position` 欄（查詢與測試用）。"""
    out = add_indicators(df, params)
    pos, positions = FLAT, []
    for close, e, s in zip(out["close"], out["ema"], out["sma"]):
        pos = desired_position(pos, close, e, s, params.mode)
        positions.append(pos)
    out["position"] = positions
    return out
