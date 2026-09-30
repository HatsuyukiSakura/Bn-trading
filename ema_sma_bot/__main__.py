"""
指令列入口：

  python -m ema_sma_bot signal                          # 各標的目前的 EMA150 訊號（公開行情，不需 API key）
  python -m ema_sma_bot backtest --start 2021-01-01     # 永續 K 線 + 實際資金費率回測
  python -m ema_sma_bot backtest --stop-grid 0,0.05,0.1,0.15   # 比較不同停損幅度
  python -m ema_sma_bot once                            # 執行一次對帳後結束
  python -m ema_sma_bot live                            # 常駐執行（每 60 秒檢查、每 5 分鐘對帳）
  python -m ema_sma_bot reset-halt                      # 人工解除熔斷
"""
import argparse
import dataclasses
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

import pandas as pd

from .backtest import run_portfolio
from .binance_client import FuturesClient
from .config import load_config
from .notifier import Notifier
from .store import Store
from .strategy import LONG, compute_signals
from .trader import Trader


def setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = TimedRotatingFileHandler("logs/bot.log", when="midnight", backupCount=60, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream)


def public_client() -> FuturesClient:
    # 行情與回測使用主網公開資料，不需要 API key
    return FuturesClient(testnet=False)


def trader_from(cfg) -> Trader:
    client = FuturesClient(cfg.api_key, cfg.api_secret, testnet=cfg.testnet, base_url=cfg.base_url or None)
    return Trader(cfg, client, Store(cfg.db_path), Notifier(cfg.telegram_token, cfg.telegram_chat_id))


def cmd_signal(cfg, args) -> int:
    client = public_client()
    client.sync_time()
    for sym in cfg.symbols:
        df = client.klines(sym, "4h", limit=cfg.kline_limit + 1)
        df = df[df["close_time"] < client.now_ms()]
        data = compute_signals(df, cfg.strategy_params)
        last = data.iloc[-1]
        ts = pd.to_datetime(int(last["close_time"]) + 1, unit="ms", utc=True)
        sig = "LONG" if int(last["position"]) == LONG else "FLAT"
        print(f"{sym:10s} {ts:%m-%d %H:%M}Z  close={last['close']:<12.6g} "
              f"EMA{cfg.ema_period}={last['ema']:<12.6g} → {sig}")
    return 0


def _load_or_fetch(client, sym, start_ms, cache_dir, kind):
    path = os.path.join(cache_dir, f"{sym}_{kind}.csv") if cache_dir else None
    if path and os.path.exists(path):
        return pd.read_csv(path)
    if client.offset_ms == 0:
        client.sync_time()
    if kind == "4h":
        df = client.historical_klines(sym, "4h", start_ms)
        df = df[df["close_time"] < client.now_ms()].reset_index(drop=True)
    else:
        df = client.funding_history(sym, start_ms)
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def cmd_backtest(cfg, args) -> int:
    client = public_client()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    klines, funding = {}, {}
    for sym in cfg.symbols:
        print(f"載入 {sym} ...", file=sys.stderr)
        klines[sym] = _load_or_fetch(client, sym, start_ms, args.cache_dir, "4h")
        if not args.no_funding:
            funding[sym] = _load_or_fetch(client, sym, start_ms, args.cache_dir, "funding")

    stops = [float(x) for x in args.stop_grid.split(",")] if args.stop_grid else [cfg.stop_loss_pct]
    first = min(int(df["open_time"].iloc[0]) for df in klines.values())
    last = max(int(df["open_time"].iloc[-1]) for df in klines.values())
    print(f"\n回測 {len(klines)} 檔 4H  {pd.to_datetime(first, unit='ms'):%Y-%m-%d} ~ "
          f"{pd.to_datetime(last, unit='ms'):%Y-%m-%d}  手續費 {args.fee:.3%}  "
          f"資金費率 {'不計' if args.no_funding else '實際歷史'}  SIGNAL_MODE={cfg.signal_mode}")

    for sl in stops:
        params = dataclasses.replace(cfg.strategy_params, stop_loss_pct=sl)
        res = run_portfolio(klines, params, args.balance, args.fee, funding)
        print(f"\n=== 停損 {'關閉' if sl == 0 else f'{sl:.1%}'} ===")
        print(res.summary())
        if args.per_symbol:
            for s in res.sleeves:
                ret = s.equity.iloc[-1] / s.equity.iloc[0] - 1
                n_stop = sum(t["reason"] == "STOP" for t in s.trades)
                print(f"  {s.symbol:10s} {ret:+9.2%}  交易 {len(s.trades):3d}  停損 {n_stop:3d}")
    return 0


def cmd_once(cfg, args) -> int:
    trader = trader_from(cfg)
    trader.client.sync_time()
    if not cfg.dry_run:
        trader.client.init_account(cfg.symbols, cfg.exchange_leverage, cfg.margin_type)
    trader.reconcile_cycle()
    return 0


def cmd_live(cfg, args) -> int:
    trader_from(cfg).run_forever()
    return 0


def cmd_reset_halt(cfg, args) -> int:
    store = Store(cfg.db_path)
    store.set("halted", "0")
    store.delete("peak_equity")
    print("已解除熔斷，權益高點將從下次對帳重新計算")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ema_sma_bot", description="幣安 USDⓈ-M 永續 4H EMA150 自動交易")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("signal", help="顯示各標的目前訊號")
    sub.add_parser("once", help="執行一次對帳後結束")
    sub.add_parser("live", help="常駐自動交易")
    sub.add_parser("reset-halt", help="人工解除熔斷")
    bt = sub.add_parser("backtest", help="歷史回測")
    bt.add_argument("--start", default="2021-01-01", help="起始日期（UTC）")
    bt.add_argument("--balance", type=float, default=10_000.0)
    bt.add_argument("--fee", type=float, default=0.0005, help="單邊手續費率（預設 0.05%% taker）")
    bt.add_argument("--no-funding", action="store_true", help="不計資金費率")
    bt.add_argument("--stop-grid", help="以逗號分隔多個停損幅度比較，例如 0,0.05,0.1,0.15")
    bt.add_argument("--cache-dir", default="data", help="K 線與資金費率快取資料夾")
    bt.add_argument("--per-symbol", action="store_true", help="列出各標的結果")
    args = parser.parse_args(argv)

    setup_logging()
    cfg = load_config()
    try:
        cfg.validate(need_keys=args.command in ("once", "live"))
    except ValueError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        return 2

    return {"signal": cmd_signal, "once": cmd_once, "live": cmd_live, "backtest": cmd_backtest,
            "reset-halt": cmd_reset_halt}[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
