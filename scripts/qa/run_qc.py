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

Every flagged FIB/SRC file's subject ID is also collected into
<output_dir>/flagged_subjects.txt (comma-joined, one line) so it can be pasted
straight into dsi_studio_pipeline.py's --subject to rerun just those subjects,
e.g. after regenerating them with a fixed mask/normalization step upstream.

Optionally also ingests qsiprep's own per-session QC (<qsiprep_dir>/sub-*/
[ses-*/]dwi/*_desc-image_qc.csv), via --qsiprep_dir. qsiprep computes this for
every subject/session already (it's the mandatory upstream step for this
pipeline), and it can catch damage DSI Studio's own SRC checks miss entirely:
on a real project, a session with crushed contrast across every b>0 shell
(caused by a handful of outlier voxels blowing out DSI Studio's SRC
auto-scaling) had #bad_slices/outlier == 0 in DSI Studio's own QC, but
qsiprep's t1post_dwi_contrast and mean_fd both flagged it clearly. See
summarize_qsiprep().

Every flagged/borderline file from every pass (SRC, FIB, qsiprep) is also
collected into <output_dir>/qc_flags.json, keyed by "sub-X_ses-Y" (or bare
"sub-X" if no session), so downstream tools - e.g. the web GUI's thumbnail
gallery - can show *why* a given subject/session is worth a second look
right next to its slice image, instead of requiring a separate look at raw
tsv/csv reports.

Usage:
    python scripts/qa/run_qc.py /data/local/134_AF19/derivatives/dsistudio
    python scripts/qa/run_qc.py /data/local/122_AF17/derivatives/dsistudio/fib --skip_src
    python scripts/qa/run_qc.py /path/to/dir --output_dir tmp/qc
    python scripts/qa/run_qc.py /path/to/dir --qsiprep_dir /data/local/129_PK01/derivatives/qsiprep
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Optional

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
QSIPREP_QC_PATTERNS = ["sub-*/dwi/*_desc-image_qc.csv", "sub-*/ses-*/dwi/*_desc-image_qc.csv"]

_SUB_SES_RE = re.compile(r"(sub-[A-Za-z0-9]+)(?:_(ses-[A-Za-z0-9]+))?")


def qc_key(name: str) -> Optional[str]:
    """Normalize a SRC/FIB/qsiprep file_name (which carry different suffixes -
    '.src.gz.sz', '.odf.qsdr.fz', '_acq-multi', etc.) down to a common
    "sub-X_ses-Y" (or bare "sub-X") key, matching how thumbnail PNGs are
    named (see src_thumbnail.friendly_stem) - the shared key that lets
    qc_flags.json be joined against the thumbnail gallery by subject/session
    regardless of which pass (SRC/FIB/qsiprep) produced the flag."""
    m = _SUB_SES_RE.match(name)
    if not m:
        return None
    sub, ses = m.group(1), m.group(2)
    return f"{sub}_{ses}" if ses else sub


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


def summarize_src(rows, min_dwi_contrast, borderline_margin=0.0):
    # DWI contrast is DSI Studio's own signal-vs-noise metric, and it can catch
    # damage that #bad slices/outlier miss entirely: a handful of extreme-
    # intensity outlier voxels in a b0 volume (e.g. a susceptibility/lipid
    # spike near the orbits) forces DSI Studio's 8-bit auto-scaling during SRC
    # conversion to crush contrast across the *whole* volume, every b>0 shell
    # included - visible in DSI Studio's own GUI as near-black DWI slices.
    # Confirmed against a real project: the file with the worst DWI contrast
    # in the whole cohort (1.197, next-worst 1.24, cohort median 2.25) had
    # bad_slices=1, outlier=0 - i.e. invisible to the existing checks despite
    # being the single worst file in the dataset. Same borderline_margin idea
    # as summarize_fib: a fixed cutoff alone is known to miss real distortion
    # sitting just above the line.
    flagged = []
    borderline = []
    for row in rows:
        bad_slices = int(row.get("#bad slices") or 0)
        outlier = int(row.get("outlier") or 0)
        contrast = float(row.get("DWI contrast") or 0)
        low_contrast = contrast < min_dwi_contrast
        if bad_slices > 0 or outlier > 0 or low_contrast:
            flagged.append((row["file name"], bad_slices, outlier, contrast))
        elif borderline_margin > 0 and contrast < min_dwi_contrast + borderline_margin:
            borderline.append((row["file name"], contrast))
    return flagged, borderline


def summarize_fib(rows, min_coherence, min_r2, borderline_margin=0.0):
    # R2 (QSDR) is only reported for QSDR reconstructions -- GQI rows leave it
    # blank. Treating a blank as 0 would flag every GQI file as failing R2,
    # which isn't a real signal, so only apply the R2 threshold when a value
    # was actually reported.
    #
    # A fixed cutoff alone is known to miss real, visually-obvious distortion:
    # on a real project, two subjects an analyst flagged by eye had R2 sitting
    # right at the cohort mean (~0.71-0.72, only just above the 0.6 default
    # cutoff) while several other equally-mediocre files near that same value
    # were never manually flagged either way - the metric doesn't cleanly
    # separate those cases. borderline_margin surfaces files within that
    # margin *above* the cutoff as a second, lower-confidence tier so a human
    # can eyeball the close calls instead of trusting the hard cutoff alone.
    flagged = []
    borderline = []
    for row in rows:
        coherence = float(row.get("Coherence Index") or 0)
        r2_raw = row.get("R2 (QSDR)")
        r2 = float(r2_raw) if r2_raw else None
        low_coherence = coherence < min_coherence
        low_r2 = r2 is not None and r2 < min_r2
        if low_coherence or low_r2:
            flagged.append((row["FileName"], coherence, r2))
        elif borderline_margin > 0 and (
            coherence < min_coherence + borderline_margin
            or (r2 is not None and r2 < min_r2 + borderline_margin)
        ):
            borderline.append((row["FileName"], coherence, r2))
    return flagged, borderline


def find_qsiprep_image_qc(qsiprep_dir: Path):
    """Every qsiprep desc-image_qc.csv under qsiprep_dir - one per subject
    (flat layout) or per subject/session (session layout), written by qsiprep
    itself as part of its own preprocessing QC, no extra run required."""
    return sorted({f for p in QSIPREP_QC_PATTERNS for f in qsiprep_dir.glob(p) if f.exists()})


def load_qsiprep_qc(files):
    """Concatenate rows from every desc-image_qc.csv file found. Each file
    already has its own file_name/subject_id/session_id columns, so no
    per-file tagging is needed here."""
    rows = []
    for f in files:
        with open(f, newline="") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def summarize_qsiprep(rows, min_dwi_contrast, max_mean_fd, borderline_margin=0.0):
    # qsiprep computes its own DWI contrast (t1post_dwi_contrast, on the same
    # post-registration data DSI Studio's SRC is built from) and mean framewise
    # displacement (motion) for every subject/session already - this pass is
    # "free" in that it re-runs nothing, just reads what qsiprep already wrote.
    # It matters because it can flag exactly what DSI Studio's own SRC QC
    # misses: confirmed on a real project, a session with a b0 outlier-voxel
    # cluster crushing contrast across every b>0 shell had DSI Studio's own
    # #bad slices/outlier both at 0, but qsiprep's t1post_dwi_contrast (1.128
    # vs cohort median 1.91) and mean_fd (0.499 vs cohort median 0.153, ~p98)
    # both flagged it independently. Same borderline_margin idea as
    # summarize_fib/summarize_src - a fixed cutoff alone is known to miss real
    # distortion sitting just above the line.
    flagged = []
    borderline = []
    for row in rows:
        name = row.get("file_name") or ""
        try:
            contrast = float(row.get("t1post_dwi_contrast") or 0)
        except ValueError:
            contrast = 0.0
        try:
            mean_fd = float(row.get("mean_fd") or 0)
        except ValueError:
            mean_fd = 0.0
        low_contrast = contrast < min_dwi_contrast
        high_motion = mean_fd > max_mean_fd
        if low_contrast or high_motion:
            flagged.append((name, contrast, mean_fd))
        elif borderline_margin > 0 and (
            contrast < min_dwi_contrast + borderline_margin
            or mean_fd > max_mean_fd - borderline_margin
        ):
            borderline.append((name, contrast, mean_fd))
    return flagged, borderline


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
    parser.add_argument("--min_dwi_contrast", type=float, default=1.5, help="Flag SRC files below this DWI contrast (default: 1.5) - catches cases like a susceptibility/lipid outlier voxel crushing 8-bit auto-scaling across a whole volume, which #bad slices/outlier alone can miss")
    parser.add_argument("--min_coherence", type=float, default=0.7, help="Flag FIB files below this Coherence Index (default: 0.7)")
    parser.add_argument("--min_r2", type=float, default=0.6, help="Flag FIB files below this R2 (default: 0.6)")
    parser.add_argument("--borderline_margin", type=float, default=0.05, help="Also list SRC/FIB files within this margin ABOVE min_dwi_contrast/min_coherence/min_r2 as a lower-confidence 'borderline' tier, for manual review (default: 0.05; set 0 to disable). Fixed cutoffs alone are known to miss real distortion sitting just above the line - see summarize_fib().")
    parser.add_argument("--flagged_subjects_out", help="Write bare subject IDs (no 'sub-' prefix) for every flagged (not borderline) file to this path, comma-joined on one line, ready to paste into dsi_studio_pipeline.py's --subject (default: <output_dir>/flagged_subjects.txt)")
    parser.add_argument("--qsiprep_dir", help="Also ingest qsiprep's own per-session QC (sub-*/[ses-*/]dwi/*_desc-image_qc.csv) from this directory - qsiprep already computes this for every subject/session, and it can catch damage DSI Studio's own SRC checks miss (see summarize_qsiprep()). Omit to skip this pass entirely.")
    parser.add_argument("--min_dwi_contrast_qsiprep", type=float, default=1.3, help="Flag sessions below this qsiprep t1post_dwi_contrast (default: 1.3, calibrated against a real cohort's ~5%% worst-contrast tail). Only used with --qsiprep_dir.")
    parser.add_argument("--max_mean_fd", type=float, default=0.4, help="Flag sessions above this qsiprep mean framewise displacement in mm (default: 0.4, calibrated against a real cohort's ~p98 motion tail). Only used with --qsiprep_dir.")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        parser.error(f"Not a directory: {source_dir}")

    dsi_studio_cmd = resolve_dsi_studio_cmd(args, source_dir)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    flagged_names = set()  # every filename flagged by any pass -- feeds flagged_subjects_out
    qc_flags: dict = {}  # "sub-X_ses-Y" -> list of {source, reason, tier} -- feeds qc_flags.json

    def add_flag(name, source, reason, tier="flagged"):
        key = qc_key(name)
        if key:
            qc_flags.setdefault(key, []).append({"source": source, "reason": reason, "tier": tier})

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
            flagged, borderline = summarize_src(rows, args.min_dwi_contrast, args.borderline_margin)
            print(
                f"  {len(rows)} file(s) checked, {len(flagged)} flagged "
                f"(bad slices > 0, outlier > 0, or DWI contrast < {args.min_dwi_contrast})"
            )
            for name, bad_slices, outlier, contrast in flagged:
                print(f"  ⚠️  {name}: bad_slices={bad_slices} outlier={outlier} dwi_contrast={contrast:.3f}")
                flagged_names.add(name)
                add_flag(name, "src", f"bad_slices={bad_slices} outlier={outlier} dwi_contrast={contrast:.3f}")
            if borderline:
                print(
                    f"  {len(borderline)} more file(s) borderline on DWI contrast (within "
                    f"{args.borderline_margin} of the cutoff) - not auto-flagged, worth a manual look:"
                )
                for name, contrast in borderline:
                    print(f"    • {name}: dwi_contrast={contrast:.3f}")
                    add_flag(name, "src", f"dwi_contrast={contrast:.3f} (borderline)", tier="borderline")
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
            flagged, borderline = summarize_fib(rows, args.min_coherence, args.min_r2, args.borderline_margin)
            print(f"  {len(rows)} file(s) checked, {len(flagged)} flagged (Coherence < {args.min_coherence} or R2 < {args.min_r2})")
            for name, coherence, r2 in flagged:
                r2_str = f"{r2:.3f}" if r2 is not None else "n/a"
                print(f"  ⚠️  {name}: coherence={coherence:.3f} r2={r2_str}")
                flagged_names.add(name)
                add_flag(name, f"fib_{label.lower()}", f"coherence={coherence:.3f} r2={r2_str}")
            if borderline:
                print(f"  {len(borderline)} more file(s) borderline (within {args.borderline_margin} of the cutoff) - not auto-flagged, worth a manual look:")
                for name, coherence, r2 in borderline:
                    r2_str = f"{r2:.3f}" if r2 is not None else "n/a"
                    print(f"    • {name}: coherence={coherence:.3f} r2={r2_str}")
                    add_flag(name, f"fib_{label.lower()}", f"coherence={coherence:.3f} r2={r2_str} (borderline)", tier="borderline")

    if args.qsiprep_dir:
        qsiprep_dir = Path(args.qsiprep_dir).resolve()
        if not qsiprep_dir.is_dir():
            print(f"WARNING: --qsiprep_dir not found: {qsiprep_dir}, skipping qsiprep QC pass")
        else:
            qc_files = find_qsiprep_image_qc(qsiprep_dir)
            if qc_files:
                rows = load_qsiprep_qc(qc_files)
                out_path = output_dir / "qsiprep_qc.csv"
                with open(out_path, "w", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"Ingested {len(qc_files)} qsiprep image_qc.csv file(s), {len(rows)} row(s) -> {out_path}")
                flagged, borderline = summarize_qsiprep(
                    rows, args.min_dwi_contrast_qsiprep, args.max_mean_fd, args.borderline_margin
                )
                print(
                    f"  {len(rows)} session(s) checked, {len(flagged)} flagged "
                    f"(t1post_dwi_contrast < {args.min_dwi_contrast_qsiprep} or mean_fd > {args.max_mean_fd})"
                )
                for name, contrast, mean_fd in flagged:
                    print(f"  ⚠️  {name}: t1post_dwi_contrast={contrast:.3f} mean_fd={mean_fd:.3f}")
                    flagged_names.add(name)
                    add_flag(name, "qsiprep", f"t1post_dwi_contrast={contrast:.3f} mean_fd={mean_fd:.3f}")
                if borderline:
                    print(
                        f"  {len(borderline)} more session(s) borderline (within {args.borderline_margin} of "
                        "a cutoff) - not auto-flagged, worth a manual look:"
                    )
                    for name, contrast, mean_fd in borderline:
                        print(f"    • {name}: t1post_dwi_contrast={contrast:.3f} mean_fd={mean_fd:.3f}")
                        add_flag(
                            name, "qsiprep",
                            f"t1post_dwi_contrast={contrast:.3f} mean_fd={mean_fd:.3f} (borderline)",
                            tier="borderline",
                        )
            else:
                print(f"No desc-image_qc.csv files found under {qsiprep_dir}, skipping qsiprep QC pass")

    if qc_flags:
        flags_path = output_dir / "qc_flags.json"
        flags_path.write_text(json.dumps(qc_flags, indent=2, sort_keys=True))
        print(f"\n{len(qc_flags)} subject/session(s) with at least one flag or borderline note -> {flags_path}")

    if flagged_names:
        subjects = sorted({
            name.split("_")[0][len("sub-"):]
            for name in flagged_names
            if name.startswith("sub-")
        })
        if subjects:
            out_path = Path(args.flagged_subjects_out).resolve() if args.flagged_subjects_out else output_dir / "flagged_subjects.txt"
            out_path.write_text(",".join(subjects) + "\n")
            print(f"\n{len(subjects)} subject(s) flagged across all passes -> {out_path}")
            print(f"Rerun just these with e.g.:")
            print(f"  python scripts/pipeline/dsi_studio_pipeline.py ... --subject {','.join(subjects)} --force fib")
            print(
                "Note: numeric QC thresholds don't catch every visually-obvious distortion (some "
                "flagged-by-eye subjects sit right at the cohort average on Coherence/R2) - also spot-check "
                "flagged and borderline subjects with the thumbnail/viewer tools in scripts/visualization/, "
                "and check whether the same subject/session looks off in qsiprep's own output (registration/"
                "normalization QC) since that's a common upstream source of this kind of distortion."
            )


if __name__ == "__main__":
    main()
