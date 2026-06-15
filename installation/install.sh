#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GITHUB_RELEASES_API="https://api.github.com/repos/frankyeh/DSI-Studio/releases/latest"
GITHUB_RELEASES_FALLBACK_API="https://api.github.com/repos/frankyeh/DSI-Studio/releases?per_page=1"
CURL_RETRY_ARGS=(--retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 30)

DSI_STUDIO_FLAG=""
INSTALL_DIR_FLAG=""
DRY_RUN=0
FORCE_REINSTALL=0

print_usage() {
    cat <<'USAGE'
DSI Studio Analysis Tools Installer
===================================
This installer now auto-installs the latest DSI Studio release when
--dsi-studio / DSI_STUDIO_CMD is not supplied. The selected asset depends on:
  - Operating system
  - CPU architecture
  - CUDA/GPU availability (Linux and Windows builds)

Optional:
    --dsi-studio /path/to/dsi_studio    Use an existing DSI Studio executable
    --install-dir /path/to/install      Override the DSI Studio install base directory
    --force-reinstall                   Re-download and replace the latest installed build
    --dry-run                           Print selected release/asset without downloading
    --help                              Show this help text

Examples:
    ./installation/install.sh
    ./installation/install.sh --dry-run
    ./installation/install.sh --install-dir /data/local/software/dsi-studio
    ./installation/install.sh --dsi-studio /data/local/software/dsi-studio/current/dsi_studio
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dsi-studio)
            shift || { echo "Missing value for --dsi-studio" >&2; exit 1; }
            DSI_STUDIO_FLAG="$1"
            shift
            ;;
        --dsi-studio=*)
            DSI_STUDIO_FLAG="${1#*=}"
            shift
            ;;
        --install-dir)
            shift || { echo "Missing value for --install-dir" >&2; exit 1; }
            INSTALL_DIR_FLAG="$1"
            shift
            ;;
        --install-dir=*)
            INSTALL_DIR_FLAG="${1#*=}"
            shift
            ;;
        --force-reinstall)
            FORCE_REINSTALL=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            print_usage
            exit 1
            ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════════════════════════════╗"
echo "║                                                                                    ║"
echo "║   🧠 DSI STUDIO ANALYSIS TOOLS INSTALLATION                                        ║"
echo "║                                                                                    ║"
echo "╚════════════════════════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

if [[ ! -f "$REPO_ROOT/scripts/connectivity/extract_connectivity_matrices.py" ]]; then
    echo -e "${RED}❌ Could not locate repository root from $SCRIPT_DIR${NC}"
    exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo -e "${RED}❌ Required interpreter not found: $PYTHON_BIN${NC}"
    exit 1
fi

ensure_command() {
    local command_name="$1"
    local help_text="$2"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo -e "${RED}❌ Required command not found: $command_name${NC}"
        echo -e "${YELLOW}$help_text${NC}"
        exit 1
    fi
}

ensure_dsi_path() {
    local candidate="$1"
    if [[ -z "$candidate" ]]; then
        return 1
    fi
    if [[ ! -f "$candidate" ]]; then
        echo -e "${RED}❌ DSI Studio executable not found: $candidate${NC}" >&2
        return 1
    fi
    return 0
}

ensure_dsi_executable() {
    local candidate="$1"
    if [[ "$PLATFORM_OS" != "windows" && -f "$candidate" && ! -x "$candidate" ]]; then
        chmod 755 "$candidate"
    fi
}

detect_platform() {
    local uname_s uname_m
    uname_s="$(uname -s)"
    uname_m="$(uname -m)"

    case "$uname_s" in
        Linux)
            PLATFORM_OS="linux"
            ;;
        Darwin)
            PLATFORM_OS="macos"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            PLATFORM_OS="windows"
            ;;
        *)
            echo -e "${RED}❌ Unsupported operating system: $uname_s${NC}"
            exit 1
            ;;
    esac

    case "$uname_m" in
        x86_64|amd64)
            PLATFORM_ARCH="x86_64"
            ;;
        arm64|aarch64)
            PLATFORM_ARCH="arm64"
            ;;
        *)
            echo -e "${RED}❌ Unsupported architecture: $uname_m${NC}"
            exit 1
            ;;
    esac
}

detect_cuda() {
    CUDA_AVAILABLE="no"
    if command -v nvidia-smi >/dev/null 2>&1; then
        if nvidia-smi >/dev/null 2>&1; then
            CUDA_AVAILABLE="yes"
        fi
    fi
}

