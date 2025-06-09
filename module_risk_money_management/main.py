import os
import json
import logging
from google.cloud import pubsub_v1
from concurrent.futures import TimeoutError
# from binance.client import Client # 如果需要查詢實時資產，則需要引入幣安客戶端
# from google.cloud import secretmanager_v1beta1 as secretmanager # 如果需要從Secret Manager獲取幣安Key

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_TRADE_COMMANDS = os.environ.get("PUBSUB_TOPIC_TRADE_COMMANDS", "trade-commands") # 訂閱的 Topic
PUBSUB_TOPIC_FINAL_TRADE_EXECUTION = os.environ.get("PUBSUB_TOPIC_FINAL_TRADE_EXECUTION", "final-trade-execution") # 發布的 Topic
PUBSUB_TOPIC_RISK_ALERTS = os.environ.get("PUBSUB_TOPIC_RISK_ALERTS", "risk-alerts") # 風險警報 Topic

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

# 訂閱路徑
trade_commands_sub_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_TRADE_COMMANDS}-sub-risk-manager")
final_trade_execution_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_FINAL_TRADE_EXECUTION)
risk_alerts_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_RISK_ALERTS)


# --- 模擬資產管理 (實際情況應從交易所API獲取或資料庫中獲取) ---
# 注意：在生產環境中，這種狀態變量會導致多個 Cloud Run 實例之間的狀態不一致。
# 必須使用共享的、持久化的儲存（例如 Firestore 或 Redis）來維護資產和倉位狀態。
# 這裡僅為範例目的。
current_portfolio_value = float(os.environ.get("INITIAL_PORTFOLIO_VALUE", "1000.0")) # 初始模擬資金
current_positions = {} # {symbol: quantity}

# 假設的單筆交易風險比例 (例如 0.01 = 1%)
DEFAULT_RISK_PER_TRADE = float(os.environ.get("DEFAULT_RISK_PER_TRADE", "0.01"))
# 假設的總風險限制 (例如 0.05 = 5%)
MAX_PORTFOLIO_RISK = float(os.environ.get("MAX_PORTFOLIO_RISK", "0.05"))


def get_current_asset_price(symbol):
    """
    獲取實時資產價格。在生產環境中，這應從數據獲取模組的數據庫或交易所 API 獲取。
    這裡簡化為從環境變數獲取一個模擬價格。
    """
    # 實際應該查詢即時價格，例如：
    # from binance.client import Client
    # client = Client(api_key, secret_key)
    # ticker = client.get_symbol_ticker(symbol=symbol)
    # return float(ticker['price'])
    
    # 簡化範例：假設所有幣種價格都是 1000 USD 以方便計算
    return float(os.environ.get(f"{symbol}_PRICE", "1000.0"))


def calculate_trade_quantity(action, symbol, current_price, risk_percentage=DEFAULT_RISK_PER_TRADE):
    """
    根據風險管理規則計算交易數量。
    這裡採用簡單的基於資金百分比的計算。
    更複雜的包括倉位大小、Kelly Criterion 等。
    """
    if current_portfolio_value <= 0:
        logging.warning("Portfolio value is zero or negative. Cannot calculate trade quantity.")
        return 0.0

    # 假設每次交易投入資金的百分比，再根據當前幣種價格換算數量
    # 這只是一個非常簡化的例子，實際的倉位管理會更複雜
    target_usd_value = current_portfolio_value * risk_percentage # 投資資金的百分比
    
    if current_price <= 0:
        logging.error(f"Current price for {symbol} is invalid: {current_price}")
        return 0.0

    quantity = target_usd_value / current_price
    
    # 這裡可以根據交易所的最小交易量、最小步長等進行調整
    # 例如：quantity = round(quantity, binance_precision)
    
    logging.info(f"Calculated trade quantity for {symbol} ({action}): {quantity:.6f} at {current_price:.2f} (USD value: {target_usd_value:.2f})")
    return quantity


def check_overall_portfolio_risk(trade_value_usd):
    """
    檢查整體投資組合風險是否超過限制。
    這需要實時查詢所有持倉和其盈虧。
    這裡僅為概念演示。
    """
    # 假設我們有一個簡單的總風險閾值
    # 在實際應用中，會計算 VaR (Value at Risk) 或其他更複雜的指標
    current_total_risk_exposure = 0.0 # 這是需要從資料庫獲取或實時計算的
    
    # 這裡的邏輯需要您根據實際定義的總風險指標來實現
    # 例如：如果當前浮虧佔總資產的百分比超過 MAX_PORTFOLIO_RISK
    
    # 簡化：如果當前這筆交易會導致模擬總價值低於某個預警線
    if current_portfolio_value - trade_value_usd < current_portfolio_value * (1 - MAX_PORTFOLIO_RISK):
        logging.warning(f"Potential trade of {trade_value_usd:.2f} USD might exceed portfolio risk limit.")
        # 可以選擇不執行這筆交易或發出警報
        return False
    return True


