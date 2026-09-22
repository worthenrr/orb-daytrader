"""Opening Range Breakout (ORB) rules, kept free of any broker code so the
live bot and the backtester use exactly the same logic.

The idea in plain English:
  1. Watch the first 15 minutes after the 9:30 ET open. Record the high and low.
  2. If a 5-minute bar later CLOSES above that high on above-average volume,
     buy. (Longs only; shorting needs a margin account.)
  3. Stop loss goes at the middle (or bottom) of the opening range.
  4. Profit target is 2x the amount risked.
  5. Anything still open at the flatten time gets sold. Nothing is held overnight.

Bars are a pandas DataFrame indexed by timezone-aware timestamps (bar START
time) with columns open, high, low, close, volume.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import math
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)


@dataclass
class OpeningRange:
    high: float
    low: float

    @property
    def mid(self):
        return (self.high + self.low) / 2

    @property
    def width(self):
        return self.high - self.low


@dataclass
class TradePlan:
    symbol: str
    entry: float
    stop: float
    target: float
    qty: int
    reason: str

    @property
    def risk_per_share(self):
        return self.entry - self.stop

    @property
    def cost(self):
        return self.entry * self.qty


def to_et(df):
    """Return a copy indexed in Eastern time."""
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(ET)
    return out.sort_index()


def session_bars(df, day):
    """Regular-session bars (9:30 to 16:00 ET) for one calendar date."""
    df = to_et(df)
    d = df[df.index.date == day]
    t = d.index.time
    return d[(t >= MARKET_OPEN) & (t < time(16, 0))]


def opening_range(bars, cfg):
    """High/low of the first OR_MINUTES. None if the data isn't complete yet."""
    if bars.empty:
        return None
    day = bars.index[0].date()
    start = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
    end = start + timedelta(minutes=cfg.or_minutes)
    orb = bars[(bars.index >= start) & (bars.index < end)]
    needed = max(1, cfg.or_minutes // cfg.bar_minutes)
    if len(orb) < needed:
        return None
    return OpeningRange(high=float(orb["high"].max()), low=float(orb["low"].min()))


def completed_bars(bars, now, cfg):
    """Drop the bar that is still forming (its end time is after `now`)."""
    return bars[bars.index + pd.Timedelta(minutes=cfg.bar_minutes) <= now]


def market_ok(market_bars, cfg):
    """Only buy breakouts when the broad market (SPY) is green on the day."""
    if not cfg.market_filter:
        return True
    if market_bars is None or market_bars.empty:
        return False
    return float(market_bars["close"].iloc[-1]) > float(market_bars["open"].iloc[0])


def breakout_signal(bars, orng, cfg):
    """Check the most recent COMPLETED bar. Returns (bool, reason)."""
    if orng is None or len(bars) < 2:
        return False, "no opening range yet"
    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if last.name.time() < cfg.entry_start:
        return False, "before entry window"
    price = float(last["close"])
    if price < cfg.min_price:
        return False, "price below minimum"
    range_pct = orng.width / orng.low * 100 if orng.low else 0
    if range_pct > cfg.max_range_pct:
        return False, f"opening range too wide ({range_pct:.1f}%)"
    if price <= orng.high:
        return False, "no breakout"
    if float(prev["close"]) > orng.high:
        return False, "breakout already happened (not fresh)"
    avg_vol = float(bars["volume"].iloc[:-1].mean())
    if avg_vol > 0 and float(last["volume"]) < cfg.volume_mult * avg_vol:
        return False, "volume too light"
    if price > orng.high * (1 + cfg.max_chase_pct / 100):
        return False, "too far above range (chasing)"
    return True, f"closed {price:.2f} above OR high {orng.high:.2f} on strong volume"


def plan_trade(symbol, entry, orng, cfg, cash_available):
    """Turn a signal into exact share count, stop and target. None = skip."""
    stop = orng.mid if cfg.stop_mode == "mid" else orng.low
    # Never use a stop so tight that normal noise hits it.
    min_gap = entry * cfg.min_stop_pct / 100
    if entry - stop < min_gap:
        stop = entry - min_gap
    stop = round(stop, 2)
    risk_ps = entry - stop
    if risk_ps <= 0:
        return None
    target = round(entry + cfg.reward_risk * risk_ps, 2)
    max_dollars = min(cfg.max_position_dollars, cash_available)
    qty = math.floor(min(cfg.risk_dollars / risk_ps, max_dollars / entry))
    if qty < 1:
        return None
    return TradePlan(symbol, round(entry, 2), stop, target, qty,
                     reason=f"risking ${risk_ps * qty:.2f} to make ${(target - entry) * qty:.2f}")
