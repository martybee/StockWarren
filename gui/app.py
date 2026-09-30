"""
Flask Web Dashboard for StockWarren
Real-time monitoring, trade history, and bot control
"""

import os
import re
import sys
import json
import logging
from datetime import datetime, date

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

logger = logging.getLogger(__name__)

# Multi-account state. `bot` always points at the CURRENTLY SELECTED account's
# bot, so every existing endpoint keeps working; switching accounts just
# repoints it. `manager` owns all accounts.
bot = None
manager = None
current_account_id = None


def set_bot(bot_instance):
    """Back-compat single-bot setter."""
    global bot
    bot = bot_instance


def set_account_manager(mgr):
    """Register the AccountManager and select the first configured account."""
    global manager, bot, current_account_id
    manager = mgr
    current_account_id = mgr.first_configured_id()
    bot = mgr.get_bot(current_account_id) if current_account_id else None


# ==================== Routes ====================

@app.route("/")
def index():
    return render_template("index.html")


# ==================== Accounts (multi-account) ====================

@app.route("/api/accounts")
def get_accounts():
    """List all accounts + which one is currently selected."""
    if manager is None:
        # Single-account fallback
        return jsonify({
            "accounts": [{"id": "default", "name": "Account", "configured": bot is not None,
                          "running": bool(bot and bot.running), "strategy": "", "error": None}],
            "current": "default" if bot is not None else None,
        })
    return jsonify({"accounts": manager.list_summary(), "current": current_account_id})


@app.route("/api/account/select", methods=["POST"])
def select_account():
    """Switch the dashboard to a different account.

    Returns the newly-selected account's status alongside `current` so the UI can
    confirm the switch in one round-trip. Two reasons it's done here rather than
    letting the client follow up with GET /api/status:

    * `strategy` is not in get_status() at all — it lives on Account, not on
      TradingBot (which consumes the strategy dict and discards the name), so a
      client-side build would need /api/status AND /api/accounts plus a join.
    * `bot` / `current_account_id` are process-wide globals shared by every
      client. Between this POST returning and a follow-up GET landing, another
      tab could re-point them and the client would describe the wrong account.
      Read here, under the same request that performed the switch, it's
      authoritative by construction.

    `current` is unchanged, so existing callers keep working.
    """
    global bot, current_account_id
    if manager is None:
        return jsonify({"error": "Multi-account not initialized"}), 400
    data = request.get_json(silent=True) or {}
    acc_id = data.get("id")
    target = manager.get_bot(acc_id)
    if target is None:
        return jsonify({"error": f"Account '{acc_id}' is not configured"}), 404
    current_account_id = acc_id
    bot = target

    account = manager.get(acc_id)
    # The switch has already happened by this point, so a broker hiccup while
    # reading status must degrade the payload — never turn a successful switch
    # into a 500 that makes the UI think it failed.
    try:
        status = target.get_status()
    except Exception as e:
        logger.error("Switched to '%s' but could not read its status: %s", acc_id, e)
        status = {"name": getattr(account, "name", acc_id), "account_id": acc_id,
                  "status_error": str(e)}
    status["strategy"] = getattr(account, "strategy_name", "") or ""
    return jsonify({"current": current_account_id, "status": status})


@app.route("/api/compare")
def get_compare():
    """Leaderboard comparing every configured account."""
    if manager is None:
        return jsonify({"rows": []})
    return jsonify({"rows": manager.compare()})


@app.route("/api/status")
def get_status():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    payload = bot.get_status()
    # Staleness guard (M6): the 88-day gotcha, made structural. The header
    # warns when the running commit no longer matches the code on disk.
    from src.utils.build_info import staleness
    payload["process"] = staleness()
    return jsonify(payload)


@app.route("/api/health")
def get_health():
    """API health check endpoint - useful for monitoring tools"""
    if bot is None:
        return jsonify({"healthy": False, "reason": "bot not initialized"}), 503

    health = bot.alpaca.health_check()
    status_code = 200 if health["healthy"] else 503
    return jsonify(health), status_code


