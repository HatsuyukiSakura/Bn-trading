import os
import json
import logging
import pandas as pd
from google.cloud import firestore
import matplotlib.pyplot as plt
import datetime # 為了日期處理
from google.cloud import pubsub_v1

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_BACKTEST_REPORTS = os.environ.get("PUBSUB_TOPIC_BACKTEST_REPORTS", "backtest-reports") # 發布回測報告

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Firestore 客戶端
db = firestore.Client(project=GCP_PROJECT_ID)

# 初始化 Pub/Sub Publisher 客戶端
publisher = pubsub_v1.PublisherClient()
backtest_reports_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_BACKTEST_REPORTS)


# Firestore Collection 名稱
KLINE_DATA_COLLECTION = os.environ.get("KLINE_DATA_COLLECTION", "historical_kline_data")
# 假設您有一個資料庫儲存歷史K線數據，或者使用數據獲取模組的數據
# 在實際應用中，可能需要從 BigQuery 或 Cloud Storage 獲取大量歷史數據


def fetch_historical_kline_data(symbol, interval, start_date, end_date):
    """
    從 Firestore 獲取歷史 K 線數據。
    在實際的回測中，您需要足夠且乾淨的歷史數據。
    """
    logging.info(f"Fetching historical kline data for {symbol}@{interval} from {start_date} to {end_date}...")
    
    # 將日期轉換為毫秒時間戳 (幣安數據通常是毫秒時間戳)
    start_ts_ms = int(start_date.timestamp() * 1000)
    end_ts_ms = int(end_date.timestamp() * 1000)

    klines = []
    try:
        # 查詢 Firestore 中的歷史數據
        # 假設您的 K 線數據是每個條目一個文檔，並且有 'symbol', 'interval', 'open_time' 等字段
        query = db.collection(KLINE_DATA_COLLECTION)\
            .where('symbol', '==', symbol)\
            .where('interval', '==', interval)\
            .where('open_time', '>=', start_ts_ms)\
            .where('open_time', '<=', end_ts_ms)\
            .order_by('open_time')\
            .stream()
        
        for doc in query:
            kline = doc.to_dict()
            klines.append({
                'open_time': kline.get('open_time'),
                'open': float(kline.get('open')),
                'high': float(kline.get('high')),
                'low': float(kline.get('low')),
                'close': float(kline.get('close')),
                'volume': float(kline.get('volume'))
                # 添加其他需要的 K 線數據字段
            })
        logging.info(f"Fetched {len(klines)} historical klines.")
        return pd.DataFrame(klines)

    except Exception as e:
        logging.error(f"Error fetching historical kline data from Firestore: {e}", exc_info=True)
        return pd.DataFrame()


def simple_moving_average_strategy(df, short_window=5, long_window=20):
    """
    簡單移動平均線交叉策略。
    當短期 SMA 上穿長期 SMA 時買入，下穿時賣出。
    """
    if df.empty or len(df) < long_window:
        logging.warning("Not enough data for SMA strategy.")
        return pd.DataFrame()

    df['SMA_Short'] = df['close'].rolling(window=short_window, min_periods=1).mean()
    df['SMA_Long'] = df['close'].rolling(window=long_window, min_periods=1).mean()

    df['Signal'] = 0 # 0: 持倉, 1: 買入, -1: 賣出
    df['Position'] = 0 # 倉位狀態 (0: 無倉位, 1: 持有多頭)

    # 生成買入信號 (金叉)
    df.loc[df['SMA_Short'] > df['SMA_Long'], 'Signal'] = 1
    # 生成賣出信號 (死叉)
    df.loc[df['SMA_Short'] < df['SMA_Long'], 'Signal'] = -1
    
    # 實現倉位邏輯
    current_position = 0
    for i in range(1, len(df)):
        if df['Signal'].iloc[i] == 1 and current_position == 0: # 金叉且無倉位 -> 買入
            df.loc[i, 'Position'] = 1
            current_position = 1
        elif df['Signal'].iloc[i] == -1 and current_position == 1: # 死叉且持有多頭 -> 賣出
            df.loc[i, 'Position'] = 0 # 平倉
            current_position = 0
        else: # 否則保持當前倉位
            df.loc[i, 'Position'] = df['Position'].iloc[i-1]

    # 處理最後一筆交易如果仍有倉位
    if current_position == 1: # 如果回測結束時仍持有倉位，則平倉
        df.loc[len(df)-1, 'Position'] = 0 # 強制平倉
        logging.info("Forced close position at end of backtest.")


    return df

