# strategy_executor.py（修正 AIModel 匯入與使用）

from ai_model import AIModel
from trade_manager import open_trade, close_trade, get_open_positions
from scanner import get_top_symbols
from risk_control import check_risk
from telegram_bot import telegram_bot
from record_logger import log_trade
from datetime import datetime
import time

class StrategyExecutor:
    def __init__(self):
        self.model = AIModel()
        self.symbols = []

    def update_symbols(self):
        # 取得打分後前3名幣種
        self.symbols = get_top_symbols(top_n=3)

    def evaluate_and_trade(self):
        for symbol in self.symbols:
            if check_risk(symbol):
                features = self.get_features(symbol)  # 這方法需實作或整合資料
                signal = self.model.predict_signal(features)
                print(f"{symbol} 信號：{signal}")

                if signal == "buy":
                    result = open_trade(symbol, "buy")
                    log_trade(symbol, "buy", result["qty"], result["entry_price"])
                    telegram_bot.send(f"已開多單：{symbol} @ {result['entry_price']}")
                elif signal == "sell":
                    result = open_trade(symbol, "sell")
                    log_trade(symbol, "sell", result["qty"], result["entry_price"])
                    telegram_bot.send(f"已開空單：{symbol} @ {result['entry_price']}")

    def auto_manage_positions(self):
        positions = get_open_positions()
        for pos in positions:
            # 加入風控條件，自動平倉邏輯
            if pos["unrealized_profit"] < -0.05 * pos["entry_price"]:
                close_trade(pos["symbol"])
                telegram_bot.send(f"虧損平倉：{pos['symbol']}")
            elif pos["unrealized_profit"] > 0.1 * pos["entry_price"]:
                close_trade(pos["symbol"])
                telegram_bot.send(f"獲利平倉：{pos['symbol']}")

    def run(self):
        self.update_symbols()
        self.evaluate_and_trade()
        self.auto_manage_positions()

    def get_features(self, symbol):
        # 這裡應整合歷史行情數據轉換為特徵格式
        # 示例可返回一個 np.ndarray（含最近一筆特徵）
        import numpy as np
        return np.random.rand(10, 5)  # 取代為實際特徵生成邏輯

# 例行任務（可設為排程）
if __name__ == "__main__":
    executor = StrategyExecutor()
    executor.run()
