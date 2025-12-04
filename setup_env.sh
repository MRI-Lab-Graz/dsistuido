#!/bin/bash

# Setup script for DSI Studio Analysis Tools environment

echo "🚀 Setting up Python environment for DSI Studio Analysis Tools..."

# Check if python3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 could not be found."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment 'venv'..."
    python3 -m venv venv
else
    echo "ℹ️  Virtual environment 'venv' already exists."
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
if [ -f "requirements.txt" ]; then
    echo "📥 Installing dependencies from requirements.txt..."
    pip install -r requirements.txt
else
    echo "⚠️  Warning: requirements.txt not found. Installing default packages..."
    pip install pandas numpy scipy
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start using the tools, run:"
echo "  source venv/bin/activate"
echo ""
echo "Then you can run the scripts:"
echo "  python run_connectometry_batch.py --config connectometry_config.json"
echo "  python extract_connectivity_matrices.py --help"
echo ""
echo "📝 Note for Headless Servers:"
echo "If you are running on a server without a display, you may need 'xvfb' to run DSI Studio."
echo "Test it with: xvfb-run -a dsi_studio --version"
echo ""
