// StockWarren Dashboard JavaScript

const API = '';
let refreshInterval;
let countdownInterval;

// Market countdown timer
function updateCountdown() {
    const now = new Date();

    // Market hours in ET: 9:30 AM - 4:00 PM
    // Create today's open/close in ET
    const etOffset = getETOffset();
    const utcNow = now.getTime() + now.getTimezoneOffset() * 60000;
    const etNow = new Date(utcNow + etOffset * 3600000);

    const etHour = etNow.getHours();
    const etMin = etNow.getMinutes();
    const etSec = etNow.getSeconds();
    const etDay = etNow.getDay(); // 0=Sun, 6=Sat

    const countdownEl = document.getElementById('market-countdown');
    if (!countdownEl) return;

    // Weekend check
    if (etDay === 0 || etDay === 6) {
        const daysUntilMon = etDay === 0 ? 1 : 2;
        const mondayOpen = new Date(etNow);
        mondayOpen.setDate(mondayOpen.getDate() + daysUntilMon);
        mondayOpen.setHours(9, 30, 0, 0);
        const diff = mondayOpen - etNow;
        countdownEl.textContent = formatCountdown(diff) + ' until Monday open';
        countdownEl.className = 'countdown closed';
        return;
    }

    const totalMinutes = etHour * 60 + etMin;
    const openMinutes = 9 * 60 + 30;   // 9:30
    const closeMinutes = 16 * 60;       // 16:00

    if (totalMinutes < openMinutes) {
        // Before market open
        const openTime = new Date(etNow);
        openTime.setHours(9, 30, 0, 0);
        const diff = openTime - etNow;
        countdownEl.textContent = formatCountdown(diff) + ' until market opens';
        countdownEl.className = 'countdown closed';
    } else if (totalMinutes < closeMinutes) {
        // Market is open
        const closeTime = new Date(etNow);
        closeTime.setHours(16, 0, 0, 0);
        const diff = closeTime - etNow;
        countdownEl.textContent = formatCountdown(diff) + ' until market closes';
        countdownEl.className = 'countdown open';
    } else {
        // After market close
        const tomorrow = new Date(etNow);
        let addDays = 1;
        if (etDay === 5) addDays = 3; // Friday -> Monday
        tomorrow.setDate(tomorrow.getDate() + addDays);
        tomorrow.setHours(9, 30, 0, 0);
        const diff = tomorrow - etNow;
        countdownEl.textContent = formatCountdown(diff) + ' until market opens';
        countdownEl.className = 'countdown closed';
    }
}

function formatCountdown(ms) {
    if (ms <= 0) return '00:00:00';
    const totalSec = Math.floor(ms / 1000);
    const hours = Math.floor(totalSec / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    const seconds = totalSec % 60;
    return String(hours).padStart(2, '0') + ':' +
           String(minutes).padStart(2, '0') + ':' +
           String(seconds).padStart(2, '0');
}

function getETOffset() {
    // Approximate ET offset: -5 (EST) or -4 (EDT)
    // EDT: 2nd Sunday March - 1st Sunday November
    const now = new Date();
    const year = now.getFullYear();
    const marchSecondSun = new Date(year, 2, 8 + (7 - new Date(year, 2, 8).getDay()) % 7);
    const novFirstSun = new Date(year, 10, 1 + (7 - new Date(year, 10, 1).getDay()) % 7);
    return (now >= marchSecondSun && now < novFirstSun) ? -4 : -5;
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
        botBadge.className = 'badge ' + (data.running ? 'badge-green' : 'badge-red');

        // Mode badge
        const modeBadge = document.getElementById('mode-badge');
        modeBadge.textContent = data.paper_mode ? 'PAPER' : 'LIVE';
        modeBadge.className = 'badge ' + (data.paper_mode ? 'badge-yellow' : 'badge-red');

        // Market status
        const marketBadge = document.getElementById('market-status');
        marketBadge.textContent = data.market_open ? 'MARKET OPEN' : 'MARKET CLOSED';
        marketBadge.className = 'badge ' + (data.market_open ? 'badge-green' : 'badge-red');

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
            updateWatchlistTags(data.watchlist);
        }

        document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString();

    } catch (err) {
        console.error('Failed to fetch status:', err);
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
    updateStatus();
}

