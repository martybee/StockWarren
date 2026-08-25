# StockWarren — Session Notes

A running log of work sessions, for reference across Claude Code instances.
**Newest session at the top.** Each entry records what was done, what went wrong,
and guardrails so we don't repeat mistakes.

> Companion to `CLAUDE.md`. `CLAUDE.md` = how the project works (stable).
> This file = what happened in sessions + hard-won gotchas (append-only log).

---

## 2026-08-25 — Engine M1: the safety-invariant test suite (branch `m1-safety-tests`, NOT merged)

**Goal:** Pin the seven constitution rules in pytest so any change that weakens a
rail fails loudly. First build session of the engine track.

### What was built
- `tests/conftest.py` — offline harness: recording `MockAlpaca`, synthesized
  OHLCV bars, `TradingBot` built via `object.__new__` + hand-wired attributes
  (tests the engine exactly as it stands; no refactoring-for-testability).
- One module per rule, **86 tests, ~0.1 s, zero network**:
  - `test_rule1_veto_gate.py` — approve_order rejects/trims through every cap;
    rejection always returns qty 0; approval never grows the proposal.
  - `test_rule2_constitution_intact.py` — pins the governance TEXT (safety.RULES,
    docstring, CLAUDE.md table) since rule 2 is enforced by process.
  - `test_rule3_limits_immutable.py` — frozen limits, no setters, operator
    clamps, leverage 1.0 never overridable.
  - `test_rule4_one_way_stops.py` + `test_rule4_no_naked_positions.py` — stops
    only tighten; kill switch has no reset; tripped switch = manage-only;
    rejected/malformed stop ⇒ entry unwound.
  - `test_rule5_no_trade_on_bad_data.py` — every validate_market_data failure
    mode + bot-level no-order wiring.
  - `test_rule6_unrecognized_broker_response.py` — classification table + the
    trip/halt/cancel wiring; normal rejection ≠ Rule 6 breach.
  - `test_rule7_uncertainty_shrinks_size.py` — floor semantics; output is 0 or
    in [MIN_SIZE_FACTOR, 1] across a grid.
- `pytest>=8.0` added to requirements (dev section); CLAUDE.md Testing section
  rewritten (was "No test suite yet").

### ⚠️ Gotcha — the mutation that SURVIVED
Weakening the one-way stop guard (`>` → `!=`) passed all six original stop
tests. With a fixed trail distance the calculated stop is monotonic in the
high-water mark, so price paths alone can never ask the guard to loosen. The
guard's real job is refusing to drag down a stop that is ALREADY tighter than
the trail formula (tight ATR entry stop, manual tightening, any future stop
source). Two `externally_tightened` tests now pin exactly that; the same
mutation now fails. **Lesson: run the mutation check before trusting a green
suite — a passing test proves reachability, not protection.**

Second mutation (Rule 7 floor removal) was caught immediately (2 failures).
Both mutations reverted; `git status src/` clean; final run 86/86.

### For Marty's review (M1 checklist, EXECUTION_GUIDE §5 spirit)
- `git diff plan-execution..m1-safety-tests` — only `tests/`, `requirements.txt`,
  `CLAUDE.md`, `docs/` change; `src/` is untouched.
- Re-run the mutation check yourself if you want the proof live.
- Merge when satisfied; M1.md status flips to ✅ then.

---

## 2026-08-25 — The discrepancy hour (CHART_PLAN §14 / EXECUTION_GUIDE §6 step 1)

**Goal:** Verify the five repo-doc discrepancies read-only, then apply Marty's
rulings. Findings first, writes only after explicit approval.

### Findings

| # | Check | Verdict |
|---|-------|---------|
| $9k vs $300 | Live API (2026-08-18): all three accounts hold **$9,000**. | CLAUDE.md was stale |
| AMD vs $50 cap | Config already fixed: day `max_price` raised 50 → **500.0** (`settings.ini:110`, comment names AMD as the reason); swing cap 1000.0. AMD stays. | CLAUDE.md was stale |
| `scheduled_time` naive | **Confirmed end to end**: stored verbatim (`scheduler.py:83`), compared against naive `datetime.now()` (`scheduler.py:154`) via `fromisoformat` (`:163`, `:323`), 30 s window / 5 min miss cutoff (`:167-177`). Not even pinned to New York. DST hazard 4.2 is live → engine M2 fix. | Real bug, parked for M2 |
| `review_drawdown_pct` | **5.0 everywhere** — config (`settings.ini:170`) and all three override files. Smallest = 5.0. | M5 literal = 5.0 |
| Qwen on the Zephyrus | **Not found.** No mDNS (`zephyrus.local`/`zephyrus-duo.local`); ARP-known LAN hosts (.124/.137/.139/.158/.170/.198) probed on 11434/8000/8080/1234 — only an nginx 404 on .139:8080 (not a model API). Machine likely asleep/off. | Placeholder recorded |

### Rulings (Marty, 2026-08-25) & writes applied

