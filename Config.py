"""All settings live here. Every value can be overridden with an environment
variable of the same name (that's how GitHub Actions passes them in).

Defaults are sized for a $400 starting account.
"""
import os
from dataclasses import dataclass, field
from datetime import time


def _env(name, default):
    return os.environ.get(name, default)


def _bool(name, default):
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "y", "on")


def _time(name, default):
    hh, mm = _env(name, default).split(":")
    return time(int(hh), int(mm))


def _list(name, default):
    return [s.strip().upper() for s in _env(name, default).split(",") if s.strip()]


@dataclass
class Config:
    # --- Account / safety -------------------------------------------------
    # Paper trading (fake money) is ON by default. To trade real money you must
    # set ALPACA_PAPER=false AND LIVE_TRADING_CONFIRM=I_ACCEPT_THE_RISK.
    paper: bool = field(default_factory=lambda: _bool("ALPACA_PAPER", True))
    live_confirm: str = field(default_factory=lambda: _env("LIVE_TRADING_CONFIRM", ""))
    # DRY_RUN=true: find signals and send alerts, but never place orders.
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))
    # Master switch. Set TRADING_ENABLED=false to pause without deleting anything.
    enabled: bool = field(default_factory=lambda: _bool("TRADING_ENABLED", True))

    # The most money the bot will ever treat as "its" account, even if the
    # brokerage account holds more.
    budget: float = field(default_factory=lambda: float(_env("ACCOUNT_BUDGET", "400")))

    # --- Risk rules -------------------------------------------------------
    risk_per_trade_pct: float = field(default_factory=lambda: float(_env("RISK_PER_TRADE_PCT", "1.5")))   # $6.00 on $400
    max_position_pct: float = field(default_factory=lambda: float(_env("MAX_POSITION_PCT", "50")))        # $200 on $400
    max_trades_per_day: int = field(default_factory=lambda: int(_env("MAX_TRADES_PER_DAY", "2")))
    daily_loss_limit_pct: float = field(default_factory=lambda: float(_env("DAILY_LOSS_LIMIT_PCT", "3")))  # $12.00 on $400

    # --- Strategy: opening range breakout ---------------------------------
    watchlist: list = field(default_factory=lambda: _list(
        "WATCHLIST", "INTC,CCJ,MP,UEC,UUUU,USAR,SAIL,LEU"))
    market_symbol: str = field(default_factory=lambda: _env("MARKET_SYMBOL", "SPY").upper())
    market_filter: bool = field(default_factory=lambda: _bool("MARKET_FILTER", True))
    bar_minutes: int = field(default_factory=lambda: int(_env("BAR_MINUTES", "5")))
    or_minutes: int = field(default_factory=lambda: int(_env("OR_MINUTES", "15")))
    reward_risk: float = field(default_factory=lambda: float(_env("REWARD_RISK", "2.0")))
    stop_mode: str = field(default_factory=lambda: _env("STOP_MODE", "mid").lower())  # "mid" or "low"
    volume_mult: float = field(default_factory=lambda: float(_env("VOLUME_MULT", "1.2")))
    max_chase_pct: float = field(default_factory=lambda: float(_env("MAX_CHASE_PCT", "0.5")))
    min_stop_pct: float = field(default_factory=lambda: float(_env("MIN_STOP_PCT", "0.3")))
    max_range_pct: float = field(default_factory=lambda: float(_env("MAX_RANGE_PCT", "4.0")))
    min_price: float = field(default_factory=lambda: float(_env("MIN_PRICE", "3")))

    # --- Schedule (US/Eastern, the exchange's clock) ----------------------
    entry_start: time = field(default_factory=lambda: _time("ENTRY_START", "09:45"))
    entry_end: time = field(default_factory=lambda: _time("ENTRY_END", "11:00"))
    flatten_time: time = field(default_factory=lambda: _time("FLATTEN_TIME", "11:45"))
    poll_seconds: int = field(default_factory=lambda: int(_env("POLL_SECONDS", "30")))

    # --- Phone alerts (optional, free): install the ntfy app, subscribe to a
    # hard-to-guess topic name, and put that name in NTFY_TOPIC.
    ntfy_topic: str = field(default_factory=lambda: _env("NTFY_TOPIC", ""))

    @property
    def risk_dollars(self):
        return self.budget * self.risk_per_trade_pct / 100

    @property
    def max_position_dollars(self):
        return self.budget * self.max_position_pct / 100

    @property
    def daily_loss_limit_dollars(self):
        return self.budget * self.daily_loss_limit_pct / 100

    def check_live_guard(self):
        if not self.paper and self.live_confirm != "I_ACCEPT_THE_RISK":
            raise SystemExit(
                "Refusing to trade real money: set LIVE_TRADING_CONFIRM=I_ACCEPT_THE_RISK "
                "if you really mean to switch ALPACA_PAPER off.")
