import os
import json
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
from google.cloud import pubsub_v1
from google.cloud import secretmanager_v1beta1 as secretmanager
from concurrent.futures import TimeoutError

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_FINAL_TRADE_EXECUTION = os.environ.get("PUBSUB_TOPIC_FINAL_TRADE_EXECUTION", "final-trade-execution") # 訂閱的 Topic
PUBSUB_TOPIC_TRADE_REPORTS = os.environ.get("PUBSUB_TOPIC_TRADE_REPORTS", "trade-reports") # 發布交易報告

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

# 訂閱路徑
final_trade_execution_sub_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_FINAL_TRADE_EXECUTION}-sub-executor")
trade_reports_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_TRADE_REPORTS)


# 初始化 Secret Manager 客戶端
secret_client = secretmanager.SecretManagerServiceClient()

def get_secret(secret_name_env_var):
    """從 Secret Manager 獲取秘密值。"""
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

def get_binance_client():
    """獲取幣安 API 客戶端"""
    api_key = get_secret("BINANCE_API_KEY_SECRET_NAME")
    secret_key = get_secret("BINANCE_SECRET_KEY_SECRET_NAME")
    if not api_key or not secret_key:
        logging.error("Binance API keys not available. Cannot initialize Binance client.")
        return None
    return Client(api_key, secret_key)


def execute_trade(client, symbol, action, quantity, order_type="MARKET", stop_loss_price=None, take_profit_price=None):
    """
    執行實際的交易操作。
    """
    logging.info(f"Attempting to execute {action} {quantity:.6f} {symbol} (Order Type: {order_type})...")
    
    trade_report = {
        "symbol": symbol,
        "action": action,
        "quantity_requested": quantity,
        "order_type": order_type,
        "status": "FAILED",
        "error_message": "",
        "timestamp": int(pd.Timestamp.now().timestamp() * 1000),
        "executed_price": None,
        "executed_quantity": None,
        "order_id": None,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price
    }

    try:
        if order_type == "MARKET":
            if action == "BUY":
                order = client.order_market_buy(symbol=symbol, quantity=quantity)
            elif action == "SELL":
                order = client.order_market_sell(symbol=symbol, quantity=quantity)
            else:
                raise ValueError(f"Unsupported action: {action}")
        # 未來可以添加 LIMIT, STOP_LOSS, TAKE_PROFIT 訂單類型
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        logging.info(f"Order placed successfully: {order}")

        # 解析訂單結果
        trade_report["status"] = order['status']
        trade_report["order_id"] = order['orderId']
        
        # 對於市價單，實際成交價和數量可能分散在多個 fills 中
        total_executed_qty = sum(float(f['qty']) for f in order['fills'])
        total_executed_quote_qty = sum(float(f['qty']) * float(f['price']) for f in order['fills'])
        
        trade_report["executed_quantity"] = total_executed_qty
        trade_report["executed_price"] = total_executed_quote_qty / total_executed_qty if total_executed_qty > 0 else None
        
        # 在實際交易中，您還需要監控訂單狀態，特別是對於限價單
        # 如果需要，這裡可以發布訂單監控指令到另一個模組
        
        # 如果有止損止盈價格，可以設置 OCO 訂單或在單獨的模組中監控
        if stop_loss_price and take_profit_price:
            logging.info(f"Attempting to place OCO order for {symbol} with SL={stop_loss_price} and TP={take_profit_price}")
            # Binance OCO 訂單示例
            # order_oco = client.order_oco_sell(
            #     symbol=symbol,
            #     quantity=trade_report["executed_quantity"],
            #     price=take_profit_price, # Limit price for take profit
            #     stopPrice=stop_loss_price, # Stop price for stop loss
            #     stopLimitPrice=stop_loss_price * 0.99, # Stop limit price (optional)
            #     stopLimitTimeInForce='GTC'
            # )
            # logging.info(f"OCO order placed: {order_oco}")
            # trade_report["oco_order_id"] = order_oco['orderListId']
            # trade_report["oco_status"] = order_oco['listStatusType']
            
            # 由於 OCO 訂單的複雜性，這裡只記錄意圖，實際執行可交由另一個模組或用戶手動設置
            trade_report["note"] = "Stop Loss/Take Profit logic needs separate handling/OCO order placement."

    except BinanceAPIException as e:
        logging.error(f"Binance API error during trade execution: {e.code} - {e.message}")
        trade_report["error_message"] = f"Binance API Error: {e.code} - {e.message}"
    except BinanceRequestException as e:
        logging.error(f"Binance request error during trade execution: {e}")
        trade_report["error_message"] = f"Binance Request Error: {e}"
    except ValueError as e:
        logging.error(f"Value error during trade execution: {e}")
        trade_report["error_message"] = f"Value Error: {e}"
    except Exception as e:
        logging.error(f"Unexpected error during trade execution: {e}", exc_info=True)
        trade_report["error_message"] = f"Unexpected Error: {e}"

    return trade_report

def process_final_trade_command(message: pubsub_v1.subscriber.message.Message):
    """
    處理接收到的最終交易指令。
    """
    client = get_binance_client()
    if not client:
        logging.error("Binance client not initialized. Cannot process trade command.")
        message.nack() # 負確認，稍後重試
        return

    try:
        command_data = json.loads(message.data.decode('utf-8'))
        symbol = command_data['symbol']
        action = command_data['action']
        quantity = command_data['quantity']
        order_type = command_data['order_type']
        stop_loss_price = command_data.get('stop_loss_price')
        take_profit_price = command_data.get('take_profit_price')

        logging.info(f"Executing final trade: {action} {quantity:.6f} {symbol}")

        trade_report = execute_trade(client, symbol, action, quantity, order_type, stop_loss_price, take_profit_price)
        
        # 發布交易報告到 Pub/Sub
        message_data = json.dumps(trade_report).encode('utf-8')
        future = publisher.publish(trade_reports_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Trade report published with ID: {f.result()}"))
        
        message.ack() # 確認訊息已處理
        logging.info(f"Processed final trade command for {symbol} and published trade report.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message from final trade command: {e}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing final trade command in executor: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Trade Execution Module...")
    
    # 創建訂閱 (如果不存在)
    try:
        subscriber.create_subscription(name=final_trade_execution_sub_path, topic=publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_FINAL_TRADE_EXECUTION))
        logging.info(f"Subscription {final_trade_execution_sub_path} created (if it didn't exist).")
    except Exception as e:
        if "AlreadyExists" in str(e):
            logging.info(f"Subscription {final_trade_execution_sub_path} already exists.")
        else:
            logging.error(f"Error creating subscription {final_trade_execution_sub_path}: {e}")
            exit(1)

    streaming_pull_future = subscriber.subscribe(final_trade_execution_sub_path, callback=process_final_trade_command)
    logging.info(f"Listening for final trade execution commands on {PUBSUB_TOPIC_FINAL_TRADE_EXECUTION}...")

    try:
        streaming_pull_future.result()
    except TimeoutError:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.error(f"Error in Pub/Sub subscription for trade executor: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Trade Execution Module stopped.")

if __name__ == '__main__':
    # 由於 Cloud Run 觸發器是針對單個事件處理的，
    # 在實際部署時，Cloud Run 會為每個收到的 Pub/Sub 訊息啟動一個新的實例。
    # 這裡的 `main` 函數主要用於定義服務行為，而不是無限循環。
    main()


