#!/usr/bin/env python3
"""
Render a single mid-axial (transversal) slice from a DSI Studio SRC file
(.src.gz or .src.gz.sz) as a small PNG thumbnail - a quick visual sanity
check for raw DWI data (motion, distortion, wrong orientation, empty/black
volumes) alongside the numeric checks in run_qc.py.

SRC files are just gzip-compressed MATLAB v5 files containing an 'image0'
b0 volume (uint8, flattened column-major/Fortran order) and a 'dimension'
entry ([X, Y, Z]) - no DSI Studio call needed to read them, just gzip+scipy.
(Verified against a real SRC file: reshape(..., order='F') is required -
'C' order produces a scrambled, unusable image.)

Also importable: dsi_studio_pipeline.py calls ensure_src_thumbnail() right
after generating each SRC file, so a thumbnail exists the moment a subject
is processed - not just when someone remembers to run a separate QC pass.

Usage (standalone backfill over a whole project, e.g. for subjects
processed before this existed):
    python scripts/qa/src_thumbnail.py /data/local/129_PK01/derivatives/dsistudio
    python scripts/qa/src_thumbnail.py /path/to/output_dir --force
"""

import argparse
import gzip
import json
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import scipy.io
from PIL import Image

THUMBNAIL_WIDTH = 200


def _load_b0_slice(src_path: Path, slice_frac: float = 0.5) -> Optional[np.ndarray]:
    """Decompress and load a SRC file's b0 volume, returning one 2D axial
    slice (X x Y) at slice_frac through the Z axis. None on any failure
    (corrupt file, unexpected structure, unfetched DataLad content) - this
    is a best-effort visual aid, never worth failing a pipeline run over.
    """
    try:
        with gzip.open(src_path, "rb") as fh:
            mat = scipy.io.loadmat(fh)
    except Exception:
        return None

    dimension = mat.get("dimension")
    image0 = mat.get("image0")
    if dimension is None or image0 is None:
        return None

    try:
        x, y, z = (int(v) for v in dimension[0][:3])
        z_idx = min(max(int(z * slice_frac), 0), z - 1)
        column = image0[:, z_idx]
        return column.reshape((x, y), order="F").astype(np.float32)
    except Exception:
        return None


def render_src_thumbnail(src_path: Path, out_png: Path, slice_frac: float = 0.5) -> bool:
    """Write a normalized, upright PNG of one axial slice to out_png. Returns
    whether it succeeded."""
    arr = _load_b0_slice(src_path, slice_frac)
    if arr is None:
        return False

    span = arr.max() - arr.min()
    normalized = ((arr - arr.min()) / span * 255) if span > 0 else np.zeros_like(arr)
    img = Image.fromarray(normalized.astype(np.uint8))
    # image0 as stored reads sideways and upside-down relative to a
    # conventional radiological axial view - this flip+rotate was checked
    # against a known SRC file and reads right-side-up afterward. The final
    # top-bottom flip corrects anterior/posterior, which otherwise come out
    # swapped (confirmed against a real slice).
    img = img.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.ROTATE_90).transpose(Image.FLIP_TOP_BOTTOM)
    if img.width > THUMBNAIL_WIDTH:
        ratio = THUMBNAIL_WIDTH / img.width
        img = img.resize((THUMBNAIL_WIDTH, int(img.height * ratio)), Image.Resampling.LANCZOS)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.save(out_png)
    except Exception:
        return False
    return True


def parse_sub_ses(name: str) -> Tuple[str, str]:
    parts = name.split("_")
    sub = next((p for p in parts if p.startswith("sub-")), "")
    ses = next((p for p in parts if p.startswith("ses-")), "")
    if ses:
        ses = ses.split(".")[0]
    return sub, ses


def friendly_stem(src_path: Path) -> str:
    """'sub-1291020_ses-2.src.gz.sz' -> 'sub-1291020_ses-2'."""
    return src_path.name.split(".src")[0]


def ensure_src_thumbnail(
    src_path: Path, thumbnails_dir: Path, force: bool = False, slice_frac: float = 0.5
) -> Optional[Path]:
    """Render (or reuse) the thumbnail for one SRC file. Skips regenerating
    if a thumbnail already exists and is newer than the source (unless
    force). Returns the thumbnail path on success, None if it couldn't be
    rendered - never raises, since this is best-effort visual QC, not a
    pipeline requirement.
    """
    stem = friendly_stem(src_path)
    out_png = thumbnails_dir / f"{stem}.png"
    if out_png.exists() and not force:
        try:
            if out_png.stat().st_mtime >= src_path.stat().st_mtime:
                return out_png
        except OSError:
            pass
    if render_src_thumbnail(src_path, out_png, slice_frac):
        return out_png
    return None


def write_manifest(thumbnails_dir: Path) -> Path:
    """Rebuild manifest.json from whatever *.png thumbnails currently exist,
    so the web GUI can list them without re-scanning SRC files itself."""
    entries = []
    for png in sorted(thumbnails_dir.glob("*.png")):
        sub, ses = parse_sub_ses(png.stem)
        entries.append({
            "name": png.stem,
            "subject": sub,
            "session": ses or None,
            "file": png.name,
        })
    manifest_path = thumbnails_dir / "manifest.json"
    manifest_path.write_text(json.dumps(entries, indent=2))
    return manifest_path


def find_src_files(output_dir: Path):
    patterns = ["*.sz", "*.src.gz"]
    candidates = set()
    flat_src_dir = output_dir / "src"
    if flat_src_dir.is_dir():
        for p in patterns:
            candidates.update(flat_src_dir.glob(p))
    for p in patterns:
        candidates.update(output_dir.glob(f"sub-*/src/{p}"))
    if not candidates and output_dir.name == "src":
        for p in patterns:
            candidates.update(output_dir.glob(p))
    return sorted({f for f in candidates if f.exists()})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "output_dir",
        help="Pipeline output_dir (searches its 'src' and 'sub-*/src' folders), or a src/ folder directly",
    )
    parser.add_argument("--thumbnails_dir", help="Where to write PNGs (default: <output_dir>/reports/thumbnails)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if an up-to-date thumbnail already exists")
    parser.add_argument("--slice_frac", type=float, default=0.5, help="Fraction through the Z axis to slice at (default: 0.5, mid-brain)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.is_dir():
        parser.error(f"Not a directory: {output_dir}")

    thumbnails_dir = (
        Path(args.thumbnails_dir).resolve() if args.thumbnails_dir else output_dir / "reports" / "thumbnails"
    )

    src_files = find_src_files(output_dir)
    if not src_files:
        print(f"No SRC (.sz/.src.gz) files found under {output_dir}")
        return

    print(f"Found {len(src_files)} SRC file(s); rendering thumbnails to {thumbnails_dir}")
    ok = 0
    failed = []
    for src_file in src_files:
        result = ensure_src_thumbnail(src_file, thumbnails_dir, force=args.force, slice_frac=args.slice_frac)
        if result:
            ok += 1
        else:
            failed.append(src_file.name)

    manifest_path = write_manifest(thumbnails_dir)
    print(f"{ok}/{len(src_files)} thumbnail(s) ready, manifest -> {manifest_path}")
    if failed:
        print(f"{len(failed)} file(s) could not be rendered:")
        for name in failed:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
