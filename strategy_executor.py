# strategy_executor.py（修正 AIModel 匯入與使用）

from ai_model import AIModel
from trade_manager import open_trade, close_trade, get_open_positions
from symbol_scanner import get_top_symbols
from risk_control import check_risk
from telegram_bot import telegram_bot
from record_logger import log_trade
from datetime import datetime
import time
import schedule
import numpy as np

class StrategyExecutor:
    def __init__(self):
        self.model = AIModel()
        self.symbols = []

    def update_symbols(self):
        # 取得打分後前3名幣種
        self.symbols = get_top_symbols(top_n=3)

    def evaluate_and_trade(self):
        for symbol in self.symbols:
            try:
                if check_risk(symbol):
                    features = self.get_features(symbol)
                    signal = self.model.predict_signal(features)
                    print(f"{symbol} 信號：{signal}")

                    if signal == "buy":
                        result = open_trade(symbol, "buy")
                        log_trade(symbol, "buy", result["qty"], result["entry_price"])
                        telegram_bot.send(f"✅ 已開多單：{symbol} @ {result['entry_price']}")
                    elif signal == "sell":
                        result = open_trade(symbol, "sell")
                        log_trade(symbol, "sell", result["qty"], result["entry_price"])
                        telegram_bot.send(f"✅ 已開空單：{symbol} @ {result['entry_price']}")
            except Exception as e:
                print(f"❌ 處理 {symbol} 發生錯誤：{e}")
                telegram_bot.send(f"⚠️ 錯誤：處理 {symbol} 失敗\n{e}")

    def auto_manage_positions(self):
        try:
            positions = get_open_positions()
            for pos in positions:
                symbol = pos["symbol"]
                unrealized = pos["unrealized_profit"]
                entry = pos["entry_price"]

                if unrealized < -0.05 * entry:
                    close_trade(symbol)
                    telegram_bot.send(f"🛑 虧損平倉：{symbol}（浮虧：{unrealized}）")
                elif unrealized > 0.1 * entry:
                    close_trade(symbol)
                    telegram_bot.send(f"💰 獲利平倉：{symbol}（浮盈：{unrealized}）")
        except Exception as e:
            print(f"❌ 自動管理持倉失敗：{e}")
            telegram_bot.send(f"⚠️ 錯誤：自動管理持倉失敗\n{e}")

    def get_features(self, symbol):
        # ✅ 此處應整合實際特徵提取邏輯（目前為測試用）
        return np.random.rand(10, 5)

    def run(self):
        print(f"📈 策略執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.update_symbols()
        self.evaluate_and_trade()
        self.auto_manage_positions()

# ✅ 每 4 小時執行一次策略
if __name__ == "__main__":
    executor = StrategyExecutor()
    schedule.every(4).hours.do(executor.run)

    print("✅ 策略排程啟動中，每 4 小時執行一次")
    telegram_bot.send("🤖 策略排程已啟動，每 4 小時執行一次")

    while True:
        schedule.run_pending()
        time.sleep(1)

