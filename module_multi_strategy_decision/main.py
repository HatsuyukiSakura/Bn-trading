import os
import json
import logging
from google.cloud import pubsub_v1
from concurrent.futures import TimeoutError

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
PUBSUB_TOPIC_COIN_SELECTION_SIGNALS = os.environ.get("PUBSUB_TOPIC_COIN_SELECTION_SIGNALS", "coin-selection-signals")
PUBSUB_TOPIC_ORDER_BOOK_SIGNALS = os.environ.get("PUBSUB_TOPIC_ORDER_BOOK_SIGNALS", "order-book-signals")
PUBSUB_TOPIC_TRADE_COMMANDS = os.environ.get("PUBSUB_TOPIC_TRADE_COMMANDS", "trade-commands") # 發布交易指令

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Pub/Sub 客戶端
subscriber = pubsub_v1.SubscriberClient()
publisher = pubsub_v1.PublisherClient()

# 訂閱路徑 (注意：為每個消費者創建不同的訂閱)
coin_selection_sub_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_COIN_SELECTION_SIGNALS}-sub-strategy")
order_book_sub_path = subscriber.subscription_path(GCP_PROJECT_ID, f"{PUBSUB_TOPIC_ORDER_BOOK_SIGNALS}-sub-strategy")
trade_commands_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_TRADE_COMMANDS)


# -------------------------------------------------------------------
# 策略狀態與信號緩存
# 在實際應用中，這些狀態應存儲在 Firestore 或 Redis 中，並定期更新
# -------------------------------------------------------------------
# 這裡使用簡單的字典來模擬實時狀態，但多個實例運行時會不一致
# 需要引入一個共享狀態層 (e.g., Redis, Firestore)
last_coin_selection_signals = {} # {symbol: signal_data}
last_order_book_signals = {}    # {symbol: signal_data}


def process_coin_selection_signal(message: pubsub_v1.subscriber.message.Message):
    """處理接收到的幣種分析與選幣信號"""
    try:
        signal_data = json.loads(message.data.decode('utf-8'))
        symbol = signal_data['symbol']
        last_coin_selection_signals[symbol] = signal_data
        logging.info(f"Received coin selection signal for {symbol}: {signal_data['recommendation']}")
        message.ack()
        # 收到信號後，立即嘗試決策
        make_decision(symbol)
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message from coin selection: {e}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing coin selection signal: {e}", exc_info=True)
        message.nack()

def process_order_book_signal(message: pubsub_v1.subscriber.message.Message):
    """處理接收到的訂單簿分析信號"""
    try:
        signal_data = json.loads(message.data.decode('utf-8'))
        symbol = signal_data['symbol']
        last_order_book_signals[symbol] = signal_data
        logging.info(f"Received order book signal for {symbol}: OBI={signal_data['order_book_imbalance']:.2f}")
        message.ack()
        # 收到信號後，立即嘗試決策
        make_decision(symbol)
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON message from order book: {e}")
        message.nack()
    except Exception as e:
        logging.error(f"Error processing order book signal: {e}", exc_info=True)
        message.nack()

