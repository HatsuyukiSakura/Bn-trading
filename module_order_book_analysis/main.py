import os
import json
import logging
from google.cloud import pubsub_v1
from concurrent.futures import TimeoutError

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_ORDER_BOOK_UPDATES = os.environ.get("PUBSUB_TOPIC_ORDER_BOOK_UPDATES", "order-book-updates") # 訂閱的 Topic
PUBSUB_TOPIC_ORDER_BOOK_SIGNALS = os.environ.get("PUBSUB_TOPIC_ORDER_BOOK_SIGNALS", "order-book-signals") # 發布的 Topic

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

subscription_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_ORDER_BOOK_UPDATES}-sub-analyzer") # 建議為每個消費者創建不同的訂閱
signals_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_ORDER_BOOK_SIGNALS)


def calculate_order_book_imbalance(bids, asks, depth=5):
    """
    計算訂單簿不平衡 (OBI)。
    考慮最上面的 'depth' 層級的買賣掛單。
    bids: list of [price, quantity]
    asks: list of [price, quantity]
    """
    if not bids or not asks:
        return 0.0

    total_bid_qty = sum(float(b[1]) for b in bids[:depth])
    total_ask_qty = sum(float(a[1]) for a in asks[:depth])

    if (total_bid_qty + total_ask_qty) == 0:
        return 0.0
    
    obi = (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)
    return obi

def process_order_book_update(message: pubsub_v1.subscriber.message.Message):
    """
    處理接收到的訂單簿更新訊息。
    """
    try:
        data = json.loads(message.data.decode('utf-8'))
        symbol = data['s']
        # 幣安深度更新的 'b' 是買單 ('bids')，'a' 是賣單 ('asks')
        bids = data['b']
        asks = data['a']

        # 這裡的 bids 和 asks 格式是 [[price, quantity], ...]
        # 在實際應用中，您需要維護一個完整的訂單簿狀態，並應用這些更新（增量更新）
        # 由於此範例只計算 OBI，我們假設這裡的數據是某個時間點的快照或足夠的增量更新。

        # 範例：計算訂單簿不平衡 (OBI)
        obi = calculate_order_book_imbalance(bids, asks)
        logging.info(f"Symbol: {symbol}, OBI: {obi:.4f}")

        # 範例：偵測大額買賣單（鯨魚訂單）
        threshold_qty = float(os.environ.get("WHALE_THRESHOLD_QTY", "100.0")) # 設定一個閾值
        large_bid_found = any(float(b[1]) >= threshold_qty for b in bids)
        large_ask_found = any(float(a[1]) >= threshold_qty for a in asks)

        signal = {
            "symbol": symbol,
            "timestamp": data['E'], # 事件時間
            "order_book_imbalance": obi,
            "large_bid_detected": large_bid_found,
            "large_ask_detected": large_ask_found,
            "signal_type": "order_book_analysis"
        }

        # 發布分析結果到新的 Pub/Sub Topic
        message_data = json.dumps(signal).encode('utf-8')
        future = publisher.publish(signals_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Signal published with ID: {f.result()}"))

        message.ack() # 確認訊息已處理，從訂閱隊列中移除
        logging.info(f"Processed order book update for {symbol} and published signal.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message: {e} - Data: {message.data.decode('utf-8')}")
        message.nack() # 負確認訊息，稍後會重新投遞
    except Exception as e:
        logging.error(f"Error processing order book update: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Order Book Analysis Module...")
    
    # 創建訂閱（如果不存在）
    # 注意：在生產環境中，通常會透過 Terraform 或手動在 GCP 控制台創建訂閱，
    # 這裡僅為範例演示。
    try:
        subscriber.create_subscription(name=subscription_path, topic=publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_ORDER_BOOK_UPDATES))
        logging.info(f"Subscription {subscription_path} created (if it didn't exist).")
    except Exception as e:
        # 如果訂閱已存在，會拋出 AlreadyExists 錯誤，可以忽略
        if "AlreadyExists" in str(e):
            logging.info(f"Subscription {subscription_path} already exists.")
        else:
            logging.error(f"Error creating subscription {subscription_path}: {e}")
            exit(1)


    streaming_pull_future = subscriber.subscribe(subscription_path, callback=process_order_book_update)
    logging.info(f"Listening for messages on {subscription_path}...")

    # Cloud Run 服務需要保持運行來持續接收 Pub/Sub 訊息
    try:
        streaming_pull_future.result()  # 阻塞主線程直到訂閱結束
    except TimeoutError:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.error(f"Error in Pub/Sub subscription: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Order Book Analysis Module stopped.")

if __name__ == '__main__':
    main()


