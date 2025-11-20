#!/bin/bash
# Migration Runner - Uses correct Python environment

cd "$(dirname "$0")"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Please run: python -m venv .venv"
    exit 1
fi

# Run migrations
echo "🗄️ Running database migrations..."
.venv/bin/python scripts/run_migrations.py "$@"