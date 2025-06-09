import os
import json
import logging
from google.cloud import firestore
from google.cloud import pubsub_v1
import pandas as pd
# from sklearn.ensemble import RandomForestRegressor # 假設用於模型優化
# from sklearn.model_selection import train_test_split
# from joblib import dump, load # 用於保存和加載模型

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 從環境變數獲取 GCP 專案 ID 和 Pub/Sub Topic 名稱
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
# PUBSUB_TOPIC_TRADE_REPORTS = os.environ.get("PUBSUB_TOPIC_TRADE_REPORTS", "trade-reports") # 從此 Topic 獲取數據
PUBSUB_TOPIC_OPTIMIZATION_ALERTS = os.environ.get("PUBSUB_TOPIC_OPTIMIZATION_ALERTS", "optimization-alerts") # 發布優化警報

if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)

# 初始化 Firestore 客戶端
db = firestore.Client(project=GCP_PROJECT_ID)

# 初始化 Pub/Sub Publisher 客戶端
publisher = pubsub_v1.PublisherClient()
optimization_alerts_topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC_OPTIMIZATION_ALERTS)

# Firestore Collection 名稱
TRADE_REPORTS_COLLECTION = os.environ.get("TRADE_REPORTS_COLLECTION", "trade_reports")
STRATEGY_CONFIG_COLLECTION = os.environ.get("STRATEGY_CONFIG_COLLECTION", "strategy_config")
# MODEL_ARTIFACTS_BUCKET = os.environ.get("MODEL_ARTIFACTS_BUCKET", f"{GCP_PROJECT_ID}-model-artifacts") # GCS bucket for models

def fetch_trade_reports(days_ago=7):
    """
    從 Firestore 獲取最近的交易報告。
    在實際應用中，可能會過濾已完成的交易。
    """
    logging.info(f"Fetching trade reports from last {days_ago} days...")
    
    # 計算時間閾值
    from datetime import datetime, timedelta, timezone
    utc_now = datetime.now(timezone.utc)
    start_time = utc_now - timedelta(days=days_ago)
    
    reports = []
    try:
        query = db.collection(TRADE_REPORTS_COLLECTION).where('timestamp', '>=', int(start_time.timestamp() * 1000)).stream()
        for doc in query:
            reports.append(doc.to_dict())
        logging.info(f"Fetched {len(reports)} trade reports.")
    except Exception as e:
        logging.error(f"Error fetching trade reports from Firestore: {e}", exc_info=True)
    return pd.DataFrame(reports)


def evaluate_performance(trade_df):
    """
    評估交易表現。這裡只是一個簡化範例。
    """
    if trade_df.empty:
        logging.info("No trade data to evaluate performance.")
        return {"total_profit_loss": 0, "win_rate": 0, "num_trades": 0, "avg_profit_per_trade": 0}

    # 假設 trade_reports 包含 'executed_price', 'action', 'quantity_requested', 'status' 等
    # 實際需要根據 'executed_price' 和後續平倉價格計算 PnL
    
    # 簡化範例：假設所有成功的交易都帶來少量利潤，失敗的則虧損
    trade_df['pnl'] = trade_df.apply(lambda row: 
                                     (row['executed_price'] * row['executed_quantity'] * 0.005 if row['status'] == 'FILLED' and row['action'] == 'BUY' else 
                                      -row['executed_price'] * row['executed_quantity'] * 0.005 if row['status'] == 'FILLED' and row['action'] == 'SELL' else 0), axis=1) # 這裡簡化為固定百分比

    # 實際 PnL 計算需要結合平倉價格
    total_pnl = trade_df['pnl'].sum()
    win_trades = trade_df[trade_df['pnl'] > 0]
    num_trades = len(trade_df[trade_df['status'] == 'FILLED'])
    win_rate = len(win_trades) / num_trades if num_trades > 0 else 0
    avg_profit_per_trade = total_pnl / num_trades if num_trades > 0 else 0

    performance_metrics = {
        "total_profit_loss": total_pnl,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "avg_profit_per_trade": avg_profit_per_trade
    }
    logging.info(f"Performance Metrics: {performance_metrics}")
    return performance_metrics

