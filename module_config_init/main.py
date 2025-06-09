import os
import json
from google.cloud import secretmanager_v1beta1 as secretmanager
from google.cloud import firestore
import logging

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 初始化 Secret Manager 客戶端
secret_client = secretmanager.SecretManagerServiceClient()

# 從環境變數獲取 GCP 專案 ID
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
if not GCP_PROJECT_ID:
    logging.error("GCP_PROJECT_ID environment variable not set.")
    exit(1)

def get_secret(secret_name_env_var):
    """
    從 Secret Manager 獲取秘密值。
    secret_name_env_var 應該是一個環境變數的名稱，該環境變數存儲了 Secret Manager 秘密的完整資源名稱。
    例如：BINANCE_API_KEY_SECRET_NAME = projects/YOUR_PROJECT_ID/secrets/BINANCE_API_KEY/versions/latest
    """
    secret_resource_name = os.environ.get(secret_name_env_var)
    if not secret_resource_name:
        logging.warning(f"Environment variable '{secret_name_env_var}' for secret not set.")
        return None

    try:
        response = secret_client.access_secret_version(name=secret_resource_name)
        secret_value = response.payload.data.decode("UTF-8")
        logging.info(f"Successfully accessed secret: {secret_name_env_var}")
        return secret_value
    except Exception as e:
        logging.error(f"Error accessing secret '{secret_resource_name}' via '{secret_name_env_var}': {e}")
        return None

def initialize_system_config():
    """
    初始化並返回所有系統配置。
    這個函數可以在其他模組中被調用以獲取配置。
    """
    config = {}

    logging.info("Initializing system configuration...")

    # --- 從 Secret Manager 獲取敏感資訊 ---
    config['BINANCE_API_KEY'] = get_secret("BINANCE_API_KEY_SECRET_NAME")
    config['BINANCE_SECRET_KEY'] = get_secret("BINANCE_SECRET_KEY_SECRET_NAME")
    config['TELEGRAM_BOT_TOKEN'] = get_secret("TELEGRAM_BOT_TOKEN_SECRET_NAME")
    config['TELEGRAM_CHAT_ID'] = get_secret("TELEGRAM_CHAT_ID_SECRET_NAME") # 可以是一個ID或多個ID的列表（json string）
    if config['TELEGRAM_CHAT_ID']:
        try:
            config['TELEGRAM_CHAT_ID'] = json.loads(config['TELEGRAM_CHAT_ID']) # 如果是JSON字串
        except json.JSONDecodeError:
            pass # 如果是單個ID，保持原樣

    # --- 從環境變數獲取非敏感配置 ---
    # 這些參數可以在部署 Cloud Run 服務時通過 --set-env-vars 設定
    config['TRADE_SYMBOL'] = os.environ.get("TRADE_SYMBOL", "BTCUSDT") # 預設交易對
    config['KLINE_INTERVAL'] = os.environ.get("KLINE_INTERVAL", "1m") # 預設K線間隔
    config['DEFAULT_RISK_PERCENTAGE'] = float(os.environ.get("DEFAULT_RISK_PERCENTAGE", "0.01")) # 預設單筆交易風險比例 (1%)

    # --- 初始化資料庫連接 ---
    try:
        config['FIRESTORE_DB'] = firestore.Client(project=GCP_PROJECT_ID)
        logging.info("Firestore client initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to initialize Firestore client: {e}")
        config['FIRESTORE_DB'] = None # 如果失敗，將其設為 None

    # 您可以在這裡初始化更多的服務，例如 Pub/Sub Publisher 客戶端等
    # from google.cloud import pubsub_v1
    # config['PUBSUB_PUBLISHER'] = pubsub_v1.PublisherClient()
    # config['PUBSUB_PROJECT_PATH'] = f"projects/{GCP_PROJECT_ID}"


    logging.info("System configuration loaded.")
    return config

# 由於這是配置模組，它可能不會作為一個獨立的 Cloud Run 服務持續運行
# 而是作為一個函數庫，在其他模組啟動時被 import 和調用。
# 但為了符合 Cloud Run 的部署規範，我們還是提供一個 __main__ 區塊。
if __name__ == "__main__":
    # 這個 main 函數主要用於測試配置獲取是否成功
    logging.info("Running module_config_init as a standalone service (for testing/initialization).")
    app_config = initialize_system_config()

    logging.info(f"Loaded Trade Symbol: {app_config.get('TRADE_SYMBOL')}")
    logging.info(f"Loaded KLine Interval: {app_config.get('KLINE_INTERVAL')}")
    logging.info(f"Loaded Default Risk Percentage: {app_config.get('DEFAULT_RISK_PERCENTAGE')}")

    # 注意：敏感資訊不應直接打印到日誌中
    # logging.info(f"Binance API Key (first 5 chars): {app_config.get('BINANCE_API_KEY', '')[:5]}...")

    if app_config.get('FIRESTORE_DB'):
        logging.info("Firestore DB connection established.")
    else:
        logging.warning("Firestore DB connection failed or not available.")

    logging.info("Configuration initialization complete.")

    # 實際應用中，這個模組可能不會作為一個獨立的 HTTP 服務運行，
    # 而是作為一個 library 被其他模組 import。
    # 如果您堅持將它部署為 Cloud Run 服務，它可能需要一個 HTTP 處理器。
    # from flask import Flask
    # app = Flask(__name__)
    # @app.route('/')
    # def hello_world():
    #     return 'Config init service running!'
    # if __name__ == '__main__':
    #     app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))



