#!/bin/bash
#
# Regenerate problematic FIB files identified by check_fib_metrics.py
#
# This script regenerates only the FIB files with incompatible structures,
# avoiding the need to reprocess all 822 files.
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if list files exist
if [ ! -f "regenerate_gqi_files.txt" ] && [ ! -f "regenerate_qsdr_files.txt" ]; then
    echo "❌ Error: No regeneration list files found!"
    echo "Run: python check_fib_metrics.py /path/to/fib/directory first"
    exit 1
fi

echo "========================================="
echo "Regenerating Problematic FIB Files"
echo "========================================="
echo

# Function to extract subject and session from FIB path
extract_sub_ses() {
    local fib_path="$1"
    local filename=$(basename "$fib_path")
    # Extract sub-XXXXX_ses-X from filename
    echo "$filename" | grep -oP 'sub-[0-9]+_ses-[0-9]+'
}

# Function to delete FIB file (prompts user)
delete_fib() {
    local fib_path="$1"
    if [ -f "$fib_path" ]; then
        echo "  Deleting: $fib_path"
        rm "$fib_path"
    fi
}

# Read GQI files if they exist
if [ -f "regenerate_gqi_files.txt" ]; then
    GQI_COUNT=$(wc -l < regenerate_gqi_files.txt)
    echo "Found $GQI_COUNT GQI files to regenerate"
    echo
    
    while IFS= read -r fib_path; do
        if [ -n "$fib_path" ]; then
            sub_ses=$(extract_sub_ses "$fib_path")
            echo "📋 Will regenerate GQI: $sub_ses"
            echo "   File: $(basename "$fib_path")"
        fi
    done < regenerate_gqi_files.txt
fi

# Read QSDR files if they exist
if [ -f "regenerate_qsdr_files.txt" ]; then
    QSDR_COUNT=$(wc -l < regenerate_qsdr_files.txt)
    echo "Found $QSDR_COUNT QSDR files to regenerate"
    echo
    
    while IFS= read -r fib_path; do
        if [ -n "$fib_path" ]; then
            sub_ses=$(extract_sub_ses "$fib_path")
            echo "📋 Will regenerate QSDR: $sub_ses"
            echo "   File: $(basename "$fib_path")"
        fi
    done < regenerate_qsdr_files.txt
fi

echo
echo "========================================="
read -p "Delete these files and regenerate? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Cancelled by user"
    exit 0
fi

echo
echo "Deleting problematic FIB files..."

# Delete GQI files
if [ -f "regenerate_gqi_files.txt" ]; then
    while IFS= read -r fib_path; do
        if [ -n "$fib_path" ]; then
            delete_fib "$fib_path"
        fi
    done < regenerate_gqi_files.txt
fi

# Delete QSDR files
if [ -f "regenerate_qsdr_files.txt" ]; then
    while IFS= read -r fib_path; do
        if [ -n "$fib_path" ]; then
            delete_fib "$fib_path"
        fi
    done < regenerate_qsdr_files.txt
fi

echo
echo "✅ Problematic files deleted!"
echo
echo "========================================="
echo "Next Steps:"
echo "========================================="
echo
echo "1. For GQI files (method 4), run:"
echo "   python dsi_studio_pipeline.py \\"
echo "     --qsiprep_dir /data/local/129_PK01/derivatives/qsiprep \\"
echo "     --output_dir /data/local/129_PK01/derivatives/dsistudio_connectomics \\"
echo "     --rawdata_dir /data/mrivault/_0_STAGING/129_PK01/rawdata \\"
echo "     --method 4 \\"
echo "     --skip_existing \\"
echo "     --require_mask \\"
echo "     --verify_rawdata \\"
echo "     --dsi_studio_path /data/local/software/dsi-studio/"
echo
echo "2. For QSDR files (method 7), run:"
echo "   python dsi_studio_pipeline.py \\"
echo "     --qsiprep_dir /data/local/129_PK01/derivatives/qsiprep \\"
echo "     --output_dir /data/local/129_PK01/derivatives/dsistudio_connectomics \\"
echo "     --rawdata_dir /data/mrivault/_0_STAGING/129_PK01/rawdata \\"
echo "     --method 7 \\"
echo "     --skip_existing \\"
echo "     --require_mask \\"
echo "     --verify_rawdata \\"
echo "     --dsi_studio_path /data/local/software/dsi-studio/"
echo
echo "The pipeline will automatically regenerate only the deleted files!"
echo
