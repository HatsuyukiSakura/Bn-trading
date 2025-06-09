import os
import json
import logging
import pandas as pd
from google.cloud import pubsub_v1
from concurrent.futures import TimeoutError

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_KLINE_UPDATES = os.environ.get("PUBSUB_TOPIC_KLINE_UPDATES", "kline-updates") # 訂閱 K 線更新
PUBSUB_TOPIC_COIN_SELECTION_SIGNALS = os.environ.get("PUBSUB_TOPIC_COIN_SELECTION_SIGNALS", "coin-selection-signals") # 發布選幣信號

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

# 訂閱路徑 (注意：為每個消費者創建不同的訂閱，以避免訊息重複消費)
subscription_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_KLINE_UPDATES}-sub-coin-selector")
signals_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_COIN_SELECTION_SIGNALS)


# -------------------------------------------------------------------
# 簡化範例：基於簡單移動平均線交叉的選幣邏輯
# 在實際應用中，這將是一個複雜的 AI/ML 模型
# -------------------------------------------------------------------
# 為了簡化，這裡假設每個K線更新都是獨立的，且模組沒有持久化狀態。
# 實際應用中，您可能需要從資料庫（例如 Firestore）讀取歷史K線，
# 或者維護一個實時的K線緩存來計算指標。
# -------------------------------------------------------------------

# 模擬一個簡單的策略狀態（實際中可能會存儲在資料庫或 Redis 中）
# 這裡用於演示，但真正的 AI 模型會更複雜，可能需要加載預訓練模型
coin_data_buffer = {} # {symbol: list of kline_data}

def calculate_sma(data, window):
    """計算簡單移動平均線"""
    if len(data) < window:
        return None
    # 假設 data 是 K 線收盤價列表
    return sum(data[-window:]) / window

def generate_simple_coin_selection_signal(kline_data):
    """
    基於簡化邏輯生成選幣信號。
    這裡僅為範例，實際會是 AI/ML 模型的預測。
    """
    symbol = kline_data['s']
    interval = kline_data['k']['i']
    close_price = float(kline_data['k']['c']) # K線收盤價
    
    # 為了演示，我們假設這個模組維護一個簡單的 K 線歷史
    if symbol not in coin_data_buffer:
        coin_data_buffer[symbol] = []
    
    # 只保留足夠計算 SMA 的 K 線數據
    coin_data_buffer[symbol].append(close_price)
    if len(coin_data_buffer[symbol]) > 20: # 例如，保留 20 根 K 線用於計算 SMA
        coin_data_buffer[symbol].pop(0)

    # 範例：計算短期 SMA 和長期 SMA
    short_window = 5
    long_window = 10

    sma_short = calculate_sma(coin_data_buffer[symbol], short_window)
    sma_long = calculate_sma(coin_data_buffer[symbol], long_window)

    signal_strength = 0.0
    recommendation = "NEUTRAL"

    if sma_short and sma_long:
        if sma_short > sma_long * 1.005: # 短期SMA顯著高於長期SMA
            recommendation = "BUY"
            signal_strength = (sma_short / sma_long - 1) * 100
        elif sma_short < sma_long * 0.995: # 短期SMA顯著低於長期SMA
            recommendation = "SELL"
            signal_strength = (sma_short / sma_long - 1) * 100

    # 實際 AI 模型會輸出概率、置信度等
    return {
        "symbol": symbol,
        "interval": interval,
        "timestamp": kline_data['E'], # K線事件時間
        "current_price": close_price,
        "recommendation": recommendation, # BUY, SELL, NEUTRAL
        "signal_strength": signal_strength, # 數值越高表示越強的信號
        "source": "coin_selection_ai_model_v1", # 標識信號來源
        "model_confidence": 0.85 # 假設的模型置信度
    }


def process_kline_update(message: pubsub_v1.subscriber.message.Message):
    """
    處理接收到的 K 線更新訊息。
    """
    try:
        kline_data = json.loads(message.data.decode('utf-8'))
        
        # 確保 K 線是閉合的，避免處理不完整的 K 線
        if not kline_data['k']['x']: # 'x' 欄位表示 K 線是否閉合
            logging.info(f"Received incomplete kline for {kline_data['s']}@{kline_data['k']['i']}. Skipping analysis.")
            message.ack() # 確認已接收，但不必處理
            return

        symbol = kline_data['s']
        interval = kline_data['k']['i']
        
        logging.info(f"Analyzing closed kline for {symbol}@{interval}...")

        # 生成選幣信號 (這裡會調用 AI 模型進行預測)
        selection_signal = generate_simple_coin_selection_signal(kline_data)

        if selection_signal['recommendation'] != "NEUTRAL":
            logging.info(f"Generated signal for {symbol}: {selection_signal['recommendation']} (Strength: {selection_signal['signal_strength']:.2f})")
            
            # 發布選幣信號到 Pub/Sub
            message_data = json.dumps(selection_signal).encode('utf-8')
            future = publisher.publish(signals_topic_path, message_data)
            future.add_done_callback(lambda f: logging.debug(f"Coin selection signal published with ID: {f.result()}"))
        else:
            logging.info(f"No strong signal for {symbol}@{interval}.")

        message.ack() # 確認訊息已處理
        logging.debug(f"Processed kline update for {symbol}@{interval}.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message: {e} - Data: {message.data.decode('utf-8')}")
        message.nack() # 負確認訊息，稍後會重新投遞
    except Exception as e:
        logging.error(f"Error processing kline update in coin selector: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Coin Analysis & Selection Module...")
    
    # 創建訂閱 (如果不存在)
    try:
        subscriber.create_subscription(name=subscription_path, topic=publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_KLINE_UPDATES))
        logging.info(f"Subscription {subscription_path} created (if it didn't exist).")
    except Exception as e:
        if "AlreadyExists" in str(e):
            logging.info(f"Subscription {subscription_path} already exists.")
        else:
            logging.error(f"Error creating subscription {subscription_path}: {e}")
            exit(1)

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=process_kline_update)
    logging.info(f"Listening for kline updates on {subscription_path}...")

    try:
        streaming_pull_future.result()
    except TimeoutError:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.error(f"Error in Pub/Sub subscription for coin selector: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Coin Analysis & Selection Module stopped.")

if __name__ == '__main__':
    main()


