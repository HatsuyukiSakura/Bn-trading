# main.py

from modules.symbol_scanner import SymbolScanner
from modules.strategy_executor import StrategyExecutor
from modules.telegram_bot import TelegramBot
from modules.logger import Logger
import schedule
import time
import threading

scanner = SymbolScanner()
executor = StrategyExecutor()
bot = TelegramBot()
logger = Logger()

def run_cycle():
    try:
        symbols = scanner.scan()
        recommendations = executor.run(symbols)
        for rec in recommendations:
            result = executor.execute_strategy(rec)
            logger.log_trade(result)
            bot.send(f"已完成交易：{result}")
    except Exception as e:
        bot.send(f"主流程異常：{e}")

schedule.every(4).hours.do(run_cycle)

def schedule_runner():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=schedule_runner).start()
bot.listen_commands()
