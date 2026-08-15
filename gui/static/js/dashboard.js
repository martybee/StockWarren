// StockWarren Dashboard JavaScript

const API = '';
let refreshInterval;
let accountsSig = '';   // last-rendered <option> markup; see updateAccounts()

// ---- Per-account identity colour ----
// The server has no colour or index concept, and shouldn't: this is purely
// presentation. /api/accounts returns the accounts in config order INCLUDING
// unconfigured ones, so array position is a stable ordinal that does not shift
// when an account loses its keys. That ordinal picks one of the four .acct-c*
// classes defined in style.css.
const ACCT_CLASSES = ['acct-c1', 'acct-c2', 'acct-c3', 'acct-c4'];
const ACCT_FLASH_MS = 700;   // must exceed the acct-flash animation duration
let ACCT_INDEX = {};         // id -> 0-based ordinal; empty until /api/accounts lands
let ACCT_CURRENT = null;     // server-selected id (a process-wide global in app.py)
let ACCOUNTS_CACHE = [];     // last /api/accounts payload, incl. each watchlist
let currentWatchlist = [];   // selected account's symbols, from /api/status
let lastAccountId = null;    // change detection; null means no status seen yet

/** Colour class for an account id, or '' while its ordinal is still unknown. */
function acctClass(id) {
    const i = ACCT_INDEX[id];
    if (i === undefined) return '';
    return ACCT_CLASSES[i % ACCT_CLASSES.length];   // a 5th account wraps to c1
}

/** Idempotently leave exactly one acct-c* class on an element. */
function setAcctColour(el, id) {
    if (!el) return;
    const want = acctClass(id);
    ACCT_CLASSES.forEach(c => { if (c !== want) el.classList.remove(c); });
    if (want) el.classList.add(want);
}

/**
 * Brief coloured ring on the identity badge and the header figures.
 * Restarted by hand: re-adding a class that is already present does NOT replay
 * a CSS animation, and rapid switching has to re-fire.
 */
function flashAccountChange() {
    document.querySelectorAll('.acct-flash-target').forEach(el => {
        el.classList.remove('acct-flashing');
        void el.offsetWidth;                 // forced reflow restarts the animation
        el.classList.add('acct-flashing');
        clearTimeout(el._acctFlashTimer);
        // A timer, NOT an animationend listener: under prefers-reduced-motion
        // the CSS sets animation:none, animationend never fires, and the class
        // (with its ring) would stay on the element permanently.
        el._acctFlashTimer = setTimeout(
            () => el.classList.remove('acct-flashing'), ACCT_FLASH_MS);
    });
}
/**
 * Set a badge's colour variant WITHOUT destroying its other classes.
 * The old code did `el.className = 'badge ' + variant`, which silently wiped the
 * `clickable` class the template puts on #safety-badge — so after the first 5s
 * refresh the badge lost its cursor and hover affordance while its onclick kept
 * working, which reads as a broken control.
 */
const BADGE_VARIANTS = ['badge-green', 'badge-red', 'badge-blue', 'badge-yellow', 'badge-neutral'];
function setBadgeVariant(el, variant) {
    if (!el) return;
    el.classList.add('badge');
    BADGE_VARIANTS.forEach(v => { if (v !== variant) el.classList.remove(v); });
    if (variant) el.classList.add(variant);
}

// ==================== Market state ====================
// One badge replaces what used to be a MARKET OPEN/CLOSED badge plus a separate
// countdown beside the title. Those had two different clocks: the badge read
// `market_open` from /api/status (hard-coded False whenever the bot was
// stopped), while the countdown did browser-side ET arithmetic that
// approximated DST and knew nothing about holidays or half-days. A stopped bot
// at 10am therefore showed "MARKET CLOSED" beside "02:13:44 until market closes".
//
// Now: /api/market (Alpaca's clock via market_calendar) gives absolute
// next_open/next_close instants; the browser interpolates the seconds against
// them. Data refreshes on a slow poll, the digits tick every second, and
// holidays/half-days are correct because the exchange supplies the boundary.

const MARKET_POLL_MS = 30000;   // market state is global and changes slowly
let marketTimer;                // 1s display tick
let marketPollTimer;
let marketState = null;         // last good /api/market payload
let controlSubMode = '--';      // "Paper"/"Live", from /api/status

/** The Overview control bar's sub-line. Reads the SAME market source as the
 *  header badge so the two can never contradict each other — they used to,
 *  because both read `market_open`, which was false whenever the bot was stopped. */
function setControlSub() {
    const el = document.getElementById('control-sub');
    if (!el) return;
    const mkt = !marketState || marketState.error ? 'unknown'
              : marketState.is_open ? 'open'
              : marketState.is_premarket ? 'pre-market'
              : marketState.is_afterhours ? 'after-hours'
              : 'closed';
    el.textContent = controlSubMode + ' · Market ' + mkt;
}

/** "21:07:03", or "1d 19:12" past a day — the old code emitted "63:24:11". */
function fmtRemaining(ms) {
    if (ms == null || ms < 0) return '--:--:--';
    const total = Math.floor(ms / 1000);
    const d = Math.floor(total / 86400);
    const h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    const p = (n) => String(n).padStart(2, '0');
    return d > 0 ? `${d}d ${p(h)}:${p(m)}` : `${p(h)}:${p(m)}:${p(sec)}`;
}

async function updateMarket() {
    try {
        const res = await fetch(API + '/api/market');
        if (!res.ok) { marketState = null; renderMarket(); return; }
        marketState = await res.json();
    } catch (err) {
        // Unknown beats guessing: renderMarket() paints the neutral '--' state
        // rather than asserting OPEN or CLOSED we cannot stand behind.
        console.error('Failed to fetch market status:', err);
        marketState = null;
    }
    renderMarket();
}

function renderMarket() {
    const el = document.getElementById('market-badge');
    if (!el) return;

    if (!marketState || marketState.error) {
        setBadgeVariant(el, 'badge-neutral');
        el.textContent = 'MARKET --';
        el.title = 'Market state unavailable — the dashboard could not reach the exchange clock. '
                 + 'This is NOT a statement that the market is closed.';
        return;
    }

    const s = marketState;
    const open = !!s.is_open;
    // Session label: premarket/after-hours are real states the old pair of
    // elements could not express at all.
    const label = open ? 'OPEN'
                : s.is_premarket ? 'PRE-MARKET'
                : s.is_afterhours ? 'AFTER-HOURS'
                : 'CLOSED';
    const target = open ? s.next_close : s.next_open;
    const word = open ? 'TO CLOSE' : 'TO OPEN';

    let remaining = '--:--:--';
    if (target) {
        // Absolute instant from the exchange, so the tick needs no re-fetch and
        // no local timezone maths.
        remaining = fmtRemaining(new Date(target).getTime() - Date.now());
    }

    setBadgeVariant(el, open ? 'badge-green' : 'badge-red');
    el.innerHTML = `${label} · <span class="mkt-time">${remaining}</span> ${word}`;
    el.title = (open ? 'The market is open.' : `The market is ${label.toLowerCase()}.`)
        + (target ? ` Next ${open ? 'close' : 'open'}: ${new Date(target).toLocaleString()}.` : '')
        + ' Source: the exchange clock, independent of whether the bot is running.';

    setControlSub();
}

