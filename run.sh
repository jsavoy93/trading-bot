#!/bin/bash
# Trading Bot Runner - Uses correct Python environment

cd "$(dirname "$0")"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Please run: python -m venv .venv"
    exit 1
fi

# Check if packages are installed
if ! .venv/bin/python -c "import alpaca_trade_api" 2>/dev/null; then
    echo "📦 Installing required packages..."
    .venv/bin/pip install -r requirements.txt
fi

# Run the trading bot
echo "🚀 Starting trading bot..."
.venv/bin/python main.py "$@"