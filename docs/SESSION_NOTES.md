# StockWarren — Session Notes

A running log of work sessions, for reference across Claude Code instances.
**Newest session at the top.** Each entry records what was done, what went wrong,
and guardrails so we don't repeat mistakes.

> Companion to `CLAUDE.md`. `CLAUDE.md` = how the project works (stable).
> This file = what happened in sessions + hard-won gotchas (append-only log).

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
