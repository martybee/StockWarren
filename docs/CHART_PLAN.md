# StockWarren Chart & Trading-Signal Plan

> **Sister doc:** [PLAN.md](PLAN.md) owns the **engine track (M1–M8)**; this file owns the **chart track (C1–C8)**. [SCHEMA.md](SCHEMA.md) is the shared constitution both answer to. (§14 Decision 1)

**Status:** Living document
**Revision:** v0.9
**Last updated:** 2026-08-19
**Owner:** Marty

---

## Revision log

| Rev | Date | Change |
|-----|------|--------|
| v0.1 | 2026-08-15 | Original plan: standalone app, three-path architecture, chart-first build order |
| v0.2 | 2026-08-15 | Added feed provenance, timestamp rules, corporate actions, reconciliation, `runs` table, idempotency, live-trading gate. Reordered milestones. |
| v0.3 | 2026-08-15 | Plain-English rewrite with fundamentals. Added 8 hazards: recent-data limit, rate limits, missing bars, extended hours, snapping rule, simulated fills, browser load, signal timeframe. |
| v0.4 | 2026-08-15 | **Re-scoped from standalone app to StockWarren feature.** Incorporated real StockWarren architecture (Flask 3 + vanilla JS, JSON/pickle storage, 3 accounts, existing kill switch & audit logs). Added account dimension, SQLite decision, polling-first realtime, seed strategy, model-path sketch. Resolved 4 of 7 open questions. |
| v0.5 | 2026-08-15 | Expanded §8 into a **strategy plugin architecture**: one Strategy interface for the seed, RF adapters, and LLM sockets (Claude API + local Qwen). Added LLM-specific hazards (nondeterminism, latency, cost, prompt reproducibility). Added `rationale` to signals; `params_hash` now covers prompts. Companion doc created: *Claude Code Execution & Review Guide*. |
| v0.6 | 2026-08-18 | **Incorporated the live execution engine's reality.** Documented the existing pipeline (composite signal ≥65% + 2 confirmations → validation → ML filter → risk engine w/ 2:1 R:R and correlation groups). **Corrected §8.3: RFs are the ML *filter*, not signal generators.** Added `disposition` to signals so vetoed signals render on the chart. New hazard 4.16 (single-process fragility, no supervisor). Resolved open question #6: fixtures = the live watchlist. |
| v0.7 | 2026-08-19 | **Reconciled with the repo's own plan** (Claude Code's PLAN.md/SCHEMA.md/M1–M8/SESSION_NOTES). New §14: two tracks, one system. Chart milestones renumbered **C1–C8** (repo engine track keeps M1–M8). Adopted: safety-test suite as prerequisite zero, pre-registered A/B/C decision rule, staleness guard, repo M8 go/no-go checklist, session-notes practice. Merged: repo M3 trade store + our signals/runs = ONE SQLite store; repo M7 decision log = a view over it. **Dropped our `order_gate.py`** — `approve_order()`/`safety.py` exist; record their verdicts, don't duplicate them. Flagged repo-doc discrepancies ($9k vs $300, AMD vs $50 cap, naive-local `scheduled_time`, SCHEMA "no database" clause). |
| v0.8 | 2026-08-19 | **Three decisions made** (§14 Decisions): docs live as **two cross-linked files** (repo `PLAN.md` = engine, this doc committed as `docs/CHART_PLAN.md`); first sitting = **discrepancy hour, then engine M1**; crash-restart policy for paper = **auto-resume if clean + crash-loop breaker** (≥3 restarts/hour trips the kill switch), re-decided at engine M8. Engine M6's parked policy question: resolved. |
| v0.9 | 2026-08-19 | **Question backlog cleared** (§14 Decisions 4–9): empty minutes = visible gap; bar revisions = ignore until C6; Lightweight Charts pinned to **v5**; engine M5 pre-registered (**N=100, no peeking before 50; profit factor + frozen drawdown cap; drawdown trip = DQ, violation/unexplained halt = pause-fix-decide; tie-break = lower max drawdown**); regular hours only confirmed; LLM sockets = **local Qwen first**, pointed at the server already running on the Zephyrus; live-capital number **deliberately deferred to engine M8**. Open-decisions table now empty except retention (parked). |

---

## 0. What changed in v0.4 and why

Three planning questions were answered, and each one reshaped the plan:

1. **#1 job = path to live automated trading.** The chart is not a product; it's the *inspection instrument* for a trading system. Every milestone is judged by whether it moves us toward safe live execution.
2. **This is a StockWarren feature, not a new app.** The standalone `alpaca_trading_system/` directory is dead. Everything below integrates with StockWarren's existing Flask backend, vanilla-JS dashboard, and file layout.
3. **Both day-trading and swing-trading must be possible.** So the bar store is timeframe-agnostic from day one (`1Min` and `1Day` are just rows with different `timeframe` values), and nothing anywhere assumes one timeframe.

