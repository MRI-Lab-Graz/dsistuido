#!/bin/bash
# Launches the DSI Studio GUI inside the GPU-capable Apptainer image, over an
# X11-forwarded SSH session (ssh -Y). For headless/CLI dsi_studio calls, use
# run_dsi_studio.sh instead -- this script is interactive-GUI only.
#
# Usage: run_dsi_studio_gui.sh [path-within-a-project]
# Walks up from that path (default: cwd) looking for
# code/dsistudio/dsi_studio_image.json to use that project's pinned image,
# the same way dsi_studio_pipeline.py and run_qc.py resolve it. Falls back to
# dsi_studio_latest.sif (with a warning) if no pin is found -- override
# either via DSI_APPTAINER_IMAGE / DSI_APPTAINER_BIND env vars.
set -euo pipefail

if [ -z "${DISPLAY:-}" ]; then
    echo "DISPLAY is not set -- connect with 'ssh -Y' first." >&2
    exit 1
fi

search_dir="$(realpath "${1:-.}")"
pin_image=""
while [ "$search_dir" != "/" ]; do
    pin_file="$search_dir/code/dsistudio/dsi_studio_image.json"
    if [ -f "$pin_file" ]; then
        pin_image="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['image'])" "$pin_file")"
        echo "Using project-pinned image: $pin_image" >&2
        break
    fi
    search_dir="$(dirname "$search_dir")"
done
[ -z "$pin_image" ] && echo "No project pin found above ${1:-.} -- using dsi_studio_latest.sif" >&2

IMAGE="${DSI_APPTAINER_IMAGE:-${pin_image:-/data/local/software/apptainer_images/dsi_studio/dsi_studio_latest.sif}}"
BIND="${DSI_APPTAINER_BIND:-/data/local}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/data/local/tmp_big/apptainer_cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-/data/local/tmp_big/tmp}"

if [ ! -f "$IMAGE" ]; then
    echo "DSI Studio Apptainer image not found: $IMAGE" >&2
    echo "Build one with: installation/apptainer/build_image.sh" >&2
    exit 1
fi

exec apptainer exec --userns --nvccli \
    -B "$BIND" \
    -B /tmp/.X11-unix:/tmp/.X11-unix \
    -B "$HOME/.Xauthority:$HOME/.Xauthority" \
    -B "$RUNTIME_DIR:$RUNTIME_DIR" \
    --env DISPLAY="$DISPLAY" \
    --env XAUTHORITY="$HOME/.Xauthority" \
    --env XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    "$IMAGE" dsi_studio