// Format currency
function fmt(val) {
    if (val === null || val === undefined || val === '--') return '--';
    return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Format percentage
function pct(val) {
    if (val === null || val === undefined) return '--';
    return Number(val).toFixed(1) + '%';
}

// Add positive/negative class
function pnlClass(val) {
    if (val > 0) return 'positive';
    if (val < 0) return 'negative';
    return 'neutral';
}

// Fetch and update status
async function updateStatus() {
    try {
        const res = await fetch(API + '/api/status');
        const data = await res.json();

        // Bot status
        const botBadge = document.getElementById('bot-status');
        botBadge.textContent = data.running ? 'RUNNING' : 'STOPPED';
        setBadgeVariant(botBadge, data.running ? 'badge-green' : 'badge-red');

        // Control bar status (top of Overview)
        const dot = document.getElementById('control-dot');
        if (dot) {
            dot.className = 'control-dot ' + (data.running ? 'running' : 'stopped');
            document.getElementById('control-state').textContent = 'Bot: ' + (data.running ? 'RUNNING' : 'STOPPED');
            controlSubMode = data.paper_mode ? 'Paper' : 'Live';
            setControlSub();
        }

        // Mode badge
        const modeBadge = document.getElementById('mode-badge');
        modeBadge.textContent = data.paper_mode ? 'PAPER' : 'LIVE';
        setBadgeVariant(modeBadge, data.paper_mode ? 'badge-yellow' : 'badge-red');

        // Which account every figure on this page belongs to. Driven off the 5s
        // poll rather than set once at switch time, so it self-corrects if
        // another tab changes the (server-global) selection out from under us.
        const accName = data.name || data.account_id || '--';
        const accId = data.account_id;
        if (accId) ACCT_CURRENT = accId;    // updateAccounts() is the other writer

        const accTag = document.getElementById('account-name-tag');
        if (accTag) accTag.textContent = accName;

        // Identity colour, re-applied every poll for the same reason as above.
        // #account-stats is in the HEADER while #account-identity now sits beside
        // the page title, so neither inherits --acct from the other — each needs
        // the class in its own right or its flash ring falls back to neutral.
        setAcctColour(accTag, accId);
        setAcctColour(document.getElementById('account-identity'), accId);
        setAcctColour(document.getElementById('account-stats'), accId);

        const idInitial = document.getElementById('acct-identity-initial');
        const idName = document.getElementById('acct-identity-name');
        if (idInitial) idInitial.textContent = (accName.trim().charAt(0) || '?').toUpperCase();
        if (idName) idName.textContent = accName;

        // Header figures for the selected account. TradingBot.get_status()
        // silently degrades `account` to {portfolio_value:0, cash:0, equity:0}
        // when the broker call fails; a missing buying_power is the tell. Show
        // '--' in that case rather than presenting fake zero balances as real.
        const acct = data.account || {};
        const degraded = acct.buying_power === undefined;
        const hdrPortfolio = document.getElementById('hdr-portfolio');
        const hdrCash = document.getElementById('hdr-cash');
        const hdrPositions = document.getElementById('hdr-positions');
        const hdrPnl = document.getElementById('hdr-daypnl');
        if (hdrPortfolio) hdrPortfolio.textContent = degraded ? '--' : fmt(acct.portfolio_value);
        if (hdrCash) hdrCash.textContent = degraded ? '--' : fmt(acct.cash);
        if (hdrPositions) {
            const stats = data.stats || {};
            const pos = stats.active_positions != null ? stats.active_positions : data.active_positions;
            hdrPositions.textContent = pos != null ? pos : '--';
        }
        if (hdrPnl) {
            const daily = (data.stats || {}).daily_pnl;
            hdrPnl.textContent = fmt(daily);
            hdrPnl.className = 'astat-value ' + pnlClass(daily);
        }

        // The real Alpaca account number — the one thing on screen that is
        // provably different per account even when every figure matches. Last 6
        // hex chars: last-4 is only a 1-in-65k collision surface, and the full
        // 36-char UUID will not fit a header that already wraps. Reuse the same
        // `degraded` tell as the figures above (that shape has no `id` either)
        // so the badge and the numbers always agree about broker reachability.
        const brokerEl = document.getElementById('acct-identity-broker');
        const identityEl = document.getElementById('account-identity');
        if (brokerEl) {
            const full = (!degraded && acct.id) ? String(acct.id) : '';
            const tail = full ? full.replace(/-/g, '').slice(-6).toUpperCase() : '';
            brokerEl.textContent = tail ? '···' + tail : '--';
            // The masked form is gibberish read aloud, hence the spelled-out label.
            brokerEl.setAttribute('aria-label', tail
                ? 'Alpaca account ending ' + tail
                : 'Alpaca account number unavailable');
            if (identityEl) {
                identityEl.title = full
                    ? accName + ' — Alpaca account ' + full
                    : accName + ' — account number unavailable (broker unreachable)';
            }
        }

        // Flash on switch. Driven off the poll rather than selectAccount()
        // because the selection is a process-wide global — another tab or a
        // curl can change it, and this poll is the only way we'd find out.
        // lastAccountId === null means no status has ever been seen, i.e. the
        // cold-load case: record it, never flash. The catch block below leaves
        // lastAccountId untouched, so a 503 during startup can't fake a switch.
        if (accId) {
            if (lastAccountId !== null && accId !== lastAccountId) flashAccountChange();
            lastAccountId = accId;
        }

        // Account
        if (data.account) {
            document.getElementById('portfolio-value').textContent = fmt(data.account.portfolio_value);
            document.getElementById('cash').textContent = fmt(data.account.cash);
            document.getElementById('buying-power').textContent = fmt(data.account.buying_power);
            document.getElementById('day-trades').textContent = data.account.day_trade_count || '0';
        }

        // Stats
        if (data.stats) {
            const dailyPnl = document.getElementById('daily-pnl');
            dailyPnl.textContent = fmt(data.stats.daily_pnl);
            dailyPnl.className = 'value ' + pnlClass(data.stats.daily_pnl);

            const totalPnl = document.getElementById('total-pnl');
            totalPnl.textContent = fmt(data.stats.total_pnl);
            totalPnl.className = 'value ' + pnlClass(data.stats.total_pnl);

            document.getElementById('win-rate').textContent = pct(data.stats.win_rate);
            document.getElementById('total-trades').textContent = data.stats.total_trades;
            document.getElementById('max-drawdown').textContent = fmt(-data.stats.max_drawdown);
            document.getElementById('consec-losses').textContent = data.stats.consecutive_losses;
        }

        // Watchlist
        if (data.watchlist) {
            currentWatchlist = data.watchlist;
            updateWatchlistTags(data.watchlist);
            renderOtherWatchlists();   // "missing" is relative to this list
        }

        document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString();

    } catch (err) {
        console.error('Failed to fetch status:', err);
    }
}

// ==================== Page router ====================

const PAGE_TITLES = {
    overview: 'Overview', compare: 'Compare Accounts', signals: 'Signals', ml: 'ML Filter',
    risk: 'Risk Engine', scanner: 'Scanner', scheduler: 'Scheduler', safety: 'Safety',
    eod: 'EOD Manager', notifications: 'Notifications',
};

function navigate(page) {
    if (!PAGE_TITLES[page]) page = 'overview';
    document.querySelectorAll('[data-page]').forEach(el => {
        el.style.display = (el.dataset.page === page) ? '' : 'none';
    });
    document.querySelectorAll('.rail-item').forEach(el => {
        el.classList.toggle('active', el.dataset.nav === page);
    });
    const title = document.getElementById('page-title');
    if (title) title.textContent = PAGE_TITLES[page];
    if (('#' + page) !== window.location.hash) {
        try { window.location.hash = page; } catch (e) {}
    }
    window.scrollTo(0, 0);
    if (page === 'safety') updateSafety();  // freshen enforcement state on open
}

function currentPageFromHash() {
    const h = (window.location.hash || '').replace('#', '');
    return PAGE_TITLES[h] ? h : 'overview';
}
window.addEventListener('hashchange', function () { navigate(currentPageFromHash()); });

// ---- Safety badge ----------------------------------------------------------
// One badge, one question: "would the safety layer let this bot open a position
// right now?" — NOT "is the bot running" (#bot-status already answers that) and
// NOT "does a kill-switch file exist", which is all it used to report. That old
// meaning let it show green SAFE while the engine was completely blocked by a
// loss limit, a position limit or a cool-off.
// The state names come from GATE_STATE in src/engine/safety.py; the mapping is
// enforced by tests/test_trading_gate.py, which fails if a gate code has no copy
// here.
const SAFETY_STATES = {
    HALTED:  { text: 'HALTED',      variant: 'badge-red',
               lead: 'HALTED — the kill switch is tripped. No new orders at all. Open positions are still managed (stops keep tightening).' },
    BLOCKED: { text: 'BLOCKED',     variant: 'badge-red',
               lead: 'BLOCKED — the risk engine is refusing new positions.' },
    PAUSED:  { text: 'PAUSED',      variant: 'badge-yellow',
               lead: 'PAUSED — cooling off after consecutive losses. Existing positions are still managed.' },
    FULL:    { text: 'AT CAPACITY', variant: 'badge-blue',
               lead: 'AT CAPACITY — every position slot is in use, so no new entries. Normal saturation, not a fault.' },
    SAFE:    { text: 'SAFE',        variant: 'badge-green',
               lead: 'SAFE — the risk engine would allow a new position right now.' },
    UNKNOWN: { text: '--',          variant: 'badge-neutral',
               lead: 'UNKNOWN — the dashboard could not read the safety state.' },
};

function agoText(s) {
    if (s == null) return '';
    if (s < 90) return s + 's ago';
    if (s < 5400) return Math.round(s / 60) + 'm ago';
    return Math.round(s / 3600) + 'h ago';
}

/** How much to trust the numbers behind the badge. Never omitted: the gate reads
 *  in-memory risk stats, which only move when the bot ticks. */
function safetyFreshness(d) {
    const f = (d && d.freshness) || {};
    const out = [];
    if (f.bot_running === false) {
        out.push(f.last_tick
            ? 'Bot is STOPPED — figures are from its last tick ' + agoText(f.stale_seconds) + '.'
            : 'Bot is STOPPED and has not ticked this session — P&L figures are zeroed, not measured.');
    } else if (f.last_tick) {
        out.push('Figures as of the last engine tick ' + agoText(f.stale_seconds) + '.');
    } else {
        out.push('The engine has not completed a tick yet this session.');
    }
    if (f.daily_stats_stale)  out.push('Daily P&L is left over from a previous day and zeroes on the next tick.');
    if (f.weekly_stats_stale) out.push('Weekly P&L is left over from a previous week and zeroes on the next tick.');
    if (f.broker_ok === false) out.push('Broker unreachable — drawdown is the last value the engine computed, not a live reading.');
    return out.join(' ');
}

function safetyState(d) {
    if (!d) return 'UNKNOWN';
    if (d.kill_switch && d.kill_switch.tripped) return 'HALTED';
    if (d.halt_new_orders) return 'BLOCKED';
    const g = d.gate || {};
    if (g.allowed === false) return SAFETY_STATES[g.state] ? g.state : 'BLOCKED';
    if (g.allowed === true) return 'SAFE';
    return 'UNKNOWN';
}

function safetyTooltip(state, d) {
    const lines = [SAFETY_STATES[state].lead];
    if (state === 'UNKNOWN') {
        lines.push('The bot may or may not be trading — do NOT read this as SAFE.');
        lines.push('Clears when /api/safety responds again.');
        return lines.join('\n');
    }
    if (state === 'HALTED') {
        const why = d.kill_switch && d.kill_switch.reason;
        if (why) lines.push('Cause: ' + why);
        lines.push(d.kill_switch && d.kill_switch.reset_hint
            ? 'To resume: ' + d.kill_switch.reset_hint
            : 'To resume, a human must remove the kill-switch file by hand.');
    } else if (state !== 'SAFE') {
        const g = d.gate || {};
        if (g.reason) lines.push('Reason: ' + g.reason);
    } else {
        lines.push('Note: this is about the safety layer only — it does not mean a trade is being placed.');
    }
    const fresh = safetyFreshness(d);
    if (fresh) lines.push(fresh);
    lines.push('Click to open the Safety section.');
    return lines.join('\n');
}

function renderSafetyBadge(state, d) {
    const el = document.getElementById('safety-badge');
    if (!el) return;
    const cfg = SAFETY_STATES[state] || SAFETY_STATES.UNKNOWN;
    el.textContent = cfg.text;
    setBadgeVariant(el, cfg.variant);   // preserves `clickable`
    el.title = safetyTooltip(state, d);
}

// Update Safety Constitution panel (rules + live enforcement state)
async function updateSafety() {
    try {
        const res = await fetch(API + '/api/safety');
        if (!res.ok) { renderSafetyBadge('UNKNOWN', null); return; }
        const d = await res.json();

        const tripped = d.kill_switch && d.kill_switch.tripped;

        // Panel kill-switch badge — still the raw kill-switch bit, deliberately.
        const ksBadge = document.getElementById('killswitch-badge');
        ksBadge.textContent = tripped ? 'KILL SWITCH: TRIPPED' : 'KILL SWITCH: ARMED';
        setBadgeVariant(ksBadge, tripped ? 'badge-red' : 'badge-green');

        // Persistent header badge — the honest summary.
        renderSafetyBadge(safetyState(d), d);

        // Reason + halt + paused
        document.getElementById('killswitch-reason').textContent =
            tripped ? (d.kill_switch.reason || '') : '';
        document.getElementById('halt-badge').style.display = d.halt_new_orders ? '' : 'none';
        document.getElementById('paused-badge').style.display = d.is_paused ? '' : 'none';

        // Reset hint (only shown when tripped — reset is a deliberate human action)
        const hint = document.getElementById('reset-hint');
        if (tripped) {
            hint.style.display = '';
            hint.textContent = 'To resume: ' + (d.kill_switch.reset_hint || 'remove the kill-switch file by hand.');
        } else {
            hint.style.display = 'none';
        }

        // Rules list
        const list = document.getElementById('rules-list');
        if (d.rules && d.rules.length) {
            list.innerHTML = d.rules.map(r => `
                <li class="rule-item">
                    <span class="rule-check">✓</span>
                    <span class="rule-num">${r.n}</span>
                    <span class="rule-text">${escapeHtml(r.rule)}
                        <span class="rule-enforced">${escapeHtml(r.enforced_by)}</span>
                    </span>
                </li>`).join('');
        }
    } catch (err) {
        // Never leave a stale green SAFE on screen when we've lost contact —
        // that would be the one failure mode that actively misleads.
        console.error('Failed to fetch safety:', err);
        renderSafetyBadge('UNKNOWN', null);
    }
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

// ==================== Risk Limits card (editable, lock to apply) ====================

const pctN = (v, d) => Number(v).toFixed(d) + '%';
const LIMIT_TILES = [
    { key: 'max_leverage',        label: 'Leverage',       editable: false, fmt: v => Number(v).toFixed(0) + 'x',
      desc: 'Cash only — no borrowed buying power (fixed at 1×).' },
    { key: 'max_positions',       label: 'Max Positions',  editable: true,  min: 1,    max: 20,     step: 1,    fmt: v => v,
      desc: 'Most positions the bot can hold open at once.' },
    { key: 'max_position_pct',    label: 'Max Position %', editable: true,  min: 1,    max: 100,    step: 1,    fmt: v => pctN(v, 0),
      desc: 'Largest share of the portfolio in any single position.' },
    { key: 'risk_per_trade_pct',  label: 'Risk / Trade',   editable: true,  min: 0.05, max: 5,      step: 0.05, fmt: v => pctN(v, 2),
      desc: 'Target risk sized per trade — drives the position size.' },
    { key: 'max_risk_per_trade_pct', label: 'Max Risk / Trade', editable: true, min: 0.1, max: 5,   step: 0.05, fmt: v => pctN(v, 2),
      desc: 'Hard ceiling on any single trade’s risk.' },
    { key: 'max_combined_open_risk_pct', label: 'Combined Open Risk', editable: true, min: 0.25, max: 20, step: 0.25, fmt: v => pctN(v, 2),
      desc: 'Cap on total risk across all open positions at once.' },
    { key: 'max_daily_loss_pct',  label: 'Daily Loss %',   editable: true,  min: 0.5,  max: 50,     step: 0.5,  fmt: v => pctN(v, 1),
      desc: 'Bot stops trading for the day after this % loss.' },
    { key: 'max_weekly_loss_pct', label: 'Weekly Loss %',  editable: true,  min: 0.5,  max: 50,     step: 0.5,  fmt: v => pctN(v, 1),
      desc: 'Bot stops trading for the week after this % loss.' },
    { key: 'review_drawdown_pct', label: 'Review Drawdown', editable: true, min: 1,    max: 50,     step: 0.5,  fmt: v => pctN(v, 1),
      desc: 'Flags the strategy for review at this drawdown (keeps trading).' },
    { key: 'shutdown_drawdown_pct', label: 'Shutdown Drawdown', editable: true, min: 1, max: 50,    step: 0.5,  fmt: v => pctN(v, 1),
      desc: 'Trips the kill switch and halts all trading at this drawdown.' },
    { key: 'max_daily_loss',      label: 'Daily Loss $',   editable: true,  min: 1,    max: 100000, step: 1,    fmt: v => '$' + Number(v).toFixed(0),
      desc: 'Absolute daily-loss cap (the % limit usually binds first).' },
    { key: 'min_risk_reward_ratio', label: 'Min R:R',      editable: true,  min: 1,    max: 10,     step: 0.1,  fmt: v => Number(v).toFixed(1) + ':1',
      desc: 'Minimum reward-to-risk ratio required before taking a trade.' },
];
let currentLimits = {};
let limitsEditing = false;

async function updateLimits() {
    if (limitsEditing) return;  // don't clobber an in-progress edit
    try {
        const res = await fetch(API + '/api/overrides');
        if (!res.ok) return;
        const d = await res.json();
        currentLimits = d.current || {};
        renderLimits(false);
    } catch (err) {
        console.error('Failed to fetch limits:', err);
    }
}

function renderLimits(editing) {
    const grid = document.getElementById('limits-grid');
    if (!grid) return;
    grid.innerHTML = LIMIT_TILES.map(t => {
        const val = currentLimits[t.key];
        if (editing && t.editable) {
            return `<div class="metric">
                <span class="label">${t.label}</span>
                <input class="limit-input" id="edit-${t.key}" type="number"
                       min="${t.min}" max="${t.max}" step="${t.step}"
                       value="${val === undefined || val === null ? '' : val}">
                <span class="metric-desc">${t.desc}</span>
            </div>`;
        }
        const lock = ' 🔒';
        const cls = t.editable ? 'value' : 'value fixed';
        const shown = (val === undefined || val === null) ? '--' : t.fmt(val);
        return `<div class="metric">
            <span class="label">${t.label}${lock}</span>
            <span class="${cls}">${shown}</span>
            <span class="metric-desc">${t.desc}</span>
        </div>`;
    }).join('');
}

function toggleLimitsEdit() {
    if (!limitsEditing) {
        limitsEditing = true;
        renderLimits(true);
        const btn = document.getElementById('limits-edit-btn');
        btn.textContent = '🔒 Lock In';
        btn.classList.add('editing');
        btn.title = 'Save & lock these limits';
        document.getElementById('limits-actions').style.display = 'flex';
        document.getElementById('limits-msg').textContent = '';
    } else {
        saveLimits();
    }
}

function exitLimitsEdit() {
    limitsEditing = false;
    const btn = document.getElementById('limits-edit-btn');
    btn.textContent = '🔓 Edit';
    btn.classList.remove('editing');
    btn.title = 'Unlock to edit';
    document.getElementById('limits-actions').style.display = 'none';
    renderLimits(false);
}

function cancelLimitsEdit() {
    exitLimitsEdit();
    document.getElementById('limits-msg').textContent = '';
}

async function saveLimits() {
    const body = {};
    LIMIT_TILES.filter(t => t.editable).forEach(t => {
        const el = document.getElementById('edit-' + t.key);
        if (el && el.value.trim() !== '') body[t.key] = el.value.trim();
    });
    const msg = document.getElementById('limits-msg');
    try {
        const res = await fetch(API + '/api/overrides', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const d = await res.json();
        if (res.ok) {
            currentLimits = d.limits || currentLimits;
            exitLimitsEdit();  // re-renders read-only with new values
            const notes = (d.notes && d.notes.length) ? ' (' + d.notes.join('; ') + ')' : '';
            msg.textContent = '🔒 Locked in.' + notes;
            msg.style.color = 'var(--accent-green)';
            updateSafety();  // header badge / kill-switch panel stay in sync
        } else {
            msg.textContent = 'Error: ' + (d.error || 'unknown');
            msg.style.color = 'var(--accent-red)';
        }
    } catch (err) {
        msg.textContent = 'Error: ' + err.message;
        msg.style.color = 'var(--accent-red)';
    }
}

async function resetLimits() {
    if (!confirm('Reset all risk limits to the config baseline?')) return;
    const msg = document.getElementById('limits-msg');
    try {
        const res = await fetch(API + '/api/overrides/reset', { method: 'POST' });
        const d = await res.json();
        if (res.ok) {
            currentLimits = d.limits || currentLimits;
            exitLimitsEdit();
            msg.textContent = 'Reset to config baseline.';
            msg.style.color = 'var(--text-secondary)';
        }
    } catch (err) {
        msg.textContent = 'Error: ' + err.message;
        msg.style.color = 'var(--accent-red)';
    }
}

// Update positions table
async function updatePositions() {
    try {
        const res = await fetch(API + '/api/positions');
        const positions = await res.json();
        const tbody = document.getElementById('positions-body');

        if (!positions || positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">No active positions</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(p => `
            <tr>
                <td><strong>${p.symbol}</strong></td>
                <td>${p.side.toUpperCase()}</td>
                <td>${p.qty}</td>
                <td>${fmt(p.avg_entry_price)}</td>
                <td>${fmt(p.current_price)}</td>
                <td class="${pnlClass(p.unrealized_pl)}">${fmt(p.unrealized_pl)}</td>
                <td class="${pnlClass(p.unrealized_plpc)}">${pct(p.unrealized_plpc * 100)}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Failed to fetch positions:', err);
    }
}

// Update orders table
async function updateOrders() {
    try {
        const res = await fetch(API + '/api/orders');
        const orders = await res.json();
        const tbody = document.getElementById('orders-body');

        if (!orders || orders.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty">No open orders</td></tr>';
            return;
        }

        tbody.innerHTML = orders.map(o => `
            <tr>
                <td><strong>${o.symbol}</strong></td>
                <td>${o.side.toUpperCase()}</td>
                <td>${o.type}</td>
                <td>${o.qty}</td>
                <td>${fmt(o.limit_price || o.stop_price || '--')}</td>
                <td>${o.status}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Failed to fetch orders:', err);
    }
}

// Update trade history
async function updateHistory() {
    try {
        const res = await fetch(API + '/api/trades');
        const trades = await res.json();
        const tbody = document.getElementById('history-body');

        if (!trades || trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">No trades yet</td></tr>';
            return;
        }

        tbody.innerHTML = trades.reverse().slice(0, 20).map(t => `
            <tr>
                <td>${new Date(t.time).toLocaleTimeString()}</td>
                <td><strong>${t.symbol}</strong></td>
                <td>${t.side.toUpperCase()}</td>
                <td>${t.qty}</td>
                <td>${fmt(t.entry_price)}</td>
                <td>${Number(t.signal_strength).toFixed(0)}%</td>
                <td>${Number(t.rr_ratio).toFixed(1)}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Failed to fetch trades:', err);
    }
}

// Watchlist
function updateWatchlistTags(symbols) {
    const container = document.getElementById('watchlist-tags');
    container.innerHTML = symbols.map(s => `
        <span class="watchlist-tag">
            ${s}
            <span class="remove" onclick="removeFromWatchlist('${s}')">&times;</span>
        </span>
    `).join('');
}

async function addToWatchlist() {
    const input = document.getElementById('watchlist-input');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;

    await fetch(API + '/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ add: symbol })
    });
    input.value = '';
    await refreshWatchlists();
}

async function removeFromWatchlist(symbol) {
    await fetch(API + '/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remove: symbol })
    });
    await refreshWatchlists();
}

/**
 * Copy symbols from another account onto THIS one.
 * @param {string|string[]} symbols  one symbol, or every symbol we're missing
 */
async function addSymbolsToWatchlist(symbols) {
    const list = Array.isArray(symbols) ? symbols : [symbols];
    if (!list.length) return;
    await fetch(API + '/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ add: list })     // the endpoint accepts a list
    });
    await refreshWatchlists();
}

/** Both panels read from different endpoints, so refresh them together —
 *  otherwise a copied symbol lingers as "missing" until the next 5s tick. */
async function refreshWatchlists() {
    await Promise.all([updateStatus(), updateAccounts(true)]);
}

/**
 * "In other accounts": for each OTHER account, the symbols it watches that this
 * one does not. Accounts with nothing to offer are omitted entirely rather than
 * rendered empty — the panel is a to-do list, not an inventory.
 */
function renderOtherWatchlists() {
    const box = document.getElementById('watchlist-others');
    if (!box) return;

    const mine = new Set(currentWatchlist);
    const others = ACCOUNTS_CACHE.filter(a => a.id !== ACCT_CURRENT && a.configured);

    if (!others.length) {
        box.innerHTML = '';
        return;
    }

    const rows = others.map(a => {
        const missing = (a.watchlist || []).filter(s => !mine.has(s));
        if (!missing.length) {
            return `<div class="wl-other-row">
                        <span class="wl-other-name ${acctClass(a.id)}">
                            <span class="acct-dot" aria-hidden="true"></span>${escapeHtml(a.name)}
                        </span>
                        <span class="wl-other-none">nothing you're missing</span>
                    </div>`;
        }
        const chips = missing.map(s =>
            `<button type="button" class="wl-add-chip" title="Add ${escapeHtml(s)} to this account's watchlist"
                     onclick="addSymbolsToWatchlist('${escapeHtml(s)}')">${escapeHtml(s)} <span aria-hidden="true">+</span></button>`
        ).join('');
        return `<div class="wl-other-row">
                    <span class="wl-other-name ${acctClass(a.id)}">
                        <span class="acct-dot" aria-hidden="true"></span>${escapeHtml(a.name)}
                    </span>
                    <span class="wl-other-chips">${chips}</span>
                    <button type="button" class="btn btn-small wl-add-all"
                            onclick="addSymbolsToWatchlist(${escapeHtml(JSON.stringify(missing))})">
                        Add all ${missing.length}
                    </button>
                </div>`;
    }).join('');

    box.innerHTML = `<div class="wl-others-head">In other accounts</div>${rows}`;
}

// Bot controls
async function startBot() {
    if (!confirm('Start the trading bot?')) return;
    await fetch(API + '/api/bot/start', { method: 'POST' });
    updateStatus();
}

async function stopBot() {
    await fetch(API + '/api/bot/stop', { method: 'POST' });
    updateStatus();
}

async function runScan() {
    navigate('scanner');   // results live on the Scanner page
    const tbody = document.getElementById('scan-body');
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Scanning...</td></tr>';

    try {
        const res = await fetch(API + '/api/scan', { method: 'POST' });
        const results = await res.json();

        if (results && results.error) {
            tbody.innerHTML = `<tr><td colspan="7" class="empty">Scan failed: ${escapeHtml(results.error)}</td></tr>`;
            return;
        }

        if (!results || results.length === 0) {
            // Say WHY it's empty. "No opportunities found" reads like a verdict on
            // the market when it usually means nothing cleared the score floor.
            tbody.innerHTML = '<tr><td colspan="7" class="empty">'
                + 'Nothing scored high enough to show. Every watchlist symbol was checked — '
                + 'none reached the minimum score (<code>min_score</code> in the <code>[scanner]</code> config). '
                + 'Quiet sessions and pre-market hours score low by nature.'
                + '</td></tr>';
            return;
        }

        tbody.innerHTML = results.map(r => `
            <tr>
                <td><strong>${r.symbol}</strong></td>
                <td>${r.score.toFixed(0)}</td>
                <td class="${r.direction > 0 ? 'positive' : 'negative'}">
                    ${r.direction > 0 ? 'BUY' : 'SELL'}
                </td>
                <td>${fmt(r.price)}</td>
                <td class="${pnlClass(r.change_pct)}">${r.change_pct.toFixed(1)}%</td>
                <td>${r.volume_ratio.toFixed(1)}x${r.volume_basis && r.volume_basis !== 'full day'
                        ? ` <span class="scan-basis" title="Today's bar is incomplete, so volume is compared on this basis instead of a finished day">${escapeHtml(r.volume_basis)}</span>`
                        : ''}</td>
                <td>${escapeHtml(r.reasons.join(', '))}</td>
            </tr>
        `).join('');
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">Scan failed</td></tr>';
    }
}

async function emergencyStop() {
    if (!confirm('EMERGENCY STOP: This will close ALL positions and cancel ALL orders. Continue?')) return;
    if (!confirm('Are you absolutely sure? This cannot be undone.')) return;
    await fetch(API + '/api/bot/emergency', { method: 'POST' });
    updateStatus();
}

// ==================== Scheduled Trades ====================

let quoteTimeout;
// ==================== Symbol Autocomplete ====================
/**
 * One symbol/company typeahead over /api/stocks/search, bound to one input and
 * one dropdown.
 *
 * There are TWO of these on the page — Schedule a Trade, and the Watchlist "Add
 * symbol" field — and they must not be able to see each other's state. That is
 * why the debounce timer, the highlighted-row index and the request sequence all
 * live in this closure, and why every DOM query below is scoped to `dropdown`
 * rather than to `document`. The single-field version got all three wrong the
 * moment a second field existed: highlightItem() selected `.autocomplete-item`
 * document-wide, activeDropdownIndex was one shared integer, and `.show` only
 * toggles `display`, so a hidden dropdown's rows stayed in the DOM and kept
 * matching that document-wide selector.
 *
 * Rows use DELEGATED listeners rather than the inline
 * onclick="selectStock('${sym}','${name}')" attributes this replaces. Inline
 * attributes cannot name an instance without a global registry, and they pushed
 * the company name through a hand-rolled `'` escape that left `"`, `&` and `<`
 * unhandled. Here the data goes into data-* attributes escaped once by
 * escapeHtml, and handlers bind once to the dropdown element — which is never
 * replaced, only its innerHTML.
 *
 * @param {object}   o
 * @param {string}   o.inputId     id of the <input>
 * @param {string}   o.dropdownId  id of the .autocomplete-dropdown
 * @param {function} o.onSelect    (symbol, name, input) => void — REQUIRED
 * @param {function} [o.onInput]   (rawValue, input) => void, per keystroke
 * @param {function} [o.onEnter]   (typedValue, input) => void, Enter with no row chosen
 * @param {function} [o.rowState]  (symbol) => {disabled?: bool, badge?: string}
 */
const AUTOCOMPLETES = [];
const AC_DEBOUNCE_MS = 200;

function createSymbolAutocomplete(o) {
    const input = document.getElementById(o.inputId);
    const dropdown = document.getElementById(o.dropdownId);
    if (!input || !dropdown) return null;

    const wrapper = input.closest('.autocomplete-wrapper');
    const minChars = o.minChars || 1;

    let searchTimeout = null;   // per-instance debounce  (was a module global)
    let activeIndex = -1;       // per-instance highlight (was a module global)
    let reqSeq = 0;             // discards responses that land out of order

    /** Selectable rows only. Notices carry no data-index, which is what keeps
     *  them off the keyboard path. */
    function items() {
        return dropdown.querySelectorAll('.autocomplete-item[data-index]');
    }

    function isOpen() { return dropdown.classList.contains('show'); }

    function open() {
        // Only one dropdown on screen: the other's rows would otherwise sit
        // there under a stale query, still catching clicks.
        AUTOCOMPLETES.forEach(ac => { if (ac !== self) ac.hide(); });
        dropdown.classList.add('show');
    }

    function hide() {
        dropdown.classList.remove('show');
        dropdown.innerHTML = '';   // emptied, not merely hidden — see header comment
        activeIndex = -1;
    }

    function highlight(index) {
        items().forEach((row, i) => row.classList.toggle('active', i === index));
    }

    /** Next selectable row in `dir`, or -1. Rows already in the watchlist are
     *  shown but inert, and the keyboard skips them rather than parking on a row
     *  Enter would refuse to act on. */
    function nextSelectable(from, dir, list) {
        let i = (from < 0 && dir < 0) ? list.length - 1 : from + dir;
        for (; i >= 0 && i < list.length; i += dir) {
            if (list[i].dataset.disabled !== '1') return i;
        }
        return -1;
    }

    /** Escape FIRST, then wrap the matched run — the other order would let
     *  escapeHtml eat the <b> we just inserted. Slices of the ORIGINAL string are
     *  used, not the uppercased query, so company-name casing survives. */
    function mark(text, query) {
        const at = query ? text.toUpperCase().indexOf(query) : -1;
        if (at < 0) return escapeHtml(text);
        return escapeHtml(text.slice(0, at))
             + '<b class="ac-match">' + escapeHtml(text.slice(at, at + query.length)) + '</b>'
             + escapeHtml(text.slice(at + query.length));
    }

    /** A non-selectable one-line message. No data-index => unreachable by arrows. */
    function notice(text) {
        dropdown.innerHTML =
            '<div class="autocomplete-item is-notice">' + escapeHtml(text) + '</div>';
        activeIndex = -1;
        open();
    }

    function render(results, query) {
        activeIndex = -1;
        dropdown.innerHTML = results.map((stock, i) => {
            const sym  = String(stock.symbol || '');
            const name = String(stock.name || sym);
            const exch = String(stock.exchange || '');
            const st = (o.rowState ? o.rowState(sym) : null) || {};
            return `<div class="autocomplete-item${st.disabled ? ' is-disabled' : ''}"
                         data-index="${i}"
                         data-symbol="${escapeHtml(sym)}"
                         data-name="${escapeHtml(name)}"
                         data-disabled="${st.disabled ? '1' : '0'}"
                         ${st.disabled ? 'aria-disabled="true"' : ''}>
                <span class="stock-row">
                    <span class="stock-symbol">${mark(sym, query)}</span>
                    <span class="stock-name">${mark(name, query)}</span>
                    ${exch ? `<span class="stock-exchange">${escapeHtml(exch)}</span>` : ''}
                    ${st.badge ? `<span class="stock-flag">${escapeHtml(st.badge)}</span>` : ''}
                </span>
                <span class="stock-desc"></span>
            </div>`;
        }).join('');
        open();
        if (o.describe) fillDescriptions(dropdown);
    }

    /**
     * Fill the second line of each row with a one-line company description.
     *
     * Deliberately AFTER the rows are on screen and never awaited by render():
     * these come from yfinance and cost ~0.3s per uncached symbol, so blocking
     * the dropdown on them would make typing feel broken. Rows that have no
     * description — many ETFs — simply keep an empty line, which collapses to
     * nothing, so there is no placeholder to flash and remove.
     */
    async function fillDescriptions(dd) {
        const rows = [...dd.querySelectorAll('.autocomplete-item[data-index]')];
        if (!rows.length) return;
        const symbols = rows.map(r => r.dataset.symbol);
        const paint = (map) => rows.forEach(r => {
            const d = map[r.dataset.symbol];
            const el = r.querySelector('.stock-desc');
            if (d && el && !el.textContent) el.textContent = d;
        });
        const url = API + '/api/stocks/profiles?symbols=' + encodeURIComponent(symbols.join(','));
        try {
            // Cached-only first: instant, and covers everything after the first
            // time a symbol is seen. Then the uncapped call fills the rest in.
            const fast = await fetch(url + '&cached=1');
            if (fast.ok && dd.isConnected) paint(await fast.json());
            const full = await fetch(url);
            if (full.ok && dd.isConnected) paint(await full.json());
        } catch (err) {
            // Cosmetic — a missing description is not worth surfacing.
            console.debug('Profile lookup failed:', err);
        }
    }

    /** Read the row, THEN hide (hide() detaches it), then hand off. */
    function commit(row) {
        if (!row || row.dataset.disabled === '1') return;   // already held — no-op
        const symbol = row.dataset.symbol;
        const name = row.dataset.name || symbol;
        hide();
        o.onSelect(symbol, name, input);
    }

    dropdown.addEventListener('click', function (e) {
        const row = e.target.closest('.autocomplete-item[data-index]');
        if (row) commit(row);
    });

    // mouseover, NOT mouseenter: mouseenter does not bubble, so it cannot be
    // delegated. Replaces onmouseenter="activeDropdownIndex=i; highlightItem(i)".
    dropdown.addEventListener('mouseover', function (e) {
        const row = e.target.closest('.autocomplete-item[data-index]');
        if (!row || row.dataset.disabled === '1') return;
        activeIndex = Number(row.dataset.index);
        highlight(activeIndex);
    });

    input.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        const query = input.value.trim().toUpperCase();

        if (query.length < minChars) { hide(); return; }

        const mine = ++reqSeq;
        searchTimeout = setTimeout(async () => {
            let results;
            try {
                const res = await fetch(API + '/api/stocks/search?q=' + encodeURIComponent(query));
                if (!res.ok) {
                    // /api/stocks/search answers 503 + {"error":...} when no bot is
                    // initialised. The old code read `.length` off that OBJECT, got
                    // undefined, and showed "No matches found" — reporting a broken
                    // backend as an empty result set.
                    if (mine === reqSeq) notice('Symbol search unavailable');
                    return;
                }
                results = await res.json();
            } catch (err) {
                if (mine === reqSeq) notice('Symbol search unavailable');
                return;
            }
            if (mine !== reqSeq) return;          // a later keystroke already won
            if (!Array.isArray(results)) {
                notice(results && results.error ? results.error : 'Symbol search unavailable');
                return;
            }
            if (results.length === 0) { notice('No matches found'); return; }
            render(results, query);
        }, AC_DEBOUNCE_MS);

        // Called HERE, not at the top, to reproduce the old ordering exactly: an
        // emptied field returns above and so never runs this.
        if (o.onInput) o.onInput(input.value, input);
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            if (isOpen()) { e.preventDefault(); hide(); }
            return;
        }

        const rows = items();
        const navigable = isOpen() && rows.length > 0;

        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (!navigable) return;
            e.preventDefault();
            const next = nextSelectable(activeIndex, e.key === 'ArrowDown' ? 1 : -1, rows);
            if (next >= 0) {
                activeIndex = next;
                highlight(activeIndex);
                rows[activeIndex].scrollIntoView({ block: 'nearest' });
            }
            return;
        }

        if (e.key !== 'Enter') return;

        // Enter as ONE explicit decision. It used to be three implicit ones: a
        // separate keypress listener did the add, and only avoided double-firing
        // because this handler happened to call preventDefault().
        if (navigable && activeIndex >= 0 && rows[activeIndex]) {
            e.preventDefault();
            commit(rows[activeIndex]);
            return;
        }
        if (navigable) hide();            // open, but nothing chosen
        if (o.onEnter) {
            e.preventDefault();
            o.onEnter(input.value.trim().toUpperCase(), input);
        }
    });

    /** Re-evaluate row state on an ALREADY-OPEN dropdown: the watchlist can move
     *  underneath it (5s poll, an "add all" chip, another browser tab). */
    function remark() {
        if (!isOpen() || !o.rowState) return;
        items().forEach(row => {
            const st = o.rowState(row.dataset.symbol) || {};
            row.dataset.disabled = st.disabled ? '1' : '0';
            row.classList.toggle('is-disabled', !!st.disabled);
            let flag = row.querySelector('.stock-flag');
            if (st.badge) {
                if (!flag) {
                    flag = document.createElement('span');
                    flag.className = 'stock-flag';
                    row.appendChild(flag);
                }
                flag.textContent = st.badge;
            } else if (flag) {
                flag.remove();
            }
        });
        const rows = items();
        if (activeIndex >= 0 && rows[activeIndex] && rows[activeIndex].dataset.disabled === '1') {
            activeIndex = -1;
            highlight(-1);
        }
    }

    const self = { hide, isOpen, remark, wrapper, input };
    AUTOCOMPLETES.push(self);
    return self;
}

