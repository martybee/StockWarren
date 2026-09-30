# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**StockWarren** is an automated stock trading bot using the Alpaca API. It uses 7 technical indicators + ML signal validation to generate trades, with one-way trailing stops adapted from the FutureWarren futures trading bot.

- **Asset class**: US Equities only (paper trading via Alpaca)
- **Trading styles**: Day trading + swing trading
- **Language**: Pure Python
- **Account**: Paper trading account

## Planning Docs (read at session start)

- [docs/PLAN.md](docs/PLAN.md) — the living plan (v0.5-draft): where the project is and the milestone arc.
- [docs/SCHEMA.md](docs/SCHEMA.md) — spec for every persisted file and API payload.
- [docs/milestones/](docs/milestones/) — one work order per milestone (M1–M8).
- [docs/SESSION_NOTES.md](docs/SESSION_NOTES.md) — append-only session log + gotchas; add an entry after substantial sessions.

Current drafts were written by Claude (2026-08-19) from the codebase; Marty edits them and will supply final source docs — treat them as living, not final.

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
- `[watchlist]` — the shared A/B/C universe: F, PLTR, SOFI, NIO, RIVN, HOOD, SNAP, AMD, BAC, T (all three accounts inherit it; do not fork per-account)

**Account sizing** (verified 2026-08-25, discrepancy hour): all three paper accounts
hold **$9,000 each** — the ~$300 era is over. Current limits:
- `max_positions = 2`
- `max_position_pct = 40.0`
- `max_daily_loss = 200.0`
- Day-trade price band `min_price = 1.0`, `max_price = 500.0` (raised from 50.0 so
  AMD stays day-tradable at $9k); swing band `10.0`–`1000.0`

## Critical Rules (from FutureWarren heritage)

1. **One-way trailing stops**: Stops can ONLY tighten, NEVER loosen. See `risk_manager.py:update_trailing_stop`. This is the most important safety invariant — do not change it.
2. **Limit orders preferred**: Bot uses limit orders for entries (0.1% above/below current price), market orders only when explicitly scheduled.
3. **Paper trading only**: `paper=True` is hardcoded in `trading_bot.py`. Don't switch to live without explicit user approval.
4. **Order placement uses 2-3 retries max**: Higher retry counts on order placement risk duplicate orders. See `broker/client.py` decorators.

## Trading Safety Constitution (HARD invariants)

These seven rules are non-negotiable and **fail-closed** — when in doubt the answer is
always NO TRADE / NO NEW ORDER / SMALLER SIZE. The canonical, in-code copy lives in
`src/engine/safety.py` (module docstring). Do not weaken any of these without explicit
user approval.

| # | Rule | Enforced by |
|---|------|-------------|
| 1 | The strategy may **propose** trades, but the risk engine can always **reject** them. | `RiskManager.approve_order()` — the single mandatory veto gate; every entry passes through it. |
| 2 | The strategy may propose **code changes**, but cannot modify production code automatically. | Governance/process (this doc). The running bot has no code-editing capability. AI agents: propose diffs, never auto-apply to a running trading process. |
| 3 | It cannot change **position limits, loss limits, or leverage**. | `RiskManager` limits are read-only `@property`s backed by a `frozen` `_RiskLimits`; the **strategy** has no setter. A **human operator** may retune them via `apply_operator_override()` / the dashboard, clamped to hard ceilings (`RiskManager.OVERRIDABLE`). `MAX_LEVERAGE = 1.0` (cash only) is never overridable. |
| 4 | It cannot **disable stops or kill switches**. | Every entry must be stop-protected or it's immediately unwound (`_unwind_unprotected`, no naked positions). `KillSwitch` is file-backed; the bot can `trip()` but has **no reset method** — a human must delete `data/KILL_SWITCH.lock`. |
| 5 | Missing, stale, contradictory, or malformed data must produce **NO TRADE**. | `safety.validate_market_data()` gates every `_evaluate_symbol`. |
| 6 | Unrecognized broker responses must produce **NO NEW ORDERS**. | `safety.validate_order_response()`; an "unrecognized" classification trips the kill switch + per-tick halt. |
| 7 | Any uncertainty should **reduce the position or result in no trade**. | `safety.compute_uncertainty_factor()` scales size in [0,1]; below `MIN_SIZE_FACTOR` ⇒ 0 (no trade). |

**Kill switch operations:** to halt the bot, `emergency_stop()` (dashboard `POST /api/bot/emergency`)
trips it. To resume, a human removes `data/KILL_SWITCH.lock` by hand — there is no
programmatic reset, by design.

