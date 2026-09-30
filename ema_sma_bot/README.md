# 幣安 4H EMA150 / SMA150 自動交易系統

獨立運作的趨勢跟隨交易機器人：每根 **4 小時 K 線收盤** 後計算 EMA150 與 SMA150，
依規則自動調整幣安帳戶部位。支援 **現貨（只做多）** 與 **USDⓈ-M 永續合約（可多空）**。

## 策略規則

以「已收盤」K 線的收盤價判斷（不使用未收盤的 K 線，避免訊號重繪）：

| 動作 | 條件 |
| --- | --- |
| 做多進場 | close > EMA150 **且** close > SMA150 **且** EMA150 > SMA150 |
| 做多出場 | close < EMA150 **且** close < SMA150（跌破兩條均線） |
| 做空進場（合約 + `ALLOW_SHORT=true`） | close < EMA150 **且** close < SMA150 **且** EMA150 < SMA150 |
| 做空出場 | close > EMA150 **且** close > SMA150 |

- 價格位於兩條均線之間時維持原部位，減少在均線附近被來回洗單。
- 多單出場時若同時符合做空條件，會直接反手做空（反之亦然）。
- EMA 算法與 TradingView / 幣安圖表一致（以前 150 根 SMA 為種子）。預設抓 1000 根 K 線讓 EMA 收斂。

## 快速開始

```bash
pip install -r ema_sma_bot/requirements.txt
cp ema_sma_bot/.env.example .env      # 填入設定
set -a; source .env; set +a

# 1. 查看目前指標與訊號（用公開行情，不需 API key）
python -m ema_sma_bot signal

# 2. 回測近 3 年
python -m ema_sma_bot backtest --days 1095 --show-trades

# 3. 模擬執行（DRY_RUN=true，只記錄不下單）
python -m ema_sma_bot live

# 4. 測試網實際下單：DRY_RUN=false、BINANCE_TESTNET=true，並填入測試網 API key
# 5. 正式網：確認以上都沒問題後，才設 BINANCE_TESTNET=false
```

`once` 子指令只處理最新一根 K 線後結束，適合搭配 cron / Cloud Scheduler（例如每 4 小時的第 1 分鐘執行）：

```cron
1 0,4,8,12,16,20 * * * cd /path/to/Bn-trading && python -m ema_sma_bot once
```

### Docker

```bash
docker build -f ema_sma_bot/Dockerfile -t ema-sma-bot .
docker run -d --restart=always --env-file .env -v $(pwd)/data:/data ema-sma-bot
```

## 設定（環境變數）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | – | API 金鑰（實盤必填）。請只開「讀取 + 交易」權限，**不要開提領**，並綁定 IP 白名單 |
| `MARKET` | `spot` | `spot` 或 `futures` |
| `BINANCE_TESTNET` | `true` | 是否使用測試網 |
| `BINANCE_BASE_URL` | – | 自訂 API 網址（例如合約 demo 環境） |
| `DRY_RUN` | `true` | `true` 時只記錄、不下單 |
| `SYMBOL` | `BTCUSDT` | 交易對 |
| `INTERVAL` | `4h` | K 線週期 |
| `EMA_PERIOD` / `SMA_PERIOD` | `150` | 均線週期 |
| `POSITION_PCT` | `0.95` | 進場時使用可用 USDT 的比例 |
| `ALLOW_SHORT` | `false` | 允許做空（僅合約） |
| `LEVERAGE` | `1` | 槓桿（僅合約） |
| `KLINE_LIMIT` | `1000` | 計算指標用的 K 線數 |
| `CLOSE_DELAY_SEC` | `5` | K 線收盤後延遲幾秒再抓資料 |
| `STATE_FILE` | `ema_sma_bot_state.json` | 記錄已處理 K 線，重啟不會重複下單 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | – | 選用：交易與錯誤通知 |

## 運作細節與注意事項

- **部位以交易所實際持倉為準**：每次決策前查詢帳戶，因此程式重啟或手動調整後都能正確對齊。
  - 現貨：帳上持有的基礎幣（如 BTC）價值 ≥ 最小下單金額即視為持多單，出場時會**全部賣出**。
    請用**專用子帳戶**執行，避免賣掉你手動持有的幣。
  - 合約：使用單向持倉模式（One-way），平倉時送出 `reduceOnly` 市價單。
- 現貨買入使用 `quoteOrderQty`（以 USDT 金額下單），賣出與合約數量會依交易所 `stepSize` 無條件捨去。
- 下單請求失敗不會自動重送（避免重複成交）；下次循環會重新查詢實際部位再決定。
- 本策略**沒有盤中停損**，只在 4H 收盤時判斷。使用槓桿時請特別注意極端行情的風險。
- 回測以「訊號 K 線的下一根開盤價」成交，預設單邊手續費 0.1%，未計滑價與資金費率。

## 測試

```bash
pip install pytest
python -m pytest tests
```

> 本程式僅供研究與學習，不構成投資建議。加密貨幣交易風險極高，請先以測試網與小額資金驗證。
