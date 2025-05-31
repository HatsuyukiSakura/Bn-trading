# risk_control.py

import datetime
from collections import defaultdict

# 每日損失限制
DAILY_LOSS_LIMIT = -50  # USDT
# 漲幅超過 5% 時保本止損（移動止損）
TRAILING_STOP_TRIGGER = 0.05

# 用於記錄每日虧損
daily_pnl = defaultdict(float)
last_exit_price = {}

def record_trade(symbol, pnl):
    date = datetime.date.today().isoformat()
    daily_pnl[date] += pnl
    print(f"[風控] 今日累積損益：{daily_pnl[date]}")

def check_daily_limit():
    date = datetime.date.today().isoformat()
    return daily_pnl[date] > DAILY_LOSS_LIMIT

def should_trigger_trailing_stop(symbol, entry_price, current_price):
    """
    若價格漲幅超過設定觸發比例，則設置保本止損
    """
    if symbol not in last_exit_price:
        last_exit_price[symbol] = entry_price

    gain = (current_price - entry_price) / entry_price
    if gain >= TRAILING_STOP_TRIGGER:
        stop_price = entry_price  # 保本止損
        if current_price <= stop_price:
            print(f"[風控] {symbol} 觸發移動止損")
            return True
    return False

def get_risk_status():
    date = datetime.date.today().isoformat()
    return f"[風控狀態] 今日累積損益：{daily_pnl[date]} USDT，最大允許虧損：{DAILY_LOSS_LIMIT} USDT"