**Risk framework (conservative starting limits, `[risk_management]`):** the engine
enforces a layered risk model, all as % of account equity:
- `risk_per_trade_pct` (0.25) sizes each trade; `max_risk_per_trade_pct` (0.50) is a
  hard ceiling per trade; `max_combined_open_risk_pct` (1.0) caps summed open risk —
  all enforced in `RiskManager.approve_order()` (trim-or-reject).
- `max_daily_loss_pct` (1.5) and `max_weekly_loss_pct` (3.0) halt trading for the
  day/week; checked in `is_trading_allowed()`.
- `review_drawdown_pct` (5.0) flags the strategy for review (warns, keeps trading);
  `shutdown_drawdown_pct` (8.0) trips the kill switch (full shutdown), driven off
  peak-to-current portfolio equity.
- **Correlation** (`[correlation_groups]`): one position per correlated group
  (e.g. `ev_auto = F,NIO,RIVN`); enforced in `approve_order()`.
- Note: 0.25% risk on a ~$300 paper account is ~$0.75/trade, which frequently sizes
  to **0 shares / no trade** — the math is correct; the account is just small.

**Operator overrides (human-only risk tuning):** risk limits are configured in
`config/settings.ini`, but `[trading]` position/cash limits are forwarded into the
`RiskManager` in `trading_bot.py` (they would otherwise be ignored — the engine only
reads the `[risk_management]` section). A human can retune limits at runtime from the
**Safety modal** (header `SAFE/HALTED` badge or the "🛡 Safety Rules" button):
`max_positions`, `risk_per_trade_pct` (risk tolerance), `max_position_pct`,
`max_daily_loss_pct`, `min_risk_reward_ratio`. Every value is **clamped to hard
ceilings** in `RiskManager.OVERRIDABLE` and **persisted** to
`data/operator_overrides.json` (re-applied on startup). Endpoints: `GET/POST /api/overrides`,
`POST /api/overrides/reset`. Leverage is never overridable.

## Hard Rules — AI-Assisted Work (both tracks)

Merged 2026-08-19 from [docs/EXECUTION_GUIDE.md](docs/EXECUTION_GUIDE.md) §1, reconciled
against the Trading Safety Constitution above. Rule 3 reworded per Marty's explicit ruling:
the guide's `trading/order_gate.py` was deleted in CHART_PLAN v0.7 — the existing gate is
the gate. These bind every AI-assisted session on both tracks (engine M1–M8, chart C1–C8).

1. **Bar timestamps = interval START, stored UTC.** New York time is display/session
   logic only. (The market calendar keeps operating in `America/New_York` — the two
   regimes coexist by design; `docs/SCHEMA.md` states both.)
2. **A signal at time T may not read any bar after T.** The signal store
   (`signals/store.py`, once built) enforces this physically; never bypass the store.