@app.route("/api/safety")
def get_safety():
    """Trading Safety Constitution + live enforcement state (Rules 1-7)."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    from src.engine.safety import RULES, GATE_STATE
    rm = bot.risk_manager
    ks = bot.kill_switch

    # Equity for the gate. A broker hiccup must not 500 this route — the kill
    # switch and pause state are still worth reporting — so degrade to 0.0 and
    # flag it, which makes the drawdown rules inapplicable rather than wrong.
    broker_ok = True
    try:
        equity = float(bot.alpaca.get_account().get("portfolio_value", 0.0) or 0.0)
    except Exception as e:
        logger.warning("Safety: could not read equity for the gate: %s", e)
        equity, broker_ok = 0.0, False

    # PURE. Never call rm.is_trading_allowed() here: that mutates the peak-equity
    # baseline, rolls the daily stats, expires pauses and sets the flag the bot
    # reads to trip the kill switch. This route is polled every few seconds by
    # every open tab. See RiskManager.is_trading_allowed()'s docstring.
    gate = rm.evaluate_gate(equity)

    # How much the figures behind that verdict can be trusted. The gate reads
    # in-memory risk stats, which only move when the bot ticks.
    last_tick = getattr(bot, "last_tick_at", None)
    now = datetime.now()
    stale_seconds = int((now - last_tick).total_seconds()) if last_tick else None
    # Daily/weekly P&L only roll over when a tick calls _check_daily_reset(). On a
    # stopped bot they can be yesterday's numbers, so say so rather than present
    # them as today's.
    today = date.today()
    daily_stale = rm.stats.trading_day != today
    weekly_stale = rm.stats.trading_week != tuple(today.isocalendar()[:2])

    return jsonify({
        "rules": RULES,
        "kill_switch": {
            "tripped": ks.is_tripped(),
            "reason": ks.reason(),
            "reset_hint": ks.manual_reset_instructions(ks.path),
        },
        "gate": {
            "allowed": gate.allowed,
            "code": gate.code,
            "reason": gate.reason,
            "state": GATE_STATE.get(gate.code, "BLOCKED"),
        },
        "freshness": {
            "bot_running": bool(bot.running),
            "last_tick": last_tick.isoformat() if last_tick else None,
            "stale_seconds": stale_seconds,
            "broker_ok": broker_ok,
            "daily_stats_stale": daily_stale,
            "weekly_stats_stale": weekly_stale,
        },
        "halt_new_orders": getattr(bot, "_halt_new_orders", False),
        "is_paused": rm.is_paused,
        "limits_locked": True,
        "limits": {
            "max_leverage": rm.max_leverage,
            "max_positions": rm.max_positions,
            "max_position_pct": rm.max_position_pct,
            "risk_per_trade_pct": rm.risk_per_trade_pct,
            "max_daily_loss": rm.max_daily_loss,
            "max_daily_loss_pct": rm.max_daily_loss_pct,
            "min_cash_reserve_pct": rm.min_cash_reserve_pct,
            "min_risk_reward_ratio": rm.min_risk_reward_ratio,
            "max_consecutive_losses": rm.max_consecutive_losses,
        },
    })


@app.route("/api/overrides", methods=["GET"])
def get_overrides():
    """Current effective limits + the allowed override ranges (for the UI form)."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    from src.engine.risk_manager import RiskManager
    return jsonify({
        "current": bot.risk_manager.get_limits(),
        "spec": RiskManager.OVERRIDABLE,
    })


@app.route("/api/overrides", methods=["POST"])
def set_overrides():
    """Apply human-operator overrides (validated + clamped by the RiskManager),
    then persist them so they survive a restart."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    data = request.get_json(silent=True) or {}
    result = bot.risk_manager.apply_operator_override(data)

    # Persist the merged, already-clamped set of overrides (per account).
    if result.get("applied"):
        from src.utils.overrides import load_overrides, save_overrides
        path = getattr(bot, "overrides_path", "data/operator_overrides.json")
        merged = load_overrides(path)
        merged.update(result["applied"])
        save_overrides(merged, path)

    return jsonify(result)


@app.route("/api/overrides/reset", methods=["POST"])
def reset_overrides():
    """Revert limits to the config baseline and clear the persisted file."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    from src.utils.overrides import clear_overrides
    limits = bot.risk_manager.reset_operator_override()
    clear_overrides(getattr(bot, "overrides_path", "data/operator_overrides.json"))
    return jsonify({"limits": limits})


