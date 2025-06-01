# main.py（Flask + Telegram + 排程，適用 Cloud Run）

from flask import Flask, request
from symbol_scanner import SymbolScanner
from strategy_executor import StrategyExecutor
from telegram_bot import TelegramBot
from google_sheets_logger import log_trade
import schedule
import time
import threading
import os

# 初始化 Flask 與元件
app = Flask(__name__)
scanner = SymbolScanner()
executor = StrategyExecutor()
bot = TelegramBot()

@app.route("/")
def home():
    return "✅ Crypto Bot is running!"

@app.route("/trigger", methods=["POST"])
def manual_trigger():
    try:
        run_cycle()
        return "✅ 策略已手動執行", 200
    except Exception as e:
        return f"❌ 失敗: {e}", 500

def run_cycle():
    try:
        symbols = scanner.scan()
        recommendations = executor.run(symbols)
        for rec in recommendations:
            result = executor.execute_strategy(rec)
            log_trade(**result)
            bot.send(f"📈 已完成交易：{result}")
    except Exception as e:
        bot.send(f"⚠️ 策略異常：{e}")

# 每 4 小時執行一次策略
schedule.every(4).hours.do(run_cycle)

def background_worker():
    threading.Thread(target=bot.listen_commands, daemon=True).start()
    while True:
        schedule.run_pending()
        time.sleep(1)

# 啟動 Flask 與背景任務
if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
