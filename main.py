# main.py（修正：使用 get_top_symbols 取代 SymbolScanner 類別）

from flask import Flask, request
from symbol_scanner import get_top_symbols
from strategy_executor import StrategyExecutor
from telegram_bot import TelegramBot
from google_sheets_logger import log_trade
import schedule
import time
import threading
import os

app = Flask(__name__)
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
        symbols = get_top_symbols()
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



app = Flask(__name__)  # 必須這行存在

# 保留 app 給 gunicorn 掛載
if __name__ == "__main__":
    from your_module import background_worker  # 如果需要
    threading.Thread(target=background_worker, daemon=True).start()

