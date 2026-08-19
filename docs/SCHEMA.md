# StockWarren — Data & API Schema Spec

> **Version:** v0.1-draft — drafted 2026-08-19 by Claude by inspecting the live
> files and API responses. This documents shapes **as they exist today**; M2 turns
> it into enforced validation. Marty may replace it with a final source spec.

Purpose: the single source of truth for every file StockWarren persists and every
API payload the dashboard depends on. If code and this spec disagree, that's a bug
in one of them — fix whichever is wrong, deliberately.

## Conventions

- All persisted state is **flat files** under `data/` and `logs/`; there is no database.
- Alpaca is the source of truth for positions, orders, and equity — never persisted locally.
- Timestamps are ISO-8601. Market logic runs in `America/New_York`.
- Proposed (M2): every JSON file gains a top-level `"schema_version": 1` field;
  loaders validate on read and **fail closed** (refuse to trade, log, keep the file
  quarantined) rather than guess.

## Files under `data/`

### `scheduled_trades.json` — writer: `src/engine/scheduler.py`

```json
{
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
| `scheduled_time` | string | naive local ISO timestamp |
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
- In-memory state (asset-list cache, per-tick evaluations) — rebuilt on restart; M7
  may give evaluations a persisted, schema'd form (decision log).
