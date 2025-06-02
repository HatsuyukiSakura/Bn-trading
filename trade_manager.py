# trade_manager.py

from binance.client import Client
from binance.enums import *
import os

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)

def open_trade(symbol, side, quantity=0.001, leverage=10):
    try:
        # 設定槓桿
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        # 建立市價單
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side.lower() == "buy" else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )

        entry_price = float(order["fills"][0]["price"]) if "fills" in order and order["fills"] else 0
        return {
            "status": "success",
            "symbol": symbol,
            "side": side,
            "qty": quantity,
            "entry_price": entry_price,
            "message": f"{symbol} {side.upper()} 開倉成功，數量：{quantity}"
        }
    except Exception as e:
        return {
            "status": "error",
            "symbol": symbol,
            "side": side,
            "message": f"{symbol} 開倉失敗：{e}"
        }

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
                return {
                    "status": "success",
                    "symbol": symbol,
                    "closed_qty": abs(amt),
                    "message": f"{symbol} 已平倉，數量：{abs(amt)}"
                }
        return {
            "status": "empty",
            "symbol": symbol,
            "message": f"{symbol} 無持倉"
        }
    except Exception as e:
        return {
            "status": "error",
            "symbol": symbol,
            "message": f"{symbol} 平倉失敗：{e}"
        }

def get_open_positions():
    try:
        positions = client.futures_position_information()
        active_positions = []
        for pos in positions:
            amt = float(pos["positionAmt"])
            if amt != 0:
                active_positions.append({
                    "symbol": pos["symbol"],
                    "positionAmt": amt,
                    "entry_price": float(pos["entryPrice"]),
                    "unrealized_profit": float(pos["unrealizedProfit"]),
                    "side": "long" if amt > 0 else "short"
                })
        return active_positions
    except Exception as e:
        return {
            "status": "error",
            "message": f"查詢持倉失敗：{e}"
        }

