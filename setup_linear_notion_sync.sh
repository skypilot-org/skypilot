#!/bin/bash
# Setup script for Linear-Notion sync

echo "🚀 Setting up Linear-Notion Sync for Skypilot Issues"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from .env.example..."
    cp .env.example .env
    echo "✅ Created .env file"
    echo ""
    echo "⚠️  Please edit .env and add your API keys:"
    echo "   1. LINEAR_API_KEY - Get from Linear Settings > API"
    echo "   2. NOTION_API_KEY - Get from https://www.notion.so/my-integrations"
    echo "   3. NOTION_DATABASE_ID - Get from your Notion issues database URL"
    echo ""
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install -r linear_notion_requirements.txt

# Check if python-dotenv is installed for loading .env files
pip install python-dotenv

echo ""
echo "✅ Setup complete!"
echo ""
echo "To run the sync:"
echo "  1. Make sure you've added your API keys to .env"
echo "  2. Run: python3 linear_notion_sync.py"
echo ""
echo "The script will:"
echo "  - Fetch all Linear issues assigned to you in 'Todo' state"
echo "  - Analyze the skypilot codebase for each issue"
echo "  - Create detailed Notion pages with proposed fixes"