def run_backtest(symbol, interval, start_date, end_date):
    """
    執行回測並生成報告。
    """
    logging.info(f"Running backtest for {symbol}@{interval} from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    df = fetch_historical_kline_data(symbol, interval, start_date, end_date)
    if df.empty:
        logging.error("No historical data available for backtesting.")
        return None

    # 運行交易策略 (這裡使用 SMA 策略作為範例)
    df_with_signals = simple_moving_average_strategy(df.copy())
    
    if df_with_signals.empty:
        logging.error("Strategy did not generate any signals or positions.")
        return None

    initial_balance = float(os.environ.get("BACKTEST_INITIAL_BALANCE", "10000.0"))
    balance = initial_balance
    trades = []
    current_position_qty = 0.0
    entry_price = 0.0

    for i in range(len(df_with_signals)):
        row = df_with_signals.iloc[i]
        
        # 買入
        if row['Position'] == 1 and current_position_qty == 0:
            # 簡化：投入固定比例的資金
            buy_amount_usd = balance * 0.99 # 幾乎所有資金
            buy_quantity = buy_amount_usd / row['close']
            
            balance -= buy_amount_usd
            current_position_qty += buy_quantity
            entry_price = row['close']
            
            trades.append({
                'timestamp': row['open_time'],
                'action': 'BUY',
                'price': row['close'],
                'quantity': buy_quantity,
                'balance': balance,
                'equity': balance + current_position_qty * row['close']
            })
            logging.debug(f"BUY at {row['close']:.4f}, Qty: {buy_quantity:.6f}, Balance: {balance:.2f}")

        # 賣出/平倉
        elif row['Position'] == 0 and current_position_qty > 0:
            sell_amount_usd = current_position_qty * row['close']
            pnl = (row['close'] - entry_price) * current_position_qty
            
            balance += sell_amount_usd
            current_position_qty = 0.0 # 清零倉位
            entry_price = 0.0 # 清零入場價格

            trades.append({
                'timestamp': row['open_time'],
                'action': 'SELL',
                'price': row['close'],
                'quantity': sell_amount_usd / row['close'],
                'pnl': pnl,
                'balance': balance,
                'equity': balance
            })
            logging.debug(f"SELL at {row['close']:.4f}, PnL: {pnl:.2f}, Balance: {balance:.2f}")

        # 更新持倉價值
        else: # 持倉中或無倉位
            if current_position_qty > 0:
                # 更新 equity
                df_with_signals.loc[i, 'equity'] = balance + current_position_qty * row['close']
            else:
                df_with_signals.loc[i, 'equity'] = balance
    
    # 計算回測結果
    final_equity = balance + current_position_qty * df_with_signals.iloc[-1]['close'] if current_position_qty > 0 else balance
    profit_loss = final_equity - initial_balance
    
    # 統計交易次數和勝率
    buy_trades = [t for t in trades if t['action'] == 'BUY']
    sell_trades = [t for t in trades if t['action'] == 'SELL']
    
    num_trades = len(sell_trades) # 假設每次賣出都是平倉
    winning_trades = [t for t in sell_trades if t.get('pnl', 0) > 0]
    win_rate = len(winning_trades) / num_trades if num_trades > 0 else 0

    backtest_report = {
        "symbol": symbol,
        "interval": interval,
        "start_date": start_date.strftime('%Y-%m-%d'),
        "end_date": end_date.strftime('%Y-%m-%d'),
        "initial_balance": initial_balance,
        "final_equity": final_equity,
        "profit_loss": profit_loss,
        "return_percentage": (profit_loss / initial_balance) * 100,
        "num_trades": num_trades,
        "win_rate": win_rate,
        "trades_log": trades # 可以將每筆交易的詳細信息包含在內
    }
    
    logging.info(f"Backtest completed for {symbol}. PnL: {profit_loss:.2f}, Return: {backtest_report['return_percentage']:.2f}%")
    
    return backtest_report

def main(event=None, context=None):
    """
    主函數，可以由 Cloud Scheduler 或 Pub/Sub 觸發。
    """
    logging.info("Backtesting Module triggered.")
    
    # 從環境變數獲取回測參數
    symbol = os.environ.get("BACKTEST_SYMBOL", "BTCUSDT")
    interval = os.environ.get("BACKTEST_INTERVAL", "1h")
    
    # 預設回測期間為最近 30 天
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=int(os.environ.get("BACKTEST_DAYS", "30")))

    # 執行回測
    report = run_backtest(symbol, interval, start_date, end_date)

    if report:
        # 將回測報告發布到 Pub/Sub
        message_data = json.dumps(report).encode('utf-8')
        future = publisher.publish(backtest_reports_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Backtest report published with ID: {f.result()}"))
        logging.info("Backtest report published successfully.")
    else:
        logging.warning("No backtest report generated.")

    logging.info("Backtesting Module finished.")

if __name__ == '__main__':
    # 用於本地測試
    # 您需要確保 Firestore 中有足夠的歷史K線數據
    # 例如：
    # db.collection(KLINE_DATA_COLLECTION).add({'symbol': 'BTCUSDT', 'interval': '1h', 'open_time': 1678886400000, 'open': 20000, 'high': 20100, 'low': 19900, 'close': 20050, 'volume': 100})
    # db.collection(KLINE_DATA_COLLECTION).add({'symbol': 'BTCUSDT', 'interval': '1h', 'open_time': 1678890000000, 'open': 20050, 'high': 20200, 'low': 20000, 'close': 20150, 'volume': 120})
    # ...
    main()


