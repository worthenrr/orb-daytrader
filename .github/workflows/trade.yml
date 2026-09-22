# ORB Day Trader

A hands-off day trading bot for a small account ($400 to $1,000). It trades one
simple, well-known setup (the Opening Range Breakout), places every stop loss and
profit target the moment it buys, sells everything by 11:45 AM Eastern, and texts
your phone what it did. You never have to watch a chart during work.

**Start in paper trading (fake money). Stay there at least 4 weeks.** Most day
traders lose money. This bot is built to keep losses small and rules strict, not
to promise profits.

## What it does every weekday

| Time (Eastern) | Time (Houston) | What happens |
|---|---|---|
| 9:07 | 8:07 | GitHub starts the bot, it waits for the open |
| 9:30 to 9:45 | 8:30 to 8:45 | Records each stock's high and low (the "opening range") |
| 9:45 to 11:00 | 8:45 to 10:00 | Buys when a 5-minute candle closes above that high on strong volume, SPY is green, and the price hasn't run too far |
| Same moment | | Sends a stop loss (middle of the range) and a target (2x the risk) to Alpaca |
| 11:45 | 10:45 | Sells anything still open, sends you a summary, shuts down |
| 12:10 | 11:10 | Backup job sells anything left, in case the main run crashed |

## Built-in safety rules (for $400)

| Rule | Default | Setting name |
|---|---|---|
| Max loss per trade | $6.00 (1.5%) | `RISK_PER_TRADE_PCT` |
| Max size of one position | $200 (50%) | `MAX_POSITION_PCT` |
| Max trades per day | 2 | `MAX_TRADES_PER_DAY` |
| Stop for the day after losing | $12 (3%) | `DAILY_LOSS_LIMIT_PCT` |
| No overnight holds | sells at 11:45 ET | `FLATTEN_TIME` |
| Paper money only | on | `ALPACA_PAPER` |

It also **never touches stocks you already own**. If you hold CCJ long term, the
bot skips CCJ. Every order it places is tagged `orb-...`, and it only ever sells
its own tagged trades.

## Setup (about 30 minutes, one time)

### 1. Alpaca account (free)
1. Sign up at alpaca.markets. You get a paper trading account instantly.
2. In the paper dashboard, click **API Keys > Generate**. Copy the key and secret.

### 2. Phone alerts (free, optional but recommended)
1. Install the **ntfy** app (iPhone or Android).
2. Tap **+** and subscribe to a made-up topic nobody would guess, like `mike-orb-7k2p9x`.

### 3. GitHub (free)
1. Create a GitHub account and a **new repository**. Upload all these files, keeping the `.github/workflows` folder.
2. Go to **Settings > Secrets and variables > Actions > Secrets** and add:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `NTFY_TOPIC` (your topic name from step 2)
3. Under the **Variables** tab you can optionally add `ACCOUNT_BUDGET`, `WATCHLIST`,
   `DRY_RUN`, `TRADING_ENABLED` to change settings without editing code.
4. Go to **Actions**, enable workflows, pick **ORB day trader > Run workflow**, choose
   `status`. If it prints your paper account, you're connected.

**Cost note:** GitHub Actions is free and unlimited for **public** repos. Your keys
stay hidden in Secrets even if the repo is public. A private repo gets 2,000 free
minutes a month, and this bot uses roughly 2,500 to 3,000, so either make it public
or expect a small bill.

### 4. Backtest first
In **Actions > Run workflow**, choose `backtest`. It tests the rules on the last 120
days and prints win rate, total P&L and worst drawdown. The full trade list is
downloadable from the run page. If the results are bad, **don't trade it**. Try a
different watchlist first.

### 5. Let it run on paper
After that it runs every weekday by itself. Check your phone alerts and the Alpaca
app at lunch. After 4+ weeks, compare paper results to the backtest.

## Going live with real money (only after paper proves out)

Add two Variables: `ALPACA_PAPER` = `false` and `LIVE_TRADING_CONFIRM` =
`I_ACCEPT_THE_RISK`, then replace the API key secrets with your **live** keys. The
bot refuses to trade real money without both.

Things to know first:
- **Accounts under $2,000 at Alpaca are cash accounts.** Stock sales take one
  business day to settle, so you can only reuse the same dollars once per day.
  Two $200 trades on a $400 account fits inside that.
- The $25,000 pattern day trader rule was eliminated as of June 4, 2026, but
  brokers have until October 2027 to switch over. Confirm with Alpaca how your
  account is treated.
- Every winning day trade is a short-term capital gain taxed like regular income.
  Keep your Alpaca year-end statements.

## Pause, stop, or change it

- **Pause:** set Variable `TRADING_ENABLED` = `false`.
- **Signals only, no orders:** set `DRY_RUN` = `true`. You'll get "would buy" alerts.
- **Sell everything now:** Actions > Run workflow > `flatten` (works after 11:45 ET), or just sell in the Alpaca app.
- **Different stocks:** set `WATCHLIST`, e.g. `INTC,MP,UEC,UUUU,SOFI`. Stocks priced over ~$200 are skipped automatically at a $400 budget (one share would break the position limit).

## Run it on your own computer instead (optional)

```
pip install -r requirements.txt
set ALPACA_API_KEY=...   (Mac: export ALPACA_API_KEY=...)
set ALPACA_SECRET_KEY=...
python backtest.py --days 120
python bot.py
```

`python -m pytest tests -q` runs the offline tests (no keys needed).

## Files

| File | Purpose |
|---|---|
| `strategy.py` | The trading rules (shared by bot and backtest) |
| `bot.py` | The daily live loop |
| `broker.py` | Talks to Alpaca |
| `backtest.py` | Tests the rules on history |
| `config.py` | Every setting and its default |
| `notify.py` | Phone alerts |
| `.github/workflows/trade.yml` | The weekday schedule |
