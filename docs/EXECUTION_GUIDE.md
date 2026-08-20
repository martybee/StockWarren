# Claude Code Execution & Review Guide

**Companion to:** *StockWarren Chart & Trading-Signal Plan* (v0.5)
**Purpose:** turn the plan into Claude Code sessions you can review confidently — and understand fully before each one starts.

The core idea: **you stay the architect; Claude Code is the contractor.** Every session gets a written work order (from the plan), produces a reviewable diff on a branch, and passes a checklist *you* run before it merges. Nothing merges that you couldn't explain to Anton or Wally.

---

## 1. One-time repo setup (do this before any coding session)

Put three kinds of files in the StockWarren repo. This takes an evening and pays for itself every session after.

```
stockwarren/
├── CLAUDE.md                     # standing rules the agent reads every session
├── docs/
│   ├── PLAN.md                   # the living plan (v0.5) — copied in, kept current
│   ├── SCHEMA.md                 # the schema spec (the plan's "next step") — written first
│   └── milestones/
│       ├── M1.md … M8.md         # one work order per milestone (template in §4)
```

### CLAUDE.md — the standing rules

This file is read by Claude Code at the start of every session. Keep it short and absolute:

```markdown
# StockWarren — rules for AI-assisted work

## Read first
- docs/PLAN.md   — the architecture. Do not contradict it; propose changes instead.
- docs/SCHEMA.md — data contracts. Timestamps, snapping, gaps are defined THERE only.

## Hard rules — never violate
1. Bar timestamps = interval START, stored UTC. NY time is display/session logic only.
2. A signal at time T may not read any bar after T. The store enforces this; never bypass the store.
3. Every order goes through trading/order_gate.py (kill switch + overrides). No direct Alpaca trading calls anywhere else.
4. Never modify gui/static/js/dashboard.js beyond the single nav hook. Chart code lives in chart.js.
5. Existing JSON/pickle files in data/ are not migrated, renamed, or restructured.
6. bars/signals/trades/runs live in SQLite (data/stockwarren.db) — signals never get written into bars.
7. New dependencies require asking first. Prefer stdlib.
8. Timestamp-touching code requires tests, including the DST-day and half-day fixtures.
9. The chart backend lives INSIDE the existing dashboard process (main.py --dash-only).
   Never start a second process that opens its own Alpaca data connection.
10. Never bypass or reorder the evaluation pipeline
    (composite signal → validation → ML filter → risk engine).
    New strategies feed INTO it; nothing goes around it to Alpaca.

## Workflow
- Work only on the branch named for the current milestone (e.g. m1-chart).
- Small commits with plain-English messages.
- Run the test suite before declaring anything done.
- If the plan is ambiguous, STOP and ask — do not decide architecture silently.

## Commands
- Run app:  python -m gui.app
- Tests:    pytest tests/ -v
```

Rule 8's last line in Workflow is the important one: the plan's whole §4 hazard list exists because these things get decided *implicitly* when nobody writes them down. The agent asking is cheap; the agent guessing is how look-ahead bias gets back in.

### SCHEMA.md comes before M1

The plan's stated next step. Write it *with* Claude in chat (not Claude Code) — it's a thinking document, not a coding task. It must contain: the four tables with exact SQLite types/constraints/indexes, and one unambiguous sentence each for the timestamp convention, the snapping rule, the empty-minute rule, and the extended-hours setting. Once it exists, it — not the plan — is the authority the code answers to.

### The git safety net (run before the first agent session)

```bash
git checkout main && git pull
git tag pre-plan-baseline && git push origin main --tags   # the forever-anchor
git checkout -b plan-execution && git push -u origin plan-execution
tar -czf ~/stockwarren-data-backup-$(date +%F).tar.gz data/ config/settings.ini
```

Three layers: `pre-plan-baseline` (frozen bookmark of the known-working system), `main` (receives nothing until work is proven — the running bot can always restart from it), `plan-execution` (every milestone branch merges here after review). **All work orders branch off `plan-execution`, never `main`.**

Revert playbook: one bad milestone → `git revert -m 1 <merge-commit>`; whole direction wrong → `git checkout main`; scorched earth → `git checkout pre-plan-baseline`.

Caveat: git reverts **code, not state**. `data/` is not in git (runtime files, pickles); re-run the `tar` backup before any milestone that touches storage (engine M2, engine M3/C2), so a code revert can be paired with a data restore.

