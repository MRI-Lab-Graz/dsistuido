# Script Index and Cleanup Plan

This file gives a short purpose for each root/script entrypoint and proposes what to keep, combine, archive, or delete.

## Primary Scripts (Keep)

| File | What it does | Recommendation |
|---|---|---|
| `scripts/pipeline/dsi_studio_pipeline.py` | Main end-to-end pipeline for SRC/FIB/database and optional connectivity extraction. | Keep as the canonical pipeline entrypoint. |
| `scripts/connectivity/extract_connectivity_matrices.py` | Connectivity extraction with config parsing, batch mode, logging, and validation helpers. | Keep. |
| `scripts/connectivity/run_connectometry_batch.py` | Batch connectometry runner with parameter sweeps, retries, and headless JPG recovery logic. | Keep. |
| `scripts/connectivity/validate_setup.py` | Pre-flight validation of DSI Studio/config/input availability. | Keep. |
| `gui.py` | Flask/Waitress UI wrapper to launch pipeline/connectometry/viewer jobs. | Keep. |
| `scripts/visualization/generate_interactive_viewer.py` | Builds a single HTML viewer from connectometry JPG outputs. | Keep. |
| `scripts/connectivity/generate_jpgs_from_tt.py` | Fallback renderer from `.tt.gz` to JPG in headless/server workflows. | Keep. |
| `scripts/qa/run_qc.py` | Runs DSI Studio's built-in QC over SRC/FIB files and flags outliers, respecting the project's pinned Apptainer image. | Keep as the main QC entrypoint. |

## Utility Scripts (Keep, but classify as tools)

| File | What it does | Recommendation |
|---|---|---|
| `scripts/qa/check_fib_metrics.py` | Scans FIB files and reports key/ODF structure compatibility. | Keep as the main FIB diagnostics tool. |
| `scripts/qa/src_thumbnail.py` | Renders a mid-axial PNG thumbnail from a SRC file for a quick visual sanity check; also imported by the pipeline. | Keep. |
| `scripts/connectivity/convert_mat_to_csv.py` | Converts DSI Studio `.mat` outputs into CSV/simple CSV files. | Keep. |
| `scripts/connectivity/export_bct_matrices.py` | Collects streamline-count matrices into a flat, BCT-ready CSV tree with normalized variants. | Keep. |
| `scripts/visualization/create_thumbnail_pdfs.py` | Creates PDF thumbnail sheets from `.inc.jpg` / `.dec.jpg` outputs. | Keep, but add CLI args in future (currently hardcoded defaults). |
| `scripts/qa/check_fib_metrics.py --inspect FILE` | Single-file key/shape inspection mode. | Replaces former standalone inspect script. |
| `scripts/pipeline/monitor_pipeline.sh` | Watches the newest pipeline log with optional color highlighting and CLI args. | Keep as active monitor utility. |
| `scripts/extract_acquisition_times.py` | One-off: T1w acquisition-time diff between ses-1/ses-2 for a single hardcoded dataset path. | Not general tooling; keep only if that dataset is still being worked on, otherwise move under `legacy/`. |

## Duplicate/Dead Files Removed

| File | Status | Recommendation |
|---|---|---|
| `dsi_studio_pipeline.py` (root) | Removed. | Use `scripts/pipeline/dsi_studio_pipeline.py` only. |
| `create_differential_fib.py` (root) | Removed. | Use `scripts/pipeline/create_differential_fib.py` only. |
| `scripts/check_fib_metrics.py` | Removed - was a byte-identical duplicate of `scripts/qa/check_fib_metrics.py`. | Use `scripts/qa/check_fib_metrics.py` only. |
| `scripts/common/utils.py` | Removed - unused by any script. | n/a |
| `theme_template/` | Removed - an unreferenced second Flask app, not part of the actual UI (`gui.py` + `templates/`). | n/a |

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
- 2026-09-12 cleanup pass: removed the confirmed-dead duplicate/unused files listed above (`scripts/check_fib_metrics.py`, `scripts/common/utils.py`, `theme_template/`, stale `REPO_STRUCTURE.txt`), and added the previously-undocumented `scripts/qa/run_qc.py`, `scripts/qa/src_thumbnail.py`, `scripts/connectivity/export_bct_matrices.py`, `scripts/extract_acquisition_times.py`.