detect_linux_release_family() {
    local version_id release_major
    version_id=""

    if command -v lsb_release >/dev/null 2>&1; then
        version_id="$(lsb_release -rs)"
    elif [[ -f /etc/os-release ]]; then
        version_id="$(. /etc/os-release && printf '%s' "$VERSION_ID")"
    fi

    release_major="${version_id%%.*}"
    case "$release_major" in
        24|25|26)
            LINUX_RELEASE_FAMILY="ubuntu2404"
            ;;
        22|23)
            LINUX_RELEASE_FAMILY="ubuntu2204"
            ;;
        20|21)
            LINUX_RELEASE_FAMILY="ubuntu2004"
            ;;
        *)
            LINUX_RELEASE_FAMILY="ubuntu2204"
            echo -e "${YELLOW}⚠️  Could not map Linux release '${version_id:-unknown}' exactly; defaulting to ubuntu2204 asset${NC}"
            ;;
    esac
}

default_install_dir() {
    if [[ -n "$INSTALL_DIR_FLAG" ]]; then
        printf '%s\n' "$INSTALL_DIR_FLAG"
        return 0
    fi

    case "$PLATFORM_OS" in
        linux)
            if [[ -d /data/local/software || -w /data/local/software ]]; then
                printf '%s\n' "/data/local/software/dsi-studio"
            else
                printf '%s\n' "$HOME/.local/share/dsi-studio"
            fi
            ;;
        macos)
            printf '%s\n' "$HOME/Applications/dsi-studio"
            ;;
        windows)
            printf '%s\n' "${LOCALAPPDATA:-$HOME/AppData/Local}/dsi-studio"
            ;;
    esac
}

fetch_latest_release_json() {
    local release_json

    ensure_command curl "Install curl to allow automatic DSI Studio downloads."

    if release_json="$(curl -fsSL "${CURL_RETRY_ARGS[@]}" -H 'Accept: application/vnd.github+json' -H 'User-Agent: dsistuido-installer' "$GITHUB_RELEASES_API" 2>/dev/null)"; then
        printf '%s\n' "$release_json"
        return 0
    fi

    release_json="$(curl -fsSL "${CURL_RETRY_ARGS[@]}" -H 'Accept: application/vnd.github+json' -H 'User-Agent: dsistuido-installer' "$GITHUB_RELEASES_FALLBACK_API")"
    RELEASE_JSON="$release_json" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ['RELEASE_JSON'])
if not payload:
    sys.exit(1)

print(json.dumps(payload[0]))
PY
}

select_release_asset() {
    local release_json="$1"
    local candidates output

    case "$PLATFORM_OS" in
        linux)
            detect_linux_release_family
            if [[ "$PLATFORM_ARCH" == "x86_64" ]]; then
                if [[ "$CUDA_AVAILABLE" == "yes" ]]; then
                    candidates="$LINUX_RELEASE_FAMILY.zip"
                else
                    candidates="$LINUX_RELEASE_FAMILY"$'_cpu.zip\n'"$LINUX_RELEASE_FAMILY.zip"
                fi
            else
                if [[ "$CUDA_AVAILABLE" == "yes" ]]; then
                    candidates="$LINUX_RELEASE_FAMILY"$'_arm64.zip'
                else
                    candidates="$LINUX_RELEASE_FAMILY"$'_cpu_arm64.zip\n'"$LINUX_RELEASE_FAMILY"$'_arm64.zip'
                fi
            fi
            candidates="$(printf '%s\n' "$candidates" | sed 's#^#dsi_studio_#')"
            ;;
        macos)
            if [[ "$PLATFORM_ARCH" == "arm64" ]]; then
                candidates=$'dsi_studio_macos-15_qt6.zip\ndsi_studio_macos-14-arm64_qt6.zip'
            else
                candidates=$'dsi_studio_macos-15-intel_qt6.zip\ndsi_studio_macos-14_qt6.zip\ndsi_studio_macos-13_qt6.zip'
            fi
            ;;
        windows)
            if [[ "$PLATFORM_ARCH" != "x86_64" ]]; then
                echo -e "${RED}❌ Windows arm64 assets are not published in the current DSI Studio release.${NC}"
                exit 1
            fi
            if [[ "$CUDA_AVAILABLE" == "yes" ]]; then
                candidates='dsi_studio_win.zip'
            else
                candidates=$'dsi_studio_win_cpu.zip\ndsi_studio_win.zip'
            fi
            ;;
    esac

    output="$(RELEASE_JSON="$release_json" CANDIDATE_LIST="$candidates" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

data = json.loads(os.environ['RELEASE_JSON'])
assets = {asset['name']: asset['browser_download_url'] for asset in data.get('assets', [])}

for candidate in [line.strip() for line in os.environ['CANDIDATE_LIST'].splitlines() if line.strip()]:
    if candidate in assets:
        print(candidate)
        print(assets[candidate])
        sys.exit(0)

