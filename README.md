# StockWarren - Automated Stock Trading Bot

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-yellow)
![Alpaca](https://img.shields.io/badge/Broker-Alpaca-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

An automated stock trading system powered by technical analysis and machine learning, built on the Alpaca API. Inspired by the [FutureWarren](https://github.com/martybee/FutureWarren) futures trading bot.

## Features

- **7 Technical Indicators**: RSI, MACD, VWAP, Bollinger Bands, EMA crossovers, Volume analysis, ATR
- **ML Signal Validation**: Random Forest / Gradient Boosting filters false signals
- **One-Way Trailing Stops**: Stops can only tighten, never loosen
- **Stock Scanner**: Auto-scan market for opportunities + custom watchlist
- **Day + Swing Trading**: Supports both trading styles
- **Web Dashboard**: Real-time monitoring with dark theme UI
- **Notifications**: Discord webhooks + email alerts
- **Paper Trading**: Test safely with Alpaca's paper trading API
- **Risk Management**: Daily loss limits, position sizing, consecutive loss protection

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/martybee/StockWarren.git
cd StockWarren
pip install -r requirements.txt
```

### 2. Configure API Keys

```bash
cp .env.example .env
# Edit .env with your Alpaca API keys
# Get free keys at: https://app.alpaca.markets/
```

### 3. Run

```bash
# Start bot + web dashboard
python main.py

# Bot only (no dashboard)
python main.py --bot-only

# Dashboard only (monitoring)
python main.py --dash-only
```

Dashboard opens at: http://localhost:5000

## Project Structure

```
StockWarren/
├── main.py                          # Entry point
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment template
├── config/
│   └── settings.ini                 # All bot configuration
├── src/
│   ├── engine/
│   │   ├── trading_bot.py          # Main bot orchestrator
│   │   └── risk_manager.py         # Risk management & trailing stops
│   ├── indicators/
│   │   └── technical.py            # 7 technical indicators
│   ├── ml/
│   │   └── signal_validator.py     # ML signal validation
│   └── scanner/
│       └── stock_scanner.py        # Market scanner & watchlist
├── alpaca/
│   └── client.py                   # Alpaca API wrapper
├── gui/
│   ├── app.py                      # Flask dashboard
│   ├── templates/index.html        # Dashboard UI
│   └── static/                     # CSS & JS
├── notifications/
│   └── notifier.py                 # Discord & email alerts
├── logs/                           # Trade logs
└── data/                           # ML models & data
```

## Configuration

All settings are in `config/settings.ini`:

| Section | Key Settings |
|---------|-------------|
| `[trading]` | Mode (day/swing/both), max positions, cash reserve |
| `[signals]` | Min signal strength, confirmations, indicator weights |
| `[risk_management]` | Stop loss %, take profit %, daily loss limit, trailing stop |
| `[indicators]` | RSI period, MACD params, BB settings, EMA periods |
| `[scanner]` | Scan interval, price/volume filters |
| `[watchlist]` | Default stock symbols |
| `[notifications]` | Discord/email enable, notification triggers |

## Trading Strategy

### Signal Generation
1. **RSI** (15%): Oversold/overbought detection
2. **MACD** (20%): Momentum and crossover signals
3. **VWAP** (15%): Price vs volume-weighted average
4. **Bollinger Bands** (10%): Mean reversion signals
5. **EMA Crossover** (15%): Trend direction (9/21/50 EMA)
6. **Volume** (15%): Volume surge confirmation
7. **ATR** (10%): Volatility assessment and stop placement

### Trade Execution
1. Scan watchlist + market for opportunities
2. Run all 7 indicators, calculate weighted composite score
3. ML model validates signal (filters ~20% false signals)
4. Check risk/reward ratio (minimum 2:1)
5. Calculate position size based on risk (1% per trade)
6. Place limit order with ATR-based stop loss
7. Monitor with one-way trailing stop

### Risk Management
- **Daily Loss Limit**: $500 or 2% of portfolio (configurable)
- **Max Positions**: 5 simultaneous
- **Position Size**: Max 20% of portfolio per position
- **Consecutive Loss Pause**: Auto-pause after 3 losses (60 min)
- **Trailing Stops**: Can ONLY tighten, NEVER loosen

## Notifications

### Discord
1. Create a webhook in your Discord server
2. Add `DISCORD_WEBHOOK_URL` to `.env`
3. Set `discord_enabled = true` in settings.ini

### Email
1. Add SMTP credentials to `.env`
2. Set `email_enabled = true` in settings.ini
3. For Gmail: use an App Password

## Requirements

- Python 3.11+
- Alpaca account (free paper trading)
- Internet connection

## Comparison with FutureWarren

| Feature | FutureWarren | StockWarren |
|---------|-------------|-------------|
| Asset | Futures (ES, NQ) | Stocks (US equities) |
| Broker | Sierra Chart | Alpaca |
| Language | C++ / Python | Python |
| Signals | Order flow analysis | Technical indicators |
| ML | Random Forest | Random Forest |
| Trailing Stops | One-way (C++) | One-way (Python) |
| Dashboard | Flask | Flask |
| Platform | Windows (Sierra Chart) | Windows, macOS, Linux |

## Disclaimer

This software is for educational purposes only. Trading stocks involves substantial risk of loss. Past performance does not guarantee future results. Always paper trade first and never risk more than you can afford to lose.
