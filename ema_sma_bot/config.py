"""從環境變數讀取設定。"""
import os
from dataclasses import dataclass

from .strategy import StrategyParams


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    api_key: str = ""
    api_secret: str = ""
    market: str = "spot"            # spot | futures (USDⓈ-M 永續)
    testnet: bool = True
    base_url: str = ""              # 自訂 API 網址（留空則依 market/testnet 自動選擇）
    dry_run: bool = True            # True 時只記錄訂單，不真正下單
    symbol: str = "BTCUSDT"
    interval: str = "4h"
    ema_period: int = 150
    sma_period: int = 150
    allow_short: bool = False       # 僅 futures 有效
    position_pct: float = 0.95      # 每次進場使用可用 USDT 的比例
    leverage: int = 1               # 僅 futures 有效
    kline_limit: int = 1000         # 抓取 K 線數量（需遠大於均線週期以讓 EMA 收斂）
    close_delay_sec: int = 5        # K 線收盤後延遲幾秒再抓資料
    state_file: str = "ema_sma_bot_state.json"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def strategy_params(self) -> StrategyParams:
        return StrategyParams(
            ema_period=self.ema_period,
            sma_period=self.sma_period,
            allow_short=self.allow_short and self.market == "futures",
        )

    def validate(self) -> None:
        if self.market not in ("spot", "futures"):
            raise ValueError("MARKET 必須是 spot 或 futures")
        if not 0 < self.position_pct <= 1:
            raise ValueError("POSITION_PCT 必須介於 0 與 1 之間")
        if self.leverage < 1:
            raise ValueError("LEVERAGE 必須 >= 1")
        if self.kline_limit < max(self.ema_period, self.sma_period) + 2:
            raise ValueError("KLINE_LIMIT 必須大於均線週期")
        if not self.dry_run and (not self.api_key or not self.api_secret):
            raise ValueError("實盤模式 (DRY_RUN=false) 需要 BINANCE_API_KEY 與 BINANCE_API_SECRET")


def load_config() -> Config:
    cfg = Config(
        api_key=os.environ.get("BINANCE_API_KEY", ""),
        api_secret=os.environ.get("BINANCE_API_SECRET", ""),
        market=os.environ.get("MARKET", "spot").strip().lower(),
        testnet=_env_bool("BINANCE_TESTNET", True),
        base_url=os.environ.get("BINANCE_BASE_URL", ""),
        dry_run=_env_bool("DRY_RUN", True),
        symbol=os.environ.get("SYMBOL", "BTCUSDT").strip().upper(),
        interval=os.environ.get("INTERVAL", "4h"),
        ema_period=int(os.environ.get("EMA_PERIOD", "150")),
        sma_period=int(os.environ.get("SMA_PERIOD", "150")),
        allow_short=_env_bool("ALLOW_SHORT", False),
        position_pct=float(os.environ.get("POSITION_PCT", "0.95")),
        leverage=int(os.environ.get("LEVERAGE", "1")),
        kline_limit=int(os.environ.get("KLINE_LIMIT", "1000")),
        close_delay_sec=int(os.environ.get("CLOSE_DELAY_SEC", "5")),
        state_file=os.environ.get("STATE_FILE", "ema_sma_bot_state.json"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    cfg.validate()
    return cfg