sys.exit(1)
PY
)" || {
        echo -e "${RED}❌ Could not find a compatible DSI Studio release asset for ${PLATFORM_OS}/${PLATFORM_ARCH} (CUDA=${CUDA_AVAILABLE}).${NC}"
        exit 1
    }

    mapfile -t SELECTED_ASSET_INFO <<< "$output"
    SELECTED_ASSET_NAME="${SELECTED_ASSET_INFO[0]}"
    SELECTED_ASSET_URL="${SELECTED_ASSET_INFO[1]}"
}

resolve_dsi_binary_in_dir() {
    local search_dir="$1"
    local binary_name direct_match

    binary_name="dsi_studio"
    if [[ "$PLATFORM_OS" == "windows" ]]; then
        binary_name="dsi_studio.exe"
    fi

    direct_match="$search_dir/$binary_name"
    if [[ -f "$direct_match" ]]; then
        printf '%s\n' "$direct_match"
        return 0
    fi

    find "$search_dir" -type f -name "$binary_name" | head -n 1
}

install_latest_dsi_studio() {
    local release_json latest_tag install_dir tmp_dir archive_path version_dir staging_dir extracted_binary relative_binary

    release_json="$(fetch_latest_release_json)"
    latest_tag="$(RELEASE_JSON="$release_json" "$PYTHON_BIN" - <<'PY'
import json
import os

print(json.loads(os.environ['RELEASE_JSON']).get('tag_name', '').strip())
PY
)"

    if [[ -z "$latest_tag" ]]; then
        echo -e "${RED}❌ Failed to determine the latest DSI Studio release tag.${NC}"
        exit 1
    fi

    select_release_asset "$release_json"
    install_dir="$(default_install_dir)"
    version_dir="$install_dir/$latest_tag"

    echo -e "${BLUE}🧭 Platform detection:${NC} ${PLATFORM_OS}/${PLATFORM_ARCH} (CUDA=${CUDA_AVAILABLE})"
    if [[ "$PLATFORM_OS" == "linux" ]]; then
        echo -e "${BLUE}🧭 Linux asset family:${NC} ${LINUX_RELEASE_FAMILY}"
    fi
    echo -e "${BLUE}📦 Latest DSI Studio release:${NC} ${latest_tag}"
    echo -e "${BLUE}📦 Selected asset:${NC} ${SELECTED_ASSET_NAME}"
    echo -e "${BLUE}📁 Install directory:${NC} ${version_dir}"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        DSI_STUDIO_CMD="$version_dir/${SELECTED_ASSET_NAME%.zip}/dsi_studio"
        if [[ "$PLATFORM_OS" == "windows" ]]; then
            DSI_STUDIO_CMD+=".exe"
        fi
        echo -e "${GREEN}✅ Dry run complete. No download performed.${NC}"
        return 0
    fi

    mkdir -p "$install_dir"

    if [[ -d "$version_dir" && "$FORCE_REINSTALL" -eq 0 ]]; then
        extracted_binary="$(resolve_dsi_binary_in_dir "$version_dir")"
        if [[ -n "$extracted_binary" && -f "$extracted_binary" ]]; then
            ensure_dsi_executable "$extracted_binary"
            DSI_STUDIO_CMD="$extracted_binary"
            echo -e "${GREEN}✅ Latest DSI Studio already installed: $DSI_STUDIO_CMD${NC}"
            return 0
        fi
    fi

    tmp_dir="$(mktemp -d)"
    archive_path="$tmp_dir/$SELECTED_ASSET_NAME"
    staging_dir="$install_dir/.${latest_tag}.staging"

    rm -rf "$staging_dir"
    mkdir -p "$staging_dir"

    echo -e "${BLUE}⬇️  Downloading DSI Studio release...${NC}"
    curl -fL "${CURL_RETRY_ARGS[@]}" "$SELECTED_ASSET_URL" -o "$archive_path"

    echo -e "${BLUE}📂 Extracting release archive...${NC}"
    "$PYTHON_BIN" - "$archive_path" "$staging_dir" <<'PY'
import sys
import zipfile

archive_path, output_dir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive_path) as archive:
    archive.extractall(output_dir)
PY

    extracted_binary="$(resolve_dsi_binary_in_dir "$staging_dir")"
    if [[ -z "$extracted_binary" || ! -f "$extracted_binary" ]]; then
        rm -rf "$tmp_dir" "$staging_dir"
        echo -e "${RED}❌ Downloaded archive did not contain a DSI Studio executable.${NC}"
        exit 1
    fi

    relative_binary="${extracted_binary#"$staging_dir"/}"
    rm -rf "$version_dir"
    mv "$staging_dir" "$version_dir"
    rm -rf "$tmp_dir"

    DSI_STUDIO_CMD="$version_dir/$relative_binary"
    ensure_dsi_executable "$DSI_STUDIO_CMD"
    echo -e "${GREEN}✅ Installed DSI Studio: $DSI_STUDIO_CMD${NC}"
}

