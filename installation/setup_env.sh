#!/bin/bash

# Setup script for DSI Studio Analysis Tools environment using UV
# UV is a lightning-fast Python package installer (https://github.com/astral-sh/uv)

echo "🚀 Setting up Python environment for DSI Studio Analysis Tools with UV..."

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo "⚡ UV not found. Installing UV..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
    
    if ! command -v uv &> /dev/null; then
        echo "❌ Error: Failed to install UV."
        echo "📌 Please install UV manually: https://github.com/astral-sh/uv"
        echo "   Or install via: pip install uv"
        exit 1
    fi
fi

echo "✓ UV is available"

# Create virtual environment if it doesn't exist (UV will manage this)
if [ ! -d "../venv" ]; then
    echo "📦 Creating virtual environment '../venv' with UV..."
    uv venv ../venv
else
    echo "ℹ️  Virtual environment '../venv' already exists."
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source ../venv/bin/activate

# Install dependencies using UV
if [ -f "../requirements.txt" ]; then
    echo "📥 Installing dependencies with UV from ../requirements.txt..."
    uv pip install -r ../requirements.txt
elif [ -f "requirements.txt" ]; then
    echo "📥 Installing dependencies with UV from local requirements.txt..."
    uv pip install -r requirements.txt
else
    echo "⚠️  Warning: requirements.txt not found. Installing default packages with UV..."
    uv pip install pandas numpy scipy
fi

echo ""
echo "✅ Setup complete with UV!"
echo ""
echo "UV provides:"
echo "  ⚡ 10-100x faster than pip"
echo "  🔒 Reliable dependency resolution"
echo "  🎯 Deterministic installs"
echo ""
echo "To start using the tools, run:"
echo "  source ../venv/bin/activate"
echo ""
echo "Then you can run the scripts:"
echo "  python ../scripts/pipeline/dsi_studio_pipeline.py --help"
echo "  python ../scripts/connectivity/validate_setup.py"
echo ""
echo "📝 Note for Headless Servers:"
echo "If you are running on a server without a display, you may need 'xvfb' to run DSI Studio."
echo "Test it with: xvfb-run -a dsi_studio --version"
echo ""
