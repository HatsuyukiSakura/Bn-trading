"""
幣安 USDⓈ-M 永續合約的精簡 REST 客戶端。

設計依據（研究報告第四章）：
- 主網 https://fapi.binance.com，測試網 https://demo-fapi.binance.com
- -1021 重新校時後重試；429 依 Retry-After 等待；418 / -2015 直接拋出讓上層停機通知
- 下單遇到網路中斷或 503「Unknown error」→ 拋出 UnknownExecution，禁止直接重送
- 條件單（停損）自 2025 年底起改走 Algo Order：POST /fapi/v1/algoOrder
"""
import hashlib
import hmac
import logging
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Optional
from urllib.parse import urlencode

import pandas as pd
import requests

log = logging.getLogger(__name__)

MAINNET = "https://fapi.binance.com"
TESTNET = "https://demo-fapi.binance.com"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


class BinanceError(Exception):
    def __init__(self, status: int, code, msg: str):
        super().__init__(f"HTTP {status} code={code} msg={msg}")
        self.status = status
        self.code = code
        self.msg = msg


class UnknownExecution(Exception):
    """下單狀態未知（503 Unknown error 或網路中斷），可能已成交，禁止直接重送。"""


def floor_to_step(value, step) -> Decimal:
    step_d = Decimal(str(step))
    value_d = Decimal(str(value))
    if step_d == 0:
        return value_d
    return (value_d / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d


def ceil_to_step(value, step) -> Decimal:
    step_d = Decimal(str(step))
    value_d = Decimal(str(value))
    if step_d == 0:
        return value_d
    return (value_d / step_d).to_integral_value(rounding=ROUND_UP) * step_d


def format_decimal(value: Decimal) -> str:
    """Decimal → 不含科學記號、不含多餘 0 的字串。"""
    text = format(Decimal(value).normalize(), "f")
    return text


def klines_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=KLINE_COLUMNS)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


