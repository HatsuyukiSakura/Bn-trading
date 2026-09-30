"""從環境變數（與 .env 檔）讀取設定。預設值依研究報告的建議。"""
import os
from dataclasses import dataclass, field

from .strategy import StrategyParams

DEFAULT_SYMBOLS = ("BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,"
                   "BNBUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,LTCUSDT")


def load_dotenv(path: str = ".env") -> None:
    """極簡 .env 讀取：KEY=VALUE，已存在的環境變數不覆蓋。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True                 # True → demo-fapi.binance.com
    base_url: str = ""                   # 自訂 REST 網址（留空自動選擇）
    dry_run: bool = False                # True 時只記錄將下的單，不送出
    symbols: list = field(default_factory=lambda: DEFAULT_SYMBOLS.split(","))
    interval: str = "4h"
    ema_period: int = 150
    sma_period: int = 150
    signal_mode: str = "ema"             # ema | ema_sma
    exchange_leverage: int = 2           # 交易所端槓桿，只當保證金緩衝
    margin_type: str = "CROSSED"
    max_gross_exposure: float = 1.0      # 總名目 ≤ 權益 × 此值（有效槓桿上限）
    rebal_band: float = 0.25             # 部位偏離目標超過 ±25% 才再平衡
    kill_switch_dd: float = 0.45         # 權益自高點回撤 45% → 全部平倉並停機
    daily_loss_pause: float = 0.20       # 24 小時內權益跌超過 20% → 暫停新開倉
    max_mark_deviation: float = 0.02     # 標記價與收盤價偏離 > 2% → 延後進場
    stop_loss_pct: float = 0.10          # 盤中停損：進場均價下方 10%；0 = 關閉
    stop_working_type: str = "MARK_PRICE"  # 停損觸發價格：MARK_PRICE | CONTRACT_PRICE
    kline_limit: int = 1000
    close_delay_sec: int = 20            # K 線收盤後等幾秒再判斷
    reconcile_every_sec: int = 300       # 對帳頻率
    db_path: str = "state.db"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def strategy_params(self) -> StrategyParams:
        return StrategyParams(ema_period=self.ema_period, sma_period=self.sma_period,
                              mode=self.signal_mode, stop_loss_pct=self.stop_loss_pct)

    def validate(self, need_keys: bool = True) -> None:
        if not self.symbols:
            raise ValueError("SYMBOLS 不可為空")
        if self.signal_mode not in ("ema", "ema_sma"):
            raise ValueError("SIGNAL_MODE 必須是 ema 或 ema_sma")
        if self.interval != "4h":
            raise ValueError("本策略只支援 INTERVAL=4h")
        if not 0 < self.max_gross_exposure <= 1.0:
            raise ValueError("MAX_GROSS_EXPOSURE 必須介於 0 與 1.0（有效槓桿不超過 1 倍）")
        if self.exchange_leverage < 1:
            raise ValueError("EXCHANGE_LEVERAGE 必須 >= 1")
        if self.margin_type not in ("CROSSED", "ISOLATED"):
            raise ValueError("MARGIN_TYPE 必須是 CROSSED 或 ISOLATED")
        if not 0 <= self.stop_loss_pct < 1:
            raise ValueError("STOP_LOSS_PCT 必須介於 0 與 1 之間")
        if self.stop_working_type not in ("MARK_PRICE", "CONTRACT_PRICE"):
            raise ValueError("STOP_WORKING_TYPE 必須是 MARK_PRICE 或 CONTRACT_PRICE")
        if not 0 < self.kill_switch_dd < 1 or not 0 < self.daily_loss_pause < 1:
            raise ValueError("KILL_SWITCH_DD / DAILY_LOSS_PAUSE 必須介於 0 與 1")
        if self.kline_limit < max(self.ema_period, self.sma_period) * 3:
            raise ValueError("KLINE_LIMIT 至少要是均線週期的 3 倍，讓 EMA 收斂")
        if need_keys and (not self.api_key or not self.api_secret):
            raise ValueError("需要 BINANCE_API_KEY 與 BINANCE_API_SECRET")


def load_config(env_file: str = ".env") -> Config:
    load_dotenv(env_file)
    e = os.environ.get
    return Config(
        api_key=e("BINANCE_API_KEY", ""),
        api_secret=e("BINANCE_API_SECRET", ""),
        testnet=_env_bool("BINANCE_TESTNET", True),
        base_url=e("BINANCE_BASE_URL", ""),
        dry_run=_env_bool("DRY_RUN", False),
        symbols=[s.strip().upper() for s in e("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()],
        interval=e("INTERVAL", "4h"),
        ema_period=int(e("EMA_PERIOD", "150")),
        sma_period=int(e("SMA_PERIOD", "150")),
        signal_mode=e("SIGNAL_MODE", "ema").strip().lower(),
        exchange_leverage=int(e("EXCHANGE_LEVERAGE", "2")),
        margin_type=e("MARGIN_TYPE", "CROSSED").strip().upper(),
        max_gross_exposure=float(e("MAX_GROSS_EXPOSURE", "1.0")),
        rebal_band=float(e("REBAL_BAND", "0.25")),
        kill_switch_dd=float(e("KILL_SWITCH_DD", "0.45")),
        daily_loss_pause=float(e("DAILY_LOSS_PAUSE", "0.20")),
        max_mark_deviation=float(e("MAX_MARK_DEVIATION", "0.02")),
        stop_loss_pct=float(e("STOP_LOSS_PCT", "0.10")),
        stop_working_type=e("STOP_WORKING_TYPE", "MARK_PRICE").strip().upper(),
        kline_limit=int(e("KLINE_LIMIT", "1000")),
        close_delay_sec=int(e("CLOSE_DELAY_SEC", "20")),
        reconcile_every_sec=int(e("RECONCILE_EVERY_SEC", "300")),
        db_path=e("DB_PATH", "state.db"),
        telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
    )