---

## 2. The per-milestone loop

Every milestone runs the same five steps. The rhythm is the point — after two milestones it becomes automatic.

**Step 1 — Understand (you, 30–60 min).** Answer that milestone's comprehension questions (§3) *out loud or on paper* before opening Claude Code. If you can't, ask Claude in chat until you can. This is the "really understand every part" mechanism — it front-loads understanding to the moment it's cheapest.

**Step 2 — Issue the work order.** Open Claude Code, point it at `docs/milestones/Mx.md` (template in §4). One milestone per session. Resist "while you're at it."

**Step 3 — Let it work on the branch.** Interruptions are fine; scope changes are not. New ideas go into the plan's revision log, not into this session.

**Step 4 — Review (you, with the checklist in §5).** Read the diff file by file. The checklists tell you what to verify by hand — not just "tests pass" but "I opened the DB and looked."

**Step 5 — Merge and log.** Merge the branch, add a line to PLAN.md's revision log, resolve any open question the milestone settled, and **append gotchas to docs/SESSION_NOTES.md** — the append-only log that carries hard-won lessons between Claude Code instances (adopted v0.7 from the repo's own practice; the 88-day-stale-process discovery is the template for what belongs there).

**Track naming (v0.7):** the repo's engine plan owns milestones **M1–M8**; this chart plan owns **C1–C8**. Every work order, branch name, and session prompt states the track — a session told "do M4" without a track name must stop and ask.

---

## 3. Comprehension checkpoints — answer before you prompt

If any answer is fuzzy, that's the topic to discuss in chat before starting the session.

**Before M1:**
- Why does a bar timestamp mean the *start* of its minute, and what specifically goes wrong on the chart if one component assumes the end?
- What is the difference between raw and split-adjusted prices, and why can't one chart mix them?
- What should the chart show for a minute in which nothing traded, and why must the backtester give the same answer?
- Why does the DST test fixture exist? What would break without it?

**Before M2:**
- What are the three separate events between "model likes this stock" and "we own shares," and why does each get its own record?
- What is a `run_id`, and what question becomes unanswerable without it?
- How does the store *physically* prevent look-ahead, rather than trusting strategies to behave?
- Why is the seed strategy deliberately dumb?

**Before M3:**
- What is look-ahead bias, in one sentence, with an example?
- Why does the plan forbid building a second P&L calculator?

**Before M4:**
- Where exactly is the seam between REST history and live data, and when does it reopen?
- Why does the free plan leave a hole at the right edge of "today," and what fills it?
- Why is 5-second polling sufficient for minute candles?

**Before M5:**
- What is idempotency, and what does a `client_order_id` prevent, concretely?
- Trace an order through the gate: which two checks happen before Alpaca is called, and which two files record the result?
- Why is slippage the number that decides whether minute-timeframe trading is viable?

**Before M6:**
- Why are paper fills systematically kinder than real fills?
- What three markers should one live decision produce, and what does the gap between them measure?

**Before any LLM-socket session:**
- Why must the raw model reply be stored with the signal?
- What goes into `params_hash` for an LLM run, and why does changing one word of the prompt create a new run?
- How can a prompt leak the future even though the store can't?