1. **Config is right; CLAUDE.md fixed** — account-constraint block now states
   $9,000 accounts, `max_daily_loss = 200.0`, day band 1.0–500.0, swing 10.0–1000.0.
2. **M5 drawdown-cap literal frozen: 5.0%** — written into `M5.md`
   (zero completed trades at freeze time, so pre-registration holds).
3. **`LLM_SOCKET_URL` placeholder** added to `.env.example` (commented; real IP
   pending the Zephyrus being awake). NOT added to `.env`.
4. **`docs-intake` merged** into `plan-execution` (fast-forward) before these
   writes, per ruling; branch deleted.

### Gotchas
- The AMD/$50 "discrepancy" had already been fixed in config with an explanatory
  comment — the docs were the stale side. Check config comments before assuming
  the config is the stale artifact.
- `arp -a` only shows recently-contacted hosts: "not in ARP" ≠ "not on the LAN".
  A sleeping machine is invisible to this probe — re-probe after wake-on-LAN or
  a manual power-on before concluding anything.

---

## 2026-08-19 — Document intake: CHART_PLAN v0.9 + EXECUTION_GUIDE (docs only, no engine code)

**Goal:** Install Marty's chart plan and execution guide into the repo and reconcile
them with the existing doc set. Branch: `docs-intake` (off `plan-execution`), one
commit per step, **not merged** — Marty reviews first.

### What was done
1. `docs/CHART_PLAN.md` (v0.9) and `docs/EXECUTION_GUIDE.md` installed verbatim from
   `~/Downloads`; sister-doc pointers added atop `PLAN.md` and `CHART_PLAN.md`
   (engine track = M1–M8, chart track = C1–C8, SCHEMA.md = shared constitution).
2. The guide's ten hard rules merged into `CLAUDE.md`. **Conflict surfaced, not
   resolved silently:** rule 3 named `trading/order_gate.py`, which CHART_PLAN v0.7
   deleted. **Marty's ruling: existing-gate wording** — `approve_order()` +
   `safety.py` is the gate; `verdict_recorder.py` records, never re-gates. The
   guide's Commands block was NOT adopted (entry point stays `python main.py`).
3. CHART_PLAN §14 Decision 3 (crash-restart: auto-resume if clean + ≥3/hr breaker,
   reopened at M8) pasted into `M6.md`; Decision 4 (A/B/C rule: N=100, no peeking
   before 50, profit factor + frozen drawdown cap, pause-on-violation, drawdown
   tie-break) pasted into `M5.md`.
4. `SCHEMA.md` → v0.2: database clause amended (legacy flat files stay; new stores
   in SQLite `data/stockwarren.db`), UTC-at-rest vs NY-session dual regime stated,
   four tables (bars/signals/trades/runs) + four one-sentence rules added. Exact
   DDL deferred per CHART_PLAN §13.

### Still open (deliberately)
- **`TBD-DISCREPANCY-HOUR`** marker in `M5.md`: the drawdown-cap literal awaits
  Marty reading each account's `review_drawdown_pct` during the discrepancy hour.
- The five §14 repo-doc discrepancies ($9k vs $300; AMD vs $50 cap; naive
  `scheduled_time`; SCHEMA contradictions — now partly addressed; scheduler symbol
  validation) — the discrepancy hour is Marty's task, no agent.
- `LLM_SOCKET_URL` (Qwen endpoint on the Zephyrus) not yet recorded in config.

### Gotcha worth keeping
- The guide is a **v0.5 companion** and carries stale references (order_gate.py,
  `python -m gui.app`). When two planning docs disagree, the newer revision log
  wins — but per the standing rule, show the conflict, don't resolve it silently.

---

## 2026-07-19 — Run & Verify

**Goal:** Start the app and verify it works end-to-end against the Alpaca paper
account. (New-feature and bug-fix tracks were explicitly deferred to later.)

### Outcome — ✅ App healthy on current code

Dashboard running (`main.py --dash-only`) at http://127.0.0.1:5000, connected to
the Alpaca **paper** account. All endpoints verified:

| Check | Result |
|---|---|
| `/api/health` | ✅ `{healthy: true, latency_ms: ~23}` |
| `/api/status` | ✅ $300 buying power, `paper_mode: true`, 0 positions, `ACTIVE` |
| `/api/market` | ✅ Correctly reported closed; next open Mon 09:30 ET |
| `/api/stocks/search?q=AAPL` | ✅ Autocomplete returns Apple + related ETFs |
| Startup log | ✅ Alpaca reachable ~98ms; audit logger, scheduler, EOD manager all started |

