# symbol_scanner.py

import time
import requests
import pandas as pd
from datetime import datetime, timedelta

SCAN_INTERVAL = 60 * 60 * 4  # 每4小時掃描一次
SYMBOL_LIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LTCUSDT"]

def get_funding_rate(symbol):
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
    res = requests.get(url).json()
    return float(res[0]["fundingRate"])

def get_open_interest(symbol):
    url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=1"
    res = requests.get(url).json()
    return float(res[0]["sumOpenInterest"])

def get_long_short_ratio(symbol):
    url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1"
    res = requests.get(url).json()
    ratio = float(res[0]["longAccount"]) / float(res[0]["shortAccount"])
    return ratio

def score_symbol(symbol):
    try:
        funding = get_funding_rate(symbol)
        open_interest = get_open_interest(symbol)
        long_short = get_long_short_ratio(symbol)

        score = (
            (-abs(funding) * 100) +  # 偏離極端
            (open_interest * 0.1) +
            (long_short * 10 if long_short > 1 else -long_short * 10)
        )
        return {"symbol": symbol, "score": round(score, 2)}
    except:
        return {"symbol": symbol, "score": -999}

def get_top_symbols(top_n=3):
    results = [score_symbol(sym) for sym in SYMBOL_LIST]
    sorted_symbols = sorted(results, key=lambda x: x["score"], reverse=True)
    return [s["symbol"] for s in sorted_symbols[:top_n]]

def run_scanner():
    print(f"[{datetime.now()}] 開始掃描...")
    top_symbols = get_top_symbols()
    print(f"推薦幣種：{top_symbols}")
    return top_symbols

if __name__ == "__main__":
    while True:
        run_scanner()
        time.sleep(SCAN_INTERVAL)