/**
 * Schedule a Trade — behaviour identical to before this refactor: every
 * keystroke also kicks the (separately debounced) quote fetch, and picking a row
 * FILLS the field and re-fetches. No onEnter: the field is in a <div>, not a
 * <form>, so there is no implicit submit to intercept.
 */
const schedAutocomplete = createSymbolAutocomplete({
    inputId: 'sched-symbol',
    dropdownId: 'symbol-dropdown',
    onInput: () => fetchQuote(),
    onSelect: (symbol, name, input) => { input.value = symbol; fetchQuote(); },
});

/**
 * Watchlist — picking a row ADDS it at once and empties the field, so a run of
 * symbols can be added without reaching for the mouse between them.
 * Enter, all three states, decided explicitly:
 *   open + row highlighted    -> that row is added; the typed fragment is not
 *   open + nothing highlighted-> dropdown closes, the TYPED text is added
 *   closed                    -> the typed text is added
 */
const watchlistAutocomplete = createSymbolAutocomplete({
    inputId: 'watchlist-input',
    dropdownId: 'watchlist-dropdown',
    describe: true,     // second line: what the company actually does

    rowState: sym => currentWatchlist.includes(sym)
        ? { disabled: true, badge: 'in watchlist' }
        : {},
    onSelect: (symbol, name, input) => {
        input.value = '';                  // cleared and refocused BEFORE the await,
        input.focus();                     // so the field is ready for the next symbol
        addSymbolsToWatchlist(symbol);     // POSTs {add:[sym]} then refreshWatchlists()
    },
    onEnter: () => addToWatchlist(),       // reads #watchlist-input, as the Add button does
});