And one discovery: **StockWarren already has trained Random Forest models (one per account), a kill switch (`KILL_SWITCH.lock`), per-account risk overrides, an append-only trade audit log, and a slippage tracker (empty).** The earlier statement "no trained model yet" is interpreted as: *models exist, but no signal history was ever persisted*. The models predict; nothing records what they predicted, when, on which data. That record is precisely what this plan builds.

---

## 1. What we are building, in one paragraph

A candlestick chart inside StockWarren's existing web dashboard, fed by Alpaca data, with overlays showing: where a model said buy/sell, where the backtest would have traded, and where Alpaca actually filled. All three drawn on the same candles, per account (alpha / beta / gamma). The purpose is to make disagreement visible — between what the model thought, what the simulation did, and what really happened — because that disagreement is where trading systems quietly lose money.

**The one question this must answer at a glance:** *is the model seeing real market structure, or getting lucky?*

---

## 2. Fundamentals

The ideas the rest of the document rests on. Read once.

**A candle** summarizes all trading in a fixed window: first price (open), highest (high), lowest (low), last price (close), shares traded (volume) — "OHLCV."

**IEX vs. SIP.** Trades happen across ~16 exchanges. **SIP** is the merged tape of all of them — the complete record. **IEX** is one small exchange carrying ~2–3% of volume. Alpaca's free plan gives IEX; the paid plan gives SIP. An IEX candle has badly understated volume, often-clipped highs/lows, and in quiet stocks, whole minutes with no data. IEX is a sketch; SIP is the photograph. Fine for building software; **not fine for judging a model.** Every candle is tagged with its feed, and feeds are never mixed silently. Daily bars are far less affected than minute bars — one more reason the swing-trading path is cheaper to validate.

**Signal ≠ order ≠ fill.** A **signal** is an opinion ("buy, 10:32:13, confidence 84%"). An **order** is a request to the broker. A **fill** is what actually happened, at a real price and time. Price moves between each step; the gap between signal price and fill price is **slippage** — real money. StockWarren already has an (empty) `slippage.csv` waiting for exactly this measurement. Three events, three records, three marker styles.

**Look-ahead bias.** A backtest is a replay of history. If any decision in the replay uses information from *after* the moment of decision, results look brilliant and trade terribly — betting on a game you already watched. Rule: **a signal at time T may only use data from before T**, enforced in code.

**Why timestamps are the single riskiest detail.** Alpaca stamps candles with the *start* of the minute, in UTC. The chart library wants plain epoch seconds. Market sessions, half-days, and daylight-saving live in New York time. If one component thinks "start of minute" and another thinks "end," every marker lands one candle off. The trap: shifted one candle *late* looks like a slow model; shifted one candle *early* looks like a model that predicts the future. The second bug looks like alpha. That's why timestamps get their own tests before anything else.

**Splits and adjustments.** After a 4-for-1 split, prices quarter overnight. Data comes either **raw** or **adjusted** (rescaled for comparability). A signal saved at a raw price, drawn on an adjusted chart, floats in empty space. Every stored price records its basis; one chart never mixes bases. StockWarren's existing split/dividend handling is reused, not rebuilt.

**Idempotency.** If the program crashes after sending an order and restarts, it must not send it again. Every order carries our own unique ID (`client_order_id`); the broker rejects duplicates. Without this, a restart can silently double a position.

**Walk-forward validation** (for the model path, §11). Train on months 1–6, test on month 7; slide forward; repeat. The model is only ever judged on data from *after* everything it trained on — the honest simulation of how it will actually be used.

---

## 3. How this fits into StockWarren as it exists today

What exists (confirmed): Flask 3 + Flask-SocketIO in `gui/app.py` serving JSON endpoints; one Jinja2 `index.html`; one hand-written vanilla-JS `dashboard.js` (~2000 lines); plain CSS with custom dark/light themes; Socket.IO initialized server-side but **unused by the client** — the UI polls REST every ~5s; state in `data/` as JSON + pickles; three accounts (alpha/beta/gamma) each with RF models and risk overrides; `KILL_SWITCH.lock`; monthly append-only trade logs; `settings.ini` + `.env`.

**And the execution engine already runs** (confirmed v0.6). At market open, each bot evaluates its 10-symbol watchlist (F, PLTR, SOFI, NIO, RIVN, HOOD, SNAP, AMD, BAC, T). An order happens only if a symbol survives four stages in sequence:

```
composite signal ≥ 65% strength, with ≥ 2 confirming indicators
        │
        ▼
market-data validation
        │
        ▼
ML filter          ← the RF models live HERE (untrained today: approves everything)
        │
        ▼
risk engine        ← 2:1 minimum risk/reward; one position per correlation
        │             group (e.g. only one of F/NIO/RIVN at a time)
        ▼
order
```

