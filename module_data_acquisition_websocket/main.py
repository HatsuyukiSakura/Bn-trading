import os
import json
import logging
from binance import ThreadedWebsocketManager
from google.cloud import pubsub_v1
from google.cloud import secretmanager_v1beta1 as secretmanager # 引入 Secret Manager 客戶端

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_KLINE_UPDATES = os.environ.get("PUBSUB_TOPIC_KLINE_UPDATES", "kline-updates") # K線更新 Topic
PUBSUB_TOPIC_ORDER_BOOK_UPDATES = os.environ.get("PUBSUB_TOPIC_ORDER_BOOK_UPDATES", "order-book-updates") # 訂單簿更新 Topic
PUBSUB_TOPIC_ACCOUNT_UPDATES = os.environ.get("PUBSUB_TOPIC_ACCOUNT_UPDATES", "account-updates") # 帳戶更新 Topic

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Secret Manager 客戶端
secret_client = secretmanager.SecretManagerServiceClient()

# 初始化 Pub/Sub Publisher 客戶端
publisher = pubsub_v1.PublisherClient()
kline_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_KLINE_UPDATES)
order_book_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_ORDER_BOOK_UPDATES)
account_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_ACCOUNT_UPDATES)


def get_secret(secret_name_env_var):
    """
    從 Secret Manager 獲取秘密值。
    """
    secret_resource_name = os.environ.get(secret_name_env_var)
    if not secret_resource_name:
        logging.warning(f"Environment variable '{secret_name_env_var}' for secret not set.")
        return None
    try:
        response = secret_client.access_secret_version(name=secret_resource_name)
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logging.error(f"Error accessing secret '{secret_resource_name}' via '{secret_name_env_var}': {e}")
        return None

def get_binance_keys():
    """獲取幣安 API 金鑰和秘密金鑰"""
    api_key = get_secret("BINANCE_API_KEY_SECRET_NAME")
    secret_key = get_secret("BINANCE_SECRET_KEY_SECRET_NAME")
    return api_key, secret_key

def handle_kline_message(msg):
    """處理接收到的 K 線 WebSocket 訊息"""
    logging.info(f"Received kline: {msg['s']}@{msg['k']['i']}")
    # 這裡可以根據需要對訊息進行過濾或處理
    # msg 結構：{'e': 'kline', 'E': 1678886400000, 's': 'BTCUSDT', ...}
    # 確保只發布已關閉的K線 (即is_final_bar為True) 或根據需求發布實時K線
    if msg['k']['x']: # 'x' 代表這根 K 線是否已關閉 (is_final_bar)
        try:
            # 將 K 線數據轉換為 JSON 字符串並發布到 Pub/Sub
            message_data = json.dumps(msg).encode('utf-8')
            future = publisher.publish(kline_topic_path, message_data)
            future.add_done_callback(callback_pubsub_publish)
            logging.debug(f"Published kline for {msg['s']}@{msg['k']['i']}")
        except Exception as e:
            logging.error(f"Error publishing kline message: {e}")

def handle_depth_message(msg):
    """處理接收到的訂單簿深度 WebSocket 訊息"""
    # 這裡的 msg 結構會是 'depthUpdate' 類型
    logging.info(f"Received depth update for {msg['s']}")
    try:
        message_data = json.dumps(msg).encode('utf-8')
        future = publisher.publish(order_book_topic_path, message_data)
        future.add_done_callback(callback_pubsub_publish)
        logging.debug(f"Published depth update for {msg['s']}")
    except Exception as e:
        logging.error(f"Error publishing depth update message: {e}")

def handle_user_data_message(msg):
    """處理接收到的用戶數據（帳戶餘額、訂單更新）WebSocket 訊息"""
    # msg 結構會包含 'e': 'outboundAccountPosition' 或 'e': 'executionReport' 等
    logging.info(f"Received user data: {msg['e']}")
    try:
        message_data = json.dumps(msg).encode('utf-8')
        future = publisher.publish(account_topic_path, message_data)
        future.add_done_callback(callback_pubsub_publish)
        logging.debug(f"Published user data: {msg['e']}")
    except Exception as e:
        logging.error(f"Error publishing user data message: {e}")

def callback_pubsub_publish(future):
    """Pub/Sub 發布完成的回調函數"""
    try:
        message_id = future.result()
        logging.debug(f"Message published with ID: {message_id}")
    except Exception as e:
        logging.error(f"Failed to publish message: {e}")


def main():
    api_key, secret_key = get_binance_keys()

    if not api_key or not secret_key:
        logging.error("Binance API keys not available. Cannot start WebSocket manager.")
        exit(1)

    logging.info("Starting Binance ThreadedWebsocketManager...")
    twm = ThreadedWebsocketManager(api_key=api_key, secret_key=secret_key)
    twm.start()

    # 從環境變數獲取訂閱的交易對和K線間隔
    trade_symbols_str = os.environ.get("TRADE_SYMBOLS", "BTCUSDT,ETHUSDT").upper()
    trade_symbols = [s.strip() for s in trade_symbols_str.split(',')]
    kline_intervals_str = os.environ.get("KLINE_INTERVALS", "1m").lower()
    kline_intervals = [i.strip() for i in kline_intervals_str.split(',')]

    logging.info(f"Subscribing to kline streams for symbols: {trade_symbols}, intervals: {kline_intervals}")
    for symbol in trade_symbols:
        for interval in kline_intervals:
            twm.start_kline_socket(callback=handle_kline_message, symbol=symbol, interval=interval)
        # 訂閱訂單簿深度流 (您可以選擇訂閱不同的深度級別，例如 @depth5, @depth10, @depth20)
        twm.start_depth_socket(callback=handle_depth_message, symbol=symbol)

    # 訂閱用戶數據流 (帳戶餘額、訂單更新等)
    # 啟動用戶數據流之前，確保您的API金鑰有讀取帳戶的權限
    logging.info("Subscribing to user data stream...")
    twm.start_user_socket(callback=handle_user_data_message)


    logging.info("WebSocket manager started. Keeping service alive...")
    # Cloud Run 服務需要保持運行來維護 WebSocket 連線
    # 這裡使用一個無限循環來防止服務終止
    try:
        while True:
            # 可以添加一些健康檢查或心跳邏輯
            import time
            time.sleep(60) # 每分鐘檢查一次，防止過度消耗CPU
    except KeyboardInterrupt:
        logging.info("Stopping WebSocket manager...")
        twm.stop()
        logging.info("Service terminated.")

if __name__ == '__main__':
    main()


