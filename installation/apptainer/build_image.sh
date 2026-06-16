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

IMAGES_DIR="${DSI_APPTAINER_IMAGES_DIR:-/data/local/software/apptainer_images}"
DEF_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dsi_studio.def"

export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/data/local/tmp_big/apptainer_cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-/data/local/tmp_big/tmp}"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$IMAGES_DIR"

build_path="$IMAGES_DIR/dsi_studio_building.sif"
rm -f "$build_path"

echo "Building image (this re-downloads the current latest release asset)..."
apptainer build --mksquashfs-args="-processors 4" "$build_path" "$DEF_FILE"

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
