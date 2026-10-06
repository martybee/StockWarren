# StockWarren — Session Notes

A running log of work sessions, for reference across Claude Code instances.
**Newest session at the top.** Each entry records what was done, what went wrong,
and guardrails so we don't repeat mistakes.

> Companion to `CLAUDE.md`. `CLAUDE.md` = how the project works (stable).
> This file = what happened in sessions + hard-won gotchas (append-only log).

---

## 2026-09-30 — Engine M6: supervision & restart-safe ops (branch `m6-supervision`, NOT merged)

**Goal:** Implement CHART_PLAN §14 Decision 3 (auto-resume if clean + crash-loop
breaker), the staleness guard, supervised caffeinate, and the runbook. Pulled
early per §14 (hazard 4.16).

### Discovery first: the bots were NOT running
The `main.py --dash-only` process from August is **gone** (no process, nothing
on :5000) — the three bots have been down for an unknown stretch. Exactly the
silent failure M6 exists to end; no positions were at risk (broker-side stops
persist at Alpaca regardless).

### What was built
- `src/engine/startup_policy.py`: Decision 3 as code. Clean-state check for ALL
  bot-starting boots (any `KILL_SWITCH*.lock` present, or any state file
  quarantined during this boot's loads ⇒ bots do not start). Breaker: ≥3
  **supervised** starts (`STOCKWARREN_SUPERVISED=1`, set only by the plist)
  within one hour ⇒ trip every account's kill switch, come up dashboard-only.
  Manual starts recorded but never counted — a human restarting thrice in an
  evening is deliberate, not a crash loop. `data/restart_history.json` is a
  versioned state file under the M2 rules (corrupt ⇒ quarantined ⇒ that boot is
  unclean — deliberately strict).
- `src/utils/build_info.py` + `/api/status.process` + header `⚠ STALE CODE`
  badge: running commit stamped at import, compared to disk HEAD (15s TTL);
  `stale` is true/false/**null** — unknown never raises a false alarm.
- Plist configured for martynbar: default mode (bots + dashboard), supervised
  env var, KeepAlive on crash only; `main.py` under supervision attaches
  `caffeinate -s -w <own pid>` — sleep prevention finally dies with the process.
- Runbook in CLAUDE.md (install/status/restart/stop/logs/clear-switch).
- 16 tests (132 total). Mutation-checked AFTER committing (M2's lesson,
  applied): breaker `>=`→`>` ⇒ 3 named failures; quarantine-blind clean check
  ⇒ 2 named failures.

### Notes for review
- `main.py` behavior change: a MANUAL `python main.py` with a tripped kill
  switch or quarantined state file now comes up dashboard-only instead of
  starting bots that idle at the tick check. Fail-closed, but a change.
  **RATIFIED by Marty 2026-10-05:** a locked system should plainly say "bots
  not started," not show RUNNING while secretly refusing. Resuming after a
  trip is a deliberate two-step: remove the lock, then start the bots.
- `dashboard.js` was edited beyond the nav hook (staleness badge in
  `updateStatus()`). Read of hard rule 4: it protects dashboard.js from CHART
  code; this is engine-track UI required verbatim by M6.md ("dashboard header
  shows a warning"). Flagged rather than silently assumed.
- launchd `UserName` key dropped from the plist — it is ignored for user
  LaunchAgents (only meaningful for system daemons).
- Still open from M2: the scheduler-bypasses-`approve_order()` ruling.

### Pending live drills (need Marty; market closed)
Cutover checklist is in M6.md: load the service, verify auto-resume + caffeinate,
`kill -9` recovery (criterion 1), reboot test (criterion 2), optional breaker
drill. Acceptance boxes for those stay unchecked until done.

---

## 2026-09-28 — Engine M2: state-file validation + tz-aware scheduling (branch `m2-schema-validation`, NOT merged)

**Goal:** Make SCHEMA.md enforced, not aspirational — versioned state files,
fail-closed loaders with quarantine — and fix the naive `scheduled_time` bug
parked at the discrepancy hour.

### What was built
- `src/utils/state_schema.py` (new, stdlib-only): `schema_version` gate
  (legacy v0 accepted + upgraded on next save; FUTURE versions refused) and
  `quarantine()` — a bad file is renamed `.invalid-<utc-stamp>`, never
  overwritten, so the next save can't destroy the evidence.
- `src/utils/overrides.py`: structural damage ⇒ quarantine + config-baseline
  limits; non-numeric/boolean values dropped loudly, intact keys kept.
- `src/engine/scheduler.py`: per-record validation (`_trade_from_dict`) — one
  damaged record no longer kills the whole load via `ScheduledTrade(**t)`
  TypeError; unparseable `scheduled_time` refused at CREATION (ValueError to
  the API) and pending unfireables moved to FAILED history instead of sitting
  pending forever; **all execution math in aware UTC**. Semantics ruled and
  documented: naive `scheduled_time` = America/New_York wall time (this Mac
  is ET — verified `/etc/localtime` — so history is preserved); ambiguous
  fall-back times = first occurrence (fold=0).
- 30 new tests (116 total, ~0.1 s, offline): quarantine/versioning/record
  validation, DST fall-back + spring-forward + half-day fixtures (hard rule 8),
  creation-time rejection. THE regression test: a trade scheduled 01:45 on
  fall-back day must NOT fire at the second 01:46 wall-clock (naive math said
  60 s elapsed; real elapsed is 3661 s ⇒ MISSED).
- SCHEMA.md → v0.3 (shipped semantics); mutation-checked twice (no-op
  quarantine: 6 named failures; UTC-naive parse: 7 named failures).

### ⚠️ Gotcha — `git checkout` as mutation-revert EATS uncommitted work
The M1 mutation-check pattern (sed → run tests → `git checkout <file>`) is
only safe on COMMITTED files. This session ran it on uncommitted work:
`git checkout src/engine/scheduler.py` silently restored the PRE-M2 version
(all six edits gone), and the brand-new `state_schema.py` couldn't be
restored at all (pathspec unknown to git — the mutation stayed on disk).
Everything was rebuilt from the session context and the suite re-verified,
but the rule is now: **commit first, mutate second.** A mutation check
belongs AFTER the feature commit, never before it.

### Still open (noted, out of M2 scope)
- Scheduler's `_execute_trade` places orders WITHOUT passing
  `approve_order()` — predates M2; flagged against hard rule 3 for Marty to
  rule on (operator-initiated trades: exempt as human orders, or gated?).
- Symbol-vs-Alpaca validation at creation (ST-0002 `SPACEX` lesson) needs a
  broker call — deferred with a note, candidate for the C-track or M6.

### For Marty's review
- `git diff plan-execution..m2-schema-validation` — src changes are confined
  to `state_schema.py` (new), `overrides.py`, `scheduler.py`.
- The three real `data/` files are untouched; they upgrade to v1 on their
  next save after the merged code runs.
- Restart note: the running dashboard process still executes pre-M2 code
  until restarted (as always).

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
