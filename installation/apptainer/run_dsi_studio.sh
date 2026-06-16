#!/bin/bash
# Drop-in replacement for a bare `dsi_studio` executable path: runs DSI Studio
# inside the GPU-capable Apptainer image (see dsi_studio.def) instead of a
# bare-metal install. Any script that takes a "dsi_studio_cmd" path can point
# at this script instead and gets the same CLI behavior, transparently
# containerized.
#
# Image and bind path are configurable via env vars so callers (e.g.
# dsi_studio_pipeline.py --apptainer) can override per-run without editing
# this file.
set -euo pipefail

IMAGE="${DSI_APPTAINER_IMAGE:-/data/local/software/apptainer_images/dsi_studio_latest.sif}"
BIND="${DSI_APPTAINER_BIND:-/data/local}"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/data/local/tmp_big/apptainer_cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-/data/local/tmp_big/tmp}"

if [ ! -f "$IMAGE" ]; then
    echo "DSI Studio Apptainer image not found: $IMAGE" >&2
    echo "Build one with: installation/apptainer/build_image.sh" >&2
    exit 1
fi

exec apptainer exec --userns --nvccli -B "$BIND" "$IMAGE" dsi_studio "$@"
