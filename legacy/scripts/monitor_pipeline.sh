#!/bin/bash
# Quick monitoring script to watch pipeline progress in real-time

# Monitor log file for progress updates
LOG_DIR="/data/local/129_PK01/derivatives/dsistudio_connectomics"

echo "╔════════════════════════════════════════════════╗"
echo "║ 📊 DSI Studio Pipeline Monitor                ║"
echo "║ Watching: $LOG_DIR                            ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# Find the most recent log file
LOG_FILE=$(ls -t "$LOG_DIR"/pipeline_*.log 2>/dev/null | head -1)

if [ -z "$LOG_FILE" ]; then
    echo "❌ No pipeline logs found in $LOG_DIR"
    exit 1
fi

echo "📋 Latest log: $(basename "$LOG_FILE")"
echo "🔄 Watching for updates... (Ctrl+C to stop)"
echo ""

# Follow the log with color highlighting
tail -f "$LOG_FILE" 2>/dev/null | while IFS= read -r line; do
    # Highlight different message types
    if [[ $line == *"✓"* ]] || [[ $line == *"successfully"* ]]; then
        echo -e "\033[32m$line\033[0m"  # Green
    elif [[ $line == *"✗"* ]] || [[ $line == *"ERROR"* ]] || [[ $line == *"failed"* ]]; then
        echo -e "\033[31m$line\033[0m"  # Red
    elif [[ $line == *"PROGRESS"* ]] || [[ $line == *"Processing"* ]]; then
        echo -e "\033[36m$line\033[0m"  # Cyan
    elif [[ $line == *"WARNING"* ]]; then
        echo -e "\033[33m$line\033[0m"  # Yellow
    else
        echo "$line"
    fi
done