@app.route("/api/summary")
def get_summary():
    """Consolidated performance summary: current (open) + history (closed)."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    stats = bot.risk_manager.get_stats()

    # Current open positions (defensive — Alpaca may be unreachable)
    positions, open_error = [], None
    try:
        positions = bot.alpaca.get_positions()
    except Exception as e:
        open_error = str(e)

    unrealized = sum(p.get("unrealized_pl", 0.0) for p in positions)
    winners = sum(1 for p in positions if p.get("unrealized_pl", 0.0) > 0)
    losers = sum(1 for p in positions if p.get("unrealized_pl", 0.0) < 0)

    pf = bot.risk_manager.stats.profit_factor
    if pf in (float("inf"), float("-inf")):
        pf = None

    return jsonify({
        "current": {
            "open_positions": len(positions),
            "unrealized_pl": unrealized,
            "winners": winners,
            "losers": losers,
            "data_available": open_error is None,
            "positions": [
                {"symbol": p["symbol"], "unrealized_pl": p.get("unrealized_pl", 0.0),
                 "unrealized_plpc": p.get("unrealized_plpc", 0.0)}
                for p in positions
            ],
        },
        "history": {
            "total_pnl": stats["total_pnl"],
            "daily_pnl": stats["daily_pnl"],
            "total_trades": stats["total_trades"],
            "winning_trades": stats["winning_trades"],
            "losing_trades": stats["losing_trades"],
            "win_rate": stats["win_rate"],
            "max_drawdown": stats["max_drawdown"],
            "profit_factor": pf,
            "consecutive_losses": stats["consecutive_losses"],
        },
    })


@app.route("/api/components")
def get_components():
    """Per-component summary data — powers the Overview cards and detail pages."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    cfg = bot.config

    def _cfg(section, option, fallback):
        try:
            return cfg.get(section, option, fallback=fallback)
        except Exception:
            return fallback

    weights = {k.replace("weight_", ""): v
               for k, v in dict(cfg["signals"]).items() if k.startswith("weight_")} \
              if cfg.has_section("signals") else {}

    mlv = bot.ml_validator
    scheduler_pending = len(scheduler.get_pending_trades()) if scheduler else 0
    scheduler_history = len(scheduler.get_history()) if scheduler else 0
    symbols = bot.watchlist.get_symbols()

    # Risk framework: limits + correlation groups + live drawdown status
    rstats = bot.risk_manager.get_stats()
    corr_groups = {}
    for sym, grp in getattr(bot.risk_manager, "_corr_map", {}).items():
        corr_groups.setdefault(grp, []).append(sym)
    risk_data = bot.risk_manager.get_limits()
    risk_data["correlation_groups"] = corr_groups
    risk_data["drawdown_pct"] = rstats.get("drawdown_pct", 0.0)
    risk_data["review_flagged"] = rstats.get("review_flagged", False)
    risk_data["shutdown_triggered"] = rstats.get("shutdown_triggered", False)
    risk_data["weekly_pnl"] = rstats.get("weekly_pnl", 0.0)
    risk_data["open_risk_amount"] = rstats.get("open_risk_amount", 0.0)

    return jsonify({
        "signals": {
            "indicators": ["RSI", "MACD", "VWAP", "Bollinger", "EMA Cross", "Volume", "ATR"],
            "min_strength": bot.min_signal_strength,
            "min_confirmations": bot.min_confirmations,
            "weights": weights,
        },
        "ml": {
            "trained": bool(getattr(mlv, "is_trained", False)),
            "samples": len(getattr(mlv, "training_history", []) or []),
        },
        "risk": risk_data,
        "scanner": {
            "watchlist_count": len(symbols),
            "symbols": symbols,
            "scan_interval": int(_cfg("scanner", "scan_interval", "300")),
        },
        "scheduler": {"pending": scheduler_pending, "history": scheduler_history},
        "safety": {
            "kill_switch_tripped": bot.kill_switch.is_tripped(),
            "halt_new_orders": getattr(bot, "_halt_new_orders", False),
        },
        "eod": {"close_before_eod_minutes": int(_cfg("day_trading", "close_before_eod_minutes", "15"))},
        "notifications": {
            "discord": str(_cfg("notifications", "discord_enabled", "false")).lower() in ("true", "yes", "on"),
            "email": str(_cfg("notifications", "email_enabled", "false")).lower() in ("true", "yes", "on"),
        },
    })


