# Legacy Scripts Archive

This folder keeps non-primary scripts that were moved out of the active workflow to reduce maintenance noise.

## Why these files were archived

- They are dataset-specific and include hardcoded paths.
- They are superseded by richer Python equivalents under the scripts folder.
- Keeping them here preserves reproducibility for historical runs without presenting them as current defaults.

## Current archive contents

- legacy/scripts/extract_connectivity_matrices.sh
- legacy/scripts/delete_problematic_fibs.sh
- legacy/scripts/regenerate_problematic_fibs.sh
- legacy/scripts/monitor_pipeline.sh
- legacy/code/src_parallel.sh

## Reactivation guideline

If any archived script is needed again, parameterize it first (CLI args + no hardcoded study paths), then move it back under scripts.
