import os
import json
import logging
from google.cloud import pubsub_v1
from google.cloud import firestore
from concurrent.futures import TimeoutError
import datetime

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_FINAL_TRADE_EXECUTION = os.environ.get("PUBSUB_TOPIC_FINAL_TRADE_EXECUTION", "final-trade-execution") # 訂閱的 Topic
PUBSUB_TOPIC_PAPER_TRADE_REPORTS = os.environ.get("PUBSUB_TOPIC_PAPER_TRADE_REPORTS", "paper-trade-reports") # 發布模擬交易報告

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

# 訂閱路徑
final_trade_execution_sub_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_FINAL_TRADE_EXECUTION}-sub-paper-trader")
paper_trade_reports_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_PAPER_TRADE_REPORTS)

# 初始化 Firestore 客戶端 (用於儲存模擬賬戶狀態)
db = firestore.Client(project=GCP_PROJECT_ID)
PAPER_ACCOUNT_COLLECTION = os.environ.get("PAPER_ACCOUNT_COLLECTION", "paper_trading_accounts")
PAPER_TRADES_COLLECTION = os.environ.get("PAPER_TRADES_COLLECTION", "paper_trading_history")


def get_paper_account_state(account_id="default_paper_account"):
    """從 Firestore 獲取模擬賬戶狀態。"""
    try:
        doc_ref = db.collection(PAPER_ACCOUNT_COLLECTION).document(account_id)
        doc = doc_ref.get()
        if doc.exists:
            state = doc.to_dict()
            logging.info(f"Loaded paper account state: {state}")
            return state
        else:
            # 初始化新賬戶
            initial_balance = float(os.environ.get("PAPER_TRADING_INITIAL_BALANCE", "10000.0"))
            new_state = {
                "balance_usd": initial_balance,
                "positions": {}, # {symbol: quantity}
                "last_update_timestamp": firestore.SERVER_TIMESTAMP
            }
            doc_ref.set(new_state)
            logging.info(f"Initialized new paper account with balance: {initial_balance}")
            return new_state
    except Exception as e:
        logging.error(f"Error getting paper account state: {e}", exc_info=True)
        return None

def update_paper_account_state(account_id, new_balance, new_positions):
    """更新模擬賬戶狀態到 Firestore。"""
    try:
        doc_ref = db.collection(PAPER_ACCOUNT_COLLECTION).document(account_id)
        doc_ref.update({
            "balance_usd": new_balance,
            "positions": new_positions,
            "last_update_timestamp": firestore.SERVER_TIMESTAMP
        })
        logging.info(f"Paper account state updated. Balance: {new_balance:.2f}")
    except Exception as e:
        logging.error(f"Error updating paper account state: {e}", exc_info=True)

def record_paper_trade(trade_record):
    """記錄模擬交易到 Firestore。"""
    try:
        db.collection(PAPER_TRADES_COLLECTION).add(trade_record)
        logging.info("Paper trade record added to Firestore.")
    except Exception as e:
        logging.error(f"Error recording paper trade: {e}", exc_info=True)

def get_mock_asset_price(symbol):
    """
    獲取模擬資產價格。
    在實際情況中，這應該從數據獲取模組的實時數據流中獲取最新價格。
    這裡簡化為從環境變數獲取，或者您也可以模擬一個隨機波動。
    """
    # 從環境變數獲取一個預設價格，或更動態地獲取
    return float(os.environ.get(f"MOCK_{symbol}_PRICE", "1000.0")) # 假設所有模擬幣種都是 1000 USD


