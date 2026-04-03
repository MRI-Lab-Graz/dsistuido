#!/usr/bin/env bash
set -euo pipefail

# Monitor DSI Studio pipeline logs with optional color highlighting.
# Defaults:
# - Uses current directory as log dir
# - Picks newest file matching pipeline_*.log

LOG_DIR="."
LOG_FILE=""
PATTERN="pipeline_*.log"
LINES=30
FOLLOW=1
COLOR=1

usage() {
    cat <<'EOF'
Usage: scripts/pipeline/monitor_pipeline.sh [OPTIONS]

Options:
  --log-dir DIR        Directory containing logs (default: current directory)
  --log-file FILE      Exact log file to watch (overrides --log-dir/--pattern)
  --pattern GLOB       Pattern for log discovery (default: pipeline_*.log)
  --lines N            Number of lines to show initially (default: 30)
  --no-follow          Print lines and exit
  --no-color           Disable ANSI colors
  -h, --help           Show this help

Examples:
    scripts/pipeline/monitor_pipeline.sh --log-dir /data/run/output
    scripts/pipeline/monitor_pipeline.sh --log-file /data/run/output/pipeline_20260403_120000.log
    scripts/pipeline/monitor_pipeline.sh --log-dir /data/run/output --pattern "*.log" --lines 100
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --log-dir)
            LOG_DIR="$2"
            shift 2
            ;;
        --log-file)
            LOG_FILE="$2"
            shift 2
            ;;
        --pattern)
            PATTERN="$2"
            shift 2
            ;;
        --lines)
            LINES="$2"
            shift 2
            ;;
        --no-follow)
            FOLLOW=0
            shift
            ;;
        --no-color)
            COLOR=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ -z "$LOG_FILE" ]]; then
    if [[ ! -d "$LOG_DIR" ]]; then
        echo "Error: log directory does not exist: $LOG_DIR" >&2
        exit 1
    fi

    # Find newest file matching pattern.
    LOG_FILE="$(find "$LOG_DIR" -maxdepth 1 -type f -name "$PATTERN" -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
fi

if [[ -z "$LOG_FILE" || ! -f "$LOG_FILE" ]]; then
    echo "Error: no matching log file found." >&2
    if [[ -n "$LOG_DIR" ]]; then
        echo "Searched in: $LOG_DIR (pattern: $PATTERN)" >&2
    fi
    exit 1
fi

echo "============================================================"
echo "DSI Studio Pipeline Monitor"
echo "File: $LOG_FILE"
echo "============================================================"

if [[ "$COLOR" -eq 0 ]]; then
    if [[ "$FOLLOW" -eq 1 ]]; then
        tail -n "$LINES" -f "$LOG_FILE"
    else
        tail -n "$LINES" "$LOG_FILE"
    fi
    exit 0
fi

paint_line() {
    local line="$1"
    if [[ "$line" == *"ERROR"* || "$line" == *"failed"* || "$line" == *"FAIL"* ]]; then
        printf '\033[31m%s\033[0m\n' "$line"  # red
    elif [[ "$line" == *"WARNING"* || "$line" == *"warn"* ]]; then
        printf '\033[33m%s\033[0m\n' "$line"  # yellow
    elif [[ "$line" == *"validated"* || "$line" == *"success"* || "$line" == *"completed"* ]]; then
        printf '\033[32m%s\033[0m\n' "$line"  # green
    elif [[ "$line" == *"Processing"* || "$line" == *"PROGRESS"* ]]; then
        printf '\033[36m%s\033[0m\n' "$line"  # cyan
    else
        printf '%s\n' "$line"
    fi
}

if [[ "$FOLLOW" -eq 1 ]]; then
    tail -n "$LINES" -f "$LOG_FILE" | while IFS= read -r line; do
        paint_line "$line"
    done
else
    tail -n "$LINES" "$LOG_FILE" | while IFS= read -r line; do
        paint_line "$line"
    done
fi
