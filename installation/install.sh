#!/bin/bash

# DSI Studio Analysis Tools Installer
# Adapted from OptiConn installer for DSI Studio Analysis Tools
#
# Usage:
#   ./install.sh --dsi-studio /absolute/path/to/dsi_studio
#
# Flags:
#   --dsi-studio <path>   (Required unless DSI_STUDIO_CMD env var is set)
#   --help                 Show usage and exit

set -e  # Exit on any error

DSI_STUDIO_FLAG=""

print_usage() {
    cat <<'USAGE'
DSI Studio Analysis Tools Installer
===================================
Required:
    --dsi-studio /path/to/dsi_studio    Absolute path to DSI Studio executable
                                        (may also be supplied via DSI_STUDIO_CMD env var)
Optional:
    --help                              Show this help text

Examples:
    ./install.sh --dsi-studio /data/local/software/dsi-studio/dsi_studio
    DSI_STUDIO_CMD=/opt/dsi_studio/dsi_studio ./install.sh
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dsi-studio)
            shift || { echo "Missing value for --dsi-studio" >&2; exit 1; }
            DSI_STUDIO_FLAG="$1"; shift ;;
        --dsi-studio=*)
            DSI_STUDIO_FLAG="${1#*=}"; shift ;;
        --help|-h)
            print_usage; exit 0 ;;
        *)
            echo "Unknown argument: $1" >&2
            print_usage
            exit 1 ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════════════════════════════╗"
echo "║                                                                                    ║"
echo "║   🧠 DSI STUDIO ANALYSIS TOOLS INSTALLATION                                        ║"
echo "║                                                                                    ║"
echo "╚════════════════════════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if we're in the right directory
if [[ ! -f "extract_connectivity_matrices.py" ]]; then
    echo -e "${RED}❌ Please run this script from the repository root${NC}"
    exit 1
fi

# Use a local virtual environment
VENV_PATH="venv"
echo -e "${BLUE}🔧 Ensuring local virtual environment at: $VENV_PATH${NC}"

if [[ ! -d "$VENV_PATH" ]]; then
    echo -e "${YELLOW}⚠️  Local virtual environment not found — creating ${VENV_PATH}...${NC}"
    python3 -m venv "$VENV_PATH"
    echo -e "${GREEN}✅ Created virtual environment at $VENV_PATH${NC}"
fi

# Activate the local venv
if [[ -f "$VENV_PATH/bin/activate" ]]; then
    source "$VENV_PATH/bin/activate"
    echo -e "${GREEN}✅ Virtual environment activated${NC}"
else
    echo -e "${RED}❌ Virtual environment activation script not found${NC}"
    exit 1
fi

# ----------------------------------------------------------------------------
# DSI Studio Path Requirement
# ----------------------------------------------------------------------------
ensure_dsi_path() {
  local candidate="$1"
  if [[ -z "$candidate" ]]; then return 1; fi
  if [[ ! -f "$candidate" ]]; then
    echo -e "${RED}❌ DSI Studio executable not found: $candidate${NC}" >&2
    return 1
  fi
  return 0
}

# Priority: flag > env var
if [[ -n "$DSI_STUDIO_FLAG" ]]; then
  export DSI_STUDIO_CMD="$DSI_STUDIO_FLAG"
fi

if [[ -z "$DSI_STUDIO_CMD" ]]; then
  echo -e "${RED}❌ Missing required DSI Studio path.${NC}"
  echo -e "${BLUE}Provide it with:${NC}"
  echo "  ./install.sh --dsi-studio /absolute/path/to/dsi_studio"
  exit 1
fi

if ! ensure_dsi_path "$DSI_STUDIO_CMD"; then
  echo -e "${RED}❌ Invalid DSI Studio path supplied: $DSI_STUDIO_CMD${NC}"
  exit 1
fi

echo -e "${GREEN}✅ DSI Studio found: $DSI_STUDIO_CMD${NC}"

# ----------------------------------------------------------------------------
# Update Configuration Files
# ----------------------------------------------------------------------------
echo -e "${BLUE}⚙️  Updating configuration files with DSI Studio path...${NC}"

update_json_config() {
    local file="$1"
    local cmd="$2"
    if [[ -f "$file" ]]; then
        # Use python to safely update JSON
        python3 -c "import json; f='$file'; d=json.load(open(f)); d['dsi_studio_cmd']='$cmd'; json.dump(d, open(f,'w'), indent=2)"
        echo -e "${GREEN}✅ Updated $file${NC}"
    fi
}

update_json_config "connectometry_simple.json" "$DSI_STUDIO_CMD"
update_json_config "connectometry_config.json" "$DSI_STUDIO_CMD"
update_json_config "example_config.json" "$DSI_STUDIO_CMD"

# ----------------------------------------------------------------------------
# Install Dependencies
# ----------------------------------------------------------------------------
REQ_FILE="requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    echo -e "${BLUE}📦 Installing Python dependencies...${NC}"
    pip install --upgrade pip >/dev/null
    pip install -r "$REQ_FILE"
    echo -e "${GREEN}✅ Dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠️  No requirements.txt found; installing defaults${NC}"
    pip install pandas numpy scipy
fi

# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
echo -e "${BLUE}🧪 Validating installation...${NC}"

# Force headless mode for validation
# export QT_QPA_PLATFORM=minimal

if python3 validate_setup.py > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Setup validation passed${NC}"
else
    echo -e "${YELLOW}⚠️  Validation had warnings (run 'python validate_setup.py' to see details)${NC}"
fi

echo ""
echo -e "${GREEN}✅ Installation complete!${NC}"
echo ""
echo -e "${BLUE}🎯 To start analysis:${NC}"
echo "1. source venv/bin/activate"
echo "2. python run_connectometry_batch.py --config connectometry_simple.json"
echo ""
