"""
精簡的幣安 REST 客戶端，支援現貨 (spot) 與 USDⓈ-M 永續合約 (futures)。
只實作本策略需要的端點。
"""
import hashlib
import hmac
import logging
import time
from decimal import ROUND_DOWN, Decimal
from typing import Optional
from urllib.parse import urlencode

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE_URLS = {
    ("spot", False): "https://api.binance.com",
    ("spot", True): "https://testnet.binance.vision",
    ("futures", False): "https://fapi.binance.com",
    ("futures", True): "https://testnet.binancefuture.com",
}

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


class BinanceAPIError(Exception):
    def __init__(self, status: int, payload):
        self.status = status
        self.payload = payload
        super().__init__(f"HTTP {status}: {payload}")


def floor_to_step(value: float, step: str) -> Decimal:
    """將數量無條件捨去到交易所 stepSize 的倍數。"""
    step_d = Decimal(step)
    if step_d == 0:
        return Decimal(str(value))
    return (Decimal(str(value)) / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d


def format_decimal(value: Decimal) -> str:
    """去除多餘的 0，避免送出 '0.00100000' 之類精度錯誤的字串。"""
    text = format(value.normalize(), "f")
    return text


def klines_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=KLINE_COLUMNS)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


class BinanceClient:
    def __init__(self, market: str = "spot", api_key: str = "", api_secret: str = "",
                 testnet: bool = True, base_url: Optional[str] = None, timeout: int = 10,
                 session: Optional[requests.Session] = None):
        if market not in ("spot", "futures"):
            raise ValueError("market 必須是 spot 或 futures")
        self.market = market
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (base_url or BASE_URLS[(market, testnet)]).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._time_offset_ms = 0
        self._symbol_info_cache: dict = {}
        prefix = "/api/v3" if market == "spot" else "/fapi/v1"
        self._p = prefix

    # ------------------------------------------------------------------ 基礎
    def _sign(self, params: dict) -> str:
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={signature}"

    def _request(self, method: str, path: str, params: Optional[dict] = None, signed: bool = False,
                 retries: int = 3):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        last_exc: Optional[Exception] = None

        for attempt in range(retries):
            if signed:
                params["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
                params.setdefault("recvWindow", 5000)
                url = f"{self.base_url}{path}?{self._sign(params)}"
            else:
                url = f"{self.base_url}{path}"
                if params:
                    url = f"{url}?{urlencode(params)}"
            try:
                resp = self.session.request(method, url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("請求失敗 (%s %s)，第 %d 次：%s", method, path, attempt + 1, exc)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                return resp.json()

            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            # 時間不同步 (-1021) → 重新校時後重試
            if isinstance(payload, dict) and payload.get("code") == -1021 and attempt < retries - 1:
                self.sync_time()
                continue
            # 限流或伺服器錯誤 → 退避重試（下單不重試，避免重複成交）
            if resp.status_code in (429, 418) or resp.status_code >= 500:
                last_exc = BinanceAPIError(resp.status_code, payload)
                if method == "GET" and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            raise BinanceAPIError(resp.status_code, payload)

        raise last_exc or RuntimeError("request failed")

    # ------------------------------------------------------------------ 公開資料
    def server_time(self) -> int:
        return int(self._request("GET", f"{self._p}/time")["serverTime"])

    def sync_time(self) -> None:
        local = int(time.time() * 1000)
        self._time_offset_ms = self.server_time() - local
        log.info("與幣安伺服器時間差：%d ms", self._time_offset_ms)

    def klines(self, symbol: str, interval: str, limit: int = 1000,
               start_time: Optional[int] = None, end_time: Optional[int] = None) -> pd.DataFrame:
        max_limit = 1000 if self.market == "spot" else 1500
        raw = self._request("GET", f"{self._p}/klines", {
            "symbol": symbol, "interval": interval, "limit": min(limit, max_limit),
            "startTime": start_time, "endTime": end_time,
        })
        return klines_to_df(raw)

    def historical_klines(self, symbol: str, interval: str, start_time: int,
                          end_time: Optional[int] = None) -> pd.DataFrame:
        """分頁抓取一段期間的 K 線（回測用）。"""
        frames = []
        cursor = start_time
        while True:
            df = self.klines(symbol, interval, limit=1000, start_time=cursor, end_time=end_time)
            if df.empty:
                break
            frames.append(df)
            cursor = int(df["open_time"].iloc[-1]) + 1
            if len(df) < 1000 or (end_time and cursor > end_time):
                break
            time.sleep(0.2)
        if not frames:
            return klines_to_df([])
        return pd.concat(frames).drop_duplicates("open_time").reset_index(drop=True)

    def closed_klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        """只回傳已收盤的 K 線（去掉仍在進行中的最後一根）。"""
        df = self.klines(symbol, interval, limit=limit + 1)
        now = self.server_time()
        return df[df["close_time"] < now].reset_index(drop=True)

    def price(self, symbol: str) -> float:
        return float(self._request("GET", f"{self._p}/ticker/price", {"symbol": symbol})["price"])

    def symbol_info(self, symbol: str) -> dict:
        if symbol not in self._symbol_info_cache:
            params = {"symbol": symbol} if self.market == "spot" else None
            data = self._request("GET", f"{self._p}/exchangeInfo", params)
            for s in data["symbols"]:
                if s["symbol"] == symbol:
                    self._symbol_info_cache[symbol] = s
                    break
            else:
                raise ValueError(f"找不到交易對 {symbol}")
        return self._symbol_info_cache[symbol]

    def symbol_filters(self, symbol: str) -> dict:
        """回傳 {'step': str, 'min_qty': float, 'min_notional': float}。"""
        info = self.symbol_info(symbol)
        filters = {f["filterType"]: f for f in info["filters"]}
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
        # MARKET_LOT_SIZE 的 stepSize 可能為 0，此時使用 LOT_SIZE
        if lot is None or Decimal(lot.get("stepSize", "0")) == 0:
            lot = filters["LOT_SIZE"]
        notional_f = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        min_notional = float(notional_f.get("minNotional") or notional_f.get("notional") or 0)
        return {
            "step": lot["stepSize"],
            "min_qty": float(lot["minQty"]),
            "min_notional": min_notional,
            "base_asset": info["baseAsset"],
            "quote_asset": info["quoteAsset"],
        }

    # ------------------------------------------------------------------ 帳戶
    def asset_balance(self, asset: str) -> float:
        """可用餘額。"""
        if self.market == "spot":
            data = self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
            for b in data["balances"]:
                if b["asset"] == asset:
                    return float(b["free"])
            return 0.0
        data = self._request("GET", "/fapi/v2/balance", signed=True)
        for b in data:
            if b["asset"] == asset:
                return float(b["availableBalance"])
        return 0.0

    def futures_position_amt(self, symbol: str) -> float:
        """合約持倉數量（正=多，負=空）。單向持倉模式。"""
        data = self._request("GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        return sum(float(p["positionAmt"]) for p in data if p["symbol"] == symbol)

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request("POST", "/fapi/v1/leverage",
                             {"symbol": symbol, "leverage": leverage}, signed=True)

    # ------------------------------------------------------------------ 下單
    def market_order(self, symbol: str, side: str, quantity: Optional[str] = None,
                     quote_qty: Optional[str] = None, reduce_only: bool = False) -> dict:
        params = {"symbol": symbol, "side": side, "type": "MARKET"}
        if quantity is not None:
            params["quantity"] = quantity
        elif quote_qty is not None:
            if self.market != "spot":
                raise ValueError("quoteOrderQty 僅支援現貨")
            params["quoteOrderQty"] = quote_qty
        else:
            raise ValueError("需要 quantity 或 quote_qty")
        if reduce_only and self.market == "futures":
            params["reduceOnly"] = "true"
        if self.market == "spot":
            params["newOrderRespType"] = "FULL"
        return self._request("POST", f"{self._p}/order", params, signed=True, retries=1)
