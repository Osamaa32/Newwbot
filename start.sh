#!/bin/bash

# Telegram Multi-Account Bot Startup Script

echo "=========================================="
echo "  Telegram Multi-Account Bot Manager"
echo "=========================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found!"
    echo "Creating from template..."
    cp .env.example .env
    echo "📝 Please edit .env file with your credentials:"
    echo "   - BOT_TOKEN: Your Telegram Bot Token"
    echo "   - API_ID: Your Telegram API ID"
    echo "   - API_HASH: Your Telegram API Hash"
    echo "   - OWNER_ID: Your Telegram User ID"
    exit 1
fi

# Create necessary directories
mkdir -p sessions
mkdir -p backups
mkdir -p logs

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

echo "📦 Installing dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

echo "🚀 Starting bot..."
python main.py
