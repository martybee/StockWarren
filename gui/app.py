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