// One handler for every instance. The old one only ever closed #symbol-dropdown,
// so any second dropdown would have stayed open forever on an outside click.
// Clicking an ENABLED row hides+detaches it first, so closest() returns null here
// and everything closes (already closed — harmless). Clicking a DISABLED row
// returns before hide(), so its wrapper is still found and that dropdown
// correctly stays open doing nothing.
document.addEventListener('click', function (e) {
    const wrapper = e.target.closest('.autocomplete-wrapper');
    AUTOCOMPLETES.forEach(ac => { if (ac.wrapper !== wrapper) ac.hide(); });
});

// ==================== End Autocomplete ====================

function toggleLimitPrice() {
    const type = document.getElementById('sched-type').value;
    document.getElementById('limit-price-group').style.display = type === 'limit' ? '' : 'none';
}

function fetchQuote() {
    clearTimeout(quoteTimeout);
    const symbol = document.getElementById('sched-symbol').value.trim().toUpperCase();
    if (symbol.length < 1) {
        document.getElementById('quote-display').style.display = 'none';
        return;
    }

    quoteTimeout = setTimeout(async () => {
        try {
            const res = await fetch(API + '/api/scheduled/quote/' + symbol);
            const data = await res.json();
            if (data.bid && data.ask) {
                const mid = ((data.bid + data.ask) / 2);
                document.getElementById('sched-quote-price').textContent = fmt(mid);
                document.getElementById('quote-display').style.display = '';
            }
        } catch (err) {
            document.getElementById('quote-display').style.display = 'none';
        }
    }, 500);
}