@app.route("/api/account")
def get_account():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        return jsonify(bot.alpaca.get_account())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
def get_positions():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        return jsonify(bot.alpaca.get_positions())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders")
def get_orders():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        status = request.args.get("status", "open")
        return jsonify(bot.alpaca.get_orders(status=status))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
def get_stats():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    return jsonify(bot.risk_manager.get_stats())


@app.route("/api/trades")
def get_trades():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    return jsonify(bot.trade_log[-50:])


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    return jsonify({"symbols": bot.watchlist.get_symbols()})


@app.route("/api/watchlist", methods=["POST"])
def update_watchlist():
    """Mutate the SELECTED account's watchlist, then persist it.

    Persisting here (rather than only in memory) is what makes dashboard edits
    survive a restart; see src/utils/watchlist_store.py. `add` accepts a list as
    well as a single symbol so "add everything account X has that I don't" is one
    request instead of N.
    """
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    data = request.get_json(silent=True) or {}

    if "add" in data:
        payload = data["add"]
        for sym in (payload if isinstance(payload, list) else [payload]):
            if str(sym).strip():
                bot.watchlist.add(str(sym))
    elif "remove" in data:
        bot.watchlist.remove(str(data["remove"]))
    elif "symbols" in data:
        bot.watchlist.set_symbols([str(s) for s in data["symbols"]])
    else:
        return jsonify({"error": "Expected one of: add, remove, symbols"}), 400

    symbols = bot.watchlist.get_symbols()
    from src.utils.watchlist_store import save_watchlist
    save_watchlist(bot.account_id, symbols)
    return jsonify({"symbols": symbols})


@app.route("/api/market")
def get_market_hours():
    """Exchange clock — the dashboard's single source of market state.

    Deliberately NOT part of /api/status: the market is global, while /api/status
    is per-account. It is also why TradingBot.get_status() no longer carries a
    `market_open` key — that one was gated on `self.running`, so a stopped bot
    reported MARKET CLOSED during regular hours.

    Uses market_calendar.get_status(), which prefers Alpaca's authoritative clock
    (so holidays and half-days are right) and falls back to local ET session
    math if the broker is unreachable. `next_open`/`next_close` are absolute
    instants, so the browser can tick a countdown without re-fetching.
    """
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        from src.utils import market_calendar as mcal
        st = mcal.get_status(bot.alpaca)
        return jsonify({
            "is_open": st.is_open,
            "is_premarket": st.is_premarket,
            "is_afterhours": st.is_afterhours,
            "server_time": st.current_time_et.isoformat(),
            "next_open": st.next_open.isoformat() if st.next_open else None,
            "next_close": st.next_close.isoformat() if st.next_close else None,
            "minutes_until_open": st.minutes_until_open,
            "minutes_until_close": st.minutes_until_close,
        })
    except Exception as e:
        logger.error("Market status unavailable: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan", methods=["POST"])
def run_scan():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        results = bot.scanner.scan_watchlist(bot.watchlist.get_symbols())
        return jsonify([{
            "symbol": r.symbol,
            "score": r.score,
            "direction": r.signal_direction,
            "price": r.price,
            "change_pct": r.change_pct,
            "volume_ratio": r.volume_ratio,
            "volume_basis": r.volume_basis,
            "reasons": r.reasons,
        } for r in results])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== Stock Search ====================

# Cache the asset list so we don't hit the API every keystroke
_asset_cache = None
_asset_cache_time = None


