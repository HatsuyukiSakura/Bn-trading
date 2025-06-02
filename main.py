# main.py（整合版）

from flask import Flask, request
from symbol_scanner import get_top_symbols
from strategy_executor import StrategyExecutor
from telegram_bot import TelegramBot
from google_sheets_logger import log_trade
import schedule
import time
import threading
import os
from binance.client import Client

# 初始化 Binance API
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
client = Client(api_key, api_secret)

print("✅ 測試連線中...")
try:
    acc_info = client.futures_account()
    print(f"🎉 成功連線到 Binance。帳戶餘額數量：{len(acc_info['assets'])}")
except Exception as e:
    print(f"❌ Binance API 測試失敗：{e}")

# 建立 Flask App
app = Flask(__name__)  # 保留給 gunicorn 掛載
executor = StrategyExecutor()
bot = TelegramBot()

# Home 路由
@app.route("/")
def home():
    return "✅ Crypto Bot is running!"

# 手動觸發策略路由
@app.route("/trigger", methods=["POST"])
def manual_trigger():
    try:
        run_cycle()
        return "✅ 策略已手動執行", 200
    except Exception as e:
        return f"❌ 失敗: {e}", 500

# 策略運行主流程
def run_cycle():
    try:
        symbols = get_top_symbols()
        recommendations = executor.run(symbols)
        for rec in recommendations:
            result = executor.execute_strategy(rec)
            log_trade(**result)
            bot.send(f"📈 已完成交易：{result}")
    except Exception as e:
        bot.send(f"⚠️ 策略異常：{e}")

# 每 4 小時執行一次
schedule.every(4).hours.do(run_cycle)

# 背景執行任務（排程與指令監聽）
def background_worker():
    threading.Thread(target=bot.listen_commands, daemon=True).start()
    while True:
        schedule.run_pending()
        time.sleep(1)

# 啟動 Web + 背景排程（僅限本地或直接運行）
if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

