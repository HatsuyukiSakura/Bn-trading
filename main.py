# main.py（支援 Flask + Telegram + 定時任務）

from flask import Flask, request
from symbol_scanner import SymbolScanner
from strategy_executor import StrategyExecutor
from telegram_bot import TelegramBot
from google_sheets_logger import log_trade
import schedule
import time
import threading
import os

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
        return "✅ 策略執行成功", 200
    except Exception as e:
        return f"❌ 策略執行失敗: {e}", 500

def run_cycle():
    try:
        symbols = scanner.scan()
        recommendations = executor.run(symbols)
        for rec in recommendations:
            result = executor.execute_strategy(rec)
            log_trade(**result)
            bot.send(f"✅ 已完成交易：{result}")
    except Exception as e:
        bot.send(f"⚠️ 主流程異常：{e}")

# 每 4 小時執行一次
schedule.every(4).hours.do(run_cycle)

def schedule_runner():
    while True:
        schedule.run_pending()
        time.sleep(1)

# 背景啟動：排程與 Telegram 控制
threading.Thread(target=schedule_runner, daemon=True).start()
threading.Thread(target=bot.listen_commands, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
