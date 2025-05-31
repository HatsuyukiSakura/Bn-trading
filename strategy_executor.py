# strategy_executor.py

from ai_model import load_model, predict_signal
from trade_manager import open_trade, close_trade, get_open_positions
from scanner import get_top_symbols
from risk_control import check_risk
from telegram_bot import telegram_bot
from record_logger import log_trade
from datetime import datetime
import time

class StrategyExecutor:
    def __init__(self):
        self.model = load_model()
        self.symbols = []

    def update_symbols(self):
        # 取得打分後前3名幣種
        self.symbols = get_top_symbols(top_n=3)

    def evaluate_and_trade(self):
        for symbol in self.symbols:
            if check_risk(symbol):
                signal = predict_signal(symbol, self.model)
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

# 例行任務（可設為排程）
if __name__ == "__main__":
    executor = StrategyExecutor()
    executor.run()