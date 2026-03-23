#!/usr/bin/env python3
"""Generate JPG previews from connectometry .tt.gz files.

This script is designed for headless/server use where the original batch run
may have produced tract files but failed to export screenshots.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def source_for_tt(tt_path: Path) -> Path:
    """Return preferred vis source for a tract file.

    For connectometry outputs like:
      prefix.inc.tt.gz / prefix.dec.tt.gz
    prefer:
      prefix.t_statistics.fz
    """
    name = tt_path.name
    for suffix in (".inc.tt.gz", ".dec.tt.gz"):
        if name.endswith(suffix):
            prefix = name[: -len(suffix)]
            candidate = tt_path.with_name(prefix + ".t_statistics.fz")
            if candidate.exists():
                return candidate
            break
    return tt_path


def build_dsi_command_preferred(
    dsi_studio_bin: str,
    source_path: Path,
    tt_path: Path,
    jpg_path: Path,
    width: int,
    height: int,
) -> List[str]:
    """Create a DSI Studio vis command for one tract file."""
    # Use the connectometry statistics file as source to preserve the native
    # anatomical context/background, and load tracts from the tt.gz file.
    cmd_chain = f"open_tract,{tt_path}+save_hd_screen,{jpg_path},{width} {height}"
    return [
        dsi_studio_bin,
        "--action=vis",
        f"--source={source_path}",
        f"--cmd={cmd_chain}",
    ]


def build_dsi_command_fallback(
    dsi_studio_bin: str,
    tt_path: Path,
    jpg_path: Path,
    width: int,
    height: int,
) -> List[str]:
    """Build conservative vis command (older behavior, typically more stable)."""
    cmd_chain = f"set_view,2+save_hd_screen,{jpg_path},{width} {height}"
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


def run_with_fallback(
    primary_command: Sequence[str],
    fallback_command: Sequence[str],
    tt_path: Path,
    jpg_path: Path,
    timeout_s: int,
    verbose: bool,
) -> Tuple[bool, str]:
    """Try primary render mode, then fallback mode if needed."""
    ok, err = run_one(
        primary_command,
        tt_path=tt_path,
        jpg_path=jpg_path,
        timeout_s=timeout_s,
        verbose=verbose,
    )
    if ok:
        return True, ""

    # Retry once using conservative fallback command.
    if verbose:
        print(f"  Retrying with fallback mode for {tt_path.name}")
    ok2, err2 = run_one(
        fallback_command,
        tt_path=tt_path,
        jpg_path=jpg_path,
        timeout_s=timeout_s,
        verbose=verbose,
    )
    if ok2:
        return True, ""
    return False, f"primary failed: {err}; fallback failed: {err2}"


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
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel render workers (default: 1)",
    )

    args = parser.parse_args()
    verbose = not args.quiet

    # DSI Studio vis is often unstable when multiple GUI instances run
    # concurrently under xvfb on headless servers.
    if args.xvfb and args.jobs > 1:
        print(
            "Warning: --xvfb with --jobs > 1 can cause segmentation faults; "
            "forcing --jobs 1 for stability."
        )
        args.jobs = 1

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
    work_items: List[Tuple[Path, Path, List[str], List[str]]] = []

    for idx, tt_path in enumerate(tt_files, start=1):
        jpg_path = expected_jpg_path(tt_path)

        if jpg_path.exists() and not args.overwrite:
            skipped += 1
            if verbose:
                print(f"[{idx}/{len(tt_files)}] SKIP existing: {jpg_path}")
            continue

        dsi_cmd = build_dsi_command_preferred(
            args.dsi_studio,
            source_path=source_for_tt(tt_path),
            tt_path=tt_path,
            jpg_path=jpg_path,
            width=args.width,
            height=args.height,
        )
        fallback_dsi_cmd = build_dsi_command_fallback(
            args.dsi_studio,
            tt_path=tt_path,
            jpg_path=jpg_path,
            width=args.width,
            height=args.height,
        )
        full_cmd = maybe_wrap_xvfb(dsi_cmd, args.xvfb)
        fallback_full_cmd = maybe_wrap_xvfb(fallback_dsi_cmd, args.xvfb)

        if args.dry_run:
            print(f"[{idx}/{len(tt_files)}] DRY-RUN: {' '.join(full_cmd)}")
            continue

        work_items.append((tt_path, jpg_path, full_cmd, fallback_full_cmd))

    if args.dry_run:
        print("\nSummary")
        print(f"  success: {success}")
        print(f"  skipped: {skipped}")
        print(f"  failed : {failed}")
        return

    jobs = max(1, args.jobs)
    if jobs == 1:
        for idx, (tt_path, jpg_path, full_cmd, fallback_full_cmd) in enumerate(work_items, start=1):
            if verbose:
                print(f"[{idx}/{len(work_items)}] Rendering {tt_path.name} -> {jpg_path.name}")

            ok, err = run_with_fallback(
                primary_command=full_cmd,
                fallback_command=fallback_full_cmd,
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
    else:
        if verbose:
            print(f"Rendering {len(work_items)} files using {jobs} parallel workers")
        else:
            print(f"Parallel mode: {len(work_items)} files with {jobs} workers")

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            future_map = {
                executor.submit(
                    run_with_fallback,
                    full_cmd,
                    fallback_full_cmd,
                    tt_path,
                    jpg_path,
                    args.timeout,
                    False,
                ): (tt_path, jpg_path)
                for (tt_path, jpg_path, full_cmd, fallback_full_cmd) in work_items
            }

            completed = 0
            for future in as_completed(future_map):
                completed += 1
                tt_path, _ = future_map[future]
                try:
                    ok, err = future.result()
                except Exception as exc:
                    ok, err = False, f"worker error: {exc}"

                if ok:
                    success += 1
                else:
                    failed += 1
                    print(f"  FAIL: {tt_path}")
                    print(f"    {err}")

                if verbose and (completed % 10 == 0 or completed == len(work_items)):
                    print(
                        f"  Progress {completed}/{len(work_items)} "
                        f"(success={success}, failed={failed})"
                    )

    print("\nSummary")
    print(f"  success: {success}")
    print(f"  skipped: {skipped}")
    print(f"  failed : {failed}")

    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()