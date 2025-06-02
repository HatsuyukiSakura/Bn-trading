# risk_control.py

import datetime
from collections import defaultdict

# 每日損失限制
DAILY_LOSS_LIMIT = -50  # USDT
# 漲幅超過 5% 時觸發移動止損機制
TRAILING_STOP_TRIGGER = 0.05

# 紀錄每日損益
daily_pnl = defaultdict(float)
# 紀錄每個幣種的最高價，用於移動止損
highest_price = {}
# 保本止損價格（會隨最高價更新）
trailing_stop_price = {}

def record_trade(symbol, pnl):
    date = datetime.date.today().isoformat()
    daily_pnl[date] += pnl
    print(f"[風控] 今日累積損益：{daily_pnl[date]}")

def check_daily_limit():
    date = datetime.date.today().isoformat()
    return daily_pnl[date] > DAILY_LOSS_LIMIT

def should_trigger_trailing_stop(symbol, entry_price, current_price):
    """
    當浮盈超過 5%，啟動保本止損機制，並持續更新最高價
    若價格下跌至保本價則觸發止損
    """
    gain = (current_price - entry_price) / entry_price

    if gain >= TRAILING_STOP_TRIGGER:
        # 啟動移動止損
        if symbol not in highest_price:
            highest_price[symbol] = current_price
        else:
            highest_price[symbol] = max(highest_price[symbol], current_price)

        trailing_stop_price[symbol] = max(entry_price, highest_price[symbol] * 0.98)

        if current_price <= trailing_stop_price[symbol]:
            print(f"[風控] {symbol} 觸發移動止損 - 目前價: {current_price:.2f}, 止損價: {trailing_stop_price[symbol]:.2f}")
            return True

    return False

def check_risk(symbol, entry_price, stop_loss, take_profit, max_loss_pct=5.0):
    """
    檢查基本風控條件是否合格，例如最大允許止損百分比
    """
    max_allowed_loss = entry_price * (max_loss_pct / 100)
    actual_loss = abs(entry_price - stop_loss)

    if actual_loss > max_allowed_loss:
        print(f"[風控] {symbol} 的止損超過最大允許虧損 {max_loss_pct}%")
        return False

    if not check_daily_limit():
        print(f"[風控] 已達每日最大虧損限制，不允許開單")
        return False

    return True

def get_risk_status():
    date = datetime.date.today().isoformat()
    return f"[風控狀態] 今日累積損益：{daily_pnl[date]} USDT，最大允許虧損：{DAILY_LOSS_LIMIT} USDT"
def check_risk(symbol):
    """
    風控檢查，回傳 True 表示允許交易，False 表示禁止交易。
    這裡示範簡單判斷是否超過每日虧損限制。
    """
    if not check_daily_limit():
        print(f"[風控] 超過每日虧損限制，禁止交易")
        return False
    # 這裡可以擴充更多風控條件，例如移動止損判斷等
    return True

