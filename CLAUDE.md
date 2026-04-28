# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**StockWarren** is an automated stock trading bot using the Alpaca API. It uses 7 technical indicators + ML signal validation to generate trades, with one-way trailing stops adapted from the FutureWarren futures trading bot.

- **Asset class**: US Equities only (paper trading via Alpaca)
- **Trading styles**: Day trading + swing trading
- **Language**: Pure Python (no C++ unlike FutureWarren)
- **Account**: Paper trading account with ~$300 (configured for small balance)

## Critical Naming Note

**Avoid naming any local module `alpaca/`** — it shadows the installed `alpaca-py` package. Our wrapper lives in `broker/` for this reason. If you create new code that imports from Alpaca, use:
- `from alpaca.trading.client import TradingClient` (the installed SDK)
- `from broker.client import AlpacaClient` (our wrapper)

## Architecture

```
StockWarren/
├── main.py                       # Entry point (--bot-only / --dash-only flags)
├── broker/client.py              # Alpaca API wrapper (NOT named "alpaca/")
├── src/
│   ├── engine/
│   │   ├── trading_bot.py       # Main orchestrator
│   │   ├── risk_manager.py      # One-way trailing stops, position sizing
│   │   └── scheduler.py         # Time-scheduled trade execution
│   ├── indicators/technical.py   # 7 indicators (RSI, MACD, VWAP, BB, EMA, Vol, ATR)
│   ├── ml/signal_validator.py   # Random Forest signal filtering
│   └── scanner/stock_scanner.py # Watchlist + market scanner
├── gui/
│   ├── app.py                   # Flask + SocketIO dashboard
│   ├── templates/index.html     # Dashboard UI
│   └── static/{css,js}          # Dark theme + JS
├── notifications/notifier.py    # Discord webhooks + email
├── config/settings.ini          # ALL configuration here
└── data/scheduled_trades.json   # Persisted scheduled trades
```

## Common Commands

```bash
# Activate venv (always do this first)
source venv/bin/activate

# Run bot + dashboard (most common)
python main.py

# Dashboard only (for monitoring without trading)
python main.py --dash-only

# Bot only (no UI)
python main.py --bot-only

# Install/update deps
pip install -r requirements.txt

# Restart dashboard during dev
pkill -f "main.py" && sleep 2 && python main.py --dash-only > /dev/null 2>&1 &
```

## Configuration

All settings live in `config/settings.ini`. Key sections:

- `[trading]` — `max_positions`, `max_position_pct`, mode (day/swing/both)
- `[signals]` — `min_signal_strength` (default 65), `min_confirmations` (default 2)
- `[risk_management]` — daily loss limits, stop loss %, trailing stop activation
- `[indicators]` — periods for RSI, MACD, EMA, Bollinger, ATR
- `[watchlist]` — default symbols (currently configured for ~$300 account: F, PLTR, SOFI, etc.)

**Account constraint**: Paper account currently has ~$300, so config is tuned for small positions:
- `max_positions = 2` (not 5)
- `max_position_pct = 40.0` (not 20)
- `max_daily_loss = 30.0` (not 500)
- `min_price = 1.0`, `max_price = 50.0` (only affordable stocks)

## Critical Rules (from FutureWarren heritage)

1. **One-way trailing stops**: Stops can ONLY tighten, NEVER loosen. See `risk_manager.py:update_trailing_stop`. This is the most important safety invariant — do not change it.
2. **Limit orders preferred**: Bot uses limit orders for entries (0.1% above/below current price), market orders only when explicitly scheduled.
3. **Paper trading only**: `paper=True` is hardcoded in `trading_bot.py`. Don't switch to live without explicit user approval.
4. **Order placement uses 2-3 retries max**: Higher retry counts on order placement risk duplicate orders. See `broker/client.py` decorators.

## Operational Safeguards (live-trading hardening)

