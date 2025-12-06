#!/bin/bash
# Trading Bot Launcher - Starts dashboard and bot together

# Parse arguments (default to balanced profile)
PROFILE="${1:-balanced}"
BOT_ARGS="-c --signal-profile $PROFILE"

# Allow passing additional flags
if [ $# -gt 1 ]; then
    shift
    BOT_ARGS="$BOT_ARGS $@"
fi

echo "🚀 Starting Trading Bot Dashboard..."
echo ""

# Start dashboard in background
nohup python run_dashboard.py > dashboard.log 2>&1 &
DASHBOARD_PID=$!

# Wait for dashboard to start
sleep 3

# Check if dashboard is running
if curl -s http://localhost:5000/health > /dev/null 2>&1; then
    echo "✅ Dashboard running at http://localhost:5000"
else
    echo "⚠️  Dashboard may still be starting..."
fi

echo ""
echo "🤖 Starting Trading Bot with profile: $PROFILE"
echo "📊 Dashboard: http://localhost:5000"
echo "📋 View live logs in the dashboard or below"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "🛑 Stopping services..."
    kill $DASHBOARD_PID 2>/dev/null
    echo "✅ All processes stopped"
    exit 0
}

# Trap Ctrl+C and other signals
trap cleanup SIGINT SIGTERM

# Run bot in foreground with logs visible
python main.py $BOT_ARGS

# If bot exits normally, cleanup
cleanup