def process_paper_trade_command(message: pubsub_v1.subscriber.message.Message):
    """
    處理接收到的最終交易指令，並在模擬環境中執行。
    """
    try:
        command_data = json.loads(message.data.decode('utf-8'))
        symbol = command_data['symbol']
        action = command_data['action']
        quantity_requested = command_data['quantity']
        order_type = command_data['order_type']
        
        logging.info(f"Processing paper trade: {action} {quantity_requested:.6f} {symbol} (Order Type: {order_type})")

        account_state = get_paper_account_state()
        if not account_state:
            logging.error("Failed to load paper account state. Cannot process paper trade.")
            message.nack()
            return

        balance = account_state['balance_usd']
        positions = account_state['positions']
        
        mock_price = get_mock_asset_price(symbol)
        if mock_price <= 0:
            logging.error(f"Invalid mock price for {symbol}: {mock_price}. Cannot execute paper trade.")
            message.nack()
            return

        executed_quantity = 0.0
        executed_price = mock_price
        status = "FAILED"
        error_message = ""
        pnl = 0.0

        if order_type == "MARKET":
            if action == "BUY":
                cost = quantity_requested * mock_price
                if balance >= cost:
                    balance -= cost
                    positions[symbol] = positions.get(symbol, 0) + quantity_requested
                    executed_quantity = quantity_requested
                    status = "FILLED"
                    logging.info(f"Paper BUY: {quantity_requested:.6f} {symbol} at {mock_price:.2f}. New balance: {balance:.2f}")
                else:
                    status = "REJECTED"
                    error_message = "Insufficient balance for paper BUY."
                    logging.warning(f"Paper BUY rejected: {error_message} (Needed: {cost:.2f}, Have: {balance:.2f})")
            elif action == "SELL":
                held_quantity = positions.get(symbol, 0)
                if held_quantity >= quantity_requested:
                    revenue = quantity_requested * mock_price
                    balance += revenue
                    positions[symbol] -= quantity_requested
                    # 假設這裡計算平倉盈虧，需要之前的買入價格
                    # 這裡簡化為不計算 PnL，或者 PnL 在後續報告中根據平均成本計算
                    executed_quantity = quantity_requested
                    status = "FILLED"
                    logging.info(f"Paper SELL: {quantity_requested:.6f} {symbol} at {mock_price:.2f}. New balance: {balance:.2f}")
                else:
                    status = "REJECTED"
                    error_message = "Insufficient position for paper SELL."
                    logging.warning(f"Paper SELL rejected: {error_message} (Needed: {quantity_requested:.6f}, Have: {held_quantity:.6f})")
            else:
                error_message = f"Unsupported action for paper trade: {action}"
                logging.error(error_message)
        else:
            error_message = f"Unsupported order type for paper trade: {order_type}"
            logging.error(error_message)

        # 更新賬戶狀態
        update_paper_account_state(account_id="default_paper_account", new_balance=balance, new_positions=positions)

        # 記錄模擬交易報告
        paper_trade_report = {
            "symbol": symbol,
            "action": action,
            "quantity_requested": quantity_requested,
            "order_type": order_type,
            "status": status,
            "error_message": error_message,
            "timestamp": int(datetime.datetime.now().timestamp() * 1000),
            "executed_price": executed_price,
            "executed_quantity": executed_quantity,
            "mock_account_balance": balance,
            "mock_account_positions": positions,
            "is_paper_trade": True,
            "pnl": pnl # 如果有計算，會在這裡
        }
        record_paper_trade(paper_trade_report)
        
        # 發布模擬交易報告到 Pub/Sub (讓日誌/通知模組也能收到)
        message_data = json.dumps(paper_trade_report).encode('utf-8')
        future = publisher.publish(paper_trade_reports_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Paper trade report published with ID: {f.result()}"))

        message.ack() # 確認訊息已處理
        logging.info(f"Processed paper trade command for {symbol} and recorded report.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message from final trade command (paper): {e}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing paper trade command: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Paper Trading Module...")
    
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

    streaming_pull_future = subscriber.subscribe(final_trade_execution_sub_path, callback=process_paper_trade_command)
    logging.info(f"Listening for final trade execution commands on {PUBSUB_TOPIC_FINAL_TRADE_EXECUTION} for paper trading...")

    try:
        streaming_pull_future.result()
    except TimeoutError:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.error(f"Error in Pub/Sub subscription for paper trader: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Paper Trading Module stopped.")

if __name__ == '__main__':
    main()


