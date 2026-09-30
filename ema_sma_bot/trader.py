"""
期望狀態收斂器（研究報告第六章）：每次執行都重新算出「最後一根已收盤 4H K 棒對應的
目標部位」，與交易所實際部位比對後補齊差額。休眠、斷網、重開機或崩潰只會讓收斂晚一點，
不會重複下單。

另加盤中停損：
- 持有多單時，在交易所掛 STOP_MARKET 條件單（Algo Order，closePosition=true，
  預設以標記價觸發），觸發價 = 進場均價 × (1 − STOP_LOSS_PCT)。
- 每次對帳也用標記價做一次軟體備援檢查，條件單失效時仍能平倉。
- 停損後該標的上鎖，須等策略本身出現出場訊號（收盤跌破 EMA150）後才解鎖，再次出現
  進場訊號才重新進場。
"""
import logging
import time
from decimal import Decimal
from typing import Optional

from .binance_client import BinanceError, FuturesClient, UnknownExecution, floor_to_step
from .config import Config
from .notifier import Notifier
from .store import Store
from .strategy import FLAT, LONG, add_indicators, desired_position, exit_signal, stop_price

log = logging.getLogger(__name__)

PERIOD_MS = 4 * 3600 * 1000
STOP_ID_PREFIX = "sl-"


class DataError(Exception):
    """K 線資料缺漏或不符預期：本輪不對該標的動作（絕不把缺資料當成空手訊號）。"""


