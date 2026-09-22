"""Test the strategy on past data BEFORE risking money.

    python backtest.py --days 120

Prints the current settings' result, then a grid comparing exit horizons
(sell before lunch, sell at the close, hold one night, hold two nights) at two
profit targets. Same entry rules as the live bot in every case.

Deliberately pessimistic assumptions:
  - You buy at the OPEN of the bar after the signal, plus slippage.
  - If a bar touches both the stop and the target, the stop is assumed first.
  - Overnight gaps fill at the opening price, not at your stop. A stop order
    does not protect you while the market is closed.
"""
import argparse
import copy
import os
from datetime import datetime, time, timedelta

import pandas as pd

from config import Config
from strategy import (ET, breakout_signal, market_ok, opening_range,
                      plan_trade, session_bars, to_et)

CLOSE = time(15, 45)  # a few minutes before the bell, to get a real fill

# label -> (nights held, cutoff time on the final day)
HORIZONS = {
    "late morning": (0, time(11, 45)),
    "early afternoon": (0, time(13, 0)),
    "same-day close": (0, CLOSE),
    "hold 1 night": (1, CLOSE),
    "hold 2 nights": (2, CLOSE),
}


def trading_days(data):
    days = set()
    for df in data.values():
        days.update(df.index.date)
    return sorted(days)


def cutoff_for(day, nights, cut_time, all_days):
    """Timestamp when the trade must be closed, skipping weekends/holidays."""
    if day not in all_days:
        return datetime.combine(day, cut_time, tzinfo=ET)
    i = all_days.index(day)
    last = all_days[min(i + nights, len(all_days) - 1)]
    return datetime.combine(last, cut_time, tzinfo=ET)


def walk_exit(bars_after, plan, cutoff, slippage_pct):
    """Step forward bar by bar until stop, target or the cutoff."""
    for ts, b in bars_after.iterrows():
        if ts >= cutoff:
            return float(b["open"]), "time exit"
        o, hi, lo = float(b["open"]), float(b["high"]), float(b["low"])
        if o <= plan.stop:                      # gapped through the stop overnight
            return o, "gap down"
        if lo <= plan.stop:
            return plan.stop * (1 - slippage_pct / 100), "stop"
        if o >= plan.target:                    # gapped past the target
            return o, "gap up"
        if hi >= plan.target:
            return plan.target, "target"
    if bars_after.empty:
        return None, None
    return float(bars_after["close"].iloc[-1]), "data ended"


def run_variant(data, cfg, nights, cut_time, slippage_pct=0.05):
    """One full pass over the history with a given exit rule."""
    all_days = trading_days(data)
    sessions = {s: {d: session_bars(df, d) for d in all_days} for s, df in data.items()}
    full = {s: pd.concat([sessions[s][d] for d in all_days if not sessions[s][d].empty])
            for s in data}
    trades = []
    open_until = {}          # symbol -> timestamp it frees up (multi-day holds tie up cash)

    for day in all_days:
        mkt = sessions.get(cfg.market_symbol, {}).get(day)
        taken, day_pnl = 0, 0.0
        symbols = [s for s in cfg.watchlist
                   if s in sessions and not sessions[s].get(day, pd.DataFrame()).empty]
        if not symbols:
            continue
        times = sorted(set().union(*[set(sessions[s][day].index) for s in symbols]))
        for t in times:
            if t.time() >= cfg.entry_end or taken >= cfg.max_trades_per_day:
                break
            if day_pnl <= -cfg.daily_loss_limit_dollars:
                break
            mkt_so_far = mkt[mkt.index <= t] if mkt is not None else None
            for sym in symbols:
                if taken >= cfg.max_trades_per_day:
                    break
                if open_until.get(sym, t) > t:          # still holding this one
                    continue
                bars = sessions[sym][day]
                upto = bars[bars.index <= t]
                if upto.empty or upto.index[-1] != t:
                    continue
                orng = opening_range(upto, cfg)
                ok, _ = breakout_signal(upto, orng, cfg)
                if not ok or not market_ok(mkt_so_far, cfg):
                    continue
                after_today = bars[bars.index > t]
                if after_today.empty:
                    continue
                entry = float(after_today["open"].iloc[0]) * (1 + slippage_pct / 100)
                if entry > orng.high * (1 + cfg.max_chase_pct / 100):
                    continue
                plan = plan_trade(sym, entry, orng, cfg, cfg.max_position_dollars)
                if plan is None or not (plan.stop < entry < plan.target):
                    continue
                entry_ts = after_today.index[0]
                cutoff = cutoff_for(day, nights, cut_time, all_days)
                ahead = full[sym][(full[sym].index > entry_ts) & (full[sym].index <= cutoff + timedelta(minutes=10))]
                exit_px, why = walk_exit(ahead, plan, cutoff, slippage_pct)
                if exit_px is None:
                    continue
                pnl = (exit_px - entry) * plan.qty
                day_pnl += pnl
                taken += 1
                open_until[sym] = cutoff
                trades.append({"date": day, "symbol": sym, "entry_time": entry_ts,
                               "qty": plan.qty, "entry": round(entry, 2), "stop": plan.stop,
                               "target": plan.target, "exit": round(exit_px, 2),
                               "exit_reason": why, "pnl": round(pnl, 2)})
    return pd.DataFrame(trades)


