"""技術指標計算。"""
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """簡單移動平均 (SMA)。前 period-1 根為 NaN。"""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    指數移動平均 (EMA)，與 TradingView / 幣安圖表相同的算法：
    以前 period 根的 SMA 作為種子，之後 alpha = 2 / (period + 1)。
    前 period-1 根為 NaN。
    """
    values = series.astype(float).to_numpy()
    out = [float("nan")] * len(values)
    if len(values) < period:
        return pd.Series(out, index=series.index)

    alpha = 2.0 / (period + 1)
    prev = values[:period].mean()
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=series.index)