async function submitScheduledTrade() {
    const symbol = document.getElementById('sched-symbol').value.trim().toUpperCase();
    const side = document.getElementById('sched-side').value;
    const qty = document.getElementById('sched-qty').value;
    const orderType = document.getElementById('sched-type').value;
    const dateVal = document.getElementById('sched-date').value;
    const timeVal = document.getElementById('sched-time').value;
    const limitPrice = document.getElementById('sched-limit').value;
    const stopLoss = document.getElementById('sched-sl').value;
    const takeProfit = document.getElementById('sched-tp').value;
    const notes = document.getElementById('sched-notes').value;

    // Validation
    if (!symbol) { alert('Please enter a stock symbol'); return; }
    if (!qty || qty <= 0) { alert('Please enter a valid quantity'); return; }
    if (!dateVal) { alert('Please select a date'); return; }
    if (!timeVal) { alert('Please select a time'); return; }
    if (orderType === 'limit' && !limitPrice) { alert('Please enter a limit price'); return; }

    const scheduledTime = dateVal + 'T' + timeVal + ':00';

    // Confirm
    const timeStr = new Date(scheduledTime).toLocaleString();
    const msg = `Schedule ${side.toUpperCase()} ${qty} ${symbol} (${orderType}) at ${timeStr}?`;
    if (!confirm(msg)) return;

    try {
        const body = {
            symbol, side, qty, order_type: orderType,
            scheduled_time: scheduledTime,
        };
        if (limitPrice) body.limit_price = limitPrice;
        if (stopLoss) body.stop_loss_pct = stopLoss;
        if (takeProfit) body.take_profit_pct = takeProfit;
        if (notes) body.notes = notes;

        const res = await fetch(API + '/api/scheduled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const result = await res.json();
        if (res.ok) {
            // Clear form
            document.getElementById('sched-symbol').value = '';
            document.getElementById('sched-qty').value = '1';
            document.getElementById('sched-limit').value = '';
            document.getElementById('sched-sl').value = '';
            document.getElementById('sched-tp').value = '';
            document.getElementById('sched-notes').value = '';
            document.getElementById('quote-display').style.display = 'none';

            updateScheduledTrades();
        } else {
            alert('Error: ' + (result.error || 'Unknown error'));
        }
    } catch (err) {
        alert('Failed to schedule trade: ' + err.message);
    }
}

async function cancelScheduledTrade(tradeId) {
    if (!confirm(`Cancel scheduled trade ${tradeId}?`)) return;

    try {
        const res = await fetch(API + '/api/scheduled/' + tradeId, { method: 'DELETE' });
        if (res.ok) {
            updateScheduledTrades();
        } else {
            alert('Failed to cancel trade');
        }
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

async function updateScheduledTrades() {
    try {
        const res = await fetch(API + '/api/scheduled');
        const data = await res.json();

        // Pending trades
        const pendingBody = document.getElementById('scheduled-body');
        if (!data.pending || data.pending.length === 0) {
            pendingBody.innerHTML = '<tr><td colspan="9" class="empty">No scheduled trades</td></tr>';
        } else {
            pendingBody.innerHTML = data.pending.map(t => {
                const schedTime = new Date(t.scheduled_time).toLocaleString();
                const sltp = [
                    t.stop_loss_pct ? `SL: ${t.stop_loss_pct}%` : '',
                    t.take_profit_pct ? `TP: ${t.take_profit_pct}%` : '',
                ].filter(Boolean).join(' / ') || '--';

                return `<tr>
                    <td><strong>${t.id}</strong></td>
                    <td><strong>${t.symbol}</strong></td>
                    <td>${t.side.toUpperCase()}</td>
                    <td>${t.qty}</td>
                    <td>${t.order_type}</td>
                    <td>${schedTime}</td>
                    <td>${sltp}</td>
                    <td>${t.notes || '--'}</td>
                    <td><button class="btn-cancel" onclick="cancelScheduledTrade('${t.id}')">Cancel</button></td>
                </tr>`;
            }).join('');
        }

        // History
        const histBody = document.getElementById('scheduled-history-body');
        if (!data.history || data.history.length === 0) {
            histBody.innerHTML = '<tr><td colspan="7" class="empty">No history</td></tr>';
        } else {
            histBody.innerHTML = data.history.reverse().slice(0, 20).map(t => {
                const schedTime = new Date(t.scheduled_time).toLocaleString();
                const execTime = t.executed_at ? new Date(t.executed_at).toLocaleTimeString() : '--';
                return `<tr>
                    <td>${t.id}</td>
                    <td><strong>${t.symbol}</strong></td>
                    <td>${t.side.toUpperCase()}</td>
                    <td>${t.qty}</td>
                    <td>${schedTime}</td>
                    <td class="status-${t.status}">${t.status.toUpperCase()}</td>
                    <td>${execTime}</td>
                </tr>`;
            }).join('');
        }
    } catch (err) {
        console.error('Failed to fetch scheduled trades:', err);
    }
}

// Set default date to today
document.getElementById('sched-date').valueAsDate = new Date();

// ==================== End Scheduled Trades ====================

// (The watchlist input's Enter-key listener lived here. It has moved into the
//  autocomplete's keydown handler as an explicit onEnter branch. Two handlers on
//  one key — where the keypress one only avoided double-firing because the
//  keydown one happened to call preventDefault() — was not a contract worth
//  keeping. See createSymbolAutocomplete.)

// ==================== Toasts ====================
// One-at-a-time, auto-dismissing notice in the top-right. Renders into
// #toast-region, which sits outside .layout on purpose: refreshAll() rewrites
// the innerHTML of most containers every 5s and navigate() hides every
// [data-page] element, and the toast has to survive both.

const TOAST_MS = 5000;       // auto-dismiss delay; mirrored into the timer bar
const TOAST_EXIT_MS = 180;   // must match the toast-out animation duration
let toastTimer = null;       // pending auto-dismiss — there is only ever one
let toastExitTimer = null;   // pending removal after the exit animation

/**
 * Show a toast, replacing whatever is already on screen.
 * @param {string} html  inner markup; caller escapes any server-supplied strings
 * @param {{error?: boolean, ms?: number}} [opts]
 */
function showToast(html, opts) {
    opts = opts || {};
    const region = document.getElementById('toast-region');
    if (!region) return;

    // Rapid switching must replace, never stack: drop the previous toast and
    // BOTH its timers outright. A surviving timer would dismiss the new toast
    // early, and cross-fading two notices reads as a glitch.
    clearTimeout(toastTimer);
    clearTimeout(toastExitTimer);
    toastTimer = null;
    toastExitTimer = null;

    // Set politeness BEFORE injecting — assistive tech reads the attribute at
    // the moment the live region mutates.
    region.setAttribute('aria-live', opts.error ? 'assertive' : 'polite');

    const ms = opts.ms || TOAST_MS;
    region.innerHTML =
        `<div class="toast${opts.error ? ' error' : ''}" onclick="dismissToast()">
            ${html}
            <div class="toast-timer" aria-hidden="true"><i style="animation-duration:${ms}ms"></i></div>
        </div>`;

    toastTimer = setTimeout(dismissToast, ms);
}

/** Dismiss the current toast — click anywhere, the ×, or the auto-dismiss timer. */
function dismissToast() {
    clearTimeout(toastTimer);
    toastTimer = null;
    const region = document.getElementById('toast-region');
    const toast = region && region.firstElementChild;
    if (!toast || toast.classList.contains('toast-leaving')) return;   // already going
    toast.classList.add('toast-leaving');
    clearTimeout(toastExitTimer);
    toastExitTimer = setTimeout(() => {
        // Only clear if nothing newer has taken the slot in the meantime.
        if (region.firstElementChild === toast) region.innerHTML = '';
        toastExitTimer = null;
    }, TOAST_EXIT_MS);
}

/**
 * Confirmation toast after an account switch: identity only.
 * The figures deliberately live in the header (#account-stats) instead — they
 * are worth reading at any time, not just for the five seconds after a switch.
 * @param {object} s  the `status` object returned by /api/account/select
 */
function showAccountToast(s) {
    s = s || {};
    const sub = [
        s.strategy || 'base strategy',
        'ML ' + (s.ml_enabled ? 'on' : 'off'),
        s.running ? 'Running' : 'Stopped',
        s.paper_mode === false ? 'Live' : 'Paper',
    ].join(' · ');

    showToast(`
        <div class="toast-head">
            <span class="toast-title">Now viewing ${escapeHtml(s.name || s.account_id || 'account')}</span>
            <button type="button" class="toast-close" aria-label="Dismiss" onclick="dismissToast()">×</button>
        </div>
        <div class="toast-sub">${escapeHtml(sub)}</div>`);
}

/** Error variant. Replaces the blocking window.alert() on the failure paths. */
function showErrorToast(message) {
    showToast(`
        <div class="toast-head">
            <span class="toast-title">Account switch failed</span>
            <button type="button" class="toast-close" aria-label="Dismiss" onclick="dismissToast()">×</button>
        </div>
        <div class="toast-sub">${escapeHtml(message)}</div>`, { error: true, ms: 8000 });
}

// ==================== Accounts (multi-account) ====================

async function updateAccounts(force) {
    try {
        const res = await fetch(API + '/api/accounts');
        if (!res.ok) return;
        const d = await res.json();
        const sel = document.getElementById('account-switcher');
        if (!sel) return;
        const accounts = d.accounts || [];
        // Rebuilt every poll: trivially cheap, and it self-heals if an account
        // is added to settings.ini while this page is open. MUST stay above the
        // accountsSig early-return below, which guards DOM writes only — gating
        // the map on it would strand the colours whenever the options are
        // unchanged, which is almost always.
        ACCT_INDEX = {};
        accounts.forEach((a, i) => { ACCT_INDEX[a.id] = i; });
        if (d.current) ACCT_CURRENT = d.current;
        ACCOUNTS_CACHE = accounts;     // carries each account's watchlist
        renderOtherWatchlists();

        // Hide the switcher entirely if there's only one (single-account) account
        sel.style.display = accounts.length > 1 ? '' : 'none';
        const html = accounts.map(a => {
            const tag = a.configured ? (a.strategy ? ' · ' + a.strategy : '') : ' · not configured';
            // <option> can hold text only — no styled dot is possible in a native
            // <select> — so the run-state light is an emoji. White, not red, for
            // an unconfigured account: red would read as a fault rather than as
            // "no keys set". Shows on the closed switcher too, for free.
            const light = !a.configured ? '⚪' : (a.running ? '🟢' : '🔴');
            return `<option value="${a.id}" ${a.configured ? '' : 'disabled'} ${a.id === d.current ? 'selected' : ''}>${escapeHtml(light + ' ' + a.name + tag)}</option>`;
        }).join('');
        // This used to rewrite innerHTML on every 5s tick, which slammed the
        // native dropdown shut mid-choice. Only touch the DOM when the options
        // actually changed, and never while the user is interacting with the
        // control. `force` is for the error path, where the <select> has to be
        // resynced to the server's real selection immediately.
        if (!force && (html === accountsSig || document.activeElement === sel)) return;
        sel.innerHTML = html;
        accountsSig = html;
    } catch (err) {
        console.error('Failed to fetch accounts:', err);
    }
}

async function selectAccount(id) {
    try {
        const res = await fetch(API + '/api/account/select', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
        });
        // A Flask 500 returns an HTML error page, so guard the parse.
        const d = await res.json().catch(() => ({}));
        if (res.ok) {
            showAccountToast(d.status);
            refreshAll();   // reload all panels for the newly-selected account
        } else {
            // Was a blocking window.alert(), which froze the whole page.
            showErrorToast(d.error || ('Cannot select that account (HTTP ' + res.status + ')'));
            updateAccounts(true);   // snap the <select> back to the real selection
        }
    } catch (err) {
        // fetch() rejects when the server is unreachable. This branch used to
        // console.error() and tell the user nothing at all, leaving the <select>
        // showing an account that was never actually selected.
        console.error('Failed to select account:', err);
        showErrorToast('Could not reach the dashboard server — account not switched.');
        updateAccounts(true);
    }
}

async function updateCompare() {
    try {
        const res = await fetch(API + '/api/compare');
        if (!res.ok) return;
        const d = await res.json();
        const body = document.getElementById('compare-body');
        if (!body) return;
        if (!d.rows || !d.rows.length) {
            body.innerHTML = '<tr><td colspan="10" class="empty">No configured accounts yet — add keys to .env.</td></tr>';
            return;
        }
        body.innerHTML = d.rows.map(r => {
            // Rows are sorted by P&L (AccountManager.compare), so array position
            // is a RANK, not an identity ordinal — the colour has to be looked
            // up by id or the colours would reshuffle as accounts change places.
            const isCurrent = r.id === ACCT_CURRENT;
            const cls = [acctClass(r.id), isCurrent ? 'is-current' : ''].filter(Boolean).join(' ');
            return `
            <tr class="${cls}" data-account-id="${escapeHtml(r.id)}"${isCurrent ? ' aria-current="true"' : ''}>
                <td><strong>${r.rank}</strong></td>
                <td><span class="acct-dot" aria-hidden="true"></span><strong>${escapeHtml(r.name)}</strong>${isCurrent ? '<span class="acct-viewing-chip">Viewing</span>' : ''}</td>
                <td>${escapeHtml(r.strategy || '-')}</td>
                <td>${r.equity == null ? '--' : fmt(r.equity)}</td>
                <td class="${pnlClass(r.total_pnl)}">${fmt(r.total_pnl)}</td>
                <td>${pct(r.win_rate)}</td>
                <td>${r.total_trades}</td>
                <td class="${(r.drawdown_pct || 0) > 0 ? 'negative' : 'neutral'}">${Number(r.drawdown_pct || 0).toFixed(1)}%</td>
                <td>${r.open_positions}</td>
                <td>${r.running ? '<span class="badge badge-green">RUNNING</span>' : '<span class="badge badge-red">STOPPED</span>'}</td>
            </tr>`;
        }).join('');
    } catch (err) {
        console.error('Failed to fetch compare:', err);
    }
}

// ==================== Performance Summary ====================

async function updateSummary() {
    try {
        const res = await fetch(API + '/api/summary');
        if (!res.ok) return;
        const d = await res.json();
        const c = d.current || {}, h = d.history || {};

        // Current — open trades
        document.getElementById('sum-open-count').textContent = c.open_positions ?? '--';
        const unreal = document.getElementById('sum-unrealized');
        unreal.textContent = fmt(c.unrealized_pl);
        unreal.className = 'value ' + pnlClass(c.unrealized_pl);
        document.getElementById('sum-open-wl').textContent = (c.winners ?? 0) + ' / ' + (c.losers ?? 0);
        document.getElementById('sum-open-note').textContent =
            (c.data_available === false) ? 'Live position data unavailable (broker offline).' : '';

        // History — closed trades
        const realized = document.getElementById('sum-realized');
        realized.textContent = fmt(h.total_pnl);
        realized.className = 'value ' + pnlClass(h.total_pnl);
        document.getElementById('sum-winrate').textContent = pct(h.win_rate);
        document.getElementById('sum-trades').textContent = h.total_trades ?? '--';
        document.getElementById('sum-pf').textContent =
            (h.profit_factor === null || h.profit_factor === undefined) ? '--' : Number(h.profit_factor).toFixed(2);
        document.getElementById('sum-dd').textContent = fmt(-Math.abs(h.max_drawdown || 0));
        document.getElementById('sum-winrate-bar').style.width =
            Math.max(0, Math.min(100, h.win_rate || 0)) + '%';
    } catch (err) {
        console.error('Failed to fetch summary:', err);
    }
}

// ==================== Nav rail (pin open) ====================
// The rail peeks open on hover by default. Pinning makes it permanent AND
// non-overlapping: html.rail-pinned widens the rail to 288px and grows
// .layout's left padding to match, so content shifts across instead of being
// covered. ALL of that is CSS — the widths, the glyph, and the 1100px fold-back
// to icons — so nothing is duplicated here and there is no resize listener to
// keep in sync.
//
// The class itself is applied by the inline script in <head>, before first
// paint. Doing it from this file would be too late: dashboard.js is the last
// element in <body>, so the page would paint at the 62px offset and then snap
// 226px sideways on the next frame.
//
// (This replaces applyRailState/toggleRail/initRail, which were dead: never
//  called, keyed off a #rail-toggle element that never existed and a .collapsed
//  class no rule ever defined, and built for the inverse "collapse" model.)
const RAIL_PIN_KEY = 'sw_rail_pinned';

function railPinned() {
    return document.documentElement.classList.contains('rail-pinned');
}

/** aria-expanded and the label are the only things JS owns here. Both are
 *  invisible, so setting them at end-of-body rather than pre-paint costs
 *  nothing. */
function syncRailPinButton() {
    const btn = document.getElementById('rail-pin');
    if (!btn) return;
    const on = railPinned();
    const label = on ? 'Unpin the menu' : 'Pin the menu open';
    btn.setAttribute('aria-expanded', on ? 'true' : 'false');
    btn.setAttribute('aria-label', label);
    btn.title = label;
}

function toggleRailPin() {
    const on = !railPinned();
    document.documentElement.classList.toggle('rail-pinned', on);
    try { localStorage.setItem(RAIL_PIN_KEY, on ? '1' : '0'); } catch (e) {}
    syncRailPinButton();
}

function initRailPin() {
    syncRailPinButton();   // the class is already on <html> from the <head> script
}

// ==================== Nav rail (drag to reorder) ====================
// Drag a row's grip to rearrange the menu; click the row itself to navigate.
// The saved order is applied PRE-PAINT by an inline script right after the <ul>
// in index.html — dashboard.js runs last in <body>, so doing it here would paint
// the authored order first and visibly reshuffle.
//
// Pointer Events with setPointerCapture, not HTML5 drag-and-drop: the rail
// expands purely on :hover, and native DnD suppresses hover updates
// inconsistently across engines. Capture also keeps the gesture alive when the
// pointer leaves the row, which it always does.
const RAIL_ORDER_KEY = 'sw_rail_order';
const RAIL_DRAG_THRESHOLD = 4;   // px of movement before a press becomes a drag

function railList() { return document.querySelector('.rail-list'); }

function railItems() {
    const l = railList();
    return l ? [...l.querySelectorAll('.rail-item')] : [];
}

function railCurrentOrder() { return railItems().map(li => li.dataset.nav); }

/** The AUTHORED order, stashed by the pre-paint script before it rearranged
 *  anything — by the time this file runs the DOM may already be custom, so that
 *  is the only moment "default" is still readable. */
function railDefaultOrder() {
    const l = railList();
    if (!l) return [];
    try { return JSON.parse(l.dataset.defaultOrder || '[]'); } catch (e) { return []; }
}

function saveRailOrder() {
    const order = railCurrentOrder();
    // Storing nothing when the order is the default keeps a stale key from
    // pinning the menu to an order the template has since changed.
    try {
        if (order.join() === railDefaultOrder().join()) localStorage.removeItem(RAIL_ORDER_KEY);
        else localStorage.setItem(RAIL_ORDER_KEY, JSON.stringify(order));
    } catch (e) {}
    syncRailResetButton();
}

/** Reset is only meaningful once the order differs. visibility, never
 *  display — the head is the rail's most height-sensitive box, and every icon
 *  below it would shift if this element came and went. */
function syncRailResetButton() {
    const btn = document.getElementById('rail-reset');
    if (!btn) return;
    btn.style.visibility =
        railCurrentOrder().join() !== railDefaultOrder().join() ? 'visible' : 'hidden';
}

function resetRailOrder() {
    const list = railList();
    if (!list) return;
    const byNav = {};
    railItems().forEach(li => { byNav[li.dataset.nav] = li; });
    railDefaultOrder().forEach(nav => { if (byNav[nav]) list.appendChild(byNav[nav]); });
    try { localStorage.removeItem(RAIL_ORDER_KEY); } catch (e) {}
    syncRailResetButton();
    updateComponents();   // Overview's Sections cards follow the same order
}

/** Move a row n places and persist. Shared by the drag and the keyboard path. */
function moveRailItem(li, delta) {
    const items = railItems();
    const from = items.indexOf(li);
    const to = Math.max(0, Math.min(items.length - 1, from + delta));
    if (from === to) return;
    const list = railList();
    if (to > from) list.insertBefore(li, items[to].nextSibling);
    else list.insertBefore(li, items[to]);
    saveRailOrder();
    updateComponents();
}

function initRailDrag() {
    const list = railList();
    if (!list) return;
    syncRailResetButton();

    let drag = null;
    let suppressNextRailClick = false;
    let suppressClickTimer = null;

    // Permanent, capture phase. Each row's navigate() is an inline onclick on the
    // <li> — a BUBBLE listener on the element itself — so a bubble listener here
    // on the parent would run too late to stop it. Explicit and local: this
    // codebase has been burned before by one handler's preventDefault()
    // implicitly suppressing another's behaviour.
    list.addEventListener('click', (ev) => {
        if (!suppressNextRailClick) return;
        suppressNextRailClick = false;
        clearTimeout(suppressClickTimer);
        ev.stopPropagation();
        ev.preventDefault();
    }, true);

    list.addEventListener('pointerdown', (e) => {
        const grip = e.target.closest('.rail-grip');
        if (!grip || e.button !== 0) return;
        const li = grip.closest('.rail-item');
        if (!li) return;
        e.preventDefault();                      // no text selection, no native drag
        drag = { li, grip, startY: e.clientY, armed: false, pointerId: e.pointerId };
        // Listen on the DOCUMENT for the rest of the gesture, not on the list.
        // Reordering calls list.insertBefore() on the very row that owns the grip,
        // and that remove-then-insert implicitly RELEASES pointer capture — after
        // which pointerup goes to whatever is under the cursor. Release out over
        // the page and the event never reaches the <ul>, so the drag would never
        // end: the row keeps following the mouse and every later move keeps
        // reordering. Document listeners are immune to both the DOM move and the
        // capture release, which is why setPointerCapture is not used at all.
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', endDrag);
        document.addEventListener('pointercancel', endDrag);
    });

    function onMove(e) {
        if (!drag || e.pointerId !== drag.pointerId) return;

        if (!drag.armed) {
            if (Math.abs(e.clientY - drag.startY) < RAIL_DRAG_THRESHOLD) return;
            drag.armed = true;
            // Holds the panel open for the whole gesture — see .rail-dragging in
            // style.css. Without it, moving past x=288 drops :hover and the row
            // being dragged vanishes under the cursor.
            document.documentElement.classList.add('rail-dragging');
            drag.li.classList.add('is-dragging');
        }

        // Midpoint test against LIVE rects — deliberately not cached: the rail can
        // scroll mid-drag and --header-h can change underneath it.
        const others = railItems().filter(x => x !== drag.li);
        let before = null;
        for (const other of others) {
            const r = other.getBoundingClientRect();
            if (e.clientY < r.top + r.height / 2) { before = other; break; }
        }
        if (before) list.insertBefore(drag.li, before);
        else list.appendChild(drag.li);

        // Auto-scroll near either end. The rail is ~740-860px tall and overflows
        // most laptop viewports, so this is needed, not decorative.
        const rail = document.getElementById('component-rail');
        if (rail && rail.scrollHeight > rail.clientHeight) {
            const rr = rail.getBoundingClientRect();
            const EDGE = 40;
            if (e.clientY < rr.top + EDGE) rail.scrollTop -= 12;
            else if (e.clientY > rr.bottom - EDGE) rail.scrollTop += 12;
        }
    }

    function endDrag(e) {
        if (!drag || (e && e.pointerId !== drag.pointerId)) return;
        const armed = drag.armed;
        drag.li.classList.remove('is-dragging');
        document.documentElement.classList.remove('rail-dragging');
        drag = null;
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', endDrag);
        document.removeEventListener('pointercancel', endDrag);

        if (!armed) return;   // below the threshold: leave the click completely alone

        // Swallow only the click THIS gesture synthesises. Arming a flag rather
        // than adding a `{once: true}` listener is deliberate: a drag released
        // outside the row produces no click at all, so a one-shot listener would
        // survive and eat the user's NEXT genuine click on the menu instead. The
        // timer guarantees the arming can never outlive the gesture.
        suppressNextRailClick = true;
        clearTimeout(suppressClickTimer);
        suppressClickTimer = setTimeout(() => { suppressNextRailClick = false; }, 350);

        saveRailOrder();
        updateComponents();
    }

    // Keyboard path — dragging is mouse-only. Alt+Arrow moves the focused row.
    list.addEventListener('keydown', (e) => {
        if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
        const grip = e.target.closest('.rail-grip');
        if (!grip) return;
        e.preventDefault();
        moveRailItem(grip.closest('.rail-item'), e.key === 'ArrowDown' ? 1 : -1);
        grip.focus();   // keep focus on the moved row so repeats work
    });

    // Grips are tabindex="-1" in the markup so they don't add ten stops to the tab
    // order; they become reachable once their row has focus.
    railItems().forEach(li => {
        li.addEventListener('focusin', () => {
            const g = li.querySelector('.rail-grip');
            if (g) g.tabIndex = 0;
        });
        li.addEventListener('focusout', () => {
            const g = li.querySelector('.rail-grip');
            if (g && !li.contains(document.activeElement)) g.tabIndex = -1;
        });
    });
}

/**
 * Publish the header's real height as --header-h.
 * The nav rail is position:fixed so it can hug the viewport's left edge at any
 * window width, which means it needs a top offset that clears the header. The
 * header wraps as the window narrows (~64px wide, ~185px at 600px), so a
 * hardcoded value would either overlap it or leave a gap. A ResizeObserver
 * catches BOTH window resizes and content-driven reflows — badge text changing
 * can re-wrap the header without the window moving at all.
 */
function trackHeaderHeight() {
    const header = document.querySelector('header');
    if (!header) return;
    const apply = () => document.documentElement.style.setProperty(
        '--header-h', header.offsetHeight + 'px');
    apply();
    if (window.ResizeObserver) new ResizeObserver(apply).observe(header);
    else window.addEventListener('resize', apply);
}

// ==================== Theme picker ====================
// Palettes live entirely in style.css under [data-theme="<id>"]. This list only
// supplies ids, labels and ordering — to add a theme, add a CSS block there and
// one row here. The preview swatches read their colours from the CSS via a
// data-theme attribute, so no colour values are duplicated in JS.
const THEMES = [
    { id: 'daylight', label: 'Daylight',  hint: 'Soft white — the default' },
    { id: 'paper',    label: 'Paper',     hint: 'Warm sepia, low glare' },
    { id: 'mist',     label: 'Mist',      hint: 'Cool blue-grey' },
    { id: 'midnight', label: 'Midnight',  hint: 'The original dark theme' },
    { id: 'slate',    label: 'Slate',     hint: 'Softer dark blue-grey' },
    { id: 'nord',     label: 'Nord',      hint: 'Arctic dark' },
];
const DEFAULT_THEME = 'daylight';

function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || DEFAULT_THEME;
}

function applyTheme(id) {
    if (!THEMES.some(t => t.id === id)) id = DEFAULT_THEME;
    document.documentElement.setAttribute('data-theme', id);
    try { localStorage.setItem('sw_theme', id); } catch (e) {}
    const label = document.getElementById('theme-btn-label');
    const theme = THEMES.find(t => t.id === id);
    if (label && theme) label.textContent = theme.label;
    // Repaint ticks/state rather than rebuilding, so focus stays put.
    document.querySelectorAll('#theme-menu .theme-opt').forEach(el => {
        const on = el.dataset.themeId === id;
        el.setAttribute('aria-checked', on ? 'true' : 'false');
        el.querySelector('.theme-opt-tick').textContent = on ? '✓' : '';
    });
}

function buildThemeMenu() {
    const menu = document.getElementById('theme-menu');
    if (!menu) return;
    menu.innerHTML = THEMES.map(t => `
        <li role="none">
          <button type="button" role="menuitemradio" aria-checked="false"
                  class="theme-opt" data-theme-id="${t.id}" title="${t.hint}">
            <span class="theme-swatch" data-theme="${t.id}" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
            <span class="theme-opt-name">${t.label}</span>
            <span class="theme-opt-tick"></span>
          </button>
        </li>`).join('');
    menu.querySelectorAll('.theme-opt').forEach(el => {
        el.addEventListener('click', () => { applyTheme(el.dataset.themeId); closeThemeMenu(); });
    });
}

function openThemeMenu() {
    document.getElementById('theme-menu')?.classList.add('show');
    document.getElementById('theme-btn')?.setAttribute('aria-expanded', 'true');
}

function closeThemeMenu() {
    document.getElementById('theme-menu')?.classList.remove('show');
    document.getElementById('theme-btn')?.setAttribute('aria-expanded', 'false');
}

function toggleThemeMenu(evt) {
    if (evt) evt.stopPropagation();   // don't trip the outside-click handler below
    const menu = document.getElementById('theme-menu');
    if (!menu) return;
    menu.classList.contains('show') ? closeThemeMenu() : openThemeMenu();
}

function initTheme() {
    buildThemeMenu();
    // The inline <head> script already applied the saved theme pre-paint; this
    // re-applies it to sync the button label and tick marks.
    let saved = DEFAULT_THEME;
    try { saved = localStorage.getItem('sw_theme') || DEFAULT_THEME; } catch (e) {}
    applyTheme(saved);
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.theme-picker')) closeThemeMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeThemeMenu();
    });
}

