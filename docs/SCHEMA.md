# StockWarren — Data & API Schema Spec

> **Version:** v0.3 — v0.1 drafted 2026-08-19 by Claude from the live files
> and API responses; v0.2 amended the same day per [CHART_PLAN.md](CHART_PLAN.md) §14
> (database clause, UTC-for-new-stores convention, "New stores" section); v0.3
> (2026-09-28) reflects engine M2 SHIPPED: schema_version + fail-closed quarantine
> for the two state files, and tz-aware `scheduled_time` semantics. Shared
> constitution for both tracks (engine M, chart C).

Purpose: the single source of truth for every file StockWarren persists and every
API payload the dashboard depends on. If code and this spec disagree, that's a bug
in one of them — fix whichever is wrong, deliberately.

## Conventions

- **Legacy state** is **flat files** under `data/` and `logs/` (JSON + pickles). It stays
  exactly as it is — never migrated, renamed, or restructured. **New stores** (bars,
  signals, trades, runs — see "New stores" below) live in **SQLite** at
  `data/stockwarren.db`, the one database in the system; the chart, the Compare page,
  ML training samples, and the decision log are views over its rows.
- Alpaca is the source of truth for positions, orders, and equity — never persisted locally.
- **Legacy files:** timestamps are ISO-8601; market/session logic runs in `America/New_York`.
  **New stores:** all timestamps at rest are **UTC**; New York time is display and
  session logic only. Both regimes coexist by design (CHART_PLAN §14, discrepancy 4).
- **Shipped (M2, 2026-09-28)** for `scheduled_trades.json` and
  `operator_overrides_*.json`: a top-level `"schema_version": 1` field, written on
  every save. Loaders validate on read and **fail closed**: a versionless file is
  legacy v0 (accepted, upgraded on next save); a structurally invalid file or a
  FUTURE schema_version is **quarantined** — renamed `<name>.invalid-<utc-stamp>`,
  never overwritten — and the caller falls back to its safe default (empty
  scheduler state / config-baseline limits). Record- or value-level damage is
  dropped loudly while intact data still loads. Enforcement:
  `src/utils/state_schema.py`; tests: `tests/test_m2_state_files.py`.
  (`company_profiles.json` is exempt: it is a cache, deleted on mismatch.)

## Files under `data/`

### `scheduled_trades.json` — writer: `src/engine/scheduler.py`

```json
{
  "schema_version": 1,
  "next_id": 4,
  "pending": [ <trade>, ... ],
  "history": [ <trade>, ... ]
}
```

Each `<trade>`:

| Field | Type | Notes |
|---|---|---|
| `id` | string | `"ST-NNNN"`, from `next_id` |
| `symbol` | string | uppercase ticker (not validated against Alpaca at entry — see ST-0002/0003 failures) |
| `side` | string | `"buy"` \| `"sell"` |
| `qty` | number | shares |
| `order_type` | string | `"market"` \| `"limit"` |
| `limit_price` | number\|null | only for limit orders |
| `stop_loss_pct`, `take_profit_pct` | number\|null | optional brackets |
| `scheduled_time` | string | ISO; **naive = America/New_York wall time** (ambiguous fall-back times = first occurrence, PEP 495 fold=0); offset-aware accepted; all comparisons happen in aware UTC; unparseable values are refused at creation (M2) |
| `status` | string | `pending` → `executed` \| `cancelled` \| `failed` \| `missed` (missed = window passed by 5 min) |
| `created_at`, `executed_at` | string | ISO; `executed_at` empty until fill |
| `result_order_id`, `error_message`, `notes` | string | audit fields |

### `operator_overrides_<account>.json` — writer: `RiskManager.apply_operator_override()`

One file per account (`alpha`, `beta`, `gamma`). Flat numeric dict; every value is
clamped to `RiskManager.OVERRIDABLE` ceilings before persisting. Observed keys:

`max_combined_open_risk_pct`, `max_daily_loss`, `max_daily_loss_pct`,
`max_position_pct`, `max_positions`, `max_risk_per_trade_pct`,
`max_weekly_loss_pct`, `min_risk_reward_ratio`, `review_drawdown_pct`,
`risk_per_trade_pct`, `shutdown_drawdown_pct`.

Leverage never appears here — `MAX_LEVERAGE = 1.0` is not overridable.

### `models/<account>/` — writer: `src/ml/signal_validator.py`

Per-account Random-Forest artifacts (pickled). Written on retrain (every 10 new
samples once ≥ 50 exist). Treat as opaque; never hand-edit.

### `company_profiles.json` — writer: dashboard prefetch

Cache of symbol → company description for the UI. Safe to delete; it rebuilds.

### `KILL_SWITCH.lock` — writer: `KillSwitch.trip()`

Presence of the file = tripped (no new orders, bot halts). **No code path removes
it**; a human deletes it by hand to resume. Absence = normal operation.

## Files under `logs/`

| File | Format | Rotation |
|---|---|---|
| `stockwarren.log` | text log, all levels | 10 MB × 5 backups |
| `errors.log` | warnings and up | rotating |
| `trades/trades_YYYY-MM.log` | append-only trade audit, one file per month | **never rotates** |
| `slippage.csv` | expected vs actual fill price + latency per fill | append-only (not yet created — no fills) |

