from binance.client import Client
from binance.enums import *
import os

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)

def open_trade(symbol, side, quantity, leverage=10):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side.lower() == "buy" else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
        )
        return f"{symbol} {side.upper()} 開倉成功，數量：{quantity}"
    except Exception as e:
        return f"{symbol} 開倉失敗：{e}"

def close_trade(symbol):
    try:
        pos_info = client.futures_position_information(symbol=symbol)
        for pos in pos_info:
            amt = float(pos["positionAmt"])
            if amt != 0:
                side = SIDE_SELL if amt > 0 else SIDE_BUY
                order = client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type=ORDER_TYPE_MARKET,
                    quantity=abs(amt)
                )
                return f"{symbol} 已平倉，數量：{abs(amt)}"
        return f"{symbol} 無持倉"
    except Exception as e:
        return f"{symbol} 平倉失敗：{e}"

def get_open_positions():
    try:
        positions = client.futures_position_information()
        active = [
            {
                "symbol": pos["symbol"],
                "amt": pos["positionAmt"],
                "entry": pos["entryPrice"],
                "unrealized": pos["unrealizedProfit"]
            }
            for pos in positions if float(pos["positionAmt"]) != 0
        ]
        if not active:
            return "目前無持倉"
        return "\n".join([
            f"{p['symbol']} 持倉 {p['amt']} 入場價 {p['entry']} 未實現盈虧 {p['unrealized']}"
            for p in active
        ])
    except Exception as e:
        return f"查詢持倉失敗：{e}"