def update_strategy_parameters(performance_metrics):
    """
    根據交易表現調整策略參數（例如，調整買賣信號閾值、風險比例）。
    這是一個基於簡單規則的自適應邏輯。
    """
    logging.info("Attempting to update strategy parameters...")
    
    # 從 Firestore 讀取當前策略配置
    try:
        strategy_doc_ref = db.collection(STRATEGY_CONFIG_COLLECTION).document("default_strategy")
        strategy_config = strategy_doc_ref.get().to_dict()
        if not strategy_config:
            strategy_config = {
                "BUY_SIGNAL_THRESHOLD": 0.5,
                "SELL_SIGNAL_THRESHOLD": -0.5,
                "DEFAULT_RISK_PER_TRADE": 0.01,
                "LAST_OPTIMIZED_AT": None
            }
            logging.warning("Strategy config not found in Firestore. Using default.")
        logging.info(f"Current strategy config: {strategy_config}")

    except Exception as e:
        logging.error(f"Error reading strategy config from Firestore: {e}")
        return

    # 根據表現調整參數 (非常簡化邏輯)
    # 如果虧損，則提高閾值，減少交易頻率/風險
    # 如果盈利，則可以適當放寬閾值，增加交易頻率/風險

    current_buy_threshold = strategy_config.get("BUY_SIGNAL_THRESHOLD", 0.5)
    current_sell_threshold = strategy_config.get("SELL_SIGNAL_THRESHOLD", -0.5)
    current_risk_per_trade = strategy_config.get("DEFAULT_RISK_PER_TRADE", 0.01)

    if performance_metrics['total_profit_loss'] < 0:
        logging.warning("Negative PnL detected. Adjusting parameters for caution.")
        new_buy_threshold = min(0.9, current_buy_threshold + 0.1) # 提高買入閾值
        new_sell_threshold = max(-0.9, current_sell_threshold - 0.1) # 降低賣出閾值
        new_risk_per_trade = max(0.005, current_risk_per_trade * 0.8) # 降低單筆風險
    elif performance_metrics['total_profit_loss'] > 0 and performance_metrics['win_rate'] > 0.55:
        logging.info("Positive PnL and good win rate. Adjusting parameters for potential expansion.")
        new_buy_threshold = max(0.1, current_buy_threshold - 0.05) # 降低買入閾值
        new_sell_threshold = min(-0.1, current_sell_threshold + 0.05) # 提高賣出閾值
        new_risk_per_trade = min(0.02, current_risk_per_trade * 1.1) # 增加單筆風險
    else:
        logging.info("Performance is neutral or needs more data. No parameter adjustment.")
        new_buy_threshold = current_buy_threshold
        new_sell_threshold = current_sell_threshold
        new_risk_per_trade = current_risk_per_trade

    updated_config = {
        "BUY_SIGNAL_THRESHOLD": round(new_buy_threshold, 2),
        "SELL_SIGNAL_THRESHOLD": round(new_sell_threshold, 2),
        "DEFAULT_RISK_PER_TRADE": round(new_risk_per_trade, 3),
        "LAST_OPTIMIZED_AT": firestore.SERVER_TIMESTAMP # 使用 Firestore 服務器時間戳
    }
    
    # 更新 Firestore 中的策略配置
    try:
        strategy_doc_ref.set(updated_config, merge=True) # merge=True 只更新指定字段
        logging.info(f"Strategy parameters updated in Firestore: {updated_config}")
        
        # 發布優化警報
        alert_message = {
            "type": "STRATEGY_OPTIMIZED",
            "old_config": strategy_config,
            "new_config": updated_config,
            "performance_metrics": performance_metrics,
            "timestamp": int(pd.Timestamp.now().timestamp() * 1000)
        }
        publisher.publish(optimization_alerts_topic_path, json.dumps(alert_message).encode('utf-8'))
        logging.info("Published strategy optimization alert.")

    except Exception as e:
        logging.error(f"Error updating strategy config in Firestore: {e}", exc_info=True)


def retrain_ai_model():
    """
    （概念性）重新訓練 AI 模型。
    這會涉及從資料庫獲取大量歷史數據，訓練模型，然後保存到 GCS。
    """
    logging.info("Initiating AI model retraining process (conceptual)...")
    
    # 這裡應該是：
    # 1. 從 BigQuery 或 Firestore 獲取大量的歷史 K 線、訂單簿、外部情緒數據等
    #    例如：historical_data_df = fetch_historical_data_for_model_training()
    # 2. 數據預處理和特徵工程
    #    X = historical_data_df[features]
    #    y = historical_data_df[target]
    # 3. 訓練模型
    #    model = RandomForestRegressor(...)
    #    model.fit(X_train, y_train)
    # 4. 評估模型性能
    # 5. 如果性能達標，將新模型保存到 Google Cloud Storage (GCS)
    #    dump(model, 'new_ai_model.joblib')
    #    # Upload to GCS:
    #    # from google.cloud import storage
    #    # storage_client = storage.Client()
    #    # bucket = storage_client.bucket(MODEL_ARTIFACTS_BUCKET)
    #    # blob = bucket.blob('ai_models/latest_model.joblib')
    #    # blob.upload_from_filename('new_ai_model.joblib')
    #    logging.info("New AI model trained and saved to GCS.")
    
    # 實際情況，重新訓練一個複雜的 AI 模型通常會是一個獨立的 Cloud Build 或 Vertex AI 作業，
    # 而不是直接在一個 Cloud Run 服務中執行。
    logging.info("AI model retraining process simulated. (Actual retraining would be external or a separate, more resource-intensive job).")

def main(event=None, context=None):
    """
    主函數，由 Cloud Scheduler 或 Pub/Sub 觸發。
    """
    logging.info("Auto Learning & Optimization Module triggered.")

    # 1. 獲取最近的交易表現數據
    trade_df = fetch_trade_reports(days_ago=int(os.environ.get("LOOKBACK_DAYS_FOR_OPTIMIZATION", "7")))
    
    # 2. 評估表現
    performance = evaluate_performance(trade_df)

    # 3. 根據表現調整策略參數
    update_strategy_parameters(performance)

    # 4. (選擇性) 重新訓練 AI 模型 - 這是一個耗時操作，可能需要獨立的服務或觸發
    # 只有當性能明顯下降，或者有足夠新數據時才執行
    # if performance['total_profit_loss'] < -100 or performance['win_rate'] < 0.4: # 簡單觸發條件
    #    retrain_ai_model()
    
    logging.info("Auto Learning & Optimization Module finished.")

if __name__ == '__main__':
    # 在本地運行用於測試
    main()