def _get_asset_list():
    """Get and cache the list of tradeable assets"""
    global _asset_cache, _asset_cache_time
    import time as _time

    # Cache for 1 hour
    if _asset_cache and _asset_cache_time and (_time.time() - _asset_cache_time) < 3600:
        return _asset_cache

    try:
        assets = bot.alpaca.trading_client.get_all_assets()
        # name_norm is precomputed here, not per request: search runs over ~9000
        # assets on every keystroke, and normalising them each time would mean
        # 9000 regex substitutions per character typed.
        _asset_cache = [
            {"symbol": a.symbol, "name": a.name or a.symbol, "exchange": a.exchange,
             "name_norm": _norm(a.name or a.symbol)}
            for a in assets
            if a.tradable and a.status.value == "active"
            and a.asset_class.value == "us_equity"
            and "." not in a.symbol
            and not a.symbol.endswith("W")
        ]
        _asset_cache_time = _time.time()
        logger.info(f"Asset cache loaded: {len(_asset_cache)} stocks")
    except Exception as e:
        logger.error(f"Failed to load assets: {e}")
        if _asset_cache is None:
            _asset_cache = []

    return _asset_cache


@app.route("/api/stocks/profiles")
def get_stock_profiles():
    """One-line company descriptions for the autocomplete rows.

    Split out from /api/stocks/search on purpose: search must stay instant on
    every keystroke, while these come from yfinance and cost ~0.3s each the first
    time a symbol is seen. The client renders rows first and fills descriptions in
    when this answers, so a slow or failing lookup never delays the list.

    ?symbols=A,B,C   — comma separated
    ?cached=1        — return ONLY what is already on disk, never fetch
    """
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    if not symbols:
        return jsonify({})
    symbols = symbols[:25]   # bound the work regardless of what the client asks

    from src.utils import company_profiles as profiles
    if request.args.get("cached") == "1":
        return jsonify(profiles.get_cached(symbols))
    # Cosmetic data: any failure returns what we already have rather than a 5xx.
    try:
        return jsonify(profiles.fetch_missing(symbols))
    except Exception as e:
        logger.warning("Profile lookup failed: %s", e)
        return jsonify(profiles.get_cached(symbols))


# Names that wrap ANOTHER company's exposure rather than being that company:
# leveraged, inverse and income-overlay products. Typing a company name should
# surface the company first and its derivative ETFs after — searching "tesla"
# used to return three leveraged ETFs above TSLA itself.
_DERIVATIVE_RE = re.compile(
    r"\b(\d+X|ULTRA(SHORT|PRO)?|INVERSE|BULL|BEAR|LONG|SHORT|"
    r"LEVERAGED|DAILY TARGET|YIELD PREMIUM|ENHANCED INCOME|COVERED CALL)\b")


def _norm(s: str) -> str:
    """Uppercase, and collapse every run of punctuation to a single space.

    So "Coca-Cola Company" and a typed "coca cola" meet in the middle. Without
    this, searching "coca cola" returned NOTHING, because the space in the query
    could never match the hyphen in the name.
    """
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()


def _search_rank(sym: str, name: str, sym_query: str, name_query: str):
    """(tier, is_derivative, symbol_length, symbol) — lower sorts first.

    Tiers exist because a plain substring test ranked nonsense above the obvious
    answer: "apple" matched "Maui Land & PineAPPLE" and, sorting only by symbol
    length, put MLP above AAPL. "ford" matched Oxford, Ashford, Burford and
    Hartford the same way.

      0  symbol is exactly the query          AAPL for "AAPL"
      1  symbol starts with the query         AAPL for "AAP"
      2  NAME starts with the query           "Apple Inc." for "apple"      <- the fix
      3  name contains it as a WHOLE WORD     "...Strategy Apple (AAPL) ETF"
      4  name contains it anywhere            "PineAPPLE" — last resort, not dropped

    Tier 2 requires a word boundary AFTER the query too, so "spacex" does not
    treat "SpaceXAI Lab Ecosystem ETF" as the company itself.
    """
    if sym_query and sym == sym_query:
        tier = 0
    elif sym_query and sym.startswith(sym_query):
        tier = 1
    elif re.match(re.escape(name_query) + r"\b", name):
        tier = 2
    elif re.search(r"\b" + re.escape(name_query) + r"\b", name):
        tier = 3
    elif name_query in name:
        tier = 4
    else:
        return None
    # Within a tier, the company beats products that merely track it, then
    # shorter symbols (a plain listing usually has one).
    return (tier, 1 if _DERIVATIVE_RE.search(name) else 0, len(sym), sym)


