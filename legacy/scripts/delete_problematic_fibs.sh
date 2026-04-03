#!/bin/bash
# Delete only the 5 problematic FIB files identified by inspect_fib.py
# These files have incomplete ODF structure and need regeneration

FIB_DIR="/data/local/129_PK01/derivatives/dsistudio_connectomics/fib"

echo "🗑️  Deleting problematic FIB files..."
echo ""

# The 4 GQI files with only 2 ODF indices
rm -v "${FIB_DIR}/sub-1292092_ses-3.odf.gqi.fz"
rm -v "${FIB_DIR}/sub-1293175_ses-1.odf.gqi.fz"
rm -v "${FIB_DIR}/sub-1293175_ses-2.odf.gqi.fz"
rm -v "${FIB_DIR}/sub-1293175_ses-3.odf.gqi.fz"

# The 1 QSDR file
rm -v "${FIB_DIR}/sub-1292092_ses-3.odf.qsdr.fz"

echo ""
echo "✅ Done! Now run the pipeline with --skip_existing to regenerate only these files"
echo ""
echo "Command (FIB generation only):"
echo "python dsi_studio_pipeline.py \\"
echo "  --qsiprep_dir /data/local/129_PK01/derivatives/qsiprep \\"
echo "  --output_dir /data/local/129_PK01/derivatives/dsistudio_connectomics \\"
echo "  --rawdata_dir /data/mrivault/_0_STAGING/129_PK01/rawdata \\"
echo "  --require_mask --skip_existing --verify_rawdata \\"
echo "  --dsi_studio_path /data/local/software/dsi-studio/"
echo ""
echo "Or with connectivity extraction:"
echo "python dsi_studio_pipeline.py \\"
echo "  --qsiprep_dir /data/local/129_PK01/derivatives/qsiprep \\"
echo "  --output_dir /data/local/129_PK01/derivatives/dsistudio_connectomics \\"
echo "  --rawdata_dir /data/mrivault/_0_STAGING/129_PK01/rawdata \\"
echo "  --require_mask --skip_existing --verify_rawdata \\"
echo "  --dsi_studio_path /data/local/software/dsi-studio/ \\"
echo "  --run_connectivity \\"
echo "  --connectivity_config /data/local/software/dsistuido/graph_analysis_config.json"