## New stores — `data/stockwarren.db` (SQLite)

Four tables, from [CHART_PLAN.md](CHART_PLAN.md) §5. Field semantics below are the
frozen part; **exact SQLite types, constraints, and indexes are the next SCHEMA task**
(CHART_PLAN §13) and will be added here before engine M2/C1 build against them.
Nothing merges at rest — the chart joins by symbol + time + account at draw time.

Conventions for the new stores (one sentence each, per CHART_PLAN §13/§14):

- **Timestamp:** a bar's timestamp is the **interval START, stored UTC**; converted to
  New York only for display and session logic.
- **Snapping:** an event at 10:32:13 belongs to the 10:32:00 candle — **floor to
  interval start**; the same rule applies to millisecond fill times.
- **Empty minutes:** a minute with no trades stores **no row** and the chart draws a
  **visible gap**; the backtester values positions at the **last traded price** — both
  answer "price at 10:37?" with the last real trade.
- **Extended hours:** **regular session only**, everywhere, until chart + model +
  backtest change together.

### `bars` — what the market did

| Field | Notes |
|---|---|
| `timestamp` | interval START, UTC |
| `symbol` | |
| `open`, `high`, `low`, `close`, `volume` | OHLCV |
| `feed` | `iex` \| `sip` — never mixed silently (hazard 4.1) |
| `adjustment` | `raw` \| `split` \| `dividend` \| `all` (hazard 4.3) |
| `timeframe` | `1Min` \| `1Day` \| … — both trading styles are rows, not schemas |

### `signals` — what a model thought

| Field | Notes |
|---|---|
| `signal_id` | |
| `run_id` | → `runs` |
| `account` | `alpha` \| `beta` \| `gamma` |
| `timestamp` | UTC, actual decision moment |
| `symbol` | |
| `signal` | `BUY` \| `SELL` \| … |
| `price` | the price the model saw |
| `confidence` | 0–1 |
| `source` | `training` \| `backtest` \| `live_model` |
| `timeframe` | bars the signal was computed on (hazard 4.15) |
| `rationale` | optional text — an LLM strategy's raw reply (CHART_PLAN §8.5); feeds the tooltip |
| `disposition` | `traded` \| `vetoed_ml` \| `vetoed_validation` \| `vetoed_risk` \| `expired` (CHART_PLAN §8.3) |

### `trades` — what actually happened

| Field | Notes |
|---|---|
| `order_id` | Alpaca's ID |
| `client_order_id` | ours — the duplicate-blocker (hazard 4.6) |
| `run_id` | → `runs` |
| `account` | `alpha` \| `beta` \| `gamma` |
| `timestamp` | UTC |
| `symbol`, `side`, `quantity` | |
| `requested_price` | |
| `fill_price` | slippage = fill − requested → feeds `slippage.csv` |
| `status` | `submitted` \| `partial` \| `filled` \| `cancelled` \| `rejected` |
| `strategy_id` | |

### `runs` — the lab notebook

| Field | Notes |
|---|---|
| `run_id` | |
| `account` | |
| `model_version` | e.g. `models/alpha/rf_2026-08-01.pkl` |
| `data_range` | (start, end) |
| `feed`, `adjustment`, `timeframe` | |
| `params_hash` | fingerprint of every parameter — for LLM runs: model+version, prompt template, temperature (CHART_PLAN §8.5) |
| `created_at` | |

Without `runs`, "what exactly did the alpha model see?" is unanswerable within weeks.

## Key API payloads (observed 2026-08-18)

### `GET /api/status` — current account's bot

Top level: `account` (Alpaca account snapshot: `equity`, `cash`, `buying_power`,
`status`, `trading_blocked`, …), `account_id`, `name`, `running`, `paper_mode`,
`ml_enabled`, `last_tick` (ISO), `active_positions`, `watchlist` (string[]),
`stats` (`daily_pnl`, `weekly_pnl`, `total_pnl`, `drawdown_pct`, `open_risk_amount`,
`win_rate`, `total_trades`, `consecutive_losses`, `is_paused`, `review_flagged`,
`shutdown_triggered`, …).

### `GET /api/accounts`

`{"accounts": [{id, name, strategy, running, configured, ml_enabled, error, watchlist[]}, ...]}`
— one entry per configured account; `error` non-null when an account failed to init.

### `GET /api/market`

`{is_open, is_premarket, is_afterhours, minutes_until_open, minutes_until_close,
next_open, next_close, server_time}` — sourced from Alpaca's clock, ET timezone.

### `GET /api/health`

`{"healthy": bool, "latency_ms": int}`; HTTP 503 when Alpaca is unreachable.

## Out of scope for this spec (deliberately)

- `config/settings.ini` — configuration, not runtime data (documented in `CLAUDE.md`).
- `.env` — secrets; never schema'd, never committed.
- In-memory state (asset-list cache, per-tick evaluations) — rebuilt on restart.
  Note: engine M7's decision log is **not** a separate store — it is a view over
  `signals` rows with their `disposition` (CHART_PLAN §14, Merger 2). Sub-threshold
  scanning chatter (< 65%) stays in the log files, never in the store (CHART_PLAN §8.3).
