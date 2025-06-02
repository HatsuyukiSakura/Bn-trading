# symbol_scanner.py

import time
import requests
import pandas as pd
from datetime import datetime
from telegram_bot import telegram_bot  # 若不使用 Telegram 可移除
import traceback

SCAN_INTERVAL = 60 * 60 * 4  # 每4小時掃描一次
SYMBOL_LIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LTCUSDT"]

# --- 指標獲取函數 ---
def get_funding_rate(symbol):
    try:
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
        res = requests.get(url).json()
        return float(res[0]["fundingRate"])
    except Exception as e:
        print(f"❌ {symbol} funding rate 取得失敗: {e}")
        return 0.0

def get_open_interest(symbol):
    try:
        url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=1"
        res = requests.get(url).json()
        return float(res[0]["sumOpenInterest"])
    except Exception as e:
        print(f"❌ {symbol} OI 取得失敗: {e}")
        return 0.0

def get_long_short_ratio(symbol):
    try:
        url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1"
        res = requests.get(url).json()
        long_ratio = float(res[0]["longAccount"])
        short_ratio = float(res[0]["shortAccount"])
        return long_ratio / short_ratio if short_ratio > 0 else 1.0
    except Exception as e:
        print(f"❌ {symbol} 多空比取得失敗: {e}")
        return 1.0

# --- 幣種評分公式 ---
def score_symbol(symbol):
    try:
        funding = get_funding_rate(symbol)
        oi = get_open_interest(symbol)
        ratio = get_long_short_ratio(symbol)

        # 可微調各項權重
        funding_score = -abs(funding) * 100
        oi_score = oi * 0.1
        long_short_score = 10 * (ratio - 1)  # ratio > 1 偏多，<1 偏空

        total_score = funding_score + oi_score + long_short_score

        return {"symbol": symbol, "score": round(total_score, 2)}
    except Exception as e:
        print(f"❌ 評分 {symbol} 時發生錯誤：{e}")
        traceback.print_exc()
        return {"symbol": symbol, "score": -999}

# --- 排名前N高分幣種 ---
def get_top_symbols(top_n=3):
    scores = [score_symbol(sym) for sym in SYMBOL_LIST]
    scores.sort(key=lambda x: x["score"], reverse=True)
    top_symbols = scores[:top_n]
    return [s["symbol"] for s in top_symbols]

# --- 主掃描執行函數 ---
def run_scanner():
    print(f"\n🧠 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始掃描幣種...")
    results = [score_symbol(sym) for sym in SYMBOL_LIST]
    sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)
    top3 = sorted_results[:3]

    print("📊 打分結果：")
    for r in sorted_results:
        print(f"{r['symbol']} → Score: {r['score']}")

    top_symbols = [s["symbol"] for s in top3]
    print(f"✅ 推薦幣種（前3）：{top_symbols}")

    # 傳送 Telegram 通知（選擇性）
    try:
        telegram_bot.send("📈 最新掃描結果：\n" + "\n".join([f"{r['symbol']}: {r['score']}" for r in top3]))
    except:
        pass

    return top_symbols

# --- CLI 測試模式 ---
if __name__ == "__main__":
    while True:
        run_scanner()
        time.sleep(SCAN_INTERVAL)
