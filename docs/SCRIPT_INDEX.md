# Script Index and Cleanup Plan

This file gives a short purpose for each root/script entrypoint and proposes what to keep, combine, archive, or delete.

## Primary Scripts (Keep)

| File | What it does | Recommendation |
|---|---|---|
| `scripts/pipeline/dsi_studio_pipeline.py` | Main end-to-end pipeline for SRC/FIB/database and optional connectivity extraction. | Keep as the canonical pipeline entrypoint. |
| `scripts/connectivity/extract_connectivity_matrices.py` | Connectivity extraction with config parsing, batch mode, logging, and validation helpers. | Keep. |
| `scripts/connectivity/run_connectometry_batch.py` | Batch connectometry runner with parameter sweeps, retries, and headless JPG recovery logic. | Keep. |
| `scripts/connectivity/validate_setup.py` | Pre-flight validation of DSI Studio/config/input availability. | Keep. |
| `scripts/web/webui.py` | Flask/Waitress UI wrapper to launch pipeline/connectometry/viewer jobs. | Keep. |
| `scripts/visualization/generate_interactive_viewer.py` | Builds a single HTML viewer from connectometry JPG outputs. | Keep. |
| `scripts/connectivity/generate_jpgs_from_tt.py` | Fallback renderer from `.tt.gz` to JPG in headless/server workflows. | Keep. |

## Utility Scripts (Keep, but classify as tools)

| File | What it does | Recommendation |
|---|---|---|
| `scripts/qa/check_fib_metrics.py` | Scans FIB files and reports key/ODF structure compatibility. | Keep as the main FIB diagnostics tool. |
| `scripts/connectivity/convert_mat_to_csv.py` | Converts DSI Studio `.mat` outputs into CSV/simple CSV files. | Keep. |
| `scripts/visualization/create_thumbnail_pdfs.py` | Creates PDF thumbnail sheets from `.inc.jpg` / `.dec.jpg` outputs. | Keep, but add CLI args in future (currently hardcoded defaults). |
| `scripts/qa/check_fib_metrics.py --inspect FILE` | Single-file key/shape inspection mode. | Replaces former standalone inspect script. |
| `scripts/pipeline/monitor_pipeline.sh` | Watches the newest pipeline log with optional color highlighting and CLI args. | Keep as active monitor utility. |
| `scripts/common/utils.py` | Shared logging/session helper functions. | Keep only if reused; otherwise inline or remove. |

## Duplicate Root Scripts

| File | Current status | Recommendation |
|---|---|---|
| `dsi_studio_pipeline.py` | Duplicate root copy has been removed. | Use `scripts/pipeline/dsi_studio_pipeline.py` only. |
| `create_differential_fib.py` | Duplicate root copy has been removed. | Use `scripts/pipeline/create_differential_fib.py` only. |

## Shell Scripts to Archive/Delete

| File | Why it should not stay as active tooling | Recommendation |
|---|---|---|
| `legacy/scripts/extract_connectivity_matrices.sh` | Overlaps with richer Python implementation; two codepaths increase maintenance burden. | Archived; keep for historical reference only. |
| `legacy/scripts/delete_problematic_fibs.sh` | Hardcoded to one dataset/path and specific subject IDs. | Archived; not for general use. |
| `legacy/scripts/regenerate_problematic_fibs.sh` | Depends on generated text files and includes dataset-specific command examples. | Archived; not for general use. |
| `legacy/scripts/monitor_pipeline.sh` | Old hardcoded monitor script. | Archived predecessor; replaced by `scripts/pipeline/monitor_pipeline.sh`. |
| `legacy/code/src_parallel.sh` | Isolated NODDI helper with relative hardcoded dsi path and no integration. | Archived; keep only if needed for old runs. |

## Immediate Combine/Delete Plan

1. Canonicalize entrypoints under `scripts/` only.
2. Keep single-file inspection in `scripts/qa/check_fib_metrics.py --inspect`.
3. Keep dataset-specific shell scripts under `legacy/` unless rewritten with parameters.
4. Keep root duplicate entrypoints removed and docs pointing to `scripts/`.
5. Keep this file updated whenever scripts are added/removed.

## Notes from current review

- `scripts/pipeline/create_differential_fib.py` had a syntax error and has been fixed.
- Root `requirements.txt` is now the canonical dependency file.
- `installation/requirements.txt` now delegates to root via `-r ../requirements.txt`.
- Standalone `scripts/inspect_fib.py` was replaced by `--inspect` mode in `scripts/qa/check_fib_metrics.py`.
- Dataset-specific shell scripts were archived to `legacy/`.
- `scripts/pipeline/monitor_pipeline.sh` was reintroduced as an active, parameterized monitor utility.