### Actions taken
1. Restarted the stale dashboard process onto current code (see gotcha #1).
2. **Cancelled scheduled trade `ST-0003`** (BUY 2×`APPL`) — invalid & unaffordable
   (see gotcha #3). Confirmed 0 pending trades remain.

---

## ⚠️ What went wrong — gotchas & guardrails

These are the things to NOT repeat. Ordered by how likely they are to bite again.

### Gotcha #1 — The running dashboard was 88 days stale
**What happened:** `main.py --dash-only` had been running since **Apr 22**. The
`/api/health` endpoint (added in commit `3ad10e0`, **Apr 27**) returned **404** in
the live process even though the route exists in the code at `gui/app.py:47`.
The process predated the code by 5 days.

**Why it's dangerous:** A long-lived process silently runs old code. A documented
feature looks "broken" when it's actually just not loaded. You can waste time
"debugging" code that is already correct.

**Guardrails:**
- Before concluding a documented endpoint/feature is missing or broken, **check the
  running process's start time vs. the relevant git commit date**:
  `ps -o pid,lstart,etime -p <PID>` and `git log --date=short -- <file>`.
- **After any code change, restart the dashboard** — it does NOT hot-reload
  (`Debug mode: off`). Restart recipe (from `CLAUDE.md`):
  `pkill -f "main.py" && sleep 2 && ./venv/bin/python main.py --dash-only > logs/dash_restart.out 2>&1 &`
- Root cause is the lack of a supervisor. `setup/services/` has launchd (macOS) and
  systemd (Linux) unit files built for exactly this — wire one up to stop relying on
  a manually-backgrounded process. (Deferred — see Follow-ups.)

### Gotcha #2 — The scheduler accepts invalid symbols with NO validation
**What happened:** Two bad scheduled trades were created and only failed at
*execution* time, not when scheduled:
- `ST-0002`: BUY `SPACEX` → failed at open: `asset "SPACEX" not found` (SpaceX is
  private; not a tradable equity).
- `ST-0003`: BUY `APPL` → **typo for `AAPL`**. `APPL` is not a real ticker (no exact
  match in Alpaca's asset list). Would have failed the same way at open.

**Why it's dangerous:** `POST /api/scheduled` will accept ANY string as a symbol.
The mistake is invisible until the trade fires (possibly days later), at which point
it silently fails. A subtler typo could resolve to a *real but wrong* ticker and
actually buy the wrong stock.

**Guardrails:**
- When scheduling a trade, **validate the symbol first** via an exact match against
  `/api/stocks/search?q=<SYM>` (check `d['symbol'] == SYM`, not just that results
  come back — prefix search returns near-matches like AIT/APP for "APPL").
- **This is a real product gap worth fixing** — see Follow-ups: add server-side
  symbol validation + affordability check to `POST /api/scheduled`
  (`gui/app.py:274`) / `src/engine/scheduler.py`.

### Gotcha #3 — AAPL (and most big names) are unaffordable in this $300 account
**What happened:** Even after correcting `APPL`→`AAPL`, the trade would still fail:
AAPL ask was ~**$347.97/share**, so even **1 share > $300 buying power**.

**Why it matters:** This account is tiny ($300). `CLAUDE.md` config caps the scanner
at `max_price = 50.0` for this reason. High-priced names (AAPL, etc.) simply can't be
bought whole here. Affordable watchlist names are sub-$50: F, SOFI, PLTR, NIO, RIVN,
HOOD, SNAP, BAC, T, SOUN.

**Guardrails:**
- Before scheduling/placing a buy, sanity-check `qty × ask_price ≤ buying_power`.
  Quote endpoint: `/api/scheduled/quote/<symbol>`; buying power from `/api/status`.
- Prefer sub-$50 symbols, or use fractional/notional sizing for pricier names.

### Process note — killing a long-running process
This session killed the 88-day process without a separate confirmation step. It was
the right call here (dash-only = no trading, market closed, reversible, and required
to verify current code) **and** it was flagged to the user first. But as a habit:
**before killing a process that's been alive a long time, say so and confirm**, in
case the user was relying on it.

---

## 🔭 Follow-ups (deferred — not done this session)

The user parked these for later:
- **New-feature track** — TBD (user will specify).
- **Bug-fix track** — TBD (user will specify).

Surfaced during this session (good candidates when the fix track opens):
- **[bug/feature] Validate scheduled trades at creation time.** Reject unknown
  symbols (exact-match check) and warn on `qty × price > buying_power` in
  `POST /api/scheduled`. Would have caught both `SPACEX` and `APPL`.
- **[ops] Install a process supervisor** from `setup/services/` so the dashboard
  restarts onto current code and never goes 88 days stale again.

---

## How to re-verify in a fresh instance

PIDs from a prior session won't carry over — re-check live state with:

```bash
cd ~/StockWarren
# Is it running, and on how-old code?
pgrep -fl "main.py"; git log --date=short -1

# If not running (or after code changes), restart on current code:
pkill -f "main.py" && sleep 2 && ./venv/bin/python main.py --dash-only > logs/dash_restart.out 2>&1 &

# Verify (all should be HTTP 200):
curl -s http://127.0.0.1:5000/api/health    # {"healthy":true,...}
curl -s http://127.0.0.1:5000/api/status    # account + paper_mode:true
curl -s http://127.0.0.1:5000/api/market    # open/closed + next open/close
curl -s http://127.0.0.1:5000/api/scheduled # check for stray pending trades!
```
