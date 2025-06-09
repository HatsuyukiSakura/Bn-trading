import os
import json
import logging
from google.cloud import pubsub_v1
from google.cloud import logging as cloud_logging
from google.cloud import secretmanager_v1beta1 as secretmanager
import requests # 用於發送 Telegram 訊息
from concurrent.futures import TimeoutError

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_TRADE_REPORTS = os.environ.get("PUBSUB_TOPIC_TRADE_REPORTS", "trade-reports")
PUBSUB_TOPIC_RISK_ALERTS = os.environ.get("PUBSUB_TOPIC_RISK_ALERTS", "risk-alerts")
PUBSUB_TOPIC_OPTIMIZATION_ALERTS = os.environ.get("PUBSUB_TOPIC_OPTIMIZATION_ALERTS", "optimization-alerts")
# 您可以添加更多需要監聽的 Topic

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Google Cloud Logging 客戶端
cloud_logger_client = cloud_logging.Client(project=GCP_PROJECT_ID)
logger = cloud_logger_client.logger("trading-bot-logs") # 設定日誌名稱

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()

# 初始化 Secret Manager 客戶端
secret_client = secretmanager.SecretManagerServiceClient()

# 獲取 Telegram 憑證
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

def get_secret(secret_name_env_var):
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

def initialize_telegram_creds():
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN_SECRET_NAME")
    chat_ids_str = get_secret("TELEGRAM_CHAT_ID_SECRET_NAME")
    if chat_ids_str:
        try:
            # 如果是JSON字串，解析為列表；否則視為單個ID
            TELEGRAM_CHAT_ID = json.loads(chat_ids_str)
        except json.JSONDecodeError:
            TELEGRAM_CHAT_ID = [chat_ids_str] # 視為單個ID的列表
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token or Chat ID(s) not configured. Telegram notifications will be disabled.")

def send_telegram_message(message_text, chat_id=None):
    """發送訊息到 Telegram。"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram notification not enabled due to missing configuration.")
        return

    target_chat_ids = TELEGRAM_CHAT_ID if chat_id is None else [chat_id]

    for cid in target_chat_ids:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": message_text,
            "parse_mode": "MarkdownV2" # 可以使用 Markdown 格式
        }
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status() # 如果請求失敗，拋出 HTTPError
            logging.info(f"Telegram message sent to chat ID {cid}.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending Telegram message to {cid}: {e}")
            logging.error(f"Telegram response: {response.text if 'response' in locals() else 'No response'}")


def process_message(message: pubsub_v1.subscriber.message.Message):
    """
    通用訊息處理函數，將訊息記錄到 Cloud Logging 並發送 Telegram 通知。
    """
    try:
        data = json.loads(message.data.decode('utf-8'))
        message_attributes = message.attributes

        event_type = message_attributes.get('eventType', 'UNKNOWN_EVENT')
        log_level = message_attributes.get('logLevel', 'INFO')

        log_entry = {
            "severity": log_level.upper(),
            "jsonPayload": data,
            "labels": {
                "event_type": event_type,
                "source_topic": message.subscription.split('/')[-2] # 從訂閱路徑解析 Topic 名稱
            }
        }
        logger.log_struct(log_entry, severity=log_level.upper())
        logging.info(f"Logged {event_type} event to Cloud Logging.")

        # 發送 Telegram 通知
        telegram_message = ""
        if event_type == "TRADE_REPORT":
            status = data.get('status', 'N/A')
            symbol = data.get('symbol', 'N/A')
            action = data.get('action', 'N/A')
            quantity = data.get('quantity_requested', 'N/A')
            executed_price = data.get('executed_price', 'N/A')
            error_msg = data.get('error_message', '')
            
            telegram_message = (
                f"**交易報告**\n"
                f"幣種: `{symbol}`\n"
                f"動作: `{action}`\n"
                f"數量: `{quantity:.6f}`\n"
                f"狀態: `{status}`\n"
                f"成交價: `{executed_price:.4f}`\n"
                f"錯誤: `{error_msg}`"
            )
        elif event_type == "RISK_ALERT":
            reason = data.get('reason', 'N/A')
            symbol = data.get('symbol', 'N/A')
            telegram_message = (
                f"🚨 **風險警報** 🚨\n"
                f"原因: `{reason}`\n"
                f"幣種: `{symbol}`"
            )
        elif event_type == "OPTIMIZATION_ALERT":
            new_config = data.get('new_config', {})
            pnl = data.get('performance_metrics', {}).get('total_profit_loss', 'N/A')
            win_rate = data.get('performance_metrics', {}).get('win_rate', 'N/A')
            telegram_message = (
                f"📈 **策略優化警報** 📊\n"
                f"P&L: `{pnl:.2f}`\n"
                f"勝率: `{win_rate:.2f}`\n"
                f"新配置:\n"
                f"  買入閾值: `{new_config.get('BUY_SIGNAL_THRESHOLD', 'N/A')}`\n"
                f"  賣出閾值: `{new_config.get('SELL_SIGNAL_THRESHOLD', 'N/A')}`\n"
                f"  單筆風險: `{new_config.get('DEFAULT_RISK_PER_TRADE', 'N/A')}`"
            )
        else:
            telegram_message = f"**Bot 事件 ({event_type}):**\n```json\n{json.dumps(data, indent=2)}\n```"

        send_telegram_message(telegram_message)
        
        message.ack() # 確認訊息已處理
        logging.info(f"Processed message from topic and sent notification.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message: {e} - Data: {message.data.decode('utf-8')}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing message in logging/notification module: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Logging & Notification Module...")
    
    initialize_telegram_creds() # 初始化 Telegram 憑證

    # 設置多個訂閱路徑
    subscriptions = []
    subscriptions.append(subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_TRADE_REPORTS}-sub-notifier"))
    subscriptions.append(subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_RISK_ALERTS}-sub-notifier"))
    subscriptions.append(subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_OPTIMIZATION_ALERTS}-sub-notifier"))
    
    # 創建訂閱 (如果不存在)
    for sub_path, topic_name in [
        (subscriptions[0], PUBSUB_TOPIC_TRADE_REPORTS),
        (subscriptions[1], PUBSUB_TOPIC_RISK_ALERTS),
        (subscriptions[2], PUBSUB_TOPIC_OPTIMIZATION_ALERTS)
    ]:
        try:
            subscriber.create_subscription(name=sub_path, topic=publisher.topic_path(GCP_PROJECT_ID, topic_name))
            logging.info(f"Subscription {sub_path} created (if it didn't exist).")
        except Exception as e:
            if "AlreadyExists" in str(e):
                logging.info(f"Subscription {sub_path} already exists.")
            else:
                logging.error(f"Error creating subscription {sub_path}: {e}")
                exit(1)

    # 啟動多個訂閱監聽
    futures = []
    for sub_path in subscriptions:
        futures.append(subscriber.subscribe(sub_path, callback=process_message))
        logging.info(f"Listening for messages on {sub_path}...")

    try:
        # 阻塞主線程，直到所有訂閱結束（實際上是永遠運行）
        for future in futures:
            future.result()
    except TimeoutError:
        for future in futures:
            future.cancel()
            future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        for future in futures:
            future.cancel()
            future.result()
        logging.error(f"Error in Pub/Sub subscription for logging/notification: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Logging & Notification Module stopped.")

if __name__ == '__main__':
    main()