class FuturesClient:
    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = True,
                 base_url: Optional[str] = None, timeout: int = 15,
                 session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (base_url or (TESTNET if testnet else MAINNET)).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        if api_key:
            self.session.headers["X-MBX-APIKEY"] = api_key
        self.offset_ms = 0.0

    # ------------------------------------------------------------------ 基礎
    def now_ms(self) -> int:
        return int(time.time() * 1000 + self.offset_ms)

    def sync_time(self) -> None:
        t0 = time.time() * 1000
        srv = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=self.timeout).json()["serverTime"]
        self.offset_ms = srv - (t0 + time.time() * 1000) / 2
        if abs(self.offset_ms) > 1000:
            log.warning("本機時間與幣安相差 %.0f ms，請檢查系統時間同步", self.offset_ms)

    def _sign(self, params: dict) -> dict:
        p = dict(params)
        p["timestamp"] = self.now_ms()
        p["recvWindow"] = 5000
        p["signature"] = hmac.new(self.api_secret.encode(), urlencode(p).encode(),
                                  hashlib.sha256).hexdigest()
        return p

    def request(self, method: str, path: str, params: Optional[dict] = None, signed: bool = False,
                retries: int = 3, is_order: bool = False):
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(retries + 1):
            p = self._sign(clean) if signed else clean
            try:
                r = self.session.request(method, self.base_url + path, params=p, timeout=self.timeout)
            except requests.RequestException as exc:
                if is_order:
                    raise UnknownExecution(str(exc)) from exc
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt)
                continue

            if r.status_code == 200:
                return r.json()
            try:
                body = r.json()
            except ValueError:
                body = {"code": None, "msg": r.text}
            if not isinstance(body, dict):
                body = {"code": None, "msg": str(body)}
            code, msg = body.get("code"), str(body.get("msg"))

            if r.status_code == 418:
                raise BinanceError(418, code, msg)
            if r.status_code == 429 and attempt < retries:
                time.sleep(int(r.headers.get("Retry-After", "60")))
                continue
            if code == -1021 and attempt < retries:
                self.sync_time()
                continue
            if r.status_code == 503 and "Unknown error" in msg:
                raise UnknownExecution(msg)
            if (r.status_code >= 500 or code == -1008) and attempt < retries:
                time.sleep(0.2 * 2 ** attempt)
                continue
            raise BinanceError(r.status_code, code, msg)
        raise BinanceError(-1, None, "retries exhausted")

    def _ok_codes(self, fn, *codes):
        try:
            return fn()
        except BinanceError as exc:
            if exc.code in codes:
                return None
            raise

    # ------------------------------------------------------------------ 行情
    def klines(self, symbol: str, interval: str = "4h", limit: int = 1000,
               start_time: Optional[int] = None, end_time: Optional[int] = None) -> pd.DataFrame:
        raw = self.request("GET", "/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "limit": min(limit, 1500),
            "startTime": start_time, "endTime": end_time,
        })
        return klines_to_df(raw)

    def historical_klines(self, symbol: str, interval: str, start_time: int,
                          end_time: Optional[int] = None) -> pd.DataFrame:
        frames, cursor = [], start_time
        while True:
            df = self.klines(symbol, interval, limit=1500, start_time=cursor, end_time=end_time)
            if df.empty:
                break
            frames.append(df)
            cursor = int(df["open_time"].iloc[-1]) + 1
            if len(df) < 1500 or (end_time and cursor > end_time):
                break
            time.sleep(0.2)
        if not frames:
            return klines_to_df([])
        return pd.concat(frames).drop_duplicates("open_time").reset_index(drop=True)

    def funding_history(self, symbol: str, start_time: int, end_time: Optional[int] = None) -> pd.DataFrame:
        """資金費率歷史（每次最多 1000 筆，自動分頁）。欄位：funding_time, rate。"""
        rows, cursor = [], start_time
        while True:
            data = self.request("GET", "/fapi/v1/fundingRate", {
                "symbol": symbol, "startTime": cursor, "endTime": end_time, "limit": 1000})
            if not data:
                break
            rows.extend(data)
            cursor = int(data[-1]["fundingTime"]) + 1
            if len(data) < 1000:
                break
            time.sleep(0.2)
        df = pd.DataFrame({
            "funding_time": [int(d["fundingTime"]) for d in rows],
            "rate": [float(d["fundingRate"]) for d in rows],
        })
        return df.drop_duplicates("funding_time").reset_index(drop=True)

    def mark_price(self, symbol: str) -> Decimal:
        return Decimal(self.request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    def exchange_filters(self, symbols) -> dict:
        """
        讀取交易規則。只接受 USDT 保證金的 PERPETUAL 合約（排除 TradFi 等其他類型）。
        回傳 {symbol: {status, step, min_qty, max_qty, min_notional, tick}}
        """
        wanted = set(symbols)
        out = {}
        for s in self.request("GET", "/fapi/v1/exchangeInfo")["symbols"]:
            if s["symbol"] not in wanted:
                continue
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
                raise RuntimeError(f"{s['symbol']} 不是 USDT 永續合約")
            f = {x["filterType"]: x for x in s["filters"]}
            mls, ls = f.get("MARKET_LOT_SIZE", f["LOT_SIZE"]), f["LOT_SIZE"]
            out[s["symbol"]] = {
                "status": s["status"],
                "step": max(Decimal(mls["stepSize"]), Decimal(ls["stepSize"])),
                "min_qty": max(Decimal(mls["minQty"]), Decimal(ls["minQty"])),
                "max_qty": Decimal(mls["maxQty"]),
                "min_notional": Decimal(f.get("MIN_NOTIONAL", {}).get("notional", "5")),
                "tick": Decimal(f["PRICE_FILTER"]["tickSize"]),
            }
        missing = wanted - set(out)
        if missing:
            raise RuntimeError(f"exchangeInfo 缺少標的：{sorted(missing)}")
        return out

    # ------------------------------------------------------------------ 帳戶
    def init_account(self, symbols, leverage: int, margin_type: str = "CROSSED") -> None:
        """單向持倉、關閉聯合保證金、設定保證金模式與交易所端槓桿。"""
        if self.request("GET", "/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]:
            self._ok_codes(lambda: self.request("POST", "/fapi/v1/positionSide/dual",
                                                {"dualSidePosition": "false"}, signed=True), -4059)
        if self.request("GET", "/fapi/v1/multiAssetsMargin", signed=True)["multiAssetsMargin"]:
            self.request("POST", "/fapi/v1/multiAssetsMargin", {"multiAssetsMargin": "false"}, signed=True)
        for sym in symbols:
            self._ok_codes(lambda: self.request("POST", "/fapi/v1/marginType",
                                                {"symbol": sym, "marginType": margin_type}, signed=True), -4046)
            self.request("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": leverage}, signed=True)

    def account_equity(self) -> Decimal:
        return Decimal(self.request("GET", "/fapi/v3/account", signed=True)["totalMarginBalance"])

    def positions(self, symbols) -> dict:
        """{symbol: {"amt": Decimal, "entry": Decimal}}，只含有持倉的標的。"""
        wanted = set(symbols)
        out = {}
        for p in self.request("GET", "/fapi/v3/positionRisk", signed=True):
            if p["symbol"] in wanted and Decimal(p["positionAmt"]) != 0:
                out[p["symbol"]] = {"amt": Decimal(p["positionAmt"]), "entry": Decimal(p["entryPrice"])}
        return out

    # ------------------------------------------------------------------ 下單
    def query_order(self, symbol: str, client_order_id: str) -> Optional[dict]:
        try:
            return self.request("GET", "/fapi/v1/order",
                                {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True)
        except BinanceError as exc:
            if exc.code == -2013:  # 訂單不存在
                return None
            raise

    def market_order(self, symbol: str, side: str, quantity: Decimal, client_order_id: str,
                     reduce_only: bool = False) -> dict:
        params = {"symbol": symbol, "side": side, "type": "MARKET",
                  "quantity": format_decimal(quantity), "newClientOrderId": client_order_id,
                  "newOrderRespType": "RESULT"}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self.request("POST", "/fapi/v1/order", params, signed=True, is_order=True)

    # ------------------------------------------------------------------ 條件單（盤中停損）
    def open_algo_orders(self, symbol: str) -> list:
        data = self.request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
        if isinstance(data, dict):  # 容錯：部分版本包在 orders 欄位
            data = data.get("orders", [])
        return data

    def place_stop_market(self, symbol: str, trigger_price: Decimal, client_algo_id: str,
                          working_type: str = "MARK_PRICE") -> dict:
        """多單停損：觸發後以市價平掉整個部位（closePosition=true）。"""
        return self.request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_MARKET",
            "triggerPrice": format_decimal(trigger_price),
            "workingType": working_type,
            "closePosition": "true",
            "priceProtect": "TRUE",
            "clientAlgoId": client_algo_id,
        }, signed=True, is_order=True)

    def cancel_algo_order(self, algo_id) -> dict:
        return self.request("DELETE", "/fapi/v1/algoOrder", {"algoId": algo_id}, signed=True)
