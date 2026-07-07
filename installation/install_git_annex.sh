#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${GIT_ANNEX_INSTALL_DIR:-/data/local/software/git-annex-standalone}"
MIN_MAJOR_VERSION=10

FORCE=0
DRY_RUN=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_usage() {
    cat <<'USAGE'
Git-Annex Upgrade Installer
===========================
This project's --qsiprep_datalad feature needs to fetch file content
(git-annex 'get') from remote DataLad datasets. Old, distro-packaged
git-annex builds (e.g. Ubuntu 22.04 ships 8.20210223) can fail to negotiate
transfer with a remote running a newer git-annex, and the remote silently
gets marked "annex-ignore" - every 'get' then fails with
"not available ... annex-ignore set: origin", even though the file genuinely
exists on the remote.

This script installs a current, standalone git-annex build via
'datalad-installer' (run through 'uv tool run', so nothing is added
permanently to this project's Python environment) and wires it into
venv/bin/activate so it's preferred over any older system git-annex once you
activate this project's venv - no sudo, no system package changes.

Usage:
    bash installation/install_git_annex.sh [--install-dir DIR] [--force] [--dry-run]

Options:
    --install-dir DIR   Where to keep the standalone build
                        (default: /data/local/software/git-annex-standalone)
    --force             Reinstall even if a current-enough git-annex is already set up here
    --dry-run           Show what would happen without downloading/installing anything
    --help              Show this help text
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)
            shift || { echo "Missing value for --install-dir" >&2; exit 1; }
            INSTALL_DIR="$1"
            shift
            ;;
        --install-dir=*)
            INSTALL_DIR="${1#*=}"
            shift
            ;;
        --force)
            FORCE=1
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

echo -e "${BLUE}"
echo "════════════════════════════════════════════════════════════════════"
echo "  Git-Annex Standalone Installer (via datalad-installer + uv)"
echo "════════════════════════════════════════════════════════════════════"
echo -e "${NC}"

BIN_DIR="$INSTALL_DIR/git-annex.linux"

current_major_version() {
    "$1" version 2>/dev/null | head -1 | grep -oP '\d+' | head -1
}

if [[ -x "$BIN_DIR/git-annex" && "$FORCE" -eq 0 ]]; then
    CURRENT_VERSION="$(current_major_version "$BIN_DIR/git-annex")"
    if [[ -n "$CURRENT_VERSION" && "$CURRENT_VERSION" -ge "$MIN_MAJOR_VERSION" ]]; then
        echo -e "${GREEN}✅ Already installed: $("$BIN_DIR/git-annex" version | head -1)${NC}"
        echo -e "${BLUE}   at: $BIN_DIR${NC}"
        echo "Re-run with --force to reinstall anyway."
        exit 0
    fi
fi

if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}❌ 'uv' not found. Install it first:${NC}"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo -e "${YELLOW}(or run installation/setup_env.sh, which installs uv automatically)${NC}"
    exit 1
fi
echo -e "${GREEN}✅ uv is available${NC}"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo -e "${YELLOW}Dry run: would download a current git-annex standalone build via${NC}"
    echo -e "${YELLOW}'uv tool run datalad-installer git-annex -m autobuild' and install it to $BIN_DIR${NC}"
    exit 0
fi

TMP_ENV_FILE="$(mktemp)"
trap 'rm -f "$TMP_ENV_FILE"' EXIT

echo -e "${BLUE}⬇️  Downloading a current git-annex build (datalad-installer, autobuild method)...${NC}"
uv tool run datalad-installer -E "$TMP_ENV_FILE" git-annex -m autobuild

DOWNLOADED_DIR="$(grep -oP '(?<=export PATH=)[^:]+' "$TMP_ENV_FILE" | head -1 | tr -d '"')"
if [[ -z "$DOWNLOADED_DIR" || ! -x "$DOWNLOADED_DIR/git-annex" ]]; then
    echo -e "${RED}❌ Could not locate the downloaded git-annex build (expected a PATH export in $TMP_ENV_FILE).${NC}"
    exit 1
fi

echo -e "${BLUE}📦 Moving it to a persistent location: $INSTALL_DIR ...${NC}"
mkdir -p "$INSTALL_DIR"
rm -rf "$BIN_DIR"
mv "$DOWNLOADED_DIR" "$BIN_DIR"

NEW_VERSION="$("$BIN_DIR/git-annex" version | head -1)"
echo -e "${GREEN}✅ Installed: $NEW_VERSION${NC}"
echo -e "${GREEN}   at: $BIN_DIR${NC}"

VENV_ACTIVATE="$REPO_ROOT/venv/bin/activate"
if [[ -f "$VENV_ACTIVATE" ]]; then
    MARKER="# dsistuido: prefer the standalone git-annex over any older system one"
    if ! grep -qF "$MARKER" "$VENV_ACTIVATE"; then
        {
            echo ""
            echo "$MARKER"
            echo "export PATH=\"$BIN_DIR:\$PATH\""
        } >> "$VENV_ACTIVATE"
        echo -e "${GREEN}✅ venv/bin/activate updated to prefer this git-annex build.${NC}"
    else
        echo -e "${BLUE}ℹ️  venv/bin/activate already prefers a standalone git-annex.${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  $VENV_ACTIVATE not found (create the venv first with installation/setup_env.sh).${NC}"
    echo "   In the meantime, add this to your shell manually:"
    echo "   export PATH=\"$BIN_DIR:\$PATH\""
fi

echo ""
echo -e "${GREEN}✅ Done.${NC} Re-activate your venv (or open a new shell) to pick it up:"
echo "   source $REPO_ROOT/venv/bin/activate"
echo "   git-annex version"