async function removeFromWatchlist(symbol) {
    await fetch(API + '/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remove: symbol })
    });
    updateStatus();
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
    document.getElementById('scan-section').style.display = 'block';
    const tbody = document.getElementById('scan-body');
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Scanning...</td></tr>';

    try {
        const res = await fetch(API + '/api/scan', { method: 'POST' });
        const results = await res.json();

        if (!results || results.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">No opportunities found</td></tr>';
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
                <td>${r.volume_ratio.toFixed(1)}x</td>
                <td>${r.reasons.join(', ')}</td>
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
let searchTimeout;
let activeDropdownIndex = -1;

// ==================== Symbol Autocomplete ====================

function onSymbolInput(value) {
    clearTimeout(searchTimeout);
    const query = value.trim().toUpperCase();

    if (query.length < 1) {
        hideDropdown();
        return;
    }

    searchTimeout = setTimeout(async () => {
        try {
            const res = await fetch(API + '/api/stocks/search?q=' + encodeURIComponent(query));
            const results = await res.json();
            showDropdown(results, query);
        } catch (err) {
            hideDropdown();
        }
    }, 200);

    // Also fetch quote
    fetchQuote();
}

function showDropdown(results, query) {
    const dropdown = document.getElementById('symbol-dropdown');

    if (!results || results.length === 0) {
        dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-secondary);cursor:default;">No matches found</div>';
        dropdown.classList.add('show');
        return;
    }

    activeDropdownIndex = -1;

    dropdown.innerHTML = results.map((stock, i) => {
        // Highlight the matching part of the symbol
        const sym = stock.symbol;
        const name = stock.name || sym;
        const exchange = stock.exchange || '';

        let highlightedSym = sym;
        const matchIdx = sym.indexOf(query);
        if (matchIdx >= 0) {
            highlightedSym = sym.substring(0, matchIdx)
                + '<span style="color:var(--text-primary)">' + query + '</span>'
                + sym.substring(matchIdx + query.length);
        }

        return `<div class="autocomplete-item" data-index="${i}"
                     onclick="selectStock('${sym}', '${name.replace(/'/g, "\\'")}')"
                     onmouseenter="activeDropdownIndex=${i}; highlightItem(${i})">
            <span class="stock-symbol">${highlightedSym}</span>
            <span class="stock-name">${name}</span>
            <span class="stock-exchange">${exchange}</span>
        </div>`;
    }).join('');

    dropdown.classList.add('show');
}

function hideDropdown() {
    const dropdown = document.getElementById('symbol-dropdown');
    dropdown.classList.remove('show');
    activeDropdownIndex = -1;
}

function selectStock(symbol, name) {
    document.getElementById('sched-symbol').value = symbol;
    hideDropdown();
    fetchQuote();
}

function highlightItem(index) {
    const items = document.querySelectorAll('.autocomplete-item');
    items.forEach((item, i) => {
        item.classList.toggle('active', i === index);
    });
}

// Keyboard navigation for dropdown
document.getElementById('sched-symbol').addEventListener('keydown', function(e) {
    const dropdown = document.getElementById('symbol-dropdown');
    const items = dropdown.querySelectorAll('.autocomplete-item[data-index]');
    if (!dropdown.classList.contains('show') || items.length === 0) return;

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeDropdownIndex = Math.min(activeDropdownIndex + 1, items.length - 1);
        highlightItem(activeDropdownIndex);
        items[activeDropdownIndex]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeDropdownIndex = Math.max(activeDropdownIndex - 1, 0);
        highlightItem(activeDropdownIndex);
        items[activeDropdownIndex]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (activeDropdownIndex >= 0 && items[activeDropdownIndex]) {
            items[activeDropdownIndex].click();
        }
    } else if (e.key === 'Escape') {
        hideDropdown();
    }
});

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    if (!e.target.closest('.autocomplete-wrapper')) {
        hideDropdown();
    }
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

// Enter key for watchlist input
document.getElementById('watchlist-input').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') addToWatchlist();
});

// Initial load and auto-refresh
function refreshAll() {
    updateStatus();
    updatePositions();
    updateOrders();
    updateHistory();
    updateScheduledTrades();
}

refreshAll();
refreshInterval = setInterval(refreshAll, 5000);

// Start countdown timer (updates every second)
updateCountdown();
countdownInterval = setInterval(updateCountdown, 1000);