@app.route("/api/stocks/search")
def search_stocks():
    """Search for stocks by symbol or company name, best match first."""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    raw = request.args.get("q", "").upper().strip()
    if len(raw) < 1:
        return jsonify([])
    # Tickers are alphanumeric, so a query containing a space or punctuation is a
    # COMPANY NAME and the symbol tiers must not apply to it. Stripping the
    # punctuation instead would invent matches: "at&t" became "ATT", which
    # prefix-matched ATTO (Attovia Therapeutics) and pushed T off the top.
    sym_query = raw if raw.isalnum() else None
    name_query = _norm(raw)

    scored = []
    for a in _get_asset_list():
        rank = _search_rank(a["symbol"].upper(), a.get("name_norm", ""),
                            sym_query, name_query)
        if rank is not None:
            scored.append((rank, a))

    scored.sort(key=lambda x: x[0])
    # Build the payload explicitly so the internal name_norm never leaks out.
    return jsonify([{"symbol": a["symbol"], "name": a["name"], "exchange": a["exchange"]}
                    for _, a in scored[:15]])


# ==================== Bot Control ====================

@app.route("/api/bot/start", methods=["POST"])
def start_bot():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    import threading
    thread = threading.Thread(target=bot.start, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/bot/stop", methods=["POST"])
def stop_bot():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    bot.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/bot/emergency", methods=["POST"])
def emergency_stop():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    bot.emergency_stop()
    return jsonify({"status": "emergency_stop_activated"})


# ==================== Scheduled Trades ====================

# Global scheduler reference (set alongside bot)
scheduler = None


def set_scheduler(scheduler_instance):
    global scheduler
    scheduler = scheduler_instance


@app.route("/api/scheduled", methods=["GET"])
def get_scheduled_trades():
    if scheduler is None:
        return jsonify({"error": "Scheduler not initialized"}), 503
    return jsonify({
        "pending": scheduler.get_pending_trades(),
        "history": scheduler.get_history(),
    })


@app.route("/api/scheduled", methods=["POST"])
def create_scheduled_trade():
    if scheduler is None:
        return jsonify({"error": "Scheduler not initialized"}), 503

    data = request.get_json()

    required = ["symbol", "side", "qty", "order_type", "scheduled_time"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    try:
        trade = scheduler.schedule_trade(
            symbol=data["symbol"],
            side=data["side"],
            qty=float(data["qty"]),
            order_type=data["order_type"],
            scheduled_time=data["scheduled_time"],
            limit_price=float(data["limit_price"]) if data.get("limit_price") else None,
            stop_loss_pct=float(data["stop_loss_pct"]) if data.get("stop_loss_pct") else None,
            take_profit_pct=float(data["take_profit_pct"]) if data.get("take_profit_pct") else None,
            notes=data.get("notes", ""),
        )
        return jsonify(trade.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled/<trade_id>", methods=["DELETE"])
def cancel_scheduled_trade(trade_id):
    if scheduler is None:
        return jsonify({"error": "Scheduler not initialized"}), 503

    if scheduler.cancel_trade(trade_id):
        return jsonify({"status": "cancelled", "id": trade_id})
    else:
        return jsonify({"error": "Trade not found or already executed"}), 404


@app.route("/api/scheduled/quote/<symbol>")
def get_quote_for_schedule(symbol):
    """Get a quick quote to help user set limit price"""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        quote = bot.alpaca.get_latest_quote(symbol.upper())
        return jsonify(quote)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== WebSocket ====================

@socketio.on("connect")
def handle_connect():
    logger.info("Dashboard client connected")


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Dashboard client disconnected")


def broadcast_update(data):
    """Send real-time update to all connected clients"""
    socketio.emit("update", data)


# ==================== Main ====================

def run_dashboard(host="127.0.0.1", port=5000):
    """Run the dashboard server"""
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
