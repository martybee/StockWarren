// StockWarren Dashboard JavaScript

const API = '';
let refreshInterval;

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
}

refreshAll();
refreshInterval = setInterval(refreshAll, 5000);
