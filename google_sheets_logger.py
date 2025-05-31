# google_sheets_logger.py

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# Google Sheets 權限授權
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("gcp_service_account.json", scope)

# Google Sheets 設定
SPREADSHEET_NAME = "Trade_Records"
SHEET_NAME = "Sheet1"

def log_trade(symbol, side, qty, entry_price, exit_price=None, pnl=None, strategy=""):
    try:
        gc = gspread.authorize(creds)
        sheet = gc.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

        row = [
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            symbol.upper(),
            side,
            float(qty),
            float(entry_price),
            float(exit_price) if exit_price else "",
            float(pnl) if pnl else "",
            strategy,
        ]
        sheet.append_row(row)
        print(f"[紀錄] 已寫入 Google Sheets：{row}")
    except Exception as e:
        print(f"[紀錄錯誤] 無法寫入 Google Sheets：{e}")

