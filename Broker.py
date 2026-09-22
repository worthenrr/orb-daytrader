"""Thin wrapper around Alpaca (alpaca-py). Everything the bot needs from the
brokerage goes through this class, which keeps the rest of the code easy to
test and easy to move to another broker later.

Every order the bot places gets a client_order_id starting with "orb-". The
bot only ever sells symbols it bought itself that day, so long-term stocks you
hold in the same account are never touched.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from strategy import ET

PREFIX = "orb-"


class AlpacaBroker:
    def __init__(self, cfg):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        key = os.environ["ALPACA_API_KEY"]
        secret = os.environ["ALPACA_SECRET_KEY"]
        self.cfg = cfg
        self.trading = TradingClient(key, secret, paper=cfg.paper)
        self.data = StockHistoricalDataClient(key, secret)
        # IEX is the free real-time feed. Paid subscribers can set DATA_FEED=sip.
        self.feed = DataFeed.SIP if os.environ.get("DATA_FEED", "iex").lower() == "sip" else DataFeed.IEX

    # ---- clock & account -------------------------------------------------
    def clock(self):
        c = self.trading.get_clock()
        return {
            "now": c.timestamp.astimezone(ET),
            "is_open": c.is_open,
            "next_open": c.next_open.astimezone(ET),
            "next_close": c.next_close.astimezone(ET),
        }

    def account(self):
        a = self.trading.get_account()
        return {
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "equity": float(a.equity),
            "last_equity": float(a.last_equity),
            "blocked": bool(a.trading_blocked or a.account_blocked),
        }

    def positions(self):
        return {p.symbol: float(p.qty) for p in self.trading.get_all_positions()}

    # ---- market data -----------------------------------------------------
    def bars(self, symbols, start, end):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        req = StockBarsRequest(
            symbol_or_symbols=list(symbols),
            timeframe=TimeFrame(self.cfg.bar_minutes, TimeFrameUnit.Minute),
            start=start, end=end, feed=self.feed,
        )
        df = self.data.get_stock_bars(req).df
        out = {}
        if df is None or df.empty:
            return out
        for sym in symbols:
            if sym in df.index.get_level_values(0):
                out[sym] = df.xs(sym, level=0)[["open", "high", "low", "close", "volume"]]
        return out

    def latest_price(self, symbol):
        from alpaca.data.requests import StockLatestTradeRequest

        res = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed))
        return float(res[symbol].price)

    # ---- orders ----------------------------------------------------------
    def submit_bracket(self, plan, client_order_id):
        """Market buy with a stop loss and profit target attached. The stop and
        target live on Alpaca's servers, so they protect you even if this
        program crashes or your internet drops."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        order = MarketOrderRequest(
            symbol=plan.symbol, qty=plan.qty, side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=plan.target),
            stop_loss=StopLossRequest(stop_price=plan.stop),
            client_order_id=client_order_id,
        )
        return self.trading.submit_order(order_data=order)

    def _orders(self, status, after=None, symbols=None):
        from alpaca.trading.requests import GetOrdersRequest

        return self.trading.get_orders(filter=GetOrdersRequest(
            status=status, after=after, symbols=symbols, limit=500, nested=False))

    def bot_symbols_today(self, day):
        """Symbols the bot bought today (rebuilt from order history, so a
        restart mid-morning still knows what it already traded)."""
        from alpaca.trading.enums import QueryOrderStatus

        start = datetime(day.year, day.month, day.day, tzinfo=ET).astimezone(timezone.utc)
        tag = f"{PREFIX}{day:%Y%m%d}-"
        syms = []
        for o in self._orders(QueryOrderStatus.ALL, after=start - timedelta(hours=1)):
            if (o.client_order_id or "").startswith(tag):
                syms.append(o.symbol)
        return syms

    def close_symbol(self, symbol):
        """Cancel the open stop/target for a symbol, then sell the shares."""
        from alpaca.trading.enums import QueryOrderStatus

        for o in self._orders(QueryOrderStatus.OPEN, symbols=[symbol]):
            try:
                self.trading.cancel_order_by_id(o.id)
            except Exception:
                pass
        # Give the cancels a moment to release the shares.
        import time as _t
        _t.sleep(2)
        return self.trading.close_position(symbol)

    def realized_today(self, day):
        """Rough P&L for the bot's symbols today, from filled orders."""
        from alpaca.trading.enums import QueryOrderStatus

        start = datetime(day.year, day.month, day.day, tzinfo=ET).astimezone(timezone.utc)
        rows = []
        for o in self._orders(QueryOrderStatus.CLOSED, after=start):
            if o.filled_qty and o.filled_avg_price:
                rows.append({"symbol": o.symbol, "side": str(o.side.value),
                             "qty": float(o.filled_qty), "price": float(o.filled_avg_price)})
        return pd.DataFrame(rows)
