# telegram_bot.py

import requests
from strategy_executor import (
    get_status, get_positions, close_position, get_balance, get_profit,
    open_position, get_logs, shutdown, get_risk, get_help, run_strategy
)

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.offset = None

    def send(self, message):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": message}
        requests.post(url, data=data)

    def get_updates(self):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        if self.offset:
            url += f"?offset={self.offset}"
        res = requests.get(url).json()
        return res.get("result", [])

    def listen_commands(self):
        updates = self.get_updates()
        for update in updates:
            message = update.get("message", {}).get("text", "")
            if not message:
                continue

            if message.startswith("/status"):
                self.send(get_status())
            elif message.startswith("/positions"):
                self.send(get_positions())
            elif message.startswith("/close"):
                try:
                    symbol = message.split()[1].upper()
                    self.send(close_position(symbol))
                except:
                    self.send("請提供正確格式，如：/close BTCUSDT")
            elif message.startswith("/balance"):
                self.send(get_balance())
            elif message.startswith("/profit"):
                self.send(get_profit())
            elif message.startswith("/open"):
                try:
                    _, symbol, side, qty = message.split()
                    self.send(open_position(symbol.upper(), side, qty))
                except:
                    self.send("請提供正確格式，如：/open BTCUSDT buy 0.01")
            elif message.startswith("/logs"):
                self.send(get_logs())
            elif message.startswith("/shutdown"):
                self.send(shutdown())
            elif message.startswith("/risk"):
                self.send(get_risk())
            elif message.startswith("/strategy"):
                self.send(run_strategy())
            elif message.startswith("/help"):
                self.send(get_help())

            self.offset = update["update_id"] + 1
