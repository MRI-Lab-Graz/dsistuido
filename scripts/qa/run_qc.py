#!/usr/bin/env python3
"""
Run DSI Studio's built-in QC (--action=qc) over SRC (.sz/.src.gz) and/or FIB
(.fz) files in a directory, and flag files worth a second look.

Wraps:
    dsi_studio --action=qc --source=*.sz,*.src.gz --check_btable=1 --output=<sz_qc.tsv>
    dsi_studio --action=qc --source=*.fz --output=<fz_qc.tsv>

Usage:
    python scripts/qa/run_qc.py /data/local/134_AF19/derivatives/dsistudio/src
    python scripts/qa/run_qc.py /data/local/122_AF17/derivatives/fz --skip_src
    python scripts/qa/run_qc.py /path/to/dir --apptainer --output_dir tmp/qc
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path

DEFAULT_DSI_STUDIO_CMD = "/data/local/software/dsi-studio/2025.04.16/dsi-studio/dsi_studio"
APPTAINER_WRAPPER = Path(__file__).resolve().parents[2] / "installation" / "apptainer" / "run_dsi_studio.sh"

SRC_PATTERNS = ["*.sz", "*.src.gz"]
FIB_PATTERNS = ["*.fz"]


def run_qc(dsi_studio_cmd, source_dir: Path, patterns, output_path: Path, check_btable: bool):
    source_glob = ",".join(str(source_dir / p) for p in patterns)
    cmd = [dsi_studio_cmd, "--action=qc", f"--source={source_glob}", f"--output={output_path}"]
    if check_btable:
        cmd.append("--check_btable=1")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if not output_path.exists():
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise RuntimeError(f"dsi_studio qc produced no output for {source_dir} ({patterns})")
    return result


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
    flagged = []
    for row in rows:
        coherence = float(row.get("Coherence Index") or 0)
        r2 = float(row.get("R2 (QSDR)") or 0)
        if coherence < min_coherence or r2 < min_r2:
            flagged.append((row["FileName"], coherence, r2))
    return flagged


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_dir", help="Directory containing .sz/.src.gz and/or .fz files")
    parser.add_argument("--output_dir", default="tmp/qc", help="Where to write qc tsv reports (default: tmp/qc)")
    parser.add_argument("--dsi_studio_cmd", default=DEFAULT_DSI_STUDIO_CMD, help="Path to dsi_studio executable")
    parser.add_argument("--apptainer", action="store_true", help="Run dsi_studio via the Apptainer image instead of the bare-metal install")
    parser.add_argument("--check_btable", type=int, default=1, choices=[0, 1], help="Passed through to the SRC qc pass (default: 1)")
    parser.add_argument("--skip_src", action="store_true", help="Skip the .sz/.src.gz pass")
    parser.add_argument("--skip_fib", action="store_true", help="Skip the .fz pass")
    parser.add_argument("--min_coherence", type=float, default=0.7, help="Flag FIB files below this Coherence Index (default: 0.7)")
    parser.add_argument("--min_r2", type=float, default=0.6, help="Flag FIB files below this R2 (default: 0.6)")
    args = parser.parse_args()

    dsi_studio_cmd = str(APPTAINER_WRAPPER) if args.apptainer else args.dsi_studio_cmd
    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        parser.error(f"Not a directory: {source_dir}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_src:
        src_files = [f for p in SRC_PATTERNS for f in source_dir.glob(p)]
        if src_files:
            out_path = output_dir / "sz_qc.tsv"
            print(f"Running SRC QC on {len(src_files)} file(s) -> {out_path}")
            run_qc(dsi_studio_cmd, source_dir, SRC_PATTERNS, out_path, bool(args.check_btable))
            rows = load_tsv(out_path)
            flagged = summarize_src(rows)
            print(f"  {len(rows)} file(s) checked, {len(flagged)} flagged (bad slices > 0 or outlier)")
            for name, bad_slices, outlier in flagged:
                print(f"  ⚠️  {name}: bad_slices={bad_slices} outlier={outlier}")
        else:
            print(f"No .sz/.src.gz files found in {source_dir}, skipping SRC QC")

    if not args.skip_fib:
        fib_files = [f for p in FIB_PATTERNS for f in source_dir.glob(p)]
        if fib_files:
            out_path = output_dir / "fz_qc.tsv"
            print(f"Running FIB QC on {len(fib_files)} file(s) -> {out_path}")
            run_qc(dsi_studio_cmd, source_dir, FIB_PATTERNS, out_path, check_btable=False)
            rows = load_tsv(out_path)
            flagged = summarize_fib(rows, args.min_coherence, args.min_r2)
            print(f"  {len(rows)} file(s) checked, {len(flagged)} flagged (Coherence < {args.min_coherence} or R2 < {args.min_r2})")
            for name, coherence, r2 in flagged:
                print(f"  ⚠️  {name}: coherence={coherence:.3f} r2={r2:.3f}")
        else:
            print(f"No .fz files found in {source_dir}, skipping FIB QC")


if __name__ == "__main__":
    main()