**Before M7/M8:**
- Why is IEX→SIP a model re-validation and not a config change?
- What evidence does §11 require before real money, and who has no deadline pressure? (You. That's the design.)

---

## 4. The work-order template (docs/milestones/Mx.md)

```markdown
# Mx — <name from the plan>

## Goal
<the milestone's Deliverable cell, expanded to 2–3 sentences>

## Read first
- docs/PLAN.md sections: <the sections this milestone implements>
- docs/SCHEMA.md
- Relevant hazards: <e.g. 4.2, 4.10 for M1>

## In scope
<bullet the concrete pieces — files, endpoints, tests>

## Explicitly out of scope
<the next milestone's work, and anything from "cut from the critical path">

## Done means
<the milestone's "done means" cell, verbatim — these are acceptance criteria>

## Branch
mx-<shortname>
```

Filling these out for M1–M3 is itself a great understanding exercise — do it with Claude in chat and argue about what's out of scope.

---

## 5. Review checklists — what YOU verify by hand

Beyond "tests pass." These are the five-to-ten-minute manual checks that catch what tests were never written for.

**M1:**
- [ ] Open `data/stockwarren.db` (`sqlite3` CLI), pick one bar, confirm its timestamp is the minute *start*, in UTC, and matches the candle on screen at the right NY wall-clock time.
- [ ] Load a known DST-transition date and a half-day; the session boundaries look right.
- [ ] Find a minute with no trades on a thin symbol; the chart does what SCHEMA.md says (gap or carry-forward), not something else.
- [ ] Toggle dark/light; chart follows.
- [ ] `dashboard.js` diff is the nav hook and *nothing* else.

**M2:**
- [ ] Pick three MA-crossover markers and verify by eye the crossover really happens on that candle.
- [ ] Look at the runs row: could you reproduce this run from it alone?
- [ ] Try to make a strategy read a future bar (in a test); confirm the store refuses.
- [ ] Hover tooltip shows the signal's fields, on the correct marker.

**M3:**
- [ ] A signal the backtest *didn't* act on shows as signal-without-trade — the distinction renders.
- [ ] Grep confirms no new P&L math was written; numbers come from StockWarren's existing accounting.

**M4:**
- [ ] Watch the chart self-update during a market session.
- [ ] Pull the network mid-session, wait, restore; the gap heals with no duplicate and no hole.
- [ ] Right-edge REST hole (recent ~15 min) is filled by live data, not left blank.
- [ ] The launchd plist from `setup/services/` is loaded; kill the dashboard process and confirm it restarts on its own, bots and chart included. Sleep is disabled during market hours.

**M5:**
- [ ] Create `KILL_SWITCH.lock`; confirm orders are refused.
- [ ] Set a tight operator override; confirm the gate blocks an oversized order.
- [ ] Kill the process between submit and fill, restart; no duplicate order (check Alpaca dashboard).
- [ ] The fill appears in the monthly trade log AND `slippage.csv` AND as a broker marker at the *fill* price.
- [ ] With one of F/NIO/RIVN held, a second correlation-group signal is vetoed — and still renders on the chart as a signal marker with `disposition: vetoed_risk` in the tooltip.
- [ ] A signal failing the 2:1 risk/reward gate is stored, not silently dropped — the chart shows what the system declined and why.

**M6:**
- [ ] One live decision shows signal, order, and fill markers; the price gaps between them are believable.
- [ ] Account selector isolates alpha/beta/gamma histories correctly.

**LLM sockets:**
- [ ] A signal's `rationale` contains the verbatim model reply.
- [ ] Force a malformed reply (test); it becomes `None` + log line, never a trade.
- [ ] The Qwen socket config differs from the Claude socket only by endpoint.
- [ ] Cost estimate exists in the runs row *before* a backtest run starts.

---

## 6. Order of operations from today

1. **The discrepancy hour (you, no agent):** verify the five §14 items — Alpaca dashboard for $9,000 vs $300; `settings.ini` for `max_price` vs the AMD-bearing watchlist; `scheduler.py` for the naive `scheduled_time`; then amend SCHEMA.md's "no database" and timezone clauses. Two additional reads while you're in there: each account's `review_drawdown_pct` (freeze the smallest as the **M5 drawdown-cap literal** in M5.md) and the Qwen server's endpoint/port on the Zephyrus (record as `LLM_SOCKET_URL` for the future socket). Log every finding in `docs/SESSION_NOTES.md`.
2. **Commit the doc set:** repo `PLAN.md` stays the engine plan; this chart plan lands as `docs/CHART_PLAN.md`; both get a one-line sister-doc pointer; extend SCHEMA.md with the four SQLite tables and the four one-sentence rules (timestamp, snapping, empty-minute, extended-hours). Paste the crash-restart policy from CHART_PLAN.md §14 into `milestones/M6.md`.
3. **Engine M1** — the safety-invariant suite — is the first Claude Code build session (comprehension checkpoint first, as always).
4. **Then:** engine M2 (validation) → engine M6 (supervision, with the decided auto-resume policy) → the unified store (engine M3 + C2 schema) → tracks run in parallel: engine M4/M5/M7 ∥ chart C1 onward. LLM sockets slot in any time after C2 as ordinary strategy sessions — backtest mode, daily bars first.

The pace that fits this plan: one milestone per week of evenings, reviews on the weekend. Slower than Claude Code *can* go — exactly as fast as your understanding *should* go.
