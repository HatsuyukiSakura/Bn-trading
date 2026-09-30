"""
實盤 / 模擬執行器：每根 4H K 線收盤後計算訊號，並將帳戶部位調整到目標部位。

- 部位以交易所實際持倉為準（重啟後不會重複下單）。
- DRY_RUN=true 時只記錄會下的單，不送出訂單；此時用狀態檔模擬部位。
"""
import json
import logging
import os
import time
from typing import Optional

from .binance_client import BinanceClient, floor_to_step, format_decimal
from .config import Config
from .notifier import Notifier
from .strategy import FLAT, LONG, SHORT, add_indicators, next_position

log = logging.getLogger(__name__)

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}
POSITION_NAMES = {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}


class Trader:
    def __init__(self, cfg: Config, client: BinanceClient, notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.client = client
        self.notifier = notifier or Notifier()
        self.params = cfg.strategy_params
        self.state = self._load_state()
        self._filters: Optional[dict] = None

    # ------------------------------------------------------------------ 狀態檔
    def _load_state(self) -> dict:
        if os.path.exists(self.cfg.state_file):
            try:
                with open(self.cfg.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as exc:
                log.warning("讀取狀態檔失敗，重新開始：%s", exc)
        return {"last_bar_open_time": None, "paper_position": FLAT}

    def _save_state(self) -> None:
        tmp = f"{self.cfg.state_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.cfg.state_file)

    @property
    def filters(self) -> dict:
        if self._filters is None:
            self._filters = self.client.symbol_filters(self.cfg.symbol)
        return self._filters

    # ------------------------------------------------------------------ 部位
    def current_position(self, price: float) -> int:
        if self.cfg.dry_run:
            return int(self.state.get("paper_position", FLAT))
        if self.cfg.market == "spot":
            base_qty = self.client.asset_balance(self.filters["base_asset"])
            # 低於最小下單金額的零頭視為空手
            notional = base_qty * price
            min_notional = max(self.filters["min_notional"], 1.0)
            return LONG if notional >= min_notional else FLAT
        amt = self.client.futures_position_amt(self.cfg.symbol)
        min_qty = self.filters["min_qty"]
        if amt >= min_qty:
            return LONG
        if amt <= -min_qty:
            return SHORT
        return FLAT

    # ------------------------------------------------------------------ 下單
    def _order(self, side: str, **kwargs) -> Optional[dict]:
        desc = f"{side} {self.cfg.symbol} {kwargs}"
        if self.cfg.dry_run:
            self.notifier.send(f"[DRY RUN] 市價單 {desc}")
            return None
        order = self.client.market_order(self.cfg.symbol, side, **kwargs)
        self.notifier.send(f"[LIVE] 已下單 {desc} → orderId={order.get('orderId')} status={order.get('status')}")
        return order

    def _close_position(self, current: int, price: float) -> None:
        if current == FLAT:
            return
        if self.cfg.market == "spot":
            qty = self.client.asset_balance(self.filters["base_asset"]) if not self.cfg.dry_run else 0.0
            qty_d = floor_to_step(qty, self.filters["step"])
            if self.cfg.dry_run:
                self._order("SELL", quantity="ALL")
                return
            if float(qty_d) * price < self.filters["min_notional"] or qty_d <= 0:
                log.warning("可賣數量 %s 低於最小下單金額，略過", qty_d)
                return
            self._order("SELL", quantity=format_decimal(qty_d))
            return

        # futures
        if self.cfg.dry_run:
            self._order("SELL" if current == LONG else "BUY", quantity="ALL", reduce_only=True)
            return
        amt = abs(self.client.futures_position_amt(self.cfg.symbol))
        qty_d = floor_to_step(amt, self.filters["step"])
        if qty_d <= 0:
            return
        side = "SELL" if current == LONG else "BUY"
        self._order(side, quantity=format_decimal(qty_d), reduce_only=True)

    def _open_position(self, target: int, price: float) -> None:
        quote = self.filters["quote_asset"]
        balance = self.client.asset_balance(quote) if not self.cfg.dry_run else 0.0
        if self.cfg.dry_run:
            self._order("BUY" if target == LONG else "SELL",
                        quote_pct=self.cfg.position_pct, leverage=self.cfg.leverage)
            return

        budget = balance * self.cfg.position_pct
        if self.cfg.market == "spot":
            if budget < self.filters["min_notional"]:
                log.warning("可用 %s 餘額 %.4f 不足以進場", quote, balance)
                return
            # quoteOrderQty 以 USDT 金額買入，避免自行處理數量精度
            self._order("BUY", quote_qty=f"{budget:.2f}")
            return

        qty_d = floor_to_step(budget * self.cfg.leverage / price, self.filters["step"])
        if qty_d <= 0 or float(qty_d) < self.filters["min_qty"] \
                or float(qty_d) * price < self.filters["min_notional"]:
            log.warning("可用 %s 餘額 %.4f 不足以進場", quote, balance)
            return
        self._order("BUY" if target == LONG else "SELL", quantity=format_decimal(qty_d))

    # ------------------------------------------------------------------ 主流程
    def step(self) -> Optional[int]:
        """處理最新一根已收盤 K 線。回傳目標部位；若這根已處理過則回傳 None。"""
        df = self.client.closed_klines(self.cfg.symbol, self.cfg.interval, self.cfg.kline_limit)
        if len(df) < max(self.params.ema_period, self.params.sma_period):
            raise RuntimeError(f"K 線數量不足：{len(df)}")

        bar = add_indicators(df, self.params).iloc[-1]
        bar_open = int(bar["open_time"])
        if self.state.get("last_bar_open_time") == bar_open:
            return None

        price = float(bar["close"])
        current = self.current_position(price)
        target = next_position(current, price, bar["ema"], bar["sma"], self.params.allow_short)

        log.info("%s %s 收盤 close=%.4f EMA%d=%.4f SMA%d=%.4f 目前=%s 目標=%s",
                 self.cfg.symbol, self.cfg.interval, price, self.params.ema_period, bar["ema"],
                 self.params.sma_period, bar["sma"], POSITION_NAMES[current], POSITION_NAMES[target])

        if target != current:
            self.notifier.send(
                f"{self.cfg.symbol} 訊號：{POSITION_NAMES[current]} → {POSITION_NAMES[target]}\n"
                f"close={price:.4f} EMA={bar['ema']:.4f} SMA={bar['sma']:.4f}")
            self._close_position(current, price)
            if target != FLAT:
                self._open_position(target, price)

        self.state["last_bar_open_time"] = bar_open
        self.state["paper_position"] = target
        self._save_state()
        return target

    def prepare_futures(self) -> None:
        """設定保證金模式與槓桿（有持倉時幣安不允許切換保證金模式，失敗只記錄警告）。"""
        try:
            self.client.set_margin_type(self.cfg.symbol, self.cfg.margin_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("設定保證金模式 %s 失敗：%s", self.cfg.margin_type, exc)
        self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)

    def seconds_until_next_close(self) -> float:
        interval_ms = INTERVAL_MS[self.cfg.interval]
        now = self.client.server_time()
        next_close = (now // interval_ms + 1) * interval_ms
        return (next_close - now) / 1000 + self.cfg.close_delay_sec

    def run_forever(self) -> None:
        mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
        net = "TESTNET" if self.cfg.testnet else "MAINNET"
        self.notifier.send(f"EMA{self.params.ema_period}/SMA{self.params.sma_period} 機器人啟動："
                           f"{self.cfg.symbol} {self.cfg.interval} {self.cfg.market} {mode} {net}")
        self.client.sync_time()
        if self.cfg.market == "futures" and not self.cfg.dry_run:
            self.prepare_futures()

        while True:
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - 常駐程式不應因單次錯誤停止
                log.exception("執行錯誤")
                self.notifier.send(f"⚠️ {self.cfg.symbol} 執行錯誤：{exc}")
                time.sleep(60)
                continue
            wait = self.seconds_until_next_close()
            log.info("等待 %.0f 秒至下一根 K 線收盤", wait)
            time.sleep(max(wait, 1))