def last_closed_bar_open(now_ms: int) -> int:
    return (now_ms // PERIOD_MS) * PERIOD_MS - PERIOD_MS


class Trader:
    def __init__(self, cfg: Config, client: FuturesClient, store: Store,
                 notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.notifier = notifier or Notifier()
        self.params = cfg.strategy_params
        self.filters: dict = {}
        self._filters_loaded_at = 0.0

    def notify(self, text: str) -> None:
        self.notifier.send(text)

    # ------------------------------------------------------------------ 資料
    def refresh_filters(self, force: bool = False) -> None:
        if force or not self.filters or time.time() - self._filters_loaded_at > 86400:
            self.filters = self.client.exchange_filters(self.cfg.symbols)
            self._filters_loaded_at = time.time()

    def closed_bars(self, symbol: str):
        df = self.client.klines(symbol, "4h", limit=self.cfg.kline_limit + 1)
        now = self.client.now_ms()
        df = df[df["close_time"] < now].reset_index(drop=True)
        if df.empty or int(df["open_time"].iloc[-1]) != last_closed_bar_open(now):
            raise DataError(f"{symbol} 最新已收盤 K 棒不符預期，本輪略過")
        if len(df) < max(self.params.ema_period, self.params.sma_period) * 3:
            raise DataError(f"{symbol} 歷史資料不足（{len(df)} 根）")
        return df

    def evaluate(self, symbol: str, holding: bool) -> dict:
        bar = add_indicators(self.closed_bars(symbol), self.params).iloc[-1]
        close, e, s = float(bar["close"]), float(bar["ema"]), float(bar["sma"])
        return {
            "desired": desired_position(LONG if holding else FLAT, close, e, s, self.params.mode),
            "exit": exit_signal(close, e, s, self.params.mode),
            "bar_open": int(bar["open_time"]),
            "close": close, "ema": e, "sma": s,
        }

    # ------------------------------------------------------------------ 下單
    def place_market(self, symbol: str, side: str, qty: Decimal, bar_open: int,
                     reduce_only: bool) -> Optional[dict]:
        """冪等市價單：clientOrderId 由標的、K 棒時間、方向、序號決定，送出前先查詢。"""
        if self.cfg.dry_run:
            self.notify(f"[DRY RUN] {symbol} {side} {qty}{' reduceOnly' if reduce_only else ''}")
            return None
        seq_key = f"seq:{symbol}:{bar_open}"
        seq = int(self.store.get(seq_key, "0"))
        cid = f"e150-{symbol}-{bar_open // 1000}-{side[0]}{seq}"[:36]
        existing = self.client.query_order(symbol, cid)
        if existing:
            self.store.set(seq_key, seq + 1)
            return existing
        try:
            resp = self.client.market_order(symbol, side, qty, cid, reduce_only=reduce_only)
            status = resp.get("status")
        except UnknownExecution as exc:
            time.sleep(3)
            resp = self.client.query_order(symbol, cid)
            status = resp.get("status") if resp else "UNKNOWN_NOT_FOUND"
            self.notify(f"{symbol} 下單狀態未知，查詢結果：{status}（{exc}）")
        self.store.record_order(cid, symbol, side, qty, status, self.client.now_ms(), resp)
        self.store.set(seq_key, seq + 1)
        self.notify(f"{symbol} {side} {qty} → {status}")
        return resp

    # ------------------------------------------------------------------ 停損條件單
    def _our_stops(self, symbol: str) -> list:
        return [o for o in self.client.open_algo_orders(symbol)
                if str(o.get("clientAlgoId", "")).startswith(STOP_ID_PREFIX)]

    def cancel_stops(self, symbol: str) -> None:
        if self.cfg.stop_loss_pct <= 0:
            return
        for o in self._our_stops(symbol):
            if self.cfg.dry_run:
                self.notify(f"[DRY RUN] 取消 {symbol} 停損單 {o.get('algoId')}")
                continue
            try:
                self.client.cancel_algo_order(o["algoId"])
            except BinanceError as exc:
                log.warning("取消 %s 停損單失敗：%s", symbol, exc)

    def stop_trigger(self, symbol: str, entry: Decimal) -> Decimal:
        raw = Decimal(str(stop_price(float(entry), self.cfg.stop_loss_pct)))
        return floor_to_step(raw, self.filters[symbol]["tick"])

    def sync_stop(self, symbol: str, entry: Decimal) -> None:
        """確保交易所上有且只有一張觸發價正確的停損條件單。"""
        if self.cfg.stop_loss_pct <= 0 or entry <= 0:
            return
        trigger = self.stop_trigger(symbol, entry)
        stops = self._our_stops(symbol)
        keep = next((o for o in stops if Decimal(str(o.get("triggerPrice", "0"))) == trigger), None)
        for o in stops:
            if o is not keep and not self.cfg.dry_run:
                try:
                    self.client.cancel_algo_order(o["algoId"])
                except BinanceError as exc:
                    log.warning("取消舊停損單失敗：%s", exc)
        if keep:
            return
        if self.cfg.dry_run:
            self.notify(f"[DRY RUN] {symbol} 掛停損 STOP_MARKET @ {trigger}")
            return
        cid = f"{STOP_ID_PREFIX}{symbol}-{self.client.now_ms() // 1000}"[:36]
        try:
            self.client.place_stop_market(symbol, trigger, cid, self.cfg.stop_working_type)
            self.notify(f"{symbol} 停損單已設定：{self.cfg.stop_working_type} ≤ {trigger}"
                        f"（進場均價 {entry}，-{self.cfg.stop_loss_pct:.1%}）")
        except UnknownExecution as exc:
            self.notify(f"{symbol} 停損單狀態未知（{exc}），下次對帳會再確認")

    # ------------------------------------------------------------------ 風控
    def kill_switch(self, equity: Decimal) -> bool:
        peak = Decimal(self.store.get("peak_equity", str(equity)))
        if equity > peak:
            peak = equity
        self.store.set("peak_equity", peak)
        if self.store.get("halted") == "1":
            return True
        if equity < peak * (1 - Decimal(str(self.cfg.kill_switch_dd))):
            self.store.set("halted", "1")
            self.notify(f"⚠️ 熔斷：權益 {equity:.2f}，高點 {peak:.2f}，全部平倉並停機（需人工解除）")
            bar = last_closed_bar_open(self.client.now_ms())
            for sym, p in self.client.positions(self.cfg.symbols).items():
                side = "SELL" if p["amt"] > 0 else "BUY"
                self.place_market(sym, side, abs(p["amt"]), bar, True)
                self.cancel_stops(sym)
            return True
        return False

    def entries_paused(self, equity: Decimal) -> bool:
        since = self.client.now_ms() - 24 * 3600 * 1000
        history = [Decimal(v) for _, v in self.store.equity_since(since)]
        high = max(history + [equity])
        paused = equity < high * (1 - Decimal(str(self.cfg.daily_loss_pause)))
        if paused != (self.store.get("entries_paused") == "1"):
            self.store.set("entries_paused", "1" if paused else "0")
            self.notify(f"⚠️ 24 小時內權益由 {high:.2f} 跌到 {equity:.2f}，暫停新開倉" if paused
                        else "24 小時跌幅恢復，恢復新開倉")
        return paused

    # ------------------------------------------------------------------ 對帳
    def reconcile_cycle(self) -> None:
        self.refresh_filters()
        equity = self.client.account_equity()
        if self.kill_switch(equity):
            return
        paused = self.entries_paused(equity)
        self.store.record_equity(self.client.now_ms(), equity)
        positions = self.client.positions(self.cfg.symbols)
        per_symbol = equity * Decimal(str(self.cfg.max_gross_exposure)) / len(self.cfg.symbols)

        for sym in self.cfg.symbols:
            try:
                self.reconcile_symbol(sym, positions.get(sym), per_symbol, paused)
            except DataError as exc:
                log.warning(str(exc))
            except BinanceError as exc:
                if exc.status == 418 or exc.code == -2015:
                    raise
                log.exception("%s 對帳失敗", sym)
                self.notify(f"{sym} 對帳失敗：{exc}")

        gross = sum(abs(p["amt"]) * self.client.mark_price(s)
                    for s, p in self.client.positions(self.cfg.symbols).items())
        if gross > equity * Decimal(str(self.cfg.max_gross_exposure)) * Decimal("1.1"):
            self.notify(f"⚠️ 總名目 {gross:.0f} 超過上限（權益 {equity:.0f}）")

    def reconcile_symbol(self, sym: str, pos: Optional[dict], per_symbol: Decimal, paused: bool) -> None:
        f = self.filters[sym]
        if f["status"] != "TRADING":
            self.notify(f"{sym} 狀態 {f['status']}，略過")
            return
        amt = pos["amt"] if pos else Decimal(0)
        entry = pos["entry"] if pos else Decimal(0)
        sig = self.evaluate(sym, holding=amt > 0)
        bar_open = sig["bar_open"]
        lock_key, long_key = f"stop_lock:{sym}", f"was_long:{sym}"

        if sig["exit"] and self.store.get(lock_key):
            self.store.delete(lock_key)
            log.info("%s 收盤出現出場訊號，解除停損鎖", sym)

        if amt < 0:
            self.notify(f"⚠️ {sym} 非預期空單 {amt}，平倉")
            self.place_market(sym, "BUY", abs(amt), bar_open, True)
            self.cancel_stops(sym)
            return

        if amt > 0:
            self.store.set(long_key, "1")
            mp = self.client.mark_price(sym)
            # 軟體備援停損：條件單沒觸發（或不存在）但價格已經跌破
            if self.cfg.stop_loss_pct > 0 and mp <= self.stop_trigger(sym, entry):
                self.notify(f"⚠️ {sym} 標記價 {mp} 跌破停損價，市價平倉")
                self.place_market(sym, "SELL", amt, bar_open, True)
                self.cancel_stops(sym)
                self.store.delete(long_key)
                if not sig["exit"]:
                    self.store.set(lock_key, bar_open)
                return
            if sig["desired"] == FLAT:
                self.place_market(sym, "SELL", amt, bar_open, True)
                self.cancel_stops(sym)
                self.store.delete(long_key)
                return
            self._rebalance(sym, amt, mp, per_symbol, bar_open, paused)
            refreshed = self.client.positions([sym]).get(sym)
            if refreshed and refreshed["amt"] > 0:
                self.sync_stop(sym, refreshed["entry"])
            return

        # amt == 0
        self.cancel_stops(sym)
        if self.store.get(long_key):
            # 本來持有多單但部位已消失：停損觸發、強平、ADL 或手動平倉
            self.store.delete(long_key)
            if self.cfg.stop_loss_pct > 0 and not sig["exit"]:
                self.store.set(lock_key, bar_open)
                self.notify(f"{sym} 部位已被平倉（停損觸發？），等待收盤跌破 EMA 後才重新進場")
        if sig["desired"] != LONG or paused or self.store.get(lock_key):
            return
        mp = self.client.mark_price(sym)
        close = Decimal(str(sig["close"]))
        if abs(mp - close) / close > Decimal(str(self.cfg.max_mark_deviation)):
            log.info("%s 標記價 %s 與收盤價 %s 偏離過大，延後進場", sym, mp, close)
            return
        qty = min(floor_to_step(per_symbol / mp, f["step"]), f["max_qty"])
        if qty < f["min_qty"] or qty * mp < f["min_notional"]:
            log.warning("%s 目標數量 %s 低於最小下單量", sym, qty)
            return
        resp = self.place_market(sym, "BUY", qty, bar_open, False)
        if resp is not None:
            self.store.set(long_key, "1")
            avg = Decimal(str(resp.get("avgPrice") or "0"))
            self.sync_stop(sym, avg if avg > 0 else mp)

    def _rebalance(self, sym, amt, mp, per_symbol, bar_open, paused) -> None:
        f = self.filters[sym]
        target = min(floor_to_step(per_symbol / mp, f["step"]), f["max_qty"])
        if target <= 0 or abs(amt - target) / target <= Decimal(str(self.cfg.rebal_band)):
            return
        delta = floor_to_step(abs(target - amt), f["step"])
        if delta < f["min_qty"]:
            return
        if target > amt and not paused and delta * mp >= f["min_notional"]:
            self.place_market(sym, "BUY", delta, bar_open, False)
        elif target < amt:
            self.place_market(sym, "SELL", delta, bar_open, True)

    # ------------------------------------------------------------------ 主迴圈
    def run_forever(self) -> None:
        env = "TESTNET" if self.cfg.testnet else "MAINNET"
        self.notify(f"EMA{self.params.ema_period} 機器人啟動（{env}{', DRY RUN' if self.cfg.dry_run else ''}），"
                    f"{len(self.cfg.symbols)} 檔，停損 {self.cfg.stop_loss_pct:.1%}")
        self.client.sync_time()
        if not self.cfg.dry_run:
            self.client.init_account(self.cfg.symbols, self.cfg.exchange_leverage, self.cfg.margin_type)
        self.refresh_filters(force=True)
        last_rec, fail = 0.0, 0
        while True:
            try:
                now = self.client.now_ms()
                bar = last_closed_bar_open(now)
                new_bar = (str(bar) != self.store.get("last_done_bar")
                           and now - (bar + PERIOD_MS) >= self.cfg.close_delay_sec * 1000)
                if new_bar or time.time() - last_rec >= self.cfg.reconcile_every_sec:
                    if time.time() - self._filters_loaded_at > 86400:
                        self.client.sync_time()
                    self.reconcile_cycle()
                    last_rec = time.time()
                    if new_bar:
                        self.store.set("last_done_bar", bar)
                fail = 0
            except BinanceError as exc:
                fail += 1
                log.exception("API 錯誤")
                if exc.status == 418 or exc.code == -2015:
                    self.notify(f"🚨 嚴重錯誤 {exc}，暫停 30 分鐘")
                    time.sleep(1800)
                elif fail >= 3:
                    self.notify(f"連續 {fail} 次 API 錯誤：{exc}")
            except Exception as exc:  # noqa: BLE001 - 常駐程式不因單次錯誤停止
                fail += 1
                log.exception("未預期錯誤")
                if fail >= 3:
                    self.notify(f"連續 {fail} 次未預期錯誤：{exc!r}")
            time.sleep(60)
