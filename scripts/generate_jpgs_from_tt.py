#!/usr/bin/env python3
"""Generate JPG previews from connectometry .tt.gz files.

This script is designed for headless/server use where the original batch run
may have produced tract files but failed to export screenshots.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence, Tuple


def find_tt_files(root: Path, include_all_tt: bool) -> List[Path]:
    """Collect tract files recursively and return a stable sorted list."""
    if include_all_tt:
        candidates = root.rglob("*.tt.gz")
    else:
        # Default to connectometry outputs used by the interactive viewer.
        candidates = list(root.rglob("*.inc.tt.gz")) + list(root.rglob("*.dec.tt.gz"))
        return sorted(set(candidates))
    return sorted(candidates)


def expected_jpg_path(tt_path: Path) -> Path:
    """Map xxx.tt.gz -> xxx.jpg while preserving folder/name stem."""
    name = tt_path.name
    if name.endswith(".tt.gz"):
        return tt_path.with_name(name[:-6] + ".jpg")
    return tt_path.with_suffix(".jpg")


def build_dsi_command(
    dsi_studio_bin: str,
    tt_path: Path,
    jpg_path: Path,
    width: int,
    height: int,
    view: int,
) -> List[str]:
    """Create a DSI Studio vis command for one tract file."""
    # Current DSI Studio builds can load tracts from --source directly.
    # Use only visualization commands in --cmd for compatibility.
    cmd_chain = f"set_view,{view}+save_hd_screen,{jpg_path},{width} {height}"
    return [
        dsi_studio_bin,
        "--action=vis",
        f"--source={tt_path}",
        f"--cmd={cmd_chain}",
    ]


def maybe_wrap_xvfb(command: Sequence[str], force_xvfb: bool) -> List[str]:
    """Wrap command in xvfb-run if requested."""
    if not force_xvfb:
        return list(command)

    xvfb_path = shutil.which("xvfb-run")
    if not xvfb_path:
        raise SystemExit("--xvfb requested but xvfb-run was not found in PATH")

    return [xvfb_path, "-a", *command]


def run_one(
    command: Sequence[str],
    tt_path: Path,
    jpg_path: Path,
    timeout_s: int,
    verbose: bool,
) -> Tuple[bool, str]:
    """Run one render job and report success/error message."""
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s}s"
    except Exception as exc:
        return False, f"execution error: {exc}"

    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"return code {proc.returncode}"
        return False, msg

    # DSI Studio may return 0 but still not save the output image.
    if not jpg_path.exists() or jpg_path.stat().st_size == 0:
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return False, "DSI Studio returned success but JPG is missing/empty. " + out[-500:].strip()

    if verbose:
        print(f"  OK: {tt_path} -> {jpg_path}")
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate JPG previews for .tt.gz files using DSI Studio vis action."
    )
    parser.add_argument("root", help="Root folder to scan recursively for tract files")
    parser.add_argument(
        "--dsi-studio",
        dest="dsi_studio",
        default="dsi_studio",
        help="DSI Studio executable path (default: dsi_studio)",
    )
    parser.add_argument("--width", type=int, default=1024, help="Output image width (default: 1024)")
    parser.add_argument("--height", type=int, default=800, help="Output image height (default: 800)")
    parser.add_argument("--view", type=int, default=2, help="DSI Studio view index (default: 2)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout in seconds per image render (default: 120)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing JPG files (default: skip existing)",
    )
    parser.add_argument(
        "--include-all-tt",
        action="store_true",
        help="Include all *.tt.gz files (default: only *.inc.tt.gz and *.dec.tt.gz)",
    )
    parser.add_argument(
        "--xvfb",
        action="store_true",
        help="Run each DSI Studio call via xvfb-run -a (recommended on headless servers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run, but do not execute DSI Studio",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce per-file output",
    )

    args = parser.parse_args()
    verbose = not args.quiet

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root does not exist: {root}")

    if not shutil.which(args.dsi_studio) and not Path(args.dsi_studio).exists():
        raise SystemExit(
            f"DSI Studio executable not found: {args.dsi_studio}. "
            "Use --dsi-studio /path/to/dsi_studio"
        )

    tt_files = find_tt_files(root, include_all_tt=args.include_all_tt)
    if not tt_files:
        print("No matching .tt.gz files found.")
        return

    print(f"Found {len(tt_files)} tract files under {root}")
    print(
        "Mode: "
        + ("all *.tt.gz" if args.include_all_tt else "*.inc.tt.gz + *.dec.tt.gz")
        + (", xvfb enabled" if args.xvfb else "")
    )

    success = 0
    skipped = 0
    failed = 0

    for idx, tt_path in enumerate(tt_files, start=1):
        jpg_path = expected_jpg_path(tt_path)

        if jpg_path.exists() and not args.overwrite:
            skipped += 1
            if verbose:
                print(f"[{idx}/{len(tt_files)}] SKIP existing: {jpg_path}")
            continue

        dsi_cmd = build_dsi_command(
            args.dsi_studio,
            tt_path,
            jpg_path,
            width=args.width,
            height=args.height,
            view=args.view,
        )
        full_cmd = maybe_wrap_xvfb(dsi_cmd, args.xvfb)

        if args.dry_run:
            print(f"[{idx}/{len(tt_files)}] DRY-RUN: {' '.join(full_cmd)}")
            continue

        if verbose:
            print(f"[{idx}/{len(tt_files)}] Rendering {tt_path.name} -> {jpg_path.name}")

        ok, err = run_one(
            full_cmd,
            tt_path=tt_path,
            jpg_path=jpg_path,
            timeout_s=args.timeout,
            verbose=verbose,
        )
        if ok:
            success += 1
        else:
            failed += 1
            print(f"  FAIL: {tt_path}")
            print(f"    {err}")

    print("\nSummary")
    print(f"  success: {success}")
    print(f"  skipped: {skipped}")
    print(f"  failed : {failed}")

    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()