Everything runs inside a single `main.py --dash-only` process. Today, these evaluations exist only as lines in `stockwarren.log` and the Scanner page — the moment they scroll away, the history of *what the system considered and why it declined* is gone. Capturing that pipeline's verdicts is now an explicit job of the signal store (§8.3), because a signal vetoed by the risk engine is precisely the "signal without a trade" the chart exists to make visible.

Integration decisions that fall out of that:

**Frontend: no build system, and we keep it that way.** Lightweight Charts ships as a standalone script — one `<script>` tag, no npm, no bundler. The chart gets its **own new file** (`gui/static/js/chart.js`); `dashboard.js` gets only the navigation hook. Adding 1000 lines to a 2000-line hand-rolled file is how that file dies. Chart colors read from the existing CSS custom properties so dark/light theming works for free.

**Backend: a Flask blueprint, not a second server.** New endpoints under `/api/chart/…` (bars, signals, trades) registered in `gui/app.py`. FastAPI is dropped from the plan — a second web framework inside one app buys nothing.

**Realtime: polling first, Socket.IO second.** The dashboard already polls every ~5s, and here's the honest observation: **a 1-minute candle changes once a minute — 5-second polling is genuinely sufficient for the chart's first live version.** So live data starts as "poll `/api/chart/bars?since=…`," matching the existing pattern. Socket.IO — already installed, never yet used — gets switched on later as its first real use case, when live model signals need push latency. This deletes a whole category of early websocket-plumbing work.

**Storage: SQLite arrives, for the new tables only.** Minute bars are the forcing function — ~390 rows/symbol/day, ~8,000/month. That's untenable as JSON and trivial for SQLite (stdlib, zero server, single file in `data/`). Existing JSON/pickle state stays exactly where it is; nothing migrates. One new file: `data/stockwarren.db`, holding the four new tables below. *(Resolves open question #3 from v0.3.)*

**Safety: integrate, don't rebuild.** v0.3 specified a kill switch, risk limits, and audit logging as new work. **They already exist.** The plan changes from "build a gate" to "wire every new order path through the existing gate": every order-submitting code path checks `KILL_SWITCH.lock` and the per-account operator overrides *before* the Alpaca call, and every submission/fill appends to the existing monthly trade log. Fills also finally start populating `slippage.csv`.

**Accounts are a first-class dimension.** Three accounts means three parallel streams of signals and trades. Every signal, trade, and run row carries `account` (`alpha` | `beta` | `gamma`). The chart gets an account selector next to the layer toggles. *(New in v0.4 — the previous data model would have silently merged three accounts into one history.)*

---

## 4. Known hazards

Things that produce *silently* wrong results — no error, just quietly bad data. Listed before milestones on purpose.

**4.1 The IEX→SIP switch is not a config change.** A model trained on IEX candles learned from distorted volume and clipped ranges; SIP changes its input distribution. Every candle and signal records its feed; the SIP milestone is a model *re-validation*, not a flag flip.

**4.2 Timestamp discipline.** One written convention — *bar timestamp = interval start, stored UTC, converted to New York only for display/session logic* — with tests that include a DST-transition day and a half-day.

**4.3 Adjustment basis.** Every price row says raw/split/dividend/all. One chart, one basis.

**4.4 The seam between history and live.** REST loads the past; the stream covers the present; candles fall in the crack between them — and the crack reopens on every disconnect. Subscribe first, then backfill to the first live candle; on reconnect, re-backfill the gap. Never assume continuity.

**4.5 Alpaca revises candles after publishing.** A signal computed on a candle that later changed rests on data that no longer exists. **Decision needed before signals go live (§9).**

**4.6 Duplicate orders on restart.** `client_order_id` everywhere; on reconnect, reconcile via REST order status instead of trusting no fill was missed.

**4.7 The live-money gate.** Already largely built (kill switch, overrides, audit log). Remaining rule: config **refuses to default to live** — paper vs. live keys are separate entries in `.env`, and going live is a deliberate multi-step act. No deadline pressure exists ("whenever it proves itself"), so the gate never has a reason to be rushed.

**4.8 Free plan hides the newest ~15 minutes of REST history.** "Load today's chart" comes back with a hole at the right edge; the live stream fills it. Same reconciliation module as 4.4 owns this.

**4.9 REST rate limits (~200 req/min).** Backfills page politely, respect rate-limit headers, back off, and cache — last week's minute bars never change (except 4.5).

**4.10 Minutes with no trades produce no candle.** Common on IEX. Chart and backtester must give the *same* answer to "what was the price at 10:37?" — gap vs. carry-forward is a recorded decision (§9), not an accident.

**4.11 Extended hours are a switch flipped everywhere or nowhere.** Regular hours only, until chart + model + backtest change together.

**4.12 Snapping rule.** A 10:32:13 signal belongs to the 10:32:00 candle (floor). Same rule for millisecond fills. If two components round differently, markers drift a candle apart and it impersonates bug 4.2.