// ==================== Component data (Overview cards + detail pages) ====================

async function updateComponents() {
    try {
        const res = await fetch(API + '/api/components');
        if (!res.ok) return;
        const d = await res.json();

        const cards = [
            { page: 'signals', icon: '📊', name: 'Signals',
              stat: `${d.signals.indicators.length} indicators · min ${d.signals.min_strength}% · ${d.signals.min_confirmations} confirmations` },
            { page: 'ml', icon: '🤖', name: 'ML Filter',
              stat: `${d.ml.trained ? 'Trained' : 'Untrained'} · ${d.ml.samples} samples` },
            { page: 'risk', icon: '🛡️', name: 'Risk Engine',
              stat: `${Number(d.risk.risk_per_trade_pct).toFixed(2)}% risk/trade · ${Number(d.risk.max_combined_open_risk_pct).toFixed(2)}% combined · DD ${Number(d.risk.drawdown_pct || 0).toFixed(1)}%` },
            { page: 'scanner', icon: '🔎', name: 'Scanner',
              stat: `${d.scanner.watchlist_count} symbols watched` },
            { page: 'scheduler', icon: '⏰', name: 'Scheduler',
              stat: `${d.scheduler.pending} pending · ${d.scheduler.history} past` },
            { page: 'safety', icon: '🚨', name: 'Safety',
              stat: d.safety.kill_switch_tripped ? 'Kill switch: TRIPPED' : 'Kill switch: armed' },
            { page: 'eod', icon: '🌅', name: 'EOD Manager',
              stat: `Flatten ${d.eod.close_before_eod_minutes} min before close` },
            { page: 'notifications', icon: '🔔', name: 'Notifications',
              stat: `Discord ${d.notifications.discord ? 'on' : 'off'} · Email ${d.notifications.email ? 'on' : 'off'}` },
        ];
        // Follow the rail's order so the two lists can never visibly disagree
        // about the same eight sections. The rail also carries overview and
        // compare, which have no card here — indexOf returns -1 for anything the
        // rail doesn't list, so those would sort first; the fallback keeps
        // unknown entries at the end instead.
        const railOrder = railCurrentOrder();
        const rank = (page) => {
            const i = railOrder.indexOf(page);
            return i === -1 ? Number.MAX_SAFE_INTEGER : i;
        };
        cards.sort((a, b) => rank(a.page) - rank(b.page));

        const wrap = document.getElementById('section-cards');
        if (wrap) {
            wrap.innerHTML = cards.map(c => `
                <div class="section-card" onclick="navigate('${c.page}')">
                    <div class="section-card-top"><span class="section-card-icon">${c.icon}</span><span class="section-card-name">${escapeHtml(c.name)}</span></div>
                    <div class="section-card-stat">${escapeHtml(c.stat)}</div>
                    <div class="section-card-link">View details →</div>
                </div>`).join('');
        }

        setHtml('signals-detail', renderSignalsDetail(d.signals));
        setHtml('ml-detail', renderMlDetail(d.ml));
        setHtml('risk-framework', renderRiskFramework(d.risk));
        setHtml('eod-detail', renderEodDetail(d.eod));
        setHtml('notif-detail', renderNotifDetail(d.notifications));
    } catch (err) {
        console.error('Failed to fetch components:', err);
    }
}

