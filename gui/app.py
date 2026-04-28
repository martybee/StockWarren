"""
Flask Web Dashboard for StockWarren
Real-time monitoring, trade history, and bot control
"""

import os
import sys
import json
import logging
from datetime import datetime

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

logger = logging.getLogger(__name__)

# Global bot reference (set by main.py)
bot = None


def set_bot(bot_instance):
    global bot
    bot = bot_instance


# ==================== Routes ====================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def get_status():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    return jsonify(bot.get_status())


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
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    data = request.get_json()
    if "add" in data:
        bot.watchlist.add(data["add"])
    elif "remove" in data:
        bot.watchlist.remove(data["remove"])
    elif "symbols" in data:
        bot.watchlist.set_symbols(data["symbols"])
    return jsonify({"symbols": bot.watchlist.get_symbols()})


@app.route("/api/market")
def get_market_hours():
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503
    try:
        return jsonify(bot.alpaca.get_market_hours())
    except Exception as e:
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
        _asset_cache = [
            {"symbol": a.symbol, "name": a.name or a.symbol, "exchange": a.exchange}
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


@app.route("/api/stocks/search")
def search_stocks():
    """Search for stocks by symbol or name"""
    if bot is None:
        return jsonify({"error": "Bot not initialized"}), 503

    query = request.args.get("q", "").upper().strip()
    if len(query) < 1:
        return jsonify([])

    assets = _get_asset_list()

    # Exact symbol matches first, then prefix matches, then contains
    exact = []
    prefix = []
    contains = []

    for a in assets:
        sym = a["symbol"].upper()
        name = a["name"].upper()

        if sym == query:
            exact.append(a)
        elif sym.startswith(query):
            prefix.append(a)
        elif query in name:
            contains.append(a)

    # Sort prefix by symbol length (shorter = more relevant)
    prefix.sort(key=lambda x: len(x["symbol"]))
    contains.sort(key=lambda x: len(x["symbol"]))

    results = exact + prefix + contains
    return jsonify(results[:15])


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
