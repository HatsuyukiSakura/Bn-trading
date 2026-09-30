"""
指令列入口：

  python -m ema_sma_bot signal                 # 顯示目前 EMA/SMA 與訊號（不下單）
  python -m ema_sma_bot once                   # 處理最新一根已收盤 K 線後結束（適合 cron）
  python -m ema_sma_bot live                   # 常駐執行，每根 4H K 線收盤後自動交易
  python -m ema_sma_bot backtest --days 1095   # 用幣安歷史資料回測
"""
import argparse
import logging
import sys
import time

import pandas as pd

from .backtest import run_backtest
from .binance_client import BinanceClient
from .config import load_config
from .notifier import Notifier
from .strategy import add_indicators, compute_positions
from .trader import POSITION_NAMES, Trader


def _client(cfg, public_mainnet: bool = False) -> BinanceClient:
    # 查詢行情 / 回測使用正式網公開資料即可，不需要 API key
    if public_mainnet:
        return BinanceClient(market=cfg.market, testnet=False)
    return BinanceClient(market=cfg.market, api_key=cfg.api_key, api_secret=cfg.api_secret,
                         testnet=cfg.testnet, base_url=cfg.base_url or None)


def cmd_signal(cfg, args) -> int:
    client = _client(cfg, public_mainnet=True)
    df = client.closed_klines(cfg.symbol, cfg.interval, cfg.kline_limit)
    data = compute_positions(df, cfg.strategy_params)
    last = add_indicators(df, cfg.strategy_params).iloc[-1]
    ts = pd.to_datetime(int(last["close_time"]) + 1, unit="ms", utc=True)
    print(f"{cfg.symbol} {cfg.interval}  最新收盤：{ts:%Y-%m-%d %H:%M} UTC")
    print(f"  close  = {last['close']:.4f}")
    print(f"  EMA{cfg.ema_period} = {last['ema']:.4f}")
    print(f"  SMA{cfg.sma_period} = {last['sma']:.4f}")
    print(f"  策略部位 = {POSITION_NAMES[int(data['position'].iloc[-1])]}")
    return 0


def cmd_once(cfg, args) -> int:
    trader = Trader(cfg, _client(cfg), Notifier(cfg.telegram_token, cfg.telegram_chat_id))
    trader.client.sync_time()
    result = trader.step()
    print("此 K 線已處理過" if result is None else f"目標部位：{POSITION_NAMES[result]}")
    return 0


def cmd_live(cfg, args) -> int:
    Trader(cfg, _client(cfg), Notifier(cfg.telegram_token, cfg.telegram_chat_id)).run_forever()
    return 0


def cmd_backtest(cfg, args) -> int:
    if args.csv:
        df = pd.read_csv(args.csv)
    else:
        client = _client(cfg, public_mainnet=True)
        start = int((time.time() - args.days * 86400) * 1000)
        df = client.historical_klines(cfg.symbol, cfg.interval, start)
        df = df[df["close_time"] < client.server_time()].reset_index(drop=True)
        if args.save_csv:
            df.to_csv(args.save_csv, index=False)
    if df.empty:
        print("沒有資料")
        return 1

    params = cfg.strategy_params
    result = run_backtest(df, params, args.balance, args.fee, cfg.leverage if cfg.market == "futures" else 1)
    first = pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms", utc=True)
    lastt = pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms", utc=True)
    print(f"回測 {cfg.symbol} {cfg.interval}  {first:%Y-%m-%d} ~ {lastt:%Y-%m-%d}  "
          f"({len(df)} 根 K 線, EMA{params.ema_period}/SMA{params.sma_period}, "
          f"{'多空' if params.allow_short else '只做多'})")
    print(result.summary())
    if args.show_trades:
        for t in result.trades:
            et = pd.to_datetime(t["entry_time"], unit="ms", utc=True)
            xt = pd.to_datetime(t["exit_time"], unit="ms", utc=True)
            print(f"  {t['side']:5s} {et:%Y-%m-%d %H:%M} @ {t['entry_price']:.4f} → "
                  f"{xt:%Y-%m-%d %H:%M} @ {t['exit_price']:.4f}  {t['pnl_pct']:+.2%}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ema_sma_bot", description="幣安 4H EMA150/SMA150 自動交易")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("signal", help="顯示目前指標與訊號")
    sub.add_parser("once", help="處理最新一根已收盤 K 線後結束")
    sub.add_parser("live", help="常駐自動交易")
    bt = sub.add_parser("backtest", help="歷史回測")
    bt.add_argument("--days", type=int, default=1095, help="回測天數（預設 3 年）")
    bt.add_argument("--csv", help="改用本機 CSV（需有 open_time, open, close, close_time 欄）")
    bt.add_argument("--save-csv", help="將下載的 K 線存成 CSV")
    bt.add_argument("--balance", type=float, default=10_000.0, help="初始資金")
    bt.add_argument("--fee", type=float, default=0.001, help="單邊手續費率（預設 0.001）")
    bt.add_argument("--show-trades", action="store_true", help="列出每筆交易")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        return 2

    return {"signal": cmd_signal, "once": cmd_once, "live": cmd_live,
            "backtest": cmd_backtest}[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
