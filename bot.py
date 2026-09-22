"""The daily trading bot.

    python bot.py            # run today's session (waits for the open if early)
    python bot.py --flatten  # backup: sell anything the bot still holds
    python bot.py --status   # print account and today's bot trades

A normal day, fully hands-off:
  09:30-09:45 ET  record the opening range on every watchlist symbol
  09:45-11:00 ET  look for breakouts, max 2 trades, stop+target sent with each buy
  11:45 ET        sell whatever is still open, send you a summary, exit
"""
import argparse
import logging
import sys
import time as _time
from datetime import datetime, timedelta

from config import Config
import notify
from strategy import (ET, MARKET_OPEN, breakout_signal, completed_bars,
                      market_ok, opening_range, plan_trade, session_bars)

log = logging.getLogger("orb")


class Session:
    def __init__(self, cfg, broker, sleep=_time.sleep):
        self.cfg, self.broker, self.sleep = cfg, broker, sleep
        self.traded = []          # symbols the bot bought (or flagged, in dry run) today
        self.skip = set()         # symbols you already own: never touched
        self.spent = 0.0
        self.loss_hit = False

    # ---------------------------------------------------------------- utils
    def now(self):
        return self.broker.clock()["now"]

    def at(self, day, t):
        return datetime.combine(day, t, tzinfo=ET)

    def bot_pnl(self, day):
        """Today's P&L on the bot's own symbols (realized + open)."""
        if not self.traded or self.cfg.dry_run:
            return 0.0
        fills = self.broker.realized_today(day)
        pnl = 0.0
        if not fills.empty:
            fills = fills[fills["symbol"].isin(self.traded)]
            for _, r in fills.iterrows():
                sign = 1 if r["side"] == "sell" else -1
                pnl += sign * r["qty"] * r["price"]
        for sym, qty in self.broker.positions().items():
            if sym in self.traded:
                pnl += qty * self.broker.latest_price(sym)
        return pnl

    def flatten(self, why):
        if self.cfg.dry_run:
            return
        held = self.broker.positions()
        for sym in dict.fromkeys(self.traded):
            if sym in held:
                try:
                    self.broker.close_symbol(sym)
                    notify.send(self.cfg, f"Closed {sym}", why)
                except Exception as exc:
                    notify.send(self.cfg, f"FAILED to close {sym}",
                                f"{exc}. Open the Alpaca app and sell it by hand.", "high")

    # ------------------------------------------------------------- entries
    def scan(self, now):
        cfg, day = self.cfg, now.date()
        symbols = [s for s in cfg.watchlist if s not in self.traded and s not in self.skip]
        if not symbols:
            return
        start = self.at(day, MARKET_OPEN)
        data = self.broker.bars(symbols + [cfg.market_symbol], start, now)
        mkt = session_bars(data[cfg.market_symbol], day) if cfg.market_symbol in data else None
        if not market_ok(mkt, cfg):
            log.info("market filter: %s is red today, no new longs", cfg.market_symbol)
            return
        for sym in symbols:
            if len(self.traded) >= cfg.max_trades_per_day:
                return
            if sym not in data:
                continue
            bars = completed_bars(session_bars(data[sym], day), now, cfg)
            orng = opening_range(bars, cfg)
            ok, why = breakout_signal(bars, orng, cfg)
            log.info("%-5s %s", sym, why)
            if not ok:
                continue
            self.enter(sym, orng, why, day)

    def enter(self, sym, orng, why, day):
        cfg = self.cfg
        price = self.broker.latest_price(sym)
        if price > orng.high * (1 + cfg.max_chase_pct / 100):
            log.info("%s ran to %.2f before we could buy; skipping", sym, price)
            return
        acct = self.broker.account()
        cash = min(acct["cash"], acct["buying_power"], cfg.budget - self.spent)
        plan = plan_trade(sym, price, orng, cfg, cash)
        if plan is None or not (plan.stop < price < plan.target):
            log.info("%s: position too small for the risk rules; skipping", sym)
            return
        msg = (f"{plan.qty} sh @ ~${plan.entry:.2f} | stop ${plan.stop:.2f} | "
               f"target ${plan.target:.2f} | {plan.reason}. Signal: {why}")
        self.traded.append(sym)
        if cfg.dry_run:
            notify.send(cfg, f"[DRY RUN] Would buy {sym}", msg)
            return
        coid = f"orb-{day:%Y%m%d}-{sym}-{len(self.traded)}"
        try:
            self.broker.submit_bracket(plan, coid)
            self.spent += plan.cost
            notify.send(cfg, f"Bought {sym}", msg)
        except Exception as exc:
            notify.send(cfg, f"Order for {sym} rejected", str(exc), "high")

    # ---------------------------------------------------------------- main
    def run(self):
        cfg = self.cfg
        cfg.check_live_guard()
        if not cfg.enabled:
            log.info("TRADING_ENABLED is false; nothing to do.")
            return "disabled"

        clk = self.broker.clock()
        now, day = clk["now"], clk["now"].date()
        if not clk["is_open"] and clk["next_open"].date() != day:
            log.info("Market closed today (weekend or holiday).")
            return "closed"
        if not clk["is_open"] and clk["next_open"] - now > timedelta(minutes=75):
            log.info("Too early (this is the off-season duplicate schedule). Exiting.")
            return "too-early"
        if now >= self.at(day, cfg.flatten_time):
            log.info("Past flatten time; the backup flatten job covers this. Exiting.")
            return "late"
        if now >= self.at(day, cfg.entry_end) and not self.broker.bot_symbols_today(day):
            log.info("Past the entry window and no bot trades today. Exiting.")
            return "late"

        acct = self.broker.account()
        if acct["blocked"]:
            notify.send(cfg, "Account blocked", "Alpaca reports trading is blocked.", "high")
            return "blocked"

        # Remember anything already traded today (in case of a restart), and
        # stay away from stocks you own for the long term.
        self.traded = list(dict.fromkeys(self.broker.bot_symbols_today(day)))
        self.skip = {s for s in self.broker.positions() if s not in self.traded}

        if not clk["is_open"]:
            wait = (clk["next_open"] - now).total_seconds()
            log.info("Waiting %.0f min for the open.", wait / 60)
            self.sleep(max(0, wait))

        mode = "DRY RUN" if cfg.dry_run else ("PAPER" if cfg.paper else "LIVE MONEY")
        notify.send(cfg, f"ORB bot started ({mode})",
                    f"Budget ${cfg.budget:.0f}, risk ${cfg.risk_dollars:.2f}/trade, "
                    f"max {cfg.max_trades_per_day} trades, daily stop -${cfg.daily_loss_limit_dollars:.2f}. "
                    f"Watching {', '.join(cfg.watchlist)}.")

        entry_start = self.at(day, cfg.entry_start)
        entry_end = self.at(day, cfg.entry_end)
        flat = self.at(day, cfg.flatten_time)

        while True:
            now = self.now()
            if now >= flat:
                break
            in_window = entry_start <= now < entry_end
            if in_window and not self.loss_hit and len(self.traded) < cfg.max_trades_per_day:
                try:
                    self.scan(now)
                except Exception as exc:
                    log.warning("scan error (will retry): %s", exc)

            pnl = self.bot_pnl(day)
            if not self.loss_hit and pnl <= -cfg.daily_loss_limit_dollars:
                self.loss_hit = True
                self.flatten(f"Daily loss limit hit (${pnl:.2f}).")
                notify.send(cfg, "Daily loss limit hit", f"Down ${-pnl:.2f}. Done for today.", "high")

            open_bot = [s for s in self.broker.positions() if s in self.traded]
            if now >= entry_end and not open_bot:
                log.info("Entry window over and nothing open. Ending early.")
                break
            self.sleep(cfg.poll_seconds)

        self.flatten("Flatten time reached. No overnight holds.")
        pnl = self.bot_pnl(day)
        notify.send(cfg, "ORB bot done for today",
                    f"Trades: {', '.join(self.traded) or 'none'}. Bot P&L today: ${pnl:+.2f}.")
        return "done"


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    p = argparse.ArgumentParser()
    p.add_argument("--flatten", action="store_true")
    p.add_argument("--status", action="store_true")
    args = p.parse_args(argv)

    from broker import AlpacaBroker
    cfg = Config()
    broker = AlpacaBroker(cfg)
    s = Session(cfg, broker)
    day = broker.clock()["now"].date()

    if args.status:
        print(broker.account())
        print("Bot symbols today:", broker.bot_symbols_today(day))
        print("Positions:", broker.positions())
        return
    if args.flatten:
        now = broker.clock()["now"]
        if now < s.at(day, cfg.flatten_time):
            log.info("Before flatten time; backup job has nothing to do.")
            return
        s.traded = broker.bot_symbols_today(day)
        s.flatten("Backup flatten job.")
        return
    s.run()


if __name__ == "__main__":
    main()
