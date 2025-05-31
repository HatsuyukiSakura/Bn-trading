# config.py

CONFIG = {
    "binance": {
        "api_key": "your_binance_api_key",
        "api_secret": "your_binance_api_secret"
    },
    "telegram": {
        "bot_token": "your_telegram_bot_token",
        "chat_id": "your_chat_id"
    },
    "risk": {
        "daily_loss_limit": -50,
        "trailing_stop_trigger": 0.05,
        "max_position_ratio": 0.05  # 每筆最多用 5% 資金
    },
    "scanner": {
        "top_n": 3,
        "score_threshold": 6.5,
        "scan_interval_sec": 60 * 60 * 4
    },
    "sheet": {
        "spreadsheet_name": "Trade_Records",
        "sheet_name": "Sheet1",
        "creds_file": "gcp_service_account.json"
    },
    "cloud": {
        "project_id": "your_gcp_project_id",
        "region": "asia-east1",
        "image": "gcr.io/your_gcp_project_id/trade-bot"
    }
}