function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

function renderSignalsDetail(s) {
    const chips = s.indicators.map(n => `<span class="chip">${escapeHtml(n)}</span>`).join('');
    const weights = s.weights || {};
    const wrows = Object.keys(weights).map(k =>
        `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(weights[k])}</td></tr>`).join('');
    return `
        <div class="metrics-grid" style="margin-bottom:16px;">
            <div class="metric"><span class="label">Min Signal Strength</span><span class="value">${s.min_strength}%</span></div>
            <div class="metric"><span class="label">Min Confirmations</span><span class="value">${s.min_confirmations}</span></div>
            <div class="metric"><span class="label">Indicators</span><span class="value">${s.indicators.length}</span></div>
        </div>
        <div class="chips">${chips}</div>
        ${wrows ? `<h3 style="margin-top:18px;">Signal Weights</h3><table><thead><tr><th>Indicator</th><th>Weight</th></tr></thead><tbody>${wrows}</tbody></table>` : ''}`;
}

function renderRiskFramework(r) {
    const dd = Number(r.drawdown_pct || 0);
    const review = Number(r.review_drawdown_pct);
    const shutdown = Number(r.shutdown_drawdown_pct);
    let state = 'Normal', cls = 'positive';
    if (r.shutdown_triggered || dd >= shutdown) { state = 'SHUTDOWN'; cls = 'negative'; }
    else if (r.review_flagged || dd >= review) { state = 'REVIEW'; cls = 'neutral'; }

    const groups = r.correlation_groups || {};
    const chips = Object.keys(groups).length
        ? Object.keys(groups).map(g =>
            `<span class="chip"><strong>${escapeHtml(g)}</strong>: ${groups[g].map(escapeHtml).join(', ')}</span>`).join('')
        : '<span class="safety-note">No correlated groups configured — each symbol is independent.</span>';

    return `
        <div class="metrics-grid" style="margin-bottom:16px;">
            <div class="metric"><span class="label">Current Drawdown</span><span class="value ${cls}">${dd.toFixed(1)}%</span></div>
            <div class="metric"><span class="label">Review At</span><span class="value">${review.toFixed(1)}%</span></div>
            <div class="metric"><span class="label">Shutdown At</span><span class="value">${shutdown.toFixed(1)}%</span></div>
            <div class="metric"><span class="label">Status</span><span class="value ${cls}">${state}</span></div>
            <div class="metric"><span class="label">Open Risk</span><span class="value">$${Number(r.open_risk_amount || 0).toFixed(2)}</span></div>
            <div class="metric"><span class="label">Weekly P&amp;L</span><span class="value ${pnlClass(r.weekly_pnl)}">${fmt(r.weekly_pnl)}</span></div>
        </div>
        <h3 style="margin-bottom:10px;">Correlation Groups <span class="safety-note">— one position per group</span></h3>
        <div class="chips">${chips}</div>`;
}

