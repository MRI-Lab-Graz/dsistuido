#!/usr/bin/env python3
"""
Collect DSI Studio streamline-count connectivity matrices into a flat,
BCT-ready directory tree: one plain NxN numeric CSV per subject/session/atlas,
plus two normalized variants:
- max-normalized ([0,1] per subject, same convention as BCT's own
  weight_conversion(W, 'normalize'))
- ROI-size-normalized (count_ij / sqrt(vol_i * vol_j)), correcting for the
  fact that larger regions collect more streamlines regardless of true
  connectivity strength. Region volumes come from the atlas .nii.gz files
  DSI Studio actually used (QSDR template space, so one fixed volume per
  region label, shared across all subjects/sessions).

Source: dsistudio/connectivity/{subject}.odf.qsdr/tracks_5000k_rk4_angle45_fa0.10/
        combined/{atlas}_*.connectivity.number_of_tracts.simple.csv
Output: dsistudio/bct_input/{atlas}/{subject}.count.csv
        dsistudio/bct_input/{atlas}/{subject}.count.normalized.csv
        dsistudio/bct_input/{atlas}/{subject}.count.roi_normalized.csv
        dsistudio/bct_input/{atlas}/roi_volumes_mm3.csv
"""

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np

ATLASES = ["AAL3", "Gordon333", "HCP-MMP", "Schaefer200", "Schaefer400"]
TRACKS_DIR = "tracks_5000k_rk4_angle45_fa0.10"
ATLAS_NIFTI_DIR = Path("/data/local/software/dsi_studio_atlases/human")


def roi_volumes_mm3(atlas: str) -> np.ndarray:
    """Volume (mm^3) of each atlas region, ordered by ascending label id.

    Same atlas file DSI Studio used for the connectivity run, in QSDR
    template space, so this is one fixed vector shared by every subject.
    """
    img = nib.load(ATLAS_NIFTI_DIR / f"{atlas}.nii.gz")
    data = img.get_fdata()
    voxel_vol = float(np.prod(img.header.get_zooms()[:3]))
    n_labels = int(data.max())
    counts = np.bincount(data.astype(int).ravel(), minlength=n_labels + 1)[1:]
    return counts * voxel_vol


def find_count_matrix(connectivity_dir: Path, subject_session: str, atlas: str) -> Path | None:
    pattern = f"{atlas}_{subject_session}.odf.qsdr_{atlas}.tt.gz.{atlas}.connectivity.number_of_tracts.simple.csv"
    path = connectivity_dir / f"{subject_session}.odf.qsdr" / TRACKS_DIR / "combined" / pattern
    return path if path.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--connectivity_dir", required=True, help="dsistudio/connectivity directory")
    ap.add_argument("--output_dir", required=True, help="where to write bct_input/")
    args = ap.parse_args()

    connectivity_dir = Path(args.connectivity_dir)
    output_dir = Path(args.output_dir)

    subject_sessions = sorted(
        p.name.replace(".odf.qsdr", "")
        for p in connectivity_dir.glob("*.odf.qsdr")
        if p.is_dir()
    )

    counts = {atlas: 0 for atlas in ATLASES}
    missing = []

    for atlas in ATLASES:
        raw_dir = output_dir / atlas
        raw_dir.mkdir(parents=True, exist_ok=True)

        volumes = roi_volumes_mm3(atlas)
        np.savetxt(raw_dir / "roi_volumes_mm3.csv", volumes, delimiter=",", fmt="%.6f")
        roi_scale = np.sqrt(np.outer(volumes, volumes))

        for subject_session in subject_sessions:
            src = find_count_matrix(connectivity_dir, subject_session, atlas)
            if src is None:
                missing.append(f"{subject_session} [{atlas}]")
                continue

            matrix = np.loadtxt(src, delimiter=",")
            if matrix.shape != roi_scale.shape:
                missing.append(f"{subject_session} [{atlas}] shape {matrix.shape} != atlas {roi_scale.shape}")
                continue

            raw_path = raw_dir / f"{subject_session}.count.csv"
            np.savetxt(raw_path, matrix, delimiter=",", fmt="%.6f")

            peak = matrix.max()
            normalized = matrix / peak if peak > 0 else matrix
            norm_path = raw_dir / f"{subject_session}.count.normalized.csv"
            np.savetxt(norm_path, normalized, delimiter=",", fmt="%.6f")

            roi_normalized = matrix / roi_scale
            roi_norm_path = raw_dir / f"{subject_session}.count.roi_normalized.csv"
            np.savetxt(roi_norm_path, roi_normalized, delimiter=",", fmt="%.6e")

            counts[atlas] += 1

    print("Wrote (raw + normalized) pairs per atlas:")
    for atlas, n in counts.items():
        print(f"  {atlas}: {n}/{len(subject_sessions)}")
    if missing:
        print(f"Missing ({len(missing)}):")
        for m in missing[:20]:
            print(f"  {m}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")


if __name__ == "__main__":
    main()