def process_trade_command(message: pubsub_v1.subscriber.message.Message):
    """
    處理接收到的交易指令，進行風險和資金管理。
    """
    global current_portfolio_value, current_positions # 允許修改全局變量，但在生產環境應避免

    try:
        command_data = json.loads(message.data.decode('utf-8'))
        symbol = command_data['symbol']
        action = command_data['action']
        original_signal_score = command_data['signal_score']

        logging.info(f"Received trade command for {symbol}: {action} (Score: {original_signal_score:.2f})")

        current_price = get_current_asset_price(symbol)
        if current_price is None:
            logging.error(f"Could not get current price for {symbol}. Skipping risk management.")
            message.nack()
            return

        # 1. 計算交易數量並進行資金管理
        quantity = calculate_trade_quantity(action, symbol, current_price)
        if quantity <= 0:
            logging.warning(f"Calculated quantity for {symbol} is zero or negative. Skipping trade.")
            message.ack()
            return

        trade_usd_value = quantity * current_price

        # 2. 檢查整體風險
        if not check_overall_portfolio_risk(trade_usd_value):
            alert_message = {
                "type": "RISK_EXCEEDED",
                "symbol": symbol,
                "action": action,
                "reason": "Portfolio risk limit might be exceeded.",
                "timestamp": command_data['timestamp']
            }
            publisher.publish(risk_alerts_topic_path, json.dumps(alert_message).encode('utf-8'))
            logging.warning(f"Trade for {symbol} ({action}) aborted due to overall portfolio risk.")
            message.ack() # 認為已處理，但沒有執行交易
            return
        
        # 3. 計算止損止盈價格 (簡化範例)
        stop_loss_price = None
        take_profit_price = None
        
        # 簡單止損止盈：例如，買入後跌 1% 止損，漲 2% 止盈
        if action == "BUY":
            stop_loss_percentage = float(os.environ.get("STOP_LOSS_PERCENTAGE_BUY", "0.01")) # 1%
            take_profit_percentage = float(os.environ.get("TAKE_PROFIT_PERCENTAGE_BUY", "0.02")) # 2%
            stop_loss_price = current_price * (1 - stop_loss_percentage)
            take_profit_price = current_price * (1 + take_profit_percentage)
        elif action == "SELL":
            # 賣空邏輯，或止盈平倉
            stop_loss_percentage = float(os.environ.get("STOP_LOSS_PERCENTAGE_SELL", "0.01"))
            take_profit_percentage = float(os.environ.get("TAKE_PROFIT_PERCENTAGE_SELL", "0.02"))
            stop_loss_price = current_price * (1 + stop_loss_percentage) # 賣出後價格上漲止損
            take_profit_price = current_price * (1 - take_profit_percentage) # 賣出後價格下跌止盈

        # 4. 更新模擬的投資組合狀態 (生產環境中這些操作會寫入資料庫)
        # 這是一個非常簡化的更新，實際需要考慮交易費用、成交價格等
        if action == "BUY":
            current_portfolio_value -= trade_usd_value
            current_positions[symbol] = current_positions.get(symbol, 0) + quantity
        elif action == "SELL":
            # 這裡假設是平倉操作，或賣空操作
            current_portfolio_value += trade_usd_value
            current_positions[symbol] = current_positions.get(symbol, 0) - quantity
            if current_positions[symbol] < 0:
                logging.warning(f"Selling more than held for {symbol}. Check logic.")
                current_positions[symbol] = 0 # 避免負值

        logging.info(f"Updated simulated portfolio value: {current_portfolio_value:.2f} USD")
        logging.info(f"Current positions: {current_positions}")


        # 構建最終交易指令
        final_trade_command = {
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "order_type": command_data['order_type'], # 從上一個模組繼承
            "price": current_price, # 市價單時，這個價格用於參考
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "timestamp": command_data['timestamp'],
            "source_signal_score": original_signal_score,
            "risk_managed": True
        }
        
        # 發布最終交易指令到 Pub/Sub
        message_data = json.dumps(final_trade_command).encode('utf-8')
        future = publisher.publish(final_trade_execution_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Final trade command published with ID: {f.result()}"))
        
        message.ack() # 確認訊息已處理
        logging.info(f"Processed trade command for {symbol} and published final execution command.")

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message from trade command: {e}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing trade command in risk manager: {e}", exc_info=True)
        message.nack()


def main():
    logging.info("Starting Risk & Money Management Module...")
    
    # 創建訂閱 (如果不存在)
    try:
        subscriber.create_subscription(name=trade_commands_sub_path, topic=publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_TRADE_COMMANDS))
        logging.info(f"Subscription {trade_commands_sub_path} created (if it didn't exist).")
    except Exception as e:
        if "AlreadyExists" in str(e):
            logging.info(f"Subscription {trade_commands_sub_path} already exists.")
        else:
            logging.error(f"Error creating subscription {trade_commands_sub_path}: {e}")
            exit(1)

    streaming_pull_future = subscriber.subscribe(trade_commands_sub_path, callback=process_trade_command)
    logging.info(f"Listening for trade commands on {PUBSUB_TOPIC_TRADE_COMMANDS}...")

    try:
        streaming_pull_future.result()
    except TimeoutError:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        streaming_pull_future.cancel()
        streaming_pull_future.result()
        logging.error(f"Error in Pub/Sub subscription for risk manager: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Risk & Money Management Module stopped.")

if __name__ == '__main__':
    main()