def stats(trades, cfg):
    if trades.empty:
        return {"trades": 0, "win_rate": 0, "pnl": 0, "pf": 0, "dd": 0,
                "half1": 0, "half2": 0, "passes": False}
    wins = trades[trades["pnl"] > 0]
    gross_win = wins["pnl"].sum()
    gross_loss = -trades[trades["pnl"] <= 0]["pnl"].sum()
    pf = gross_win / gross_loss if gross_loss else float("inf")
    equity = cfg.budget + trades["pnl"].cumsum()
    dd = float((equity - equity.cummax()).min())
    mid = len(trades) // 2
    h1 = trades["pnl"].iloc[:mid].sum()
    h2 = trades["pnl"].iloc[mid:].sum()
    return {"trades": len(trades), "win_rate": len(wins) / len(trades),
            "pnl": trades["pnl"].sum(), "pf": pf, "dd": dd, "half1": h1, "half2": h2,
            "passes": pf > 1.2 and len(trades) >= 25 and h1 > 0 and h2 > 0}


def summarize(trades, n_days, cfg):
    """Detailed write-up of one variant (used for the current settings)."""
    if trades.empty:
        return f"No trades in {n_days} days. Try a longer period or loosen VOLUME_MULT."
    s = stats(trades, cfg)
    return "\n".join([
        f"Days tested:        {n_days}",
        f"Trades:             {s['trades']}",
        f"Win rate:           {s['win_rate']:.0%}",
        f"Total P&L:          ${s['pnl']:+.2f} on ${cfg.budget:.0f} ({s['pnl'] / cfg.budget:+.1%})",
        f"Average trade:      ${trades['pnl'].mean():+.2f}",
        f"Profit factor:      {s['pf']:.2f}",
        f"Worst drawdown:     ${s['dd']:.2f}",
        "Exits:              " + ", ".join(f"{k} {v}" for k, v in trades["exit_reason"].value_counts().items()),
        "",
        "By symbol:",
        trades.groupby("symbol")["pnl"].agg(["count", "sum"]).round(2).to_string(),
    ])


def run_grid(data, cfg, slippage_pct=0.05):
    """Every exit horizon at two profit targets, with a pass/fail per row."""
    rows, best, best_trades = [], None, None
    for rr in (1.0, 2.0):
        for label, (nights, cut_time) in HORIZONS.items():
            c = copy.copy(cfg)
            c.reward_risk = rr
            t = run_variant(data, c, nights, cut_time, slippage_pct)
            s = stats(t, c)
            rows.append({"exit": label, "target": f"{rr:.0f}:1", "trades": s["trades"],
                         "win%": round(s["win_rate"] * 100), "P&L $": round(s["pnl"], 2),
                         "prof factor": round(s["pf"], 2), "drawdown $": round(s["dd"], 2),
                         "1st half $": round(s["half1"], 2), "2nd half $": round(s["half2"], 2),
                         "verdict": "PASSES" if s["passes"] else ""})
            if s["passes"] and (best is None or s["pf"] > best):
                best, best_trades = s["pf"], (label, rr, t)
    return pd.DataFrame(rows), best_trades


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
    ap.add_argument("--no-grid", action="store_true")
    a = ap.parse_args()

    cfg = Config()
    data = load_csv(a.csv_dir) if a.csv_dir else load_alpaca(cfg, a.days)
    data = {s: to_et(df) for s, df in data.items()}
    n_days = len(trading_days(data))

    print("=" * 64)
    print("CURRENT SETTINGS (sell by 11:45 ET, 2:1 target)")
    print("=" * 64)
    base = run_variant(data, cfg, 0, cfg.flatten_time, a.slippage)
    print(summarize(base, n_days, cfg))
    if not base.empty:
        base.to_csv(a.out, index=False)

    if not a.no_grid:
        print()
        print("=" * 64)
        print("EXIT TIMING AND HOLDING PERIOD")
        print("=" * 64)
        print("A row PASSES only if profit factor > 1.2, at least 25 trades,")
        print("and both halves of the period were profitable.")
        print("Overnight holds fill gaps at the open: a stop cannot protect")
        print("you while the market is closed.")
        print()
        grid, best = run_grid(data, cfg, a.slippage)
        print(grid.to_string(index=False))
        print()
        if best is None:
            print("VERDICT: nothing passed. This strategy has no edge on this")
            print("watchlist. Do not trade it.")
        else:
            label, rr, t = best
            print(f"VERDICT: best passing variant is '{label}' at a {rr:.0f}:1 target.")
            print("A single passing row is not proof. Treat it as worth paper")
            print("trading, not as a reason to fund the account.")
            t.to_csv("best_variant_trades.csv", index=False)