def make_decision(symbol):
    """
    根據各種信號和策略邏輯，生成交易指令。
    這是一個簡化的多策略整合邏輯。
    """
    coin_signal = last_coin_selection_signals.get(symbol)
    ob_signal = last_order_book_signals.get(symbol)

    if not coin_signal:
        logging.debug(f"No coin selection signal for {symbol} yet.")
        return

    # --- 策略組合邏輯範例 ---
    # 策略 1: 基於 AI 選幣的基礎推薦
    primary_recommendation = coin_signal['recommendation']
    primary_strength = coin_signal['signal_strength']

    # 策略 2: 結合訂單簿信號進行確認或增強
    confirmation_strength = 0.0
    if ob_signal:
        if primary_recommendation == "BUY" and ob_signal['order_book_imbalance'] > 0.1: # 買方力量較強
            confirmation_strength += ob_signal['order_book_imbalance'] * 0.5 # 加強買入信號
            logging.info(f"Order book confirms BUY for {symbol} with OBI {ob_signal['order_book_imbalance']:.2f}")
        elif primary_recommendation == "SELL" and ob_signal['order_book_imbalance'] < -0.1: # 賣方力量較強
            confirmation_strength += abs(ob_signal['order_book_imbalance']) * 0.5 # 加強賣出信號
            logging.info(f"Order book confirms SELL for {symbol} with OBI {ob_signal['order_book_imbalance']:.2f}")

        # 考慮鯨魚訂單影響
        if primary_recommendation == "BUY" and ob_signal['large_bid_detected']:
            confirmation_strength += 0.1 # 大型買單加強信號
            logging.info(f"Large bid detected for {symbol}, strengthening BUY signal.")
        elif primary_recommendation == "SELL" and ob_signal['large_ask_detected']:
            confirmation_strength += 0.1 # 大型賣單加強信號
            logging.info(f"Large ask detected for {symbol}, strengthening SELL signal.")
            
    final_signal_score = primary_strength + confirmation_strength
    
    # 設置觸發交易的閾值 (這些可以從配置模組或自動學習模組動態獲取)
    BUY_THRESHOLD = float(os.environ.get("BUY_SIGNAL_THRESHOLD", "0.5"))
    SELL_THRESHOLD = float(os.environ.get("SELL_SIGNAL_THRESHOLD", "-0.5")) # 賣出閾值通常為負值

    trade_action = "NONE"
    if primary_recommendation == "BUY" and final_signal_score > BUY_THRESHOLD:
        trade_action = "BUY"
    elif primary_recommendation == "SELL" and final_signal_score < SELL_THRESHOLD:
        trade_action = "SELL"
    
    if trade_action != "NONE":
        trade_command = {
            "symbol": symbol,
            "action": trade_action, # BUY / SELL
            "signal_score": final_signal_score,
            "timestamp": int(pd.Timestamp.now().timestamp() * 1000), # 當前時間戳
            "order_type": "MARKET", # 簡化為市價單，未來可以更靈活
            "quantity_type": "PERCENT_BALANCE", # 這裡可以指定數量類型 (例如：固定金額、百分比)
            "quantity_value": float(os.environ.get("DEFAULT_TRADE_QUANTITY_PERCENT", "0.001")) # 投資組合的千分之一作為範例
        }
        logging.info(f"Generated trade command for {symbol}: {trade_command['action']}")
        
        # 發布交易指令到 Pub/Sub
        message_data = json.dumps(trade_command).encode('utf-8')
        future = publisher.publish(trade_commands_topic_path, message_data)
        future.add_done_callback(lambda f: logging.debug(f"Trade command published with ID: {f.result()}"))
    else:
        logging.info(f"No strong enough trade signal for {symbol}. Score: {final_signal_score:.2f}")


def main():
    logging.info("Starting Multi-Strategy Decision Module...")
    
    # 創建訂閱 (如果不存在)
    for sub_path, topic_name in [
        (coin_selection_sub_path, PUBSUB_TOPIC_COIN_SELECTION_SIGNALS),
        (order_book_sub_path, PUBSUB_TOPIC_ORDER_BOOK_SIGNALS)
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

    # 同時訂閱兩個 Topic
    futures = []
    futures.append(subscriber.subscribe(coin_selection_sub_path, callback=process_coin_selection_signal))
    futures.append(subscriber.subscribe(order_book_sub_path, callback=process_order_book_signal))
    
    logging.info(f"Listening for signals on {PUBSUB_TOPIC_COIN_SELECTION_SIGNALS} and {PUBSUB_TOPIC_ORDER_BOOK_SIGNALS}...")

    try:
        # 阻塞主線程直到訂閱結束，或者可以設置一個循環來保持服務運行
        for future in futures:
            future.result() # 這裡可能會阻塞，如果一個訂閱結束，其他也會受影響
        # 更健壯的方式是使用 threading.Thread 來處理多個訂閱，並在主線程中保持服務運行
        # 或者 Cloud Run 會自動處理多個 Pub/Sub 觸發器
    except TimeoutError:
        for future in futures:
            future.cancel()
            future.result()
        logging.warning("Pub/Sub subscription timed out.")
    except Exception as e:
        for future in futures:
            future.cancel()
            future.result()
        logging.error(f"Error in Pub/Sub subscription for strategy module: {e}", exc_info=True)
    finally:
        subscriber.close()
        logging.info("Multi-Strategy Decision Module stopped.")

if __name__ == '__main__':
    # 由於 Cloud Run 觸發器是針對單個事件處理的，
    # 在實際部署時，Cloud Run 會為每個收到的 Pub/Sub 訊息啟動一個新的實例。
    # 這裡的 `main` 函數主要用於定義服務行為，而不是無限循環。
    # 如果這個模組被 Pub/Sub 消息觸發，它只會處理那條消息，然後退出。
    # 持久化的狀態（last_coin_selection_signals 等）需要外部資料庫支持。
    #
    # 但為了讓它在本地運行時能持續監聽，我們保留了訂閱邏輯。
    # 在 Cloud Run 部署時，每個 Pub/Sub 消息觸發的 Cloud Run 實例都會執行 main()。
    # 如果要保持狀態，請使用 Firestore 或 Redis。
    main()


