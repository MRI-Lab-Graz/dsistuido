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

Optionally also ingests qsiprep's own per-session QC (<qsiprep_dir>/sub-*/
[ses-*/]dwi/*_desc-image_qc.csv), via --qsiprep_dir. qsiprep computes this for
every subject/session already (it's the mandatory upstream step for this
pipeline), and it can catch damage DSI Studio's own SRC checks miss entirely:
on a real project, a session with crushed contrast across every b>0 shell
(caused by a handful of outlier voxels blowing out DSI Studio's SRC
auto-scaling) had #bad_slices/outlier == 0 in DSI Studio's own QC, but
qsiprep's t1post_dwi_contrast and mean_fd both flagged it clearly.

Severity, not a single pass/fail: every numeric metric (DWI contrast, bad
slices, outlier, coherence, R2, qsiprep contrast, mean FD, ...) is graded
against the *cohort's own distribution* from this same run (see
metric_severity()) into severe / moderate / mild / ok, rather than one fixed
absolute cutoff. A fixed cutoff alone either misses real distortion sitting
just above the line, or - the opposite failure, seen on a real project -
floods the output with near-identical "flagged" status for a trivially common
value (e.g. "#bad slices > 0" alone flagged 44% of one real cohort with no way
to tell a single incidental bad slice from a genuinely severe file) alongside
the rare, actually-severe cases. Percentile-based severity self-calibrates to
whatever cohort is actually being checked instead of a number tuned on a
different project, and gives a graded signal a human can actually triage.

Two consolidated outputs feed downstream tools (e.g. the web GUI's thumbnail
gallery and QC dashboard) instead of requiring a separate look at raw tsv/csv
reports:
    <output_dir>/qc_flags.json   - "sub-X_ses-Y" -> list of {source, metric,
                                    value, tier, reason}, tier in
                                    severe/moderate/mild, only for sessions
                                    with at least one non-"ok" metric.
    <output_dir>/qc_metrics.json - every metric's raw value (and tier) for
                                    every session that has it, regardless of
                                    tier - the full cohort distribution, for
                                    an MRIQC-style group scatter/box plot.

Every severe/moderate session's subject ID is also collected into
<output_dir>/flagged_subjects.txt (comma-joined, one line) so it can be pasted
straight into dsi_studio_pipeline.py's --subject to rerun just those subjects,
e.g. after regenerating them with a fixed mask/normalization step upstream.

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

# Severity bands, as the worst fraction of the cohort a value needs to sit in
# to earn each tier - e.g. "severe" means only ~2% of this same run's files
# are this bad or worse. Ordered worst-first; the first band a value's
# worst_fraction is under wins. Same bands apply to every metric.
SEVERITY_BANDS = (("severe", 0.02), ("moderate", 0.10), ("mild", 0.25))

_SUB_SES_RE = re.compile(r"(sub-[A-Za-z0-9]+)(?:_(ses-[A-Za-z0-9]+))?")


def qc_key(name: str) -> Optional[str]:
    """Normalize a SRC/FIB/qsiprep file_name (which carry different suffixes -
    '.src.gz.sz', '.odf.qsdr.fz', '_acq-multi', etc.) down to a common
    "sub-X_ses-Y" (or bare "sub-X") key, matching how thumbnail PNGs are
    named (see src_thumbnail.friendly_stem) - the shared key that lets
    qc_flags.json/qc_metrics.json be joined against the thumbnail gallery by
    subject/session regardless of which pass (SRC/FIB/qsiprep) produced it."""
    m = _SUB_SES_RE.match(name)
    if not m:
        return None
    sub, ses = m.group(1), m.group(2)
    return f"{sub}_{ses}" if ses else sub


def worst_fraction(values, value: float, worse_is_high: bool) -> float:
    """Fraction of `values` that is as bad or worse than `value` - e.g. 0.02
    means only the worst 2% of this cohort (including this one) reaches this
    value or beyond. Ties count as "as bad", matching the intuitive "how rare
    is a value this extreme" reading. O(n) per call, fine at QC-report scale
    (hundreds to a few thousand rows)."""
    n = len(values)
    if n == 0:
        return 1.0
    as_bad_or_worse = sum(1 for v in values if (v >= value if worse_is_high else v <= value))
    return as_bad_or_worse / n


def severity_tier(frac: float) -> str:
    for tier, cutoff in SEVERITY_BANDS:
        if frac < cutoff:
            return tier
    return "ok"


def metric_severity(values, value: float, worse_is_high: bool):
    """Returns (tier, worst_fraction) for one value against its cohort."""
    frac = worst_fraction(values, value, worse_is_high)
    return severity_tier(frac), frac


_TIER_RANK = {"ok": 0, "mild": 1, "moderate": 2, "severe": 3}


def worse_tier(a: str, b: str) -> str:
    return a if _TIER_RANK[a] >= _TIER_RANK[b] else b


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


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Each metric spec: (metric_key, label, column name in the source rows,
# worse_is_high). Shared by summarize_src/summarize_fib/summarize_qsiprep and
# by the metrics.json writer, so the severity grading and the group
# scatter/box plot are always built from exactly the same numbers.
SRC_METRICS = [
    ("src_dwi_contrast", "SRC: DWI contrast", "DWI contrast", False),
    ("src_bad_slices", "SRC: #bad slices", "#bad slices", True),
    ("src_outlier", "SRC: outlier", "outlier", True),
]
FIB_METRICS = [
    ("coherence", "FIB: Coherence Index", "Coherence Index", False),
    ("r2", "FIB: R2 (QSDR)", "R2 (QSDR)", False),
]
QSIPREP_METRICS = [
    ("qsiprep_dwi_contrast", "qsiprep: t1post DWI contrast", "t1post_dwi_contrast", False),
    ("qsiprep_mean_fd", "qsiprep: mean FD (mm)", "mean_fd", True),
]


def summarize_metrics(rows, name_col, metrics, source, name_prefix=""):
    """Generic pass: for every metric spec, grade every row's value against
    the *other rows in this same call* (the cohort for this pass) and return
    (flags, metric_points).

    flags: list of (name, tier, [(metric_key, value, tier, frac), ...]) for
    rows with at least one non-"ok" metric - only those make it into
    qc_flags.json.

    metric_points: {metric_key: {name: (value, tier)}} for every row that has
    a numeric value for that metric, regardless of tier - the full
    distribution, for qc_metrics.json/the group scatter plot.
    """
    columns = {}
    for metric_key, _label, column, worse_is_high in metrics:
        columns[metric_key] = [_to_float(row.get(column)) for row in rows if row.get(column) not in (None, "")]

    flags = []
    metric_points = {metric_key: {} for metric_key, *_ in metrics}
    for row in rows:
        name = name_prefix + (row.get(name_col) or "")
        row_metrics = []
        for metric_key, label, column, worse_is_high in metrics:
            raw = row.get(column)
            if raw in (None, ""):
                continue
            value = _to_float(raw)
            tier, frac = metric_severity(columns[metric_key], value, worse_is_high)
            metric_points[metric_key][name] = (value, tier)
            if tier != "ok":
                row_metrics.append((metric_key, label, value, tier, frac))
        if row_metrics:
            overall = "ok"
            for *_rest, tier, _frac in row_metrics:
                overall = worse_tier(overall, tier)
            flags.append((name, overall, row_metrics, source))
    return flags, metric_points


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
    parser.add_argument("--flagged_subjects_out", help="Write bare subject IDs (no 'sub-' prefix) for every severe/moderate file to this path, comma-joined on one line, ready to paste into dsi_studio_pipeline.py's --subject (default: <output_dir>/flagged_subjects.txt)")
    parser.add_argument("--qsiprep_dir", help="Also ingest qsiprep's own per-session QC (sub-*/[ses-*/]dwi/*_desc-image_qc.csv) from this directory - qsiprep already computes this for every subject/session, and it can catch damage DSI Studio's own SRC checks miss. Omit to skip this pass entirely.")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        parser.error(f"Not a directory: {source_dir}")

    dsi_studio_cmd = resolve_dsi_studio_cmd(args, source_dir)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    flagged_names = set()  # every filename with a severe/moderate metric -- feeds flagged_subjects_out
    qc_flags: dict = {}  # "sub-X_ses-Y" -> list of {source, metric, value, tier, reason} -- feeds qc_flags.json
    qc_metrics: dict = {}  # metric_key -> {"label":..., "worse_is_high":..., "values": {name: {"value":..., "tier":...}}}

    def record_metric_points(metric_key, label, worse_is_high, points):
        bucket = qc_metrics.setdefault(metric_key, {"label": label, "worse_is_high": worse_is_high, "values": {}})
        for name, (value, tier) in points.items():
            key = qc_key(name)
            if key:
                bucket["values"][key] = {"value": value, "tier": tier}

    def add_flags(flags):
        for name, overall_tier, row_metrics, source in flags:
            metric_str = " ".join(f"{mk}={v:.3f}[{t}]" for mk, _label, v, t, _f in row_metrics)
            print(f"  {'⚠️ ' if overall_tier == 'severe' else '• '}{name}: {overall_tier} - {metric_str}")
            if overall_tier in ("severe", "moderate"):
                flagged_names.add(name)
            key = qc_key(name)
            if not key:
                continue
            for metric_key, label, value, tier, frac in row_metrics:
                reason = f"{label}={value:.3f} (worst {frac * 100:.0f}% of cohort)"
                qc_flags.setdefault(key, []).append({
                    "source": source, "metric": metric_key, "value": value, "tier": tier, "reason": reason,
                })

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
            flags, metric_points = summarize_metrics(rows, "file name", SRC_METRICS, "src")
            print(f"  {len(rows)} file(s) checked, {len(flags)} with at least one non-ok metric:")
            add_flags(flags)
            for metric_key, label, _column, worse_is_high in SRC_METRICS:
                record_metric_points(metric_key, label, worse_is_high, metric_points[metric_key])
        else:
            print(f"No .sz/.src.gz files found in {src_dir}, skipping SRC QC")

    if not args.skip_fib:
        fib_dir = resolve_search_dir(source_dir, "fib", FIB_PATTERNS)
        fib_passes = [
            ("QSDR", find_files(fib_dir, FIB_PATTERNS), [fib_dir / p for p in FIB_PATTERNS], output_dir / "fz_qc_qsdr.tsv"),
            ("GQI", find_subject_files(source_dir, "fib", FIB_PATTERNS), [source_dir / f"sub-*/fib/{p}" for p in FIB_PATTERNS], output_dir / "fz_qc_gqi.tsv"),
        ]
        for label_name, fib_files, fib_patterns, out_path in fib_passes:
            if not fib_files:
                print(f"No {label_name} .fz files found under {source_dir}, skipping {label_name} FIB QC")
                continue
            print(f"Running {label_name} FIB QC on {len(fib_files)} file(s) -> {out_path}")
            run_qc(dsi_studio_cmd, fib_patterns, out_path, check_btable=False, file_count=len(fib_files))
            dereference_report_filenames(out_path, fib_files)
            rows = load_tsv(out_path)
            source = f"fib_{label_name.lower()}"
            flags, metric_points = summarize_metrics(rows, "FileName", FIB_METRICS, source)
            print(f"  {len(rows)} file(s) checked, {len(flags)} with at least one non-ok metric:")
            add_flags(flags)
            for metric_key, label, _column, worse_is_high in FIB_METRICS:
                record_metric_points(f"{source}_{metric_key}", f"{label_name} {label}", worse_is_high, metric_points[metric_key])

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
                flags, metric_points = summarize_metrics(rows, "file_name", QSIPREP_METRICS, "qsiprep")
                print(f"  {len(rows)} session(s) checked, {len(flags)} with at least one non-ok metric:")
                add_flags(flags)
                for metric_key, label, _column, worse_is_high in QSIPREP_METRICS:
                    record_metric_points(metric_key, label, worse_is_high, metric_points[metric_key])
            else:
                print(f"No desc-image_qc.csv files found under {qsiprep_dir}, skipping qsiprep QC pass")

    if qc_flags:
        flags_path = output_dir / "qc_flags.json"
        flags_path.write_text(json.dumps(qc_flags, indent=2, sort_keys=True))
        print(f"\n{len(qc_flags)} subject/session(s) with at least one non-ok metric -> {flags_path}")

    if qc_metrics:
        metrics_path = output_dir / "qc_metrics.json"
        metrics_path.write_text(json.dumps(qc_metrics, indent=2, sort_keys=True))
        print(f"Full per-metric distribution ({len(qc_metrics)} metric(s)) -> {metrics_path}")

    if flagged_names:
        subjects = sorted({
            name.split("_")[0][len("sub-"):]
            for name in flagged_names
            if name.startswith("sub-")
        })
        if subjects:
            out_path = Path(args.flagged_subjects_out).resolve() if args.flagged_subjects_out else output_dir / "flagged_subjects.txt"
            out_path.write_text(",".join(subjects) + "\n")
            print(f"\n{len(subjects)} subject(s) severe/moderate across all passes -> {out_path}")
            print(f"Rerun just these with e.g.:")
            print(f"  python scripts/pipeline/dsi_studio_pipeline.py ... --subject {','.join(subjects)} --force fib")
            print(
                "Note: percentile-based severity is relative to *this* cohort - if every subject in a run "
                "shares the same problem, none of them will stand out as severe. Also spot-check with the "
                "thumbnail gallery and the QC dashboard's scatter/box plots, and check whether the same "
                "subject/session looks off in qsiprep's own output (registration/normalization QC) since "
                "that's a common upstream source of this kind of distortion."
            )


if __name__ == "__main__":
    main()
