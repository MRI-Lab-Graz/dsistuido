#!/usr/bin/env python3
"""
Run DSI Studio's built-in QC (--action=qc) over SRC (.sz/.src.gz) and/or FIB
(.fz) files in a directory, and flag files worth a second look.

If source_dir has "src" and/or "fib" subfolders (the flat aggregate layout
dsi_studio_pipeline.py always creates under its output_dir), those are searched
instead of source_dir itself -- so pointing this at a pipeline's top-level
output_dir just works. Otherwise source_dir is searched directly (e.g. pointing
straight at .../src).

FIB is checked in two separate passes, since the pipeline's DataLad mode writes
GQI reconstructions only into each subject's own sub-*/fib/ folder while QSDR
reconstructions go into the flat fib/ folder -- the two are not duplicates of
each other, so a single flat-folder pass silently misses one of them:
    - fz_qc_qsdr.tsv: the flat fib/ folder (or source_dir if pointed there directly)
    - fz_qc_gqi.tsv:  every sub-*/fib/*.fz under source_dir

Wraps:
    dsi_studio --action=qc --source=<file1,file2,...> --check_btable=1 --output=<sz_qc.tsv>
    dsi_studio --action=qc --source=<file1,file2,...> --output=<fz_qc_*.tsv>

By default this runs against the *same* DSI Studio Apptainer image that
dsi_studio_pipeline.py pinned for this project (read from
<project_root>/code/dsistudio/dsi_studio_image.json), so QC always checks
files with the exact build that produced them -- DSI Studio ships new builds
almost daily, so an unpinned "latest" would silently drift out of sync.
Use --apptainer_image for a one-off check against a different build (doesn't
change the pin), or --dsi_studio_cmd to bypass pinning entirely.

Usage:
    python scripts/qa/run_qc.py /data/local/134_AF19/derivatives/dsistudio
    python scripts/qa/run_qc.py /data/local/122_AF17/derivatives/dsistudio/fib --skip_src
    python scripts/qa/run_qc.py /path/to/dir --output_dir tmp/qc
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

# stdout is piped to a log file (not a TTY) when run from the web GUI, which
# makes Python fully block-buffer it instead of flushing per line -- progress
# prints below would otherwise sit invisible until the whole script exits.
sys.stdout.reconfigure(line_buffering=True)

# Bare-metal fallback only used when no project pin exists and --apptainer_image
# wasn't given -- see resolve_dsi_studio_cmd. Prefer the pinned container so QC
# always matches the exact DSI Studio build that generated the SRC/FIB files;
# DSI Studio ships new builds almost daily, so an unpinned bare-metal path
# silently drifts out of sync with what a project was actually processed with.
DEFAULT_DSI_STUDIO_CMD = "/data/local/software/dsi-studio/2025.04.16/dsi-studio/dsi_studio"
APPTAINER_WRAPPER = Path(__file__).resolve().parents[2] / "installation" / "apptainer" / "run_dsi_studio.sh"
DEFAULT_APPTAINER_IMAGES_DIR = Path("/data/local/software/apptainer_images")

SRC_PATTERNS = ["*.sz", "*.src.gz"]
FIB_PATTERNS = ["*.fz"]


def find_project_pin(source_dir: Path) -> Path | None:
    """Locate the dsi_studio_image.json that dsi_studio_pipeline.py pinned for
    this project, so QC checks files with the same DSI Studio build that
    produced them instead of whatever happens to be the current default.

    Mirrors dsi_studio_pipeline.py's own output_dir -> project_root inference
    (output_dir/../.. when output_dir's parent is named "derivatives", else
    output_dir/..), trying source_dir itself and its parent as candidate
    "output_dir"s since QC is sometimes pointed at output_dir directly and
    sometimes at a src/ or fib/ subfolder within it (see module docstring).
    """
    for candidate_output_dir in (source_dir, source_dir.parent):
        if candidate_output_dir.parent.name == "derivatives":
            project_root = candidate_output_dir.parent.parent
        else:
            project_root = candidate_output_dir.parent
        pin_file = project_root / "code" / "dsistudio" / "dsi_studio_image.json"
        if pin_file.exists():
            return pin_file
    return None


def resolve_dsi_studio_cmd(args, source_dir: Path) -> str:
    """Pick which dsi_studio to run, preferring (in order): an explicit
    --dsi_studio_cmd override, an explicit --apptainer_image override, this
    project's pinned Apptainer image, --apptainer's unpinned "latest" image,
    and finally the bare-metal DEFAULT_DSI_STUDIO_CMD. Never writes the pin --
    only dsi_studio_pipeline.py runs advance a project's pin; QC just follows
    it, or reports honestly when it can't find one.
    """
    if args.dsi_studio_cmd:
        return args.dsi_studio_cmd

    if args.apptainer_image:
        image = Path(args.apptainer_image).resolve()
        if not image.exists():
            print(f"WARNING: --apptainer_image not found: {image}")
        print(f"Using explicitly requested DSI Studio image: {image.name}")
        os.environ["DSI_APPTAINER_IMAGE"] = str(image)
        return str(APPTAINER_WRAPPER)

    pin_file = find_project_pin(source_dir)
    if pin_file:
        try:
            pinned_image = Path(json.loads(pin_file.read_text())["image"])
        except Exception as e:
            print(f"WARNING: could not read {pin_file}: {e}; falling back to unpinned latest image")
            pinned_image = None
        if pinned_image and pinned_image.exists():
            print(f"Using this project's pinned DSI Studio image ({pin_file}): {pinned_image.name}")
            latest_link = DEFAULT_APPTAINER_IMAGES_DIR / "dsi_studio_latest.sif"
            if latest_link.exists() and latest_link.resolve() != pinned_image:
                print(
                    f"  Note: a newer DSI Studio build is available ({latest_link.resolve().name}) "
                    f"but this project stays pinned to {pinned_image.name}. Re-run the pipeline with "
                    f"--apptainer_image to upgrade deliberately, or pass --apptainer_image to this "
                    f"script for a one-off comparison."
                )
            os.environ["DSI_APPTAINER_IMAGE"] = str(pinned_image)
            return str(APPTAINER_WRAPPER)
        if pinned_image:
            print(f"WARNING: pinned image {pinned_image} (from {pin_file}) no longer exists; falling back")

    if args.apptainer:
        print("No project pin found; using unpinned dsi_studio_latest.sif")
        return str(APPTAINER_WRAPPER)

    print(f"No project pin found and --apptainer not set; falling back to bare-metal {DEFAULT_DSI_STUDIO_CMD}")
    return DEFAULT_DSI_STUDIO_CMD


def resolve_search_dir(source_dir: Path, subdir_name: str, patterns) -> Path:
    """Prefer the pipeline's dedicated aggregate subfolder (e.g. <output_dir>/src)
    over source_dir itself, so pointing QC at the pipeline's top-level output_dir
    finds files without recursing into every per-subject folder."""
    subdir = source_dir / subdir_name
    if subdir.is_dir() and any(subdir.glob(p) for p in patterns):
        return subdir
    return source_dir


def find_files(directory: Path, patterns):
    """Files directly inside directory (non-recursive), skipping broken symlinks
    (e.g. DataLad/git-annex content that hasn't been fetched yet). Used only to
    count matches and decide whether to run a pass -- the actual --source we
    hand to dsi_studio is a short wildcard pattern, not this expanded list (see
    run_qc: passing hundreds of comma-joined literal paths overflows a
    filesystem path-length check inside dsi_studio itself)."""
    return sorted({f for p in patterns for f in directory.glob(p) if f.exists()})


def find_subject_files(source_dir: Path, subdir_name: str, patterns):
    """Files one level under every sub-*/<subdir_name>/ folder (the DataLad
    per-subject layout), skipping broken symlinks. Counting only, see find_files."""
    return sorted({f for p in patterns for f in source_dir.glob(f"sub-*/{subdir_name}/{p}") if f.exists()})


def run_qc(dsi_studio_cmd, source_patterns, output_path: Path, check_btable: bool, file_count: int):
    # dsi_studio does its own wildcard expansion, so keep --source short (a few
    # glob patterns) rather than passing every matched file individually --
    # with a few hundred files that would overflow a filesystem path-length
    # check inside dsi_studio and it fails with "File name too long" without
    # writing any output.
    source_arg = ",".join(str(p) for p in source_patterns)
    cmd = [dsi_studio_cmd, "--action=qc", f"--source={source_arg}", f"--output={output_path}"]
    if check_btable:
        cmd.append("--check_btable=1")

    # Stream dsi_studio's own progress live instead of capturing it silently --
    # a single QC pass can take minutes, and without this the web GUI's live
    # job log shows nothing at all until the whole pass finishes. Keep a
    # rolling tail for the error message if it fails.
    tail = deque(maxlen=200)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line)
    proc.wait()

    if not output_path.exists():
        sys.stderr.write("".join(tail)[-2000:])
        raise RuntimeError(f"dsi_studio qc produced no output for {file_count} file(s)")
    return proc


def dereference_report_filenames(report_path: Path, files):
    """dsi_studio resolves symlinks before reporting a file, so DataLad/git-annex
    per-subject files show up in the FileName column as their opaque annex
    object name (e.g. MD5E-s...-<hash>.gqi.fz) instead of the friendly
    sub-XXXX_ses-Y name. Rewrite the report in place using the resolved name of
    each file we actually queried to map back to its original filename."""
    resolved_to_friendly = {f.resolve().name: f.name for f in files}
    with open(report_path, newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    for row in rows[1:]:
        if row and row[0] in resolved_to_friendly:
            row[0] = resolved_to_friendly[row[0]]
    with open(report_path, "w", newline="") as f:
        csv.writer(f, delimiter="\t").writerows(rows)


def load_tsv(path: Path):
    """Parse a dsi_studio qc.tsv report, dropping the trailing summary line."""
    with open(path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [row for row in reader if not any(v is None for v in row.values())]


def summarize_src(rows):
    flagged = []
    for row in rows:
        bad_slices = int(row.get("#bad slices") or 0)
        outlier = int(row.get("outlier") or 0)
        if bad_slices > 0 or outlier > 0:
            flagged.append((row["file name"], bad_slices, outlier))
    return flagged


def summarize_fib(rows, min_coherence, min_r2):
    # R2 (QSDR) is only reported for QSDR reconstructions -- GQI rows leave it
    # blank. Treating a blank as 0 would flag every GQI file as failing R2,
    # which isn't a real signal, so only apply the R2 threshold when a value
    # was actually reported.
    flagged = []
    for row in rows:
        coherence = float(row.get("Coherence Index") or 0)
        r2_raw = row.get("R2 (QSDR)")
        low_coherence = coherence < min_coherence
        low_r2 = bool(r2_raw) and float(r2_raw) < min_r2
        if low_coherence or low_r2:
            flagged.append((row["FileName"], coherence, float(r2_raw) if r2_raw else None))
    return flagged


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_dir", help="Directory containing .sz/.src.gz and/or .fz files")
    parser.add_argument("--output_dir", default="tmp/qc", help="Where to write qc tsv reports (default: tmp/qc)")
    parser.add_argument("--dsi_studio_cmd", default=None, help="Path to a specific dsi_studio executable, bypassing project pinning entirely")
    parser.add_argument("--apptainer", action="store_true", help="If no project pin is found, use the unpinned dsi_studio_latest.sif instead of falling back to bare metal")
    parser.add_argument("--apptainer_image", help="Check against this specific .sif image instead of the project's pin (one-off comparison; does not change the pin)")
    parser.add_argument("--check_btable", type=int, default=1, choices=[0, 1], help="Passed through to the SRC qc pass (default: 1)")
    parser.add_argument("--skip_src", action="store_true", help="Skip the .sz/.src.gz pass")
    parser.add_argument("--skip_fib", action="store_true", help="Skip the .fz pass")
    parser.add_argument("--min_coherence", type=float, default=0.7, help="Flag FIB files below this Coherence Index (default: 0.7)")
    parser.add_argument("--min_r2", type=float, default=0.6, help="Flag FIB files below this R2 (default: 0.6)")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        parser.error(f"Not a directory: {source_dir}")

    dsi_studio_cmd = resolve_dsi_studio_cmd(args, source_dir)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_src:
        src_dir = resolve_search_dir(source_dir, "src", SRC_PATTERNS)
        src_files = find_files(src_dir, SRC_PATTERNS)
        if src_files:
            out_path = output_dir / "sz_qc.tsv"
            print(f"Running SRC QC on {len(src_files)} file(s) in {src_dir} -> {out_path}")
            src_patterns = [src_dir / p for p in SRC_PATTERNS]
            run_qc(dsi_studio_cmd, src_patterns, out_path, bool(args.check_btable), len(src_files))
            dereference_report_filenames(out_path, src_files)
            rows = load_tsv(out_path)
            flagged = summarize_src(rows)
            print(f"  {len(rows)} file(s) checked, {len(flagged)} flagged (bad slices > 0 or outlier)")
            for name, bad_slices, outlier in flagged:
                print(f"  ⚠️  {name}: bad_slices={bad_slices} outlier={outlier}")
        else:
            print(f"No .sz/.src.gz files found in {src_dir}, skipping SRC QC")

    if not args.skip_fib:
        fib_dir = resolve_search_dir(source_dir, "fib", FIB_PATTERNS)
        fib_passes = [
            ("QSDR", find_files(fib_dir, FIB_PATTERNS), [fib_dir / p for p in FIB_PATTERNS], output_dir / "fz_qc_qsdr.tsv"),
            ("GQI", find_subject_files(source_dir, "fib", FIB_PATTERNS), [source_dir / f"sub-*/fib/{p}" for p in FIB_PATTERNS], output_dir / "fz_qc_gqi.tsv"),
        ]
        for label, fib_files, fib_patterns, out_path in fib_passes:
            if not fib_files:
                print(f"No {label} .fz files found under {source_dir}, skipping {label} FIB QC")
                continue
            print(f"Running {label} FIB QC on {len(fib_files)} file(s) -> {out_path}")
            run_qc(dsi_studio_cmd, fib_patterns, out_path, check_btable=False, file_count=len(fib_files))
            dereference_report_filenames(out_path, fib_files)
            rows = load_tsv(out_path)
            flagged = summarize_fib(rows, args.min_coherence, args.min_r2)
            print(f"  {len(rows)} file(s) checked, {len(flagged)} flagged (Coherence < {args.min_coherence} or R2 < {args.min_r2})")
            for name, coherence, r2 in flagged:
                r2_str = f"{r2:.3f}" if r2 is not None else "n/a"
                print(f"  ⚠️  {name}: coherence={coherence:.3f} r2={r2_str}")


if __name__ == "__main__":
    main()
