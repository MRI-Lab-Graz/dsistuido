#!/bin/bash
# Build (or refresh) the GPU-capable DSI Studio Apptainer image.
#
# Wraps the official prebuilt Linux release asset (CUDA-enabled) in an
# Ubuntu 22.04 base matching this host's glibc, so --nv/--nvccli GPU
# passthrough works. Re-run this any time to pick up the maintainer's
# latest build - DSI Studio ships new builds every few days without
# bumping its GitHub release tag, so the build date embedded in the
# binary (not the tag) is what we use to name the image.
set -euo pipefail

IMAGES_DIR="${DSI_APPTAINER_IMAGES_DIR:-/data/local/software/apptainer_images/dsi_studio}"
DEF_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dsi_studio.def"
REPO="frankyeh/DSI-Studio"
ASSET_NAME="dsi_studio_ubuntu2204.zip"

export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/data/local/tmp_big/apptainer_cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-/data/local/tmp_big/tmp}"
export SINGULARITY_CACHEDIR="$APPTAINER_CACHEDIR"
export SINGULARITY_TMPDIR="$APPTAINER_TMPDIR"
export TMPDIR="$APPTAINER_TMPDIR"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$IMAGES_DIR"

for cmd in curl apptainer; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: required command '$cmd' not found on PATH" >&2; exit 1; }
done

# The maintainer's GitHub release tags don't reliably ship the Linux CUDA
# asset -- e.g. the newest release as of 2026-08 (2026.7.25) is Windows-only;
# the asset lives under an older tag (2025.04.16) and gets refreshed there via
# `gh release upload --clobber` on almost every CI run instead of a fresh tag.
# So: walk releases newest-first and use whichever one currently has it.
# DSI_ASSET_URL lets a caller that already resolved this (e.g.
# check_and_build.sh) skip the extra API round-trip.
asset_url="${DSI_ASSET_URL:-}"
if [ -z "$asset_url" ]; then
    command -v jq >/dev/null 2>&1 || { echo "ERROR: required command 'jq' not found on PATH" >&2; exit 1; }
    echo "Resolving newest GitHub release that ships $ASSET_NAME..."
    releases_json=$(curl -s -m 30 "https://api.github.com/repos/${REPO}/releases?per_page=20")
    asset_url=$(printf '%s' "$releases_json" | jq -r --arg name "$ASSET_NAME" '
        [.[] | select(.assets[]?.name == $name)][0].assets[]?
        | select(.name == $name) | .browser_download_url' | head -1)
    if [ -z "$asset_url" ] || [ "$asset_url" = "null" ]; then
        echo "ERROR: no release in the last 20 ships $ASSET_NAME; aborting." >&2
        exit 1
    fi
fi
echo "Using asset: $asset_url"

build_path="$IMAGES_DIR/dsi_studio_building.sif"
rm -f "$build_path"

echo "Building image (downloading $asset_url)..."
apptainer build --mksquashfs-args="-processors 4" --build-arg ASSET_URL="$asset_url" "$build_path" "$DEF_FILE"

build_info=$(apptainer exec "$build_path" cat /opt/dsi-studio/BUILD_INFO.txt)
echo "Embedded build info: $build_info"

# Parse a date like "Jun 15 2026" out of the version string.
build_date=$(echo "$build_info" | grep -oE '[A-Za-z]{3} [0-9]{1,2} [0-9]{4}' | head -1)
if [ -z "$build_date" ]; then
    echo "Could not parse build date from version string; keeping generic name." >&2
    final_path="$IMAGES_DIR/dsi_studio_$(date +%Y%m%d_%H%M%S).sif"
else
    iso_date=$(date -d "$build_date" +%Y-%m-%d)
    final_path="$IMAGES_DIR/dsi_studio_hou-${iso_date}.sif"
fi

if [ -f "$final_path" ]; then
    echo "Image for this build date already exists: $final_path"
    rm -f "$build_path"
else
    mv "$build_path" "$final_path"
    echo "Built: $final_path"
fi

ln -sf "$(basename "$final_path")" "$IMAGES_DIR/dsi_studio_latest.sif"
echo "Updated symlink: $IMAGES_DIR/dsi_studio_latest.sif -> $(basename "$final_path")"
