# StockWarren — Living Plan

> **Sister doc:** [CHART_PLAN.md](CHART_PLAN.md) owns the **chart track (C1–C8)**; this file owns the **engine track (M1–M8)**. [SCHEMA.md](SCHEMA.md) is the shared constitution both answer to. (CHART_PLAN §14 Decision 1)

> **Version:** v0.5-draft — drafted 2026-08-19 by Claude from the codebase, config,
> and session history. **This is a stand-in**: Marty will edit it and/or replace it
> with the final source plan. Treat every milestone below as a proposal, not a commitment.
>
> Status legend: ✅ done · 🔄 in progress · ⏳ planned

## Vision

Prove, with paper money and hard safety rails, that a rules-plus-ML equities bot
can trade profitably and safely enough to justify a small live allocation — and
never let the strategy outrun the risk engine while doing it.

## Where we are (2026-08-19)

- **Three paper accounts** (`alpha`, `beta`, `gamma`, $9,000 each) run side by side
  in one process, differing **only in strategy parameters**, so any performance gap
  is attributable to the parameters. Shared 10-symbol watchlist
  (F, PLTR, SOFI, NIO, RIVN, HOOD, SNAP, AMD, BAC, T).
- **Decision chain:** scanner/7-indicator signals → ML filter (untrained; approves
  all) → risk-engine veto gate (`approve_order()`) → order.
- **Safety constitution** (7 fail-closed rules) documented in `CLAUDE.md`, enforced
  in `src/engine/safety.py`. Kill switch is file-backed with no programmatic reset.
- **Dashboard:** Flask + vanilla JS, nav-rail sections per subsystem, 5-second
  polling (SocketIO is initialized server-side but unused by the page).
- **Gaps:** ML untrained (0 completed trades); trade history/P&L not
  consolidated; `slippage.csv` empty (no fills yet). ~~No test suite~~ — M1's
  safety-invariant suite merged 2026-09-15 (132 tests after M2+M6).
  ~~Supervisor not loaded~~ — M6 merged 2026-10-05: policy, staleness guard,
  configured plist; the `launchctl load` cutover drill is the remaining step.

## The strategy experiment

The three accounts are an A/B/C test. The Compare page is the leaderboard. The
decision rule for picking a winner must be written down **before** results exist
(see M5) so we don't cherry-pick after the fact.

## Milestones

One work order per milestone in [docs/milestones/](milestones/). Proposed arc:
*verify the rails → formalize the data → measure → learn → decide → harden →
observe → graduate.*

| # | Title | Theme | Status |
|---|-------|-------|--------|
| [M1](milestones/M1.md) | Safety-invariant test suite | Trust the rails before anything else | ✅ 2026-09-15 |
| [M2](milestones/M2.md) | Data schema & validation | Make `SCHEMA.md` enforced, not aspirational | ✅ 2026-09-30 |
| [M3](milestones/M3.md) | Trade history & P&L persistence | One canonical record per completed trade | ⏳ |
| [M4](milestones/M4.md) | ML pipeline to first trained model | From completed trades to a filtering model | ⏳ |
| [M5](milestones/M5.md) | Strategy comparison & winner decision | Pre-registered decision rule for the A/B/C test | ⏳ |
| [M6](milestones/M6.md) | Process supervision & restart-safe ops | Survive crashes, sleeps, and stale processes | ✅ 2026-10-05 (all drills verified live, breaker included) |
| [M7](milestones/M7.md) | Real-time dashboard & decision log | See the bot think without tailing logs | ⏳ |
| [M8](milestones/M8.md) | Live-readiness review | Written go/no-go; human approval gate | ⏳ |

## Next step

Per the repo setup, **`SCHEMA.md` is written first**: it freezes the shapes of the
persisted JSON state files and API payloads so M1's tests and M2's validators have
a spec to check against. Draft v0.1 exists at [docs/SCHEMA.md](SCHEMA.md).

## Operating principles (standing, from the constitution)

- Fail closed: uncertainty ⇒ no trade / smaller size.
- Paper trading until an explicit, human, written go decision (M8).
- One-way trailing stops — tighten only, never loosen.
- Risk limits are human-tunable only, clamped to hard ceilings; leverage fixed at 1×.
- The bot can trip the kill switch but never reset it.

## Open questions for Marty

- Is this 8-milestone arc right?Reorder/replace freely — it was inferred, not specified.
- What's the winner metric for M5 (profit factor? drawdown-adjusted? win rate at N trades)?
- Any target date or criteria for the M8 live decision, and the live dollar size?