function renderMlDetail(m) {
    return `
        <div class="metrics-grid">
            <div class="metric"><span class="label">Model Status</span><span class="value ${m.trained ? 'positive' : 'neutral'}">${m.trained ? 'Trained' : 'Untrained'}</span></div>
            <div class="metric"><span class="label">Training Samples</span><span class="value">${m.samples}</span></div>
        </div>
        <p class="safety-note" style="margin-top:12px;">${m.trained
            ? 'The model is actively filtering signals before trades are placed.'
            : 'Approving all signals until enough completed trades accumulate to train.'}</p>`;
}

function renderEodDetail(e) {
    return `<div class="metrics-grid">
        <div class="metric"><span class="label">Flatten Before Close</span><span class="value">${e.close_before_eod_minutes} min</span></div>
    </div>`;
}

function renderNotifDetail(n) {
    const badge = (on) => `<span class="value ${on ? 'positive' : 'neutral'}">${on ? 'On' : 'Off'}</span>`;
    return `<div class="metrics-grid">
        <div class="metric"><span class="label">Discord Alerts</span>${badge(n.discord)}</div>
        <div class="metric"><span class="label">Email Alerts</span>${badge(n.email)}</div>
    </div>`;
}

// Initial load and auto-refresh
function refreshAll() {
    updateAccounts();
    updateCompare();
    updateStatus();
    updateSummary();
    updateComponents();
    updateSafety();
    updateLimits();
    updatePositions();
    updateOrders();
    updateHistory();
    updateScheduledTrades();
}

initTheme();
initRailPin();         // class is already on <html>; this only syncs aria/label
initRailDrag();        // grips, reorder persistence, keyboard Alt+Arrow
trackHeaderHeight();   // the fixed nav rail's top offset depends on this
navigate(currentPageFromHash());
// Resolve the account id -> ordinal map before the first coloured paint.
// refreshAll() fires its fetches without awaiting, so updateStatus() can
// otherwise land first and paint a neutral badge for a full 5s cycle.
// updateAccounts() swallows its own errors, so .finally() always runs.
updateAccounts().finally(refreshAll);
refreshInterval = setInterval(refreshAll, 5000);

// Market state on its own cadence, NOT inside refreshAll(): the market is global
// while refreshAll() is per-account work. Data every 30s, digits every second —
// the countdown interpolates against the exchange's absolute next_open/next_close,
// so the seconds stay smooth between fetches without re-asking the server.
updateMarket();
marketPollTimer = setInterval(updateMarket, MARKET_POLL_MS);
marketTimer = setInterval(renderMarket, 1000);