- **Retry logic** (`src/utils/retry.py`): Exponential backoff for transient errors. Detects 408/429/5xx and connection errors. Permanent errors (4xx auth) fail fast.
- **Rotating logs** (`src/utils/logging_setup.py`): `logs/stockwarren.log` (10MB × 5 backups), `logs/errors.log` (warnings+), and append-only `logs/trades/trades_YYYY-MM.log` audit log that never rotates.
- **Market calendar** (`src/utils/market_calendar.py`): All times in `America/New_York`. Use `get_status(alpaca_client)` to get authoritative market state from Alpaca's clock.
- **EOD flattening** (`src/engine/eod_manager.py`): Background thread that closes day-trade positions N minutes before close (config: `close_before_eod_minutes`). Swing positions are spared. Untracked positions are closed defensively.
- **Slippage tracker** (`src/utils/slippage_tracker.py`): Records expected vs actual fill price + latency to `logs/slippage.csv`. Critical for paper-vs-live reality check.
- **Startup health check** (`main.py`): Waits up to 120s (configurable via `--startup-timeout`) for Alpaca API. Use `--skip-startup-check` to bypass during dev.
- **Graceful shutdown**: `main.py` catches SIGTERM/SIGINT and stops scheduler/EOD/bot in order before exit.

## Process Supervisors

- **macOS**: [setup/services/com.stockwarren.bot.plist](setup/services/com.stockwarren.bot.plist) — replace `YOUR_USER` and load with `launchctl load`.
- **Linux**: [setup/services/stockwarren.service](setup/services/stockwarren.service) — replace `YOUR_USER` and enable with `systemctl`.
- **Cross-platform**: [setup/supervisor.sh](setup/supervisor.sh) — bash wrapper with exponential backoff and rate limiting (max 10 restarts/hour).

## Health Endpoint

`GET /api/health` returns `{"healthy": bool, "latency_ms": int}` with 503 if Alpaca API is unreachable. Hook this into uptime monitors (UptimeRobot, healthchecks.io, etc.).

## Key API Endpoints (Flask)

- `GET /api/status` — Bot + account status (used by dashboard polling)
- `POST /api/scan` — Run watchlist scanner
- `GET /api/scheduled` — List pending + history of scheduled trades
- `POST /api/scheduled` — Schedule a new trade
- `DELETE /api/scheduled/<id>` — Cancel a scheduled trade
- `GET /api/stocks/search?q=<query>` — Autocomplete stock symbols
- `POST /api/bot/{start,stop,emergency}` — Bot control

## Asset Cache

`gui/app.py` caches the full Alpaca asset list for 1 hour (~9000 stocks). The cache is used by the autocomplete endpoint. First call after restart is slow (~2-3 sec), subsequent calls are instant.

## Scheduled Trades

`src/engine/scheduler.py` runs in its own background thread. Checks every 5 seconds for trades whose `scheduled_time` has arrived. Persists to `data/scheduled_trades.json`. Skips execution if market is closed and marks as MISSED if the window passes by 5 minutes.

## ML Model

The Random Forest validator starts untrained. It approves all signals until enough trades have completed. Models are saved to `data/models/` and auto-retrain every 10 new training samples once 50+ samples exist.

## Environment

- Python 3.11+ in venv at `venv/`
- API keys in `.env` (gitignored) — `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- Logs in `logs/stockwarren.log` and `logs/trade_history.csv`
- Dashboard runs at `http://127.0.0.1:5000` (localhost only by default)

## Testing

No test suite yet. Manual testing flow:
1. Start dashboard: `python main.py --dash-only`
2. Verify connection: `curl http://127.0.0.1:5000/api/status`
3. Test market data: `curl http://127.0.0.1:5000/api/stocks/search?q=AAPL`
4. Check market hours: `curl http://127.0.0.1:5000/api/market`

## GitHub

Repository: https://github.com/martybee/StockWarren

When committing, the user's git is configured as `martybee`. Sister project FutureWarren lives at https://github.com/martybee/FutureWarren.