**4.13 Paper fills are pretend fills.** Simulated, systematically kinder than reality — better prices, no partials, no rejections. Paper results prove *plumbing*, never *profitability*. This matters most right before going live, which is exactly when it's most tempting to forget.

**4.14 Don't drown the browser.** ~8,000 minute candles/symbol/month. Load windowed to the visible range; fetch on scroll; cap markers per viewport. Extra weight now that the chart lives inside an already-large hand-rolled dashboard.

**4.15 Signal timeframe is stored, never assumed.** A signal computed on daily bars, drawn on a specific minute candle, implies precision that never existed. Every signal records its timeframe; day-level signals render pinned to session open in a visually distinct style. Critical here because *both* timeframes are in scope by decision.

**4.16 Everything lives in one process that nothing restarts.** All three bots — and, once built, the chart backend and the single Alpaca data connection — run inside `main.py --dash-only`. If that process dies or the Mac sleeps, all trading silently stops, and nothing brings it back (the launchd plist in `setup/services/` exists but isn't loaded). Two consequences for this plan. First: the chart backend **must live inside that same process** — a second process would fight for the one allowed Alpaca connection (§3) and create two half-alive systems. Second: before M5 puts real paper orders in flight, the supervisor gets loaded and sleep gets disabled during market hours — because an unsupervised process that dies *between order submission and fill* is exactly the scenario 4.6's reconciliation exists for, and it shouldn't get its first test in production by accident.

---

## 5. Data model — four tables in `data/stockwarren.db`

Three tables for the three kinds of events, plus the lab notebook that makes them trustworthy.

**bars** — what the market did
```
timestamp    interval START, UTC
symbol
open, high, low, close, volume
feed         iex | sip                    (4.1)
adjustment   raw | split | dividend | all (4.3)
timeframe    1Min | 1Day | ...            (both styles supported)
```

**signals** — what a model thought
```
signal_id
run_id       → runs
account      alpha | beta | gamma
timestamp    UTC, actual decision moment
symbol
signal       BUY | SELL | ...
price        the price the model saw
confidence   0–1
source       training | backtest | live_model
timeframe    bars the signal was computed on (4.15)
rationale    optional text — an LLM strategy's raw reply / stated reasoning (§8.5); feeds the tooltip
disposition  traded | vetoed_ml | vetoed_validation | vetoed_risk | expired   (§8.3)
```

**trades** — what actually happened
```
order_id           Alpaca's ID
client_order_id    ours — the duplicate-blocker (4.6)
run_id             → runs
account            alpha | beta | gamma
timestamp          UTC
symbol
side, quantity
requested_price
fill_price         slippage = fill − requested → feeds slippage.csv
status             submitted | partial | filled | cancelled | rejected
strategy_id
```

**runs** — the lab notebook
```
run_id
account
model_version      e.g. models/alpha/rf_2026-08-01.pkl
data_range         (start, end)
feed, adjustment, timeframe
params_hash        fingerprint of every parameter — for LLM runs: model+version, prompt template, temperature (§8.5)
created_at
```

Without `runs`, "what exactly did the alpha model see?" is unanswerable within weeks. Nothing merges at rest — the bars table never grows a `buy=True` column; the chart joins by symbol + time + account at draw time.

---

## 6. Markers — two labels, not four categories

Every marker is described by two independent fields: `source` (*who says so* — training · backtest · live_model · broker) and `kind` (*what event* — signal · order · fill). Shape from `kind`, color from `source`, account shown via the selector rather than more marker styles. Twelve combinations, zero renderer special cases, and a new source (fourth account, second broker) needs no new drawing code.

---

## 7. Frontend notes

Lightweight Charts via standalone `<script>` tag — fits the no-build-system reality exactly.

- **Pin the major version now.** v5 moved markers out of `series.setMarkers()` into a plugin. *(Open question #5.)*
- **Markers can't be hovered natively.** The tooltip ("confidence, entry, exit, P&L") is hand-built: listen to crosshair moves, find the nearest marker by time ourselves, draw our own overlay. Budgeted in Milestone 2.
- **Theme via the existing CSS custom properties** so dark/light works without new code.
- Layer toggles + account selector:
```
Account: [alpha ▾]
☑ Training signals   ☑ Backtest trades
☑ Live model signals ☑ Broker fills
```

---

## 8. Strategies — the seed first, then plugins (ML models and LLM sockets)

A **strategy** is anything that looks at market data and emits signals. The architecture's promise: the pipeline (store → chart → backtest → paper orders → gate) never knows or cares *what kind of brain* produced a signal. A moving-average rule, a Random Forest, Claude over an API, and a Qwen model running on the Zephyrus Duo are all just strategies.

### 8.1 The seed strategy — solving the chicken-and-egg

There is no persisted signal history yet, so Milestone 2 would have nothing to draw. Fix: a deliberately dumb, fully transparent rules strategy — a moving-average crossover — whose only job is to *exercise the entire pipeline*: generate signals with a `run_id`, store them, draw them, backtest them, place paper orders from them.

Its stupidity is the feature. Every marker is verifiable by eye ("the fast MA crossed there — yes, the triangle is on the right candle"), so it debugs the plumbing without any argument about whether the model is right. Everything that follows drops into slots the seed has proven end-to-end.

### 8.2 The Strategy interface — one contract for every brain

```
Strategy.generate(window, position) -> Signal | None

window    bars up to and including time T — and physically nothing after T
position  current holdings/context for the account
returns   a Signal (side, price, confidence, optional rationale) or None
```

Two rules make this interface trustworthy:

1. **The no-look-ahead rule is enforced by the store, not by politeness.** A strategy receives its bar window from `signals/store.py`, which is *incapable* of returning bars past the decision timestamp. A strategy cannot cheat even if written badly.
2. **Every strategy runs under a `run_id`.** The `runs` row records exactly which strategy, which parameters, which data. Comparing an MA crossover to an RF to Claude is then just comparing runs on the same chart.

### 8.3 Where the existing engine fits — a correction

v0.5 assumed the RF models generate signals. **Wrong.** In the real pipeline (§3), signals come from the **composite indicator engine** (≥65% strength, ≥2 confirmations), and the RFs are the **ML filter** — a yes/no stage that vetoes or passes rule-generated signals. (Untrained today, it passes everything — which makes *right now* the perfect time to start recording its verdicts, so there's a before/after baseline once it's trained.)

So the mapping is:

- **The composite engine is strategy #0.** Its cleared signals (≥65%) get written to the signal store under a `run_id`, like any strategy's.
- **Filter and risk verdicts are data, not just log lines.** Every stored signal records its `disposition`: `traded`, `vetoed_ml`, `vetoed_validation`, `vetoed_risk`, or `expired`. A signal that cleared 65% but died at the correlation-group rule appears on the chart as a signal marker with no trade marker — hover shows *which stage killed it and why*. That's the plan's founding purpose (§1) applied to the system that already exists.
- **New strategies (seed, LLM sockets) feed INTO the pipeline, never around it.** A Strategy per §8.2 replaces or runs beside the composite engine at the top; validation, ML filter, and risk engine still stand between any strategy's opinion and an order.
- Sub-threshold evaluations (the constant scanning chatter below 65%) are *not* stored — they stay in the log. The store records opinions strong enough to act on, not every glance.

### 8.4 LLM sockets — Claude API and local Qwen

An LLM strategy is an adapter that formats the bar window (plus position context) into a prompt, calls a model, and parses the reply into a Signal.

Two sockets, one adapter, different endpoints:

| | **Claude socket** | **Local Qwen socket** |
|---|---|---|
| Endpoint | Anthropic API | OpenAI-compatible local server (Ollama / vLLM / llama.cpp) — natural host: the Zephyrus Duo Ubuntu box |
| Cost | per call | electricity |
| Latency | ~seconds | ~seconds (hardware-dependent) |
| Availability | needs internet + key | fully offline |

Because both speak "prompt in, text out," `llm_socket.py` is one class with a configurable endpoint — the Qwen socket is the Claude socket pointed at `localhost`.

**Where they fit:** any time after M2. They are just strategies; no milestone changes. Start them in `source=backtest` mode over historical windows (cheap to evaluate honestly) before ever running them live.

### 8.5 Hazards specific to LLM strategies

These are real enough to list alongside §4:

- **Nondeterminism.** Ask the same model the same question twice, get two answers. Temperature 0 reduces but does not eliminate this. Mitigation: store the **raw model reply** in the signal's `rationale` field, so every marker on the chart can show *why* — and any weird signal can be audited verbatim.
- **Reproducibility.** "Which model said this?" must be answerable forever. `params_hash` for an LLM run includes: model name **and version**, the full prompt template, temperature, and the definition of what goes in the context window. Change one word of the prompt → new run.
- **Latency decides the timeframe.** A multi-second call is fine for daily bars, workable for minute bars, absurd for anything faster. LLM strategies inherit the timeframe field like everyone else (4.15) and should start on daily.
- **Cost discipline (Claude socket).** A per-minute-bar live strategy = ~390 calls/day/symbol. Backtesting a year = tens of thousands. Budget per run, computed *before* the run starts, recorded in the runs row.
- **Prompt-side look-ahead.** The store can't return future bars, but a careless prompt can leak the future anyway ("here is data through Friday, what should I have done Tuesday?"). Prompt templates are code — reviewed, versioned, tested with the same rigor.
- **Parsing failures.** The model will sometimes return something that isn't a valid signal. A parse failure is a `None`, logged — never a guessed trade.
- **The gate is indifferent to eloquence.** An LLM signal goes through the **existing** `approve_order()`/`safety.py` path — kill switch, overrides, limits — exactly like every other signal. A confident-sounding rationale earns zero privileges. (v0.7: our planned `order_gate.py` is deleted — the gate already exists and is getting a test suite; see §14.)

---

## 9. Open decisions

| # | Question | Blocks | Status |
|---|----------|--------|--------|
| 1 | Revised candles (4.5) | C6 | **resolved: ignore revisions for now; decision reopens before live signals (C6)** |
| 2 | Empty minutes (4.10) | C1 | **resolved: visible gap — the chart draws nothing, the backtester values positions at last traded price; both answer "price at 10:37" with the last real trade** |
| 3 | Storage engine | — | **resolved: SQLite for new tables, existing JSON untouched** |
| 4 | Who writes signals | — | **resolved: signal store lives inside StockWarren; models write to it directly** |
| 5 | Lightweight Charts major version | C1 | **resolved: pin v5 — current API, markers via the plugin system, no future migration** |
| 6 | Test symbols | M1 | **resolved: fixtures come from the live watchlist (F, PLTR, SOFI, NIO, RIVN, HOOD, SNAP, AMD, BAC, T) — SPCX question moot. Addendum: all ten are liquid, so add one deliberately thin symbol to fixtures purely to exercise the empty-minute rule (4.10).** |
| 7 | Minute-bar retention | later | open |
| 8 | UI stack | — | **resolved: existing Flask + vanilla JS; blueprint + new chart.js; no FastAPI, no build system** |
| 9 | Realtime transport | — | **resolved: 5s polling first (sufficient for minute candles), Socket.IO when live signals need push** |

---

## 10. Milestones

Ordering principle unchanged: *prove the data is honest, then prove the signals are real, then automate.* Renumbered **C1–C8** in v0.7: the repo's own plan (see §14) owns M1–M8 for the engine track; these are the chart track. Any "Mx" in earlier sections of this document refers to the corresponding "Cx". The C-track starts only after engine M1 (safety tests) and M2 (schema validation) are green, and C2 shares its store with engine M3.

| # | Deliverable | Done means |
|---|-------------|------------|
| **C1** | Chart page in the dashboard: historical candles + volume, one symbol/date, both `1Day` and `1Min`. Flask blueprint `/api/chart/bars`; SQLite bar store. | Timestamp, DST-day, half-day, gap (4.10), and adjustment tests pass. Theme follows dark/light. |
| **C2** | Signal store + `runs` + **seed strategy** (§8) drawn on historical charts. Hand-built marker tooltip. One unified SQLite store shared with engine M3 (§14). | Scroll any past day and see MA-crossover markers, verifiable by eye, on exactly the right candles. **Pipeline go/no-go.** |
| **C3** | Backtest entries/exits as a second layer, sourced from StockWarren's existing backtester. | Model opinion vs. backtest action visibly distinct; no second P&L engine built. |
| **C4** | Live IEX minute candles via 5s polling; reconciliation module (4.4 + 4.8). Prereq: engine M6 (supervision) loaded. | Chart self-updates; kill the network mid-session and the gap heals; kill the process and the supervisor brings it back on current code. |
| **C5** | Composite-engine + seed-strategy signals recorded with `disposition`, with orders flowing through the **existing** `approve_order()`/`safety.py` path — record verdicts, never re-gate. Fills populate `slippage.csv`. | Restart mid-order without duplicating (4.6); a vetoed signal renders as signal-without-trade with the vetoing stage in the tooltip. |
| **C6** | RF-filter verdicts and (later) LLM-socket signals live, per account; Socket.IO push shared with engine M7's decision log. | Signal, order, and fill for one decision visible side by side; slippage measured per account. |
| **C7** | SIP feed — explicit model re-validation (4.1). | Old-feed and new-feed results compared knowingly, never merged. |
| **C8** | Chart support for the live go/no-go. **The gate itself is engine M8** — this milestone only supplies its evidence. | Engine M8's written checklist (slippage report, drawdown window, decision history) can be completed from the chart and store alone, without grepping logs. |

**Cut from the critical path (unchanged):** tick-by-tick candle updates (IEX makes the forming candle wrong anyway; `series.update()` in an afternoon if ever wanted) and any second P&L calculator (StockWarren's equity accounting is the single source of truth).

---

## 11. Model path — sketch only, by request

The infrastructure above is the paved road; this is the map of where it goes. None of these gate M1–M5.

1. **Persist first.** The existing RF models start writing every prediction to the signal store under a `run_id` — even before anyone judges them. History has to exist before it can be inspected.
2. **Baseline honestly.** Walk-forward validation (§2) of the current RFs per account, judged against buy-and-hold and against the seed strategy. If an RF can't beat the dumb MA crossover out-of-sample, that's essential — and cheap — to learn now.
3. **Decide the timeframe with evidence.** Both are in scope; run the walk-forward on daily and minute data separately. Daily needs less data, suffers less from IEX distortion, trades slower. Minute needs SIP sooner and lives or dies on slippage. Let the results, not preference, pick where to focus.
4. **Iterate inside the harness.** Features, labels, model class — every experiment is a `run_id`, every run's signals land on the chart, look-ahead discipline enforced by the store's API (a signal generator physically can't query bars past its decision timestamp).
5. **Promote by evidence.** A model earns live-money consideration (M8) only from walk-forward results plus measured paper slippage — proven on the same chart everyone can look at.

---

## 12. File layout (inside StockWarren)

```
stockwarren/
├── gui/
│   ├── app.py                    # + register chart blueprint
│   ├── chart_api.py              # NEW: /api/chart/* endpoints
│   ├── templates/index.html      # + chart panel / nav entry
│   └── static/
│       ├── js/dashboard.js       # nav hook only — nothing else added
│       ├── js/chart.js           # NEW: all chart logic, own file
│       └── css/                  # chart reads existing theme variables
├── market_data/                  # NEW package
│   ├── historical.py             # paged, rate-limited, cached (4.9)
│   ├── live_poll.py              # 5s poll now; stream later
│   └── reconcile.py              # REST↔live seam (4.4, 4.8)
├── signals/                      # NEW package
│   └── store.py                  # enforces no-look-ahead at the API (§8.2, §11.4)
├── strategies/                   # NEW package — every "brain" lives here (§8)
│   ├── base.py                   # the Strategy interface (§8.2)
│   ├── ma_crossover.py           # the seed (§8.1)
│   ├── llm_socket.py             # one adapter, two endpoints: Claude API / local Qwen (§8.4)
│   └── prompts/                  # versioned prompt templates — these are code (§8.5)
├── trading/
│   └── verdict_recorder.py       # NEW: writes approve_order()/pipeline verdicts as dispositions.
│                                 # (order_gate.py DELETED in v0.7 — the gate exists: safety.py)
├── store/
│   ├── db.py                     # SQLite: data/stockwarren.db — the ONE store (chart + Compare + decision log)
│   └── migrations/
├── data/                         # existing JSON/pickle untouched; + stockwarren.db
├── models/alpha|beta|gamma/      # existing RF FILTERS — their verdicts now recorded (§8.3)
├── config/settings.ini + .env    # + ALPACA_FEED, EXTENDED_HOURS, paper/live keys
└── tests/                        # engine M1 safety suite + timestamp/DST/half-day/gap fixtures
```

---

## 13. Next step

Unchanged in spirit, sharper in scope: extend the repo's **SCHEMA.md** with the four SQLite tables — exact types, constraints, indexes — plus one unambiguous sentence each for the timestamp convention (4.2), snapping rule (4.12), empty-minute rule (4.10), and extended-hours setting (4.11). Resolve the SCHEMA.md contradictions noted in §14 first. Then engine M1 → M2 → C1.

---

## 14. Two tracks, one system — reconciliation with the repo's plan (v0.7)

On 2026-08-19 Claude Code drafted its own PLAN.md, SCHEMA.md, milestones M1–M8, and SESSION_NOTES.md from the repo. Its plan hardens the **engine** (tests, validation, trade history, ML training, A/B/C decision, supervision, decision log, go/no-go); this plan builds the **observation layer**. Complementary, with two collisions. Rulings:

**Numbering.** Engine track keeps **M1–M8** (it's in the repo). Chart track is **C1–C8** (this doc, §10). No Claude Code session may ever be told "do M4" without the track name.

**Sequence.** Engine M1 (safety tests) and M2 (schema validation) come before *everything*, including C1 — un-tested rails aren't worth charting. Engine **M6 (supervision) is pulled early** — before C4 and before daily paper reliance — because the 88-day-stale-process gotcha is hazard 4.16 proven empirically, and the bots evaluate at every open *today*, unsupervised.

**Merger 1 — one store.** Engine M3's trade store and this plan's bars/signals/trades/runs are **one SQLite database** (`data/stockwarren.db`). The Compare page, the ML trainer's samples, the chart, and the decision log are views over the same rows. Engine M3's "structured CSV acceptable" clause is struck: the chart needs joins.

**Merger 2 — the decision log is a view.** Engine M7's "symbol → verdict → reason" feed is this plan's signals-with-`disposition` rendered as a list instead of as chart markers. No ring buffer, no second decisions store — same rows, two views, one Socket.IO stream feeding both.

**Deletion.** Our planned `order_gate.py` is gone: `approve_order()` + `safety.py` exist, carry the constitution, and get a mutation-checked test suite in engine M1. This plan's job shrinks to `verdict_recorder.py` — capturing what the existing gate decided, per stage, as dispositions. Building a second gate in front of a tested gate is the two-P&L-engines mistake in a safety vest.

**Adopted from the engine plan wholesale:** pre-registered A/B/C decision rule frozen before N/2 trades (engine M5); the staleness guard (running commit vs. disk HEAD, surfaced in the dashboard header); engine M8's written go/no-go checklist as the *only* live gate (C8 merely supplies its evidence); SESSION_NOTES.md as an append-only gotcha log — now step 5 of every session loop in the execution guide.

**Contributed to the engine plan:** engine M4's model evaluation must be **walk-forward** (train past → test future), not a random held-out split — at N≈50, a random split leaks regime; config must **refuse to default to live** (added to engine M8's checklist); LLM sockets and the seed strategy enter as strategies feeding the existing pipeline (§8), never around it.

**Repo-doc discrepancies to verify before engine M1** (each is cheap to check and expensive to inherit):

1. **$9,000 vs $300** — PLAN.md says three accounts at $9,000 each; SESSION_NOTES observed $300 buying power with zero positions. One is stale; position sizing inherits the answer.
2. **AMD vs the $50 scanner cap** — the watchlist carries AMD; `max_price = 50` excludes it; the session notes' affordable list swaps in SOUN. Watchlist, cap, or notes — one is stale. (Our C1 fixtures adopted this watchlist; open question #6 reopens until resolved.)
3. **`scheduled_time` is naive local time** — hazard 4.2 already live in production: with a 5-minute execution window, a DST transition turns a scheduled trade into `missed` or fires it an hour off. Fix candidate for engine M2.
4. **SCHEMA.md's conventions block ours** — "there is no database" and NY-time market logic contradict the SQLite store and UTC-at-rest. SCHEMA.md gains a "new stores" section stating both regimes, or engine M2's validators will enforce yesterday's truth.
5. **Scheduler symbol validation gap** (their gotcha #2) — `SPACEX`/`APPL` accepted at creation, failing days later at execution. Engine M2 territory; also a lesson our review checklists already encode: verify by hand, at creation time, not at fire time.

### Decisions (2026-08-19, Marty)

1. **Document layout: two cross-linked files.** Repo `docs/PLAN.md` stays the engine plan; this document is committed as `docs/CHART_PLAN.md` beside it. Each carries a one-line pointer to its sister at the top; `docs/SCHEMA.md` is the shared constitution both answer to. Rationale: Claude Code sessions read only their track's document and stay focused.
2. **First sitting: facts, then armor.** The discrepancy hour (verify the five items above, log findings in SESSION_NOTES.md) precedes everything; engine M1 (safety-invariant tests) is the first build session. C1 waits until the watchlist question is settled and the rails are tested.
3. **Crash-restart policy (paper phase): auto-resume if clean, with a crash-loop breaker.** On supervised restart, the bots resume trading only if `KILL_SWITCH.lock` is absent AND all state files pass validation; **three or more restarts within one hour trips the kill switch automatically** and the process comes up dashboard-only. Rationale: on paper money, an unmanaged open position is a more real risk than an unsupervised bot; the breaker caps the crash→trade→crash loop. **This decision is explicitly reopened at engine M8** — for live money the calculus flips toward human-click. This resolves engine M6's parked policy question; paste this paragraph into M6.md.
4. **Engine M5 pre-registration — frozen before any account's first completed trade:** minimum **N = 100** completed trades per account, with **no interim judging before 50**. Primary metric: **profit factor, subject to a max-drawdown cap** whose value is a **frozen literal** — during the discrepancy hour, read each account's configured `review_drawdown_pct` and write the smallest into M5.md as a number, immune to later config overrides. Disqualifiers: **hitting `shutdown_drawdown_pct` = disqualified**; a **constitution violation or unexplained halt = pause the whole experiment** (kill switch, fix, written decision to resume or restart) rather than DQ'ing the unlucky account, since violations are shared-code bugs. Tie-break: **lower max drawdown wins.** Paste this item into M5.md's "decision rule" section.
5. **Empty minutes: visible gap.** No-trade minutes draw nothing on the chart; the backtester values positions at the last traded price. Both systems answer "what was the price at 10:37?" identically: the last real trade.
6. **Bar revisions: ignored for now.** Signals stand on the bar as first published. Reopened at C6, before live signals carry consequences.
7. **Lightweight Charts: pinned to v5.** Markers via the v5 plugin system from day one.
8. **Extended hours: confirmed regular-session-only** (default made explicit), everywhere, until chart + model + backtest change together.
9. **LLM sockets: local Qwen first**, pointed at the inference server **already running on the Zephyrus** — during the discrepancy hour, note its endpoint and port into config as `LLM_SOCKET_URL`. Claude API socket follows later as the same adapter with a different endpoint. **Live-capital sizing: no number written — deliberately deferred to engine M8**, where the PDT rule (day-trade limits under $25k margin) must be part of the written decision.
