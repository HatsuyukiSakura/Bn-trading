# 幣安 U 本位永續合約 4H EMA150 自動交易系統

依《幣安 U 本位永續合約 4H EMA150 趨勢策略：Windows 本地自動交易系統研究與實作報告》實作，
另外加上**盤中停損**。

## 策略規則

| 項目 | 規則 |
| --- | --- |
| 訊號 | 4H 已收盤 K 棒：close > EMA150 → 做多；close < EMA150 → 平倉；相等 → 維持（`SIGNAL_MODE=ema`） |
| 方向 | 純做多（出現非預期空單會自動以 reduceOnly 平掉） |
| 標的 | BTC、ETH、SOL、XRP、DOGE、BNB、ADA、LINK、AVAX、LTC（USDT 永續），等權 1/N |
| 槓桿 | 有效槓桿 ≤ 1 倍（總名目 ≤ 權益）；交易所端設 2 倍只當保證金緩衝；全倉、單向持倉、關閉聯合保證金 |
| 再平衡 | 部位偏離目標 ±25% 以上才調整 |
| **盤中停損** | 進場後立即在交易所掛 `STOP_MARKET` 條件單（Algo Order，`closePosition=true`），觸發價 = 進場均價 × (1 − 10%)，預設以**標記價**觸發，避免被成交價插針掃掉 |
| 停損後 | 該標的上鎖，須等收盤跌破 EMA150（策略出場訊號）後才解鎖，之後再次站上 EMA150 才重新進場 |
| 熔斷 | 權益自高點回撤 45% → 全部平倉、停機，需人工 `reset-halt` |
| 暫停開倉 | 24 小時內權益下跌超過 20% → 暫停新開倉 |
| 異常行情 | 標記價與收盤價偏離 > 2% 延後進場；K 棒缺漏時不動作（絕不把缺資料當成平倉訊號） |

> `SIGNAL_MODE=ema_sma` 可改用 EMA150 + SMA150 雙均線規則（站上雙線且 EMA > SMA 進場、跌破雙線出場）。

### 停損的雙重保護

1. **交易所條件單**：`POST /fapi/v1/algoOrder`（2025 年底起條件單改走 Algo Order 服務，送 `/fapi/v1/order` 會回 `-4120`）。
   每次對帳（每 5 分鐘）確認只有一張觸發價正確的停損單；加碼使均價改變時自動換單；部位出場後取消。
   只處理 `clientAlgoId` 以 `sl-` 開頭的單，不會動到你手動掛的單。
2. **軟體備援**：對帳時若標記價已低於停損價但部位仍在（條件單失效），直接 reduceOnly 市價平倉。

## 系統設計（期望狀態收斂器）

每 60 秒檢查一次；有新的已收盤 4H K 棒（收盤後 20 秒）或距上次對帳滿 5 分鐘，就執行一次對帳：

1. 讀取權益 → 熔斷 / 24h 暫停檢查
2. 每檔：抓永續 K 線（只取 `closeTime < 伺服器時間`，並確認最後一根就是上一個 4H 區間）→ 算 EMA150
3. 與交易所**實際持倉**比對 → 下市價單補齊差額 → 同步停損條件單

休眠、斷網、重開機或崩潰都只會讓收斂晚一點，不會重複下單：

- `newClientOrderId` 由「標的、K 棒時間、方向、序號」決定，送單前先查詢是否已存在
- 下單遇到 503「Unknown error」或網路中斷時**只查詢、不重送**
- `-1021` 自動校時；429 依 `Retry-After` 等待；418 / `-2015` 通知並暫停 30 分鐘

本地 SQLite（`state.db`）只存權益高點、熔斷旗標、停損鎖與下單紀錄；持倉一律以交易所為準。

## 使用方式

```bash
pip install -r ema_sma_bot/requirements.txt
cp ema_sma_bot/.env.example .env        # 填入測試網金鑰

python -m ema_sma_bot signal             # 各標的目前訊號（公開行情，不需金鑰）
python -m ema_sma_bot backtest --start 2021-01-01 --per-symbol
python -m ema_sma_bot backtest --stop-grid 0,0.05,0.08,0.1,0.15,0.2   # 比較停損幅度
python -m ema_sma_bot once               # 執行一次對帳
python -m ema_sma_bot live               # 常駐
python -m ema_sma_bot reset-halt         # 熔斷後人工解除
```

回測使用**永續合約 K 線 + `/fapi/v1/fundingRate` 實際資金費率**，手續費預設 0.05%（taker），
下載的資料會快取在 `data/`。停損以 K 線最低價近似標記價觸發，跳空時以開盤價成交。
各標的獨立計算後加總（實盤的 ±25% 再平衡未模擬）。

**停損幅度請先用 `--stop-grid` 回測再決定。** 太緊的停損在 4H 趨勢策略裡容易被正常波動掃出場，
而且停損後要等跌破 EMA 才會重新進場，可能錯過後續行情。

## Windows 常駐（NSSM）

```text
nssm install BinanceEMA "C:\Python311\python.exe" "-m ema_sma_bot live"
nssm set BinanceEMA AppDirectory "C:\bot\Bn-trading"
nssm set BinanceEMA AppStdout "C:\bot\Bn-trading\logs\stdout.log"
nssm set BinanceEMA AppStderr "C:\bot\Bn-trading\logs\stderr.log"
nssm set BinanceEMA AppRotateFiles 1
nssm set BinanceEMA AppExit Default Restart
nssm set BinanceEMA AppRestartDelay 10000
nssm set BinanceEMA Start SERVICE_DELAYED_AUTO_START
nssm start BinanceEMA
```

- 關閉休眠：`powercfg /change standby-timeout-ac 0`、`powercfg /h off`
- 時間同步：`w32tm /config /manualpeerlist:"time.stdtime.gov.tw,0x8 time.google.com,0x8" /syncfromflags:manual /update` 後 `w32tm /resync`
- 日誌在 `logs/bot.log`（每日輪替，保留 60 天）

也可用 Docker：`docker build -f ema_sma_bot/Dockerfile -t ema-bot .`，
`docker run -d --restart=always --env-file .env -v %cd%/data:/data ema-bot`。

## 上線步驟（報告第十一章）

1. **回測補強**：用永續 K 線 + 實際資金費率重跑，並用 `--stop-grid` 決定停損幅度
2. **測試網至少 2 週**：確認訊號與回測一致、精度 / reduceOnly / 停損單正常、斷網與重開機演練、重複執行不重複下單
3. **小資金實盤 4–8 週**：預定資金的 5–10%，核對滑價、資金費與手續費
4. **正式運行**：分 2–3 次加碼

## 測試

```bash
pip install pytest
python -m pytest tests
```

> 本程式僅供研究與學習，不構成投資建議。加密貨幣合約交易風險極高；台灣《虛擬資產服務法》
> 目前不含衍生品業務類別，幣安亦未在台完成洗錢防制登記，請自行評估法規風險。