3. **Every order goes through the EXISTING gate** — `RiskManager.approve_order()` +
   `src/engine/safety.py` (kill switch, overrides, limits). No direct Alpaca trading
   calls anywhere else. `trading/verdict_recorder.py` records the gate's verdicts as
   dispositions — it never re-gates. (Supersedes the guide's `order_gate.py`.)
4. **Never modify `gui/static/js/dashboard.js` beyond the single nav hook.** Chart
   code lives in `gui/static/js/chart.js`, its own file.
5. **Existing JSON/pickle files in `data/` are not migrated, renamed, or restructured.**
6. **bars/signals/trades/runs live in SQLite** (`data/stockwarren.db`) — signals never
   get written into the bars table.
7. **New dependencies require asking first.** Prefer stdlib.
8. **Timestamp-touching code requires tests**, including the DST-transition-day and
   half-day fixtures.
9. **The chart backend lives INSIDE the existing dashboard process** (`main.py
   --dash-only`). Never start a second process that opens its own Alpaca data connection.
10. **Never bypass or reorder the evaluation pipeline** (composite signal → validation →
    ML filter → risk engine). New strategies feed INTO it; nothing goes around it to Alpaca.

Workflow, from the same guide: work only on the current milestone's branch; every work
order, branch, and prompt names its **track** (engine "M" vs chart "C") — a session told
"do M4" with no track name must stop and ask; when the plan is ambiguous, STOP and ask
rather than deciding architecture silently.

(The guide's Commands block is **not** adopted: this repo's entry point remains
`python main.py`, not `python -m gui.app` — see Common Commands above.)

## Operational Safeguards (live-trading hardening)

- **Retry logic** (`src/utils/retry.py`): Exponential backoff for transient errors. Detects 408/429/5xx and connection errors. Permanent errors (4xx auth) fail fast.
- **Rotating logs** (`src/utils/logging_setup.py`): `logs/stockwarren.log` (10MB × 5 backups), `logs/errors.log` (warnings+), and append-only `logs/trades/trades_YYYY-MM.log` audit log that never rotates.
- **Market calendar** (`src/utils/market_calendar.py`): All times in `America/New_York`. Use `get_status(alpaca_client)` to get authoritative market state from Alpaca's clock.
- **EOD flattening** (`src/engine/eod_manager.py`): Background thread that closes day-trade positions N minutes before close (config: `close_before_eod_minutes`). Swing positions are spared. Untracked positions are closed defensively.
- **Slippage tracker** (`src/utils/slippage_tracker.py`): Records expected vs actual fill price + latency to `logs/slippage.csv`. Critical for paper-vs-live reality check.
- **Startup health check** (`main.py`): Waits up to 120s (configurable via `--startup-timeout`) for Alpaca API. Use `--skip-startup-check` to bypass during dev.
- **Graceful shutdown**: `main.py` catches SIGTERM/SIGINT and stops scheduler/EOD/bot in order before exit.

## Process Supervisors (engine M6: supervised operation)

- **macOS (THE deployment)**: [setup/services/com.stockwarren.bot.plist](setup/services/com.stockwarren.bot.plist)
  — configured for `martynbar`, runs **default mode** (bots + dashboard) with
  `STOCKWARREN_SUPERVISED=1`. KeepAlive restarts on crash, not on clean exit.
- **Linux**: [setup/services/stockwarren.service](setup/services/stockwarren.service) — untested, replace `YOUR_USER` (out of M6 scope).
- **Cross-platform**: [setup/supervisor.sh](setup/supervisor.sh) — bash fallback wrapper (max 10 restarts/hour).

**Crash-restart policy (CHART_PLAN §14 Decision 3, `src/engine/startup_policy.py`):**
on a supervised start, bots auto-resume ONLY if clean — no `KILL_SWITCH*.lock`
anywhere and no state file quarantined during load. **≥ 3 supervised restarts
within one hour trips every account's kill switch** and the process comes up
dashboard-only. Manual (`python main.py` by hand) starts get the same clean-state
check but never count toward the breaker. Starts are recorded in
`data/restart_history.json` (versioned state file). Under supervision, `main.py`
attaches `caffeinate -s -w <own pid>` — no hand-run caffeinate, ever.
**Staleness guard** (`src/utils/build_info.py`): `/api/status` carries
`process.{running_commit, disk_commit, stale}`; the dashboard header shows
**⚠ STALE CODE** when the running process no longer matches disk HEAD.
This policy is re-decided at engine M8 before any live money.

### Runbook

```bash
# install (once) / start
cp setup/services/com.stockwarren.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.stockwarren.bot.plist

launchctl list | grep stockwarren                        # status (PID, last exit code)
launchctl kickstart -k gui/$(id -u)/com.stockwarren.bot  # deliberate restart (e.g. to load new code)
launchctl unload ~/Library/LaunchAgents/com.stockwarren.bot.plist   # stop + disable

tail -f logs/launchd.out.log logs/stockwarren.log        # logs
curl -s http://127.0.0.1:5000/api/health                 # is it serving?

# clear a kill switch (HUMAN action; understand the cause first)
ls data/KILL_SWITCH*.lock && cat data/KILL_SWITCH*.lock  # what tripped, when, why
rm data/KILL_SWITCH_<account>.lock                       # then restart or start bots from the UI

# state-file quarantines (why a boot came up dashboard-only)
ls data/*.invalid-*
```

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

**Safety-invariant suite (engine M1):**

```bash
venv/bin/python -m pytest tests/ -q     # 86 tests, fully offline, ~0.1s
```

One module per constitution rule (`tests/test_rule<N>_*.py`, rule 4 gets two:
one-way stops and kill-switch/no-naked-positions). `tests/conftest.py` holds the
shared harness: a recording `MockAlpaca`, synthesized OHLCV bars, and a
hand-wired `TradingBot` (built via `object.__new__` so no network or config is
touched). The suite is mutation-checked — weakening a rule guard (the one-way
stop `>`, the Rule 7 `MIN_SIZE_FACTOR` floor) makes named tests fail. Keep it
that way: a new safety rule lands WITH a test that dies if the rule is weakened.

`tests/test_trading_gate.py` is an older standalone script (also runs under
`venv/bin/python tests/test_trading_gate.py`); it executes at pytest collection
time and fails the run if its checks fail.

Manual smoke flow:
1. Start dashboard: `python main.py --dash-only`
2. Verify connection: `curl http://127.0.0.1:5000/api/status`
3. Test market data: `curl http://127.0.0.1:5000/api/stocks/search?q=AAPL`
4. Check market hours: `curl http://127.0.0.1:5000/api/market`

## GitHub

Repository: https://github.com/martybee/StockWarren

When committing, the user's git is configured as `martybee`. Sister project FutureWarren lives at https://github.com/martybee/FutureWarren.
