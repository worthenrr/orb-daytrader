"""Test the strategy on past data BEFORE risking money.

    python backtest.py --days 120                 # pulls history from Alpaca (free)
    python backtest.py --csv-dir data/            # or use your own CSVs (SYMBOL.csv)

Uses the exact same rules as the live bot. Assumptions (deliberately
pessimistic): you buy at the OPEN of the bar after the signal plus slippage,
and if a bar touches both the stop and the target, the stop is assumed to hit
first.
"""
import argparse
import os
from datetime import datetime, timedelta

import pandas as pd

from config import Config
from strategy import (ET, breakout_signal, market_ok, opening_range,
                      plan_trade, session_bars, to_et)


def simulate_day(day, data, cfg, slippage_pct=0.05):
    """Return a list of trade dicts for one day."""
    sessions = {s: session_bars(df, day) for s, df in data.items()}
    mkt = sessions.get(cfg.market_symbol)
    trades, taken, day_pnl, spent = [], set(), 0.0, 0.0
    symbols = [s for s in cfg.watchlist if s in sessions and not sessions[s].empty]
    if not symbols:
        return trades
    # Walk forward bar by bar across all symbols, like the live loop does.
    times = sorted(set().union(*[set(sessions[s].index) for s in symbols]))
    for t in times:
        if t.time() >= cfg.entry_end or len(taken) >= cfg.max_trades_per_day:
            break
        if day_pnl <= -cfg.daily_loss_limit_dollars:
            break
        mkt_so_far = mkt[mkt.index <= t] if mkt is not None else None
        for sym in symbols:
            if sym in taken or len(taken) >= cfg.max_trades_per_day:
                continue
            bars = sessions[sym]
            upto = bars[bars.index <= t]
            if upto.empty or upto.index[-1] != t:
                continue
            orng = opening_range(upto, cfg)
            ok, _ = breakout_signal(upto, orng, cfg)
            if not ok or not market_ok(mkt_so_far, cfg):
                continue
            after = bars[bars.index > t]
            if after.empty:
                continue
            entry = float(after["open"].iloc[0]) * (1 + slippage_pct / 100)
            if entry > orng.high * (1 + cfg.max_chase_pct / 100):
                continue
            plan = plan_trade(sym, entry, orng, cfg, cfg.budget - spent)
            if plan is None or not (plan.stop < entry < plan.target):
                continue
            exit_px, exit_why = None, None
            for ts, b in after.iterrows():
                if ts.time() >= cfg.flatten_time:
                    exit_px, exit_why = float(b["open"]), "flatten"
                    break
                if float(b["low"]) <= plan.stop:
                    exit_px, exit_why = plan.stop * (1 - slippage_pct / 100), "stop"
                    break
                if float(b["high"]) >= plan.target:
                    exit_px, exit_why = plan.target, "target"
                    break
            if exit_px is None:
                exit_px, exit_why = float(after["close"].iloc[-1]), "close"
            pnl = (exit_px - entry) * plan.qty
            day_pnl += pnl
            spent += plan.cost
            taken.add(sym)
            trades.append({"date": day, "symbol": sym, "entry_time": after.index[0],
                           "qty": plan.qty, "entry": round(entry, 2), "stop": plan.stop,
                           "target": plan.target, "exit": round(exit_px, 2),
                           "exit_reason": exit_why, "pnl": round(pnl, 2)})
    return trades


def run_backtest(data, cfg, slippage_pct=0.05):
    data = {s: to_et(df) for s, df in data.items()}
    days = sorted({d for df in data.values() for d in df.index.date})
    trades = []
    for day in days:
        trades += simulate_day(day, data, cfg, slippage_pct)
    return pd.DataFrame(trades), len(days)


def summarize(trades, n_days, cfg):
    if trades.empty:
        return f"No trades in {n_days} days. Try a longer period or loosen VOLUME_MULT."
    wins = trades[trades["pnl"] > 0]
    equity = cfg.budget + trades["pnl"].cumsum()
    drawdown = (equity - equity.cummax()).min()
    gross_win = wins["pnl"].sum()
    gross_loss = -trades[trades["pnl"] <= 0]["pnl"].sum()
    lines = [
        f"Days tested:        {n_days}",
        f"Trades:             {len(trades)}",
        f"Win rate:           {len(wins) / len(trades):.0%}",
        f"Total P&L:          ${trades['pnl'].sum():+.2f} on ${cfg.budget:.0f}"
        f" ({trades['pnl'].sum() / cfg.budget:+.1%})",
        f"Average trade:      ${trades['pnl'].mean():+.2f}",
        f"Profit factor:      {gross_win / gross_loss:.2f}" if gross_loss else "Profit factor:      n/a",
        f"Worst drawdown:     ${drawdown:.2f}",
        "Exits:              " + ", ".join(f"{k} {v}" for k, v in trades["exit_reason"].value_counts().items()),
        "",
        "By symbol:",
        trades.groupby("symbol")["pnl"].agg(["count", "sum"]).round(2).to_string(),
    ]
    return "\n".join(lines)


def load_alpaca(cfg, days):
    from broker import AlpacaBroker

    b = AlpacaBroker(cfg)
    end = datetime.now(ET) - timedelta(minutes=20)
    start = end - timedelta(days=days)
    return b.bars(cfg.watchlist + [cfg.market_symbol], start, end)


def load_csv(folder):
    data = {}
    for f in os.listdir(folder):
        if f.lower().endswith(".csv"):
            df = pd.read_csv(os.path.join(folder, f), index_col=0, parse_dates=True)
            df.columns = [c.lower() for c in df.columns]
            data[f[:-4].upper()] = df[["open", "high", "low", "close", "volume"]]
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--csv-dir")
    ap.add_argument("--slippage", type=float, default=0.05, help="percent per side")
    ap.add_argument("--out", default="backtest_trades.csv")
    a = ap.parse_args()
    cfg = Config()
    data = load_csv(a.csv_dir) if a.csv_dir else load_alpaca(cfg, a.days)
    trades, n = run_backtest(data, cfg, a.slippage)
    print(summarize(trades, n, cfg))
    if not trades.empty:
        trades.to_csv(a.out, index=False)
        print(f"\nEvery trade saved to {a.out}")