update_json_config() {
    local file="$1"
    local cmd="$2"
    "$PYTHON_BIN" - "$file" "$cmd" <<'PY'
import json
import sys
from pathlib import Path

file_path = Path(sys.argv[1])
cmd = sys.argv[2]

data = json.loads(file_path.read_text())
data['dsi_studio_cmd'] = cmd
file_path.write_text(json.dumps(data, indent=2) + '\n')
PY
    echo -e "${GREEN}✅ Updated ${file#$REPO_ROOT/}${NC}"
}

install_python_dependencies() {
    local req_file
    req_file="$REPO_ROOT/requirements.txt"
    if [[ -f "$req_file" ]]; then
        echo -e "${BLUE}📦 Installing Python dependencies...${NC}"
        python -m pip install --upgrade pip >/dev/null
        python -m pip install -r "$req_file"
        echo -e "${GREEN}✅ Dependencies installed${NC}"
    else
        echo -e "${YELLOW}⚠️  No requirements.txt found; installing defaults${NC}"
        python -m pip install pandas numpy scipy
    fi
}

ensure_valid_venv() {
    local venv_path="$1"

    if [[ -d "$venv_path" ]]; then
        if [[ ! -x "$venv_path/bin/python" ]] || ! "$venv_path/bin/python" -c 'import sys' >/dev/null 2>&1; then
            echo -e "${YELLOW}⚠️  Existing virtual environment is invalid; recreating ${venv_path}${NC}"
            rm -rf "$venv_path"
        fi
    fi

    if [[ ! -d "$venv_path" ]]; then
        echo -e "${YELLOW}⚠️  Local virtual environment not found — creating ${venv_path}...${NC}"
        "$PYTHON_BIN" -m venv "$venv_path"
        echo -e "${GREEN}✅ Created virtual environment at $venv_path${NC}"
    fi
}

activate_venv() {
    local venv_path
    venv_path="$REPO_ROOT/venv"
    echo -e "${BLUE}🔧 Ensuring local virtual environment at: ${venv_path}${NC}"

    ensure_valid_venv "$venv_path"

    if [[ -f "$venv_path/bin/activate" ]]; then
        # shellcheck disable=SC1090
        source "$venv_path/bin/activate"
        if ! python -m pip --version >/dev/null 2>&1; then
            echo -e "${YELLOW}⚠️  Virtual environment pip is invalid; recreating ${venv_path}${NC}"
            rm -rf "$venv_path"
            ensure_valid_venv "$venv_path"
            # shellcheck disable=SC1090
            source "$venv_path/bin/activate"
        fi
        echo -e "${GREEN}✅ Virtual environment activated${NC}"
    else
        echo -e "${RED}❌ Virtual environment activation script not found${NC}"
        exit 1
    fi
}

detect_platform
detect_cuda

activate_venv

if [[ -n "$DSI_STUDIO_FLAG" ]]; then
    export DSI_STUDIO_CMD="$DSI_STUDIO_FLAG"
fi

if [[ -z "${DSI_STUDIO_CMD:-}" ]]; then
    install_latest_dsi_studio
else
    if ! ensure_dsi_path "$DSI_STUDIO_CMD"; then
        echo -e "${RED}❌ Invalid DSI Studio path supplied: $DSI_STUDIO_CMD${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Using existing DSI Studio executable: $DSI_STUDIO_CMD${NC}"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    exit 0
fi

echo -e "${BLUE}⚙️  Updating configuration files with DSI Studio path...${NC}"
for config_file in "$REPO_ROOT"/configs/*.json; do
    [[ -f "$config_file" ]] || continue
    update_json_config "$config_file" "$DSI_STUDIO_CMD"
done

install_python_dependencies

echo -e "${BLUE}🧪 Validating installation...${NC}"
if "$PYTHON_BIN" "$REPO_ROOT/scripts/connectivity/validate_setup.py" --config "$REPO_ROOT/configs/example_config.json" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Setup validation passed${NC}"
else
    echo -e "${YELLOW}⚠️  Validation had warnings (run '$PYTHON_BIN $REPO_ROOT/scripts/connectivity/validate_setup.py --config $REPO_ROOT/configs/example_config.json' to see details)${NC}"
fi

echo ""
echo -e "${GREEN}✅ Installation complete!${NC}"
echo ""
echo -e "${BLUE}🎯 To start analysis:${NC}"
echo "1. source $REPO_ROOT/venv/bin/activate"
echo "2. python $REPO_ROOT/scripts/connectivity/run_connectometry_batch.py --config $REPO_ROOT/configs/connectometry_config.json"
echo ""
