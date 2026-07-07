#!/usr/bin/env python3
"""
DSI Studio Preprocessing Pipeline

This script automates the preprocessing steps for DSI Studio starting from qsiprep outputs:
1. SRC file generation
2. Reconstruction (FIB file generation)
3. Connectometry Database creation

Usage:
    python scripts/pipeline/dsi_studio_pipeline.py --qsiprep_dir /path/to/qsiprep --output_dir ./dsi_studio_output
    
    # Skip existing files (useful for resuming interrupted runs)
    python scripts/pipeline/dsi_studio_pipeline.py ... --skip_existing
    
    # Force overwrite existing files (useful for regenerating corrupted files)
    python scripts/pipeline/dsi_studio_pipeline.py ... --skip_existing --force
"""

import os
import sys
import subprocess
import argparse
import logging
import random
import gzip
import shutil
import csv
import json
import shlex
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict

DEFAULT_APPTAINER_IMAGES_DIR = Path("/data/local/software/apptainer_images")

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for terminal output"""
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        if 'PILOT MODE' in record.msg or 'Pipeline completed' in record.msg:
            record.msg = f"{self.BOLD}{record.msg}{self.RESET}"
        return super().format(record)

def setup_logging(output_dir: Path):
    """Setup logging to file and stdout with colors"""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    # File handler (no colors)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # Console handler (with colors)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

class DSIStudioPipeline:
    def __init__(self, args):
        self.qsiprep_dir = Path(args.qsiprep_dir).resolve()
        self.output_dir = Path(args.output_dir).resolve()

        # BIDS-style project root: explicit --project_root wins; otherwise infer
        # from the common "<dataset>/derivatives/<pipeline>" layout, falling back
        # to the output dir's parent if that layout isn't detected.
        if args.project_root:
            self.project_root = Path(args.project_root).resolve()
        elif self.output_dir.parent.name == "derivatives":
            self.project_root = self.output_dir.parent.parent
        else:
            self.project_root = self.output_dir.parent

        # Read qsiprep-DataLad settings and (if needed) clone the source
        # *before* any mkdir happens below - 'datalad clone'/'git clone'
        # refuse to clone into an existing non-empty directory, and
        # self.code_dir.mkdir() a few lines down would otherwise pre-create
        # project_root (making it non-empty) before we ever get a chance to
        # clone into it.
        self.qsiprep_datalad_source = args.qsiprep_datalad_source
        self.qsiprep_datalad = args.qsiprep_datalad or bool(self.qsiprep_datalad_source)
        self.qsiprep_datalad_branch = args.qsiprep_datalad_branch
        if self.qsiprep_datalad:
            self._qsiprep_clone_root = self._ensure_qsiprep_clone()
        else:
            self._qsiprep_clone_root = None

        self.code_dir = self.project_root / "code" / "dsistudio"
        self.code_dir.mkdir(parents=True, exist_ok=True)

        # Set DSI Studio command path
        if args.dsi_studio_path:
            self.dsi_studio_cmd = str(Path(args.dsi_studio_path) / "dsi_studio")
        else:
            self.dsi_studio_cmd = args.dsi_studio_cmd

        # Atlas presence checks need a real DSI Studio install directory even
        # when --apptainer swaps self.dsi_studio_cmd for the container wrapper.
        self.atlas_reference_dir = Path(self.dsi_studio_cmd).parent

        self.use_datalad = args.datalad
        self.use_apptainer = args.apptainer or self.use_datalad  # datalad containers-run needs a registered container
        self._apptainer_image_arg = args.apptainer_image
        self.container_name = "dsi-studio"
        if self.use_apptainer:
            wrapper = Path(__file__).resolve().parents[2] / "installation" / "apptainer" / "run_dsi_studio.sh"
            self.dsi_studio_cmd = str(wrapper)
            if args.apptainer_bind:
                os.environ["DSI_APPTAINER_BIND"] = args.apptainer_bind

        self.method = args.method
        self.param0 = args.param0
        self.threads = args.threads
        self.db_name = args.db_name
        self.pilot = args.pilot
        self.verify_rawdata = args.verify_rawdata
        self.require_mask = args.require_mask
        self.require_t1w = args.require_t1w
        self.skip_existing = args.skip_existing
        self.force_arg = args.force
        # Parse force argument: can be 'database', 'diffs', 'src', 'fib', 'all', or None
        self.force_components = set()
        if self.force_arg:
            if self.force_arg == 'all':
                self.force_components = {'src', 'fib', 'diffs', 'database'}
            else:
                self.force_components.add(self.force_arg)
        self.session_filter = self._normalize_bids_filter(args.session, "ses")
        self.acq_filter = self._normalize_bids_filter(args.acq, "acq")
        self.space_filter = self._normalize_bids_filter(args.space, "space")
        self.min_file_age = args.min_file_age  # Minimum age in seconds
        self.dry_run = args.dry_run
        self.connectivity_only = args.connectivity_only
        # connectivity-only mode has nothing to do without the extraction step
        self.run_connectivity = args.run_connectivity or self.connectivity_only
        self.connectivity_config = Path(args.connectivity_config).resolve() if args.connectivity_config else None
        self.connectivity_output_dir = Path(args.connectivity_output_dir).resolve() if args.connectivity_output_dir else self.output_dir / "connectivity"
        self.connectivity_threads = args.connectivity_threads

        # (qsiprep_datalad/qsiprep_datalad_source/qsiprep_datalad_branch were
        # already read above, before project_root's directories were created.)
        # Opt-in, explicit push of *only* this run's own output subtree to
        # its remote - never touches qsiprep or any sibling dataset, and
        # never runs unless the user asks for it on this specific invocation.
        self.push_derivatives = args.push_derivatives

        # Optional rawdata path for cross-checking
        if args.rawdata_dir:
            self.rawdata_dir = Path(args.rawdata_dir).resolve()
        else:
            # Default: sibling rawdata directory
            self.rawdata_dir = self.qsiprep_dir.parent / "rawdata"
        
        # Per-subject output is always nested as <output_dir>/<subject_id>/
        # {src,fib,diff,reports}/... - this mirrors how qsiprep/DataLad
        # themselves organize per-subject data, and keeps one layout whether
        # or not --datalad is on (--datalad additionally makes each subject
        # folder its own nested DataLad dataset; without it, they're just
        # plain directories). See _ensure_subject_dir().
        self.connectivity_output_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = setup_logging(self.output_dir)
        self.logger.info(f"Starting DSI Studio Pipeline")
        self.logger.info(f"QSIPREP Dir: {self.qsiprep_dir}")
        self.logger.info(f"Output Dir: {self.output_dir}")

        if self.qsiprep_datalad:
            self._setup_qsiprep_datalad()

        pin_file_preexisted = (self.code_dir / "dsi_studio_image.json").exists()

        if self.use_apptainer:
            self._resolve_apptainer_image(self._apptainer_image_arg)

        if self.use_datalad:
            self._setup_datalad_superdataset(pin_file_preexisted)

        self._validate_dsi_studio()
        self._check_cuda_status()

        # Basic counters for a final summary
        self.stats = {
            "found": 0,
            "processed": 0,
            "skipped_missing": 0,
            "skipped_existing": 0,
            "src_ok": 0,
            "fib_ok": 0,
            "diff_ok": 0,
            "diff_failed": 0,
            "errors": [],
        }
        
        # Track processing details for HTML reports
        self.subject_details: Dict[str, List[Dict]] = {}

    def _validate_dsi_studio(self):
        """Validate DSI Studio installation"""
        try:
            result = subprocess.run([self.dsi_studio_cmd, "--version"], capture_output=True, text=True)
            self.logger.info(f"DSI Studio version: {result.stdout.strip()}")
        except Exception as e:
            self.logger.error(f"DSI Studio command '{self.dsi_studio_cmd}' not found or failed: {e}")
            if not self.dry_run:
                sys.exit(1)

    def _check_cuda_status(self):
        """Check CUDA availability via nvidia-smi; warn if unavailable."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
                if lines:
                    self.logger.info(f"CUDA detected; GPUs: {lines}")
                else:
                    self.logger.warning("nvidia-smi returned no GPU entries; CUDA may be unavailable.")
            else:
                self.logger.warning("nvidia-smi failed; proceeding without CUDA check.")
        except FileNotFoundError:
            self.logger.warning("nvidia-smi not found; skipping CUDA check.")
        except Exception as e:
            self.logger.warning(f"CUDA check encountered an issue: {e}")

    def _resolve_apptainer_image(self, explicit_image: Optional[str]):
        """Pick which Apptainer image this project uses, and pin it.

        A project (output_dir) is pinned to the exact image it first ran with,
        so re-running it later never silently picks up a newer DSI Studio
        build (the Mar->Jun build regression is exactly the failure mode this
        avoids). A brand-new project pins whatever 'latest' resolves to right
        now. Passing --apptainer_image explicitly is a deliberate version
        change and updates the pin. Either way, if a newer build than the
        pinned one is available, we log a one-line notice but never switch
        automatically.
        """
        pin_file = self.code_dir / "dsi_studio_image.json"
        latest_link = DEFAULT_APPTAINER_IMAGES_DIR / "dsi_studio_latest.sif"
        latest_target = latest_link.resolve() if latest_link.exists() else None

        pinned_image = None
        if pin_file.exists():
            try:
                data = json.loads(pin_file.read_text())
                candidate = Path(data.get("image", ""))
                if candidate.exists():
                    pinned_image = candidate
            except Exception as e:
                self.logger.warning(f"Could not read {pin_file}: {e}")

        if explicit_image:
            chosen = Path(explicit_image).resolve()
            if not chosen.exists():
                self.logger.warning(f"--apptainer_image not found: {chosen}")
            if pinned_image and chosen != pinned_image:
                self.logger.info(f"Switching this project's pinned DSI Studio image: {pinned_image.name} -> {chosen.name}")
            pinned_image = chosen
        elif pinned_image is None:
            if latest_target is None:
                raise RuntimeError(
                    "No Apptainer image found. Build one with installation/apptainer/build_image.sh"
                )
            pinned_image = latest_target
            self.logger.info(f"New project: pinning DSI Studio image to {pinned_image.name}")
        else:
            self.logger.info(f"Project pinned to DSI Studio image: {pinned_image.name}")

        try:
            pin_file.write_text(json.dumps({
                "image": str(pinned_image),
                "pinned_at": datetime.now().isoformat(),
            }, indent=2))
        except Exception as e:
            self.logger.warning(f"Could not write {pin_file}: {e}")

        if latest_target and latest_target != pinned_image:
            self.logger.info(
                f"Note: a newer DSI Studio build is available ({latest_target.name}) "
                f"but this project stays on {pinned_image.name}. "
                f"Re-run with --apptainer_image to upgrade deliberately."
            )

        self.apptainer_image_path = pinned_image
        os.environ["DSI_APPTAINER_IMAGE"] = str(pinned_image)

    def _setup_datalad_superdataset(self, pin_file_preexisted: bool):
        """Make project_root a DataLad superdataset and register the pinned
        container in it, so every dsi_studio call can be run through
        `datalad containers-run` for full command/provenance tracking.

        Only bootstraps brand-new projects. If this project already has a
        version pin from a prior (non-DataLad) run, we don't retroactively
        convert it - that's a deliberate, separate decision, not a side
        effect of turning this flag on.
        """
        if self.project_root not in (self.output_dir, *self.output_dir.parents):
            self.logger.error(
                f"--project_root ({self.project_root}) is not an ancestor of --output_dir "
                f"({self.output_dir}); DataLad needs the subject dataset nested inside the "
                f"superdataset. Refusing to run 'datalad create' here - pass a --project_root "
                f"that actually contains output_dir, or drop it and let it auto-infer. "
                f"Continuing without DataLad for this run."
            )
            self.use_datalad = False
            return

        datalad_dir = self.project_root / ".datalad"
        if pin_file_preexisted and not datalad_dir.exists():
            self.logger.warning(
                f"{self.project_root} was already used without DataLad tracking; "
                f"not retroactively converting it. Continuing without DataLad for this run."
            )
            self.use_datalad = False
            return

        if not datalad_dir.exists():
            self.logger.info(f"Initializing DataLad superdataset at {self.project_root}")
            result = subprocess.run(
                ["datalad", "create", "--force", "-c", "text2git", str(self.project_root)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                self.logger.error(f"datalad create failed: {result.stderr.strip()}")
                self.use_datalad = False
                return

        self._register_datalad_container(self.project_root)

    def _register_datalad_container(self, dataset_dir: Path):
        """Register the pinned Apptainer image in a dataset via a symlink,
        not DataLad's default copy-into-dataset behavior - the image is a
        few hundred MB and shared across every project; copying it into
        each project's dataset would duplicate that on disk per project.
        """
        env_dir = dataset_dir / ".datalad" / "environments" / self.container_name
        image_link = env_dir / "image"
        env_dir.mkdir(parents=True, exist_ok=True)

        needs_save = False
        already_correct = image_link.is_symlink() and image_link.resolve() == self.apptainer_image_path
        if not already_correct:
            if image_link.exists() or image_link.is_symlink():
                image_link.unlink()
            image_link.symlink_to(self.apptainer_image_path)
            subprocess.run(
                ["git", "-C", str(dataset_dir), "add", str(image_link.relative_to(dataset_dir))],
                capture_output=True, text=True
            )
            needs_save = True

        call_fmt = "apptainer exec --userns --nvccli -B /data/local {img} {cmd}"
        rel_image = str(image_link.relative_to(dataset_dir))
        for key, value in [
            (f"datalad.containers.{self.container_name}.image", rel_image),
            (f"datalad.containers.{self.container_name}.cmdexec", call_fmt),
        ]:
            result = subprocess.run(
                ["datalad", "configuration", "--dataset", str(dataset_dir), "--scope", "branch",
                 "set", f"{key}={value}"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                self.logger.warning(f"Could not set {key}: {result.stderr.strip()}")
            else:
                needs_save = True

        if needs_save:
            # Scoped to the registration path only - dataset_dir may be a large,
            # pre-existing study root with unrelated content; an unscoped
            # 'datalad save' walks and saves everything in the tree, not just
            # what this method touched.
            subprocess.run(
                ["datalad", "save", "-d", str(dataset_dir), "-m", f"Register {self.container_name} container",
                 str(env_dir.relative_to(dataset_dir))],
                capture_output=True, text=True
            )

    def _checkout_datalad_branch(self, path: Path, branch: str) -> bool:
        """Checkout `branch` in the git repo at `path` if it exists there (as
        a local or remote-tracking ref) and isn't already checked out.
        Returns whether the branch was found, so callers can tell "already
        on it" / "checked out" apart from "this dataset doesn't use that
        branch at all" - not every dataset in a hierarchy necessarily does.

        May run before self.logger exists (called from _ensure_qsiprep_clone
        at the very top of __init__), so it falls back to print() then.
        """
        log = getattr(self, "logger", None)

        current = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True
        )
        if current.returncode == 0 and current.stdout.strip() == branch:
            return True
        has_branch = any(
            subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", ref],
                capture_output=True, text=True
            ).returncode == 0
            for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}")
        )
        if not has_branch:
            return False
        result = subprocess.run(
            ["git", "-C", str(path), "checkout", branch],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()[-300:]
            msg = f"Could not checkout '{branch}' in {path}: {err}"
            log.warning(msg) if log else print(f"WARNING: {msg}", file=sys.stderr)
        else:
            msg = f"Checked out '{branch}' branch in {path}"
            log.info(msg) if log else print(msg)
        return True

    def _checkout_datalad_branch_in_subdatasets(self, root: Path, branch: str):
        """Recursively checkout `branch` in every already-installed subdataset
        beneath `root` (root itself isn't touched here - callers handle that
        separately). Needed because a subdataset's pinned commit in its
        parent's tree can be stale (e.g. still pointing at an empty initial
        commit) even once that subdataset's own '{branch}' has real data -
        each level is independent and has to be checked out explicitly, it
        isn't inherited from the parent's checkout.
        """
        branch_q = shlex.quote(branch)
        shell_cmd = (
            f"(git rev-parse --verify --quiet refs/remotes/origin/{branch_q} >/dev/null 2>&1 "
            f"|| git rev-parse --verify --quiet refs/heads/{branch_q} >/dev/null 2>&1) || exit 0; "
            f"[ \"$(git rev-parse --abbrev-ref HEAD)\" = {branch_q} ] && exit 0; "
            f"git checkout {branch_q} >/dev/null 2>&1"
        )
        subprocess.run(
            ["git", "-C", str(root), "submodule", "foreach", "--recursive", "--quiet", shell_cmd],
            capture_output=True, text=True
        )

    def _fix_stale_annex_remote(self, path: Path, expected_url: Optional[str] = None):
        """Heal a repo's 'origin' remote left in a broken state by an earlier
        attempt: update the URL if it doesn't match `expected_url`, and clear
        a stale annex-ignore flag. A prior run using an old git-annex, or a
        source URL git-annex couldn't parse, can permanently set
        annex-ignore=true and/or leave the wrong URL in this repo's local
        git config - fixing the git-annex version or URL afterwards does not
        retroactively clear either one, so this is re-checked every run
        rather than only at first clone.
        """
        log = getattr(self, "logger", None)

        if expected_url:
            current = subprocess.run(
                ["git", "-C", str(path), "remote", "get-url", "origin"],
                capture_output=True, text=True
            )
            if current.returncode == 0 and current.stdout.strip() != expected_url:
                subprocess.run(
                    ["git", "-C", str(path), "remote", "set-url", "origin", expected_url],
                    capture_output=True, text=True
                )
                msg = f"Updated stale 'origin' URL in {path}: {current.stdout.strip()} -> {expected_url}"
                log.info(msg) if log else print(msg)

        ignore = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.annex-ignore"],
            capture_output=True, text=True
        )
        if ignore.returncode == 0 and ignore.stdout.strip() == "true":
            subprocess.run(
                ["git", "-C", str(path), "config", "--unset", "remote.origin.annex-ignore"],
                capture_output=True, text=True
            )
            msg = f"Cleared stale annex-ignore flag on 'origin' in {path} (left over from an earlier attempt)"
            log.info(msg) if log else print(msg)

    def _resync_installed_subdataset_remotes(self, root: Path):
        """Recursively heal 'origin' on every already-installed subdataset
        beneath `root` (root itself is handled by the caller): recompute
        each one's expected URL from its .gitmodules-registered relative URL
        resolved against its own immediate (already-healed) parent, then
        apply the same annex-ignore/URL fix as _fix_stale_annex_remote.
        Subdatasets installed by an earlier, broken attempt can be stuck
        pointing at a URL resolved against a since-corrected parent, which
        persists even after the parent itself is fixed.
        """
        gitmodules = root / ".gitmodules"
        if not gitmodules.exists():
            return
        parent_url_result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True
        )
        if parent_url_result.returncode != 0:
            return
        parent_url = parent_url_result.stdout.strip()

        paths_result = subprocess.run(
            ["git", "config", "--file", str(gitmodules), "--get-regexp", r"^submodule\..*\.path$"],
            capture_output=True, text=True
        )
        if paths_result.returncode != 0:
            return

        for line in paths_result.stdout.splitlines():
            key, _, rel_path = line.partition(" ")
            if not rel_path:
                continue
            name = key[len("submodule."):-len(".path")]
            sub_path = root / rel_path
            if not (sub_path / ".git").exists():
                continue  # not installed yet - nothing to fix

            url_result = subprocess.run(
                ["git", "config", "--file", str(gitmodules), "--get", f"submodule.{name}.url"],
                capture_output=True, text=True
            )
            registered_url = url_result.stdout.strip() if url_result.returncode == 0 else ""
            expected_child_url = (
                parent_url.rstrip("/") + "/" + registered_url[2:]
                if registered_url.startswith("./") else None
            )
            self._fix_stale_annex_remote(sub_path, expected_url=expected_child_url)
            self._resync_installed_subdataset_remotes(sub_path)

    @staticmethod
    def _is_derivatives_sibling(child: Path, root: Path) -> bool:
        """True if `child` looks like <root>/derivatives/<something> - the
        layout this lab uses for per-tool sibling subdatasets
        (derivatives/qsiprep, derivatives/dsistudio, derivatives/mriqc, ...).

        Deliberately narrower than "child is somewhere under root": almost
        any reasonable directory layout has qsiprep_dir nested somewhere
        under project_root (e.g. both under the same study folder) without
        that meaning they're part of the same DataLad dataset - only the
        specific 'derivatives/' convention implies that.
        """
        try:
            parts = child.relative_to(root).parts
        except ValueError:
            return False
        return len(parts) >= 2 and parts[0] == "derivatives"

    def _ensure_qsiprep_clone(self) -> Optional[Path]:
        """Clone the qsiprep DataLad source if it isn't present locally yet.

        Called at the very top of __init__, before project_root or any of
        its subdirectories are created - 'datalad clone'/'git clone' refuse
        to clone into an existing non-empty directory, and self.code_dir's
        mkdir a few lines later would otherwise pre-create project_root
        (making it non-empty) before we ever get a chance to clone into it.
        No self.logger exists yet at this point, so failures print directly.

        Also checks out self.qsiprep_datalad_branch at the top level here
        (not just later in _setup_qsiprep_datalad()) - our own later mkdir
        calls for project_root's subdirectories (code_dir, output_dir, etc.)
        would otherwise create a plain untracked 'derivatives' folder before
        we get a chance to check out a branch also named 'derivatives',
        which git then refuses to disambiguate ("could be both a local file
        and a tracking branch").

        Handles the shared per-study layout (a study superdataset with e.g.
        derivatives/qsiprep and derivatives/dsistudio as sibling
        subdatasets): if qsiprep_dir is <project_root>/derivatives/<x>,
        project_root is what gets cloned, and qsiprep_dir is just a
        subdataset path within it, installed later in
        _setup_qsiprep_datalad(). Otherwise qsiprep_dir is cloned directly
        as its own independent dataset.
        """
        if self._is_derivatives_sibling(self.qsiprep_dir, self.project_root):
            clone_root = self.project_root
            # A source URL pointing at qsiprep itself (e.g. picked via a
            # remote browser that was drilled all the way down to
            # ".../derivatives/qsiprep" instead of stopping at the study
            # folder) would, if used as-is here, clone the wrong thing -
            # only the qsiprep subdataset, flattened directly onto
            # project_root, instead of the whole study with qsiprep nested
            # inside it. Strip that matching suffix so the study-level
            # parent gets cloned instead.
            if self.qsiprep_datalad_source:
                rel = str(self.qsiprep_dir.relative_to(self.project_root)).replace(os.sep, "/")
                source = self.qsiprep_datalad_source.rstrip("/")
                if source.endswith("/" + rel):
                    corrected = source[: -(len(rel) + 1)]
                    print(
                        f"NOTE: --qsiprep_datalad_source pointed at qsiprep itself "
                        f"({self.qsiprep_datalad_source}) but qsiprep_dir is nested under "
                        f"project_root - cloning its study-level parent instead: {corrected}"
                    )
                    self.qsiprep_datalad_source = corrected
        else:
            clone_root = self.qsiprep_dir

        if clone_root.exists() and (clone_root / ".datalad").exists():
            # An earlier attempt (old git-annex, or a URL git-annex couldn't
            # parse) can leave 'origin' pointed at a stale URL and/or
            # permanently flagged annex-ignore=true in this already-cloned
            # repo's local git config - upgrading git-annex or fixing the
            # source URL afterwards doesn't retroactively clear either, so a
            # previously-poisoned clone stays broken forever unless this is
            # re-checked (and healed) on every run, not just at first clone.
            self._fix_stale_annex_remote(clone_root, expected_url=self.qsiprep_datalad_source)
            self._checkout_datalad_branch(clone_root, self.qsiprep_datalad_branch)
            return clone_root

        if not self.qsiprep_datalad_source:
            print(
                f"ERROR: --qsiprep_datalad requires {clone_root} to already be a DataLad "
                f"dataset, or --qsiprep_datalad_source to clone it from. Disabling qsiprep "
                f"DataLad handling for this run.",
                file=sys.stderr,
            )
            self.qsiprep_datalad = False
            return None

        # When clone_root == project_root, the web UI's "Create Project" step
        # (or an earlier run of this same script) may have already created
        # <project_root>/code/dsistudio/* (profile, presets, logs) before
        # this run - 'datalad clone' refuses to clone into a non-empty
        # directory. If that known, self-authored scaffold is the *only*
        # thing there, move it aside and merge it back in after cloning
        # instead of failing or silently losing it. Anything else present
        # is unexpected content we shouldn't move/delete automatically.
        scaffold_backup = None
        if clone_root.exists() and not (clone_root / ".datalad").exists():
            entries = list(clone_root.iterdir())
            if entries and [e.name for e in entries] != ["code"]:
                unexpected = ", ".join(sorted(e.name for e in entries))
                print(
                    f"ERROR: {clone_root} already exists with content ({unexpected}) but isn't "
                    f"a DataLad dataset yet - refusing to clone into it. Move or remove this "
                    f"content manually, or use a different project_root.",
                    file=sys.stderr,
                )
                self.qsiprep_datalad = False
                return None
            if entries:  # exactly ["code"] - our own scaffold, safe to move aside
                scaffold_backup = clone_root.parent / f".{clone_root.name}.dsistudio_scaffold_backup"
                if scaffold_backup.exists():
                    shutil.rmtree(scaffold_backup)
                shutil.move(str(clone_root / "code"), str(scaffold_backup))
                clone_root.rmdir()

        print(f"Cloning DataLad dataset from {self.qsiprep_datalad_source} to {clone_root}")
        clone_root.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["datalad", "clone", self.qsiprep_datalad_source, str(clone_root)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            print(f"ERROR: datalad clone of qsiprep source failed: {err}", file=sys.stderr)
            if scaffold_backup is not None:
                clone_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(scaffold_backup), str(clone_root / "code"))
            self.qsiprep_datalad = False
            return None

        if scaffold_backup is not None:
            dest_code_dir = clone_root / "code"
            src_dsistudio = scaffold_backup / "dsistudio"
            if src_dsistudio.exists():
                dest_dsistudio = dest_code_dir / "dsistudio"
                if dest_dsistudio.exists():
                    # The clone itself already tracks a code/dsistudio/ (e.g.
                    # another machine's run was pushed here before) - merge
                    # ours in rather than clobbering it.
                    shutil.copytree(str(src_dsistudio), str(dest_dsistudio), dirs_exist_ok=True)
                    shutil.rmtree(str(src_dsistudio))
                else:
                    dest_code_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_dsistudio), str(dest_dsistudio))
            shutil.rmtree(scaffold_backup, ignore_errors=True)

        self._checkout_datalad_branch(clone_root, self.qsiprep_datalad_branch)
        return clone_root

    def _setup_qsiprep_datalad(self):
        """Finish preparing the read-only qsiprep source for DataLad access:
        the clone and the top-level branch checkout already happened in
        _ensure_qsiprep_clone() (before any directories were created under
        project_root); this installs subdataset registrations (no content)
        so file trees become visible for globbing in find_qsiprep_files(),
        and fixes up any stale submodule pins recursively.

        Some labs keep the actual processed data on a dedicated branch
        (self.qsiprep_datalad_branch, e.g. 'derivatives') rather than the
        default one - a subdataset's pinned commit can be stale (pointing at
        an empty initial commit) even once its own branch has real data, so
        that branch has to be checked out independently in every nested
        subdataset too, not just at the top level.

        This source is never written back to - only clone/get/branch
        checkouts are ever run against it, never save/push/unlock/drop.
        """
        clone_root = self._qsiprep_clone_root
        if clone_root is None:
            return  # cloning failed or was disabled - already logged in _ensure_qsiprep_clone()

        self.logger.info(f"Installing subdataset registrations (no content) under {clone_root}")
        result = subprocess.run(
            ["datalad", "get", "-n", "-r", "-J", str(self.threads), "-d", str(clone_root), str(clone_root)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()[-1000:]
            self.logger.warning(f"datalad get -n -r on {clone_root} had issues: {err}")

        if clone_root != self.qsiprep_dir:
            # qsiprep_dir may be several submodule-levels deep (e.g.
            # derivatives/qsiprep under a study superdataset) - make sure
            # it specifically is installed even if the recursive pass above
            # somehow missed it. Cheap and idempotent if already installed.
            subprocess.run(
                ["datalad", "get", "-n", "-d", str(clone_root), str(self.qsiprep_dir)],
                capture_output=True, text=True
            )

        # Heal any already-installed subdataset (e.g. from an earlier,
        # broken attempt) whose 'origin' is stuck with a stale URL and/or a
        # permanently-set annex-ignore flag, *before* checking out branches
        # in them - otherwise content fetches for a previously-poisoned
        # subdataset would keep failing even though the top-level clone and
        # git-annex itself are now fine.
        self._resync_installed_subdataset_remotes(clone_root)
        self._checkout_datalad_branch_in_subdatasets(clone_root, self.qsiprep_datalad_branch)

    def _ensure_datalad_content(self, paths: List[Path]):
        """Fetch content for the given qsiprep-source paths via 'datalad get'
        if it isn't present locally yet (unfetched git-annex files show up
        as broken symlinks, which glob() still lists by name). Read-only:
        only ever 'get's from this source, never saves/pushes/drops it.
        No-op if --qsiprep_datalad wasn't enabled.
        """
        if not self.qsiprep_datalad:
            return
        missing = [p for p in paths if not p.exists()]
        if not missing:
            return
        if self.dry_run:
            self.logger.info(f"[dry-run] datalad get {' '.join(str(p) for p in missing)}")
            return
        result = subprocess.run(
            ["datalad", "get", "-d", str(self.qsiprep_dir)] + [str(p) for p in missing],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()[-500:]
            self.logger.warning(f"datalad get failed for {[p.name for p in missing]}: {err}")

    def _ensure_subject_dir(self, subject_id: str) -> Path:
        """Return this subject's own subfolder under output_dir - always
        nested (<output_dir>/<subject_id>/...), whether or not --datalad is
        on. With --datalad, it's created as a proper nested DataLad dataset
        of the project superdataset (and the container gets registered in
        it); without --datalad, it's just a plain directory. Subject-nested
        output is the standard layout either way - it mirrors how
        qsiprep/DataLad themselves organize per-subject data, rather than
        being a DataLad-only special case.
        """
        subject_dir = self.output_dir / subject_id
        if self.use_datalad:
            if not (subject_dir / ".datalad").exists():
                self.logger.info(f"Creating nested DataLad dataset for {subject_id}")
                result = subprocess.run(
                    ["datalad", "create", "-d", str(self.project_root), str(subject_dir)],
                    capture_output=True, text=True
                )
                if result.returncode != 0:
                    self.logger.error(f"datalad create for {subject_id} failed: {result.stderr.strip()}")
            self._register_datalad_container(subject_dir)
        else:
            subject_dir.mkdir(parents=True, exist_ok=True)
        return subject_dir

    def _datalad_run(self, dataset_dir: Path, dsi_args: List[str], output_glob: str, message: str) -> bool:
        """Run a dsi_studio call via `datalad containers-run` so it's recorded
        as a single, replayable commit - the exact command, container, exit
        code, and declared outputs all end up in the commit message, and
        `datalad rerun <commit>` replays it later.
        """
        if self.dry_run:
            self.logger.info(f"[dry-run] datalad containers-run -d {dataset_dir} -- dsi_studio {' '.join(dsi_args)}")
            return True
        cmd = [
            "datalad", "containers-run",
            "-d", str(dataset_dir),
            "-n", self.container_name,
            "--explicit", "--expand", "outputs",
            "-o", output_glob,
            "-m", message,
            "--", "dsi_studio",
        ] + dsi_args
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(dataset_dir))
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            self.logger.error(f"datalad containers-run failed: {err[-2000:]}")
            return False
        return True

    def _should_force(self, component: str) -> bool:
        """Check if a specific component should be force-regenerated.
        
        Args:
            component: One of 'src', 'fib', 'diffs', 'database'
        
        Returns:
            True if force regeneration is enabled for this component
        """
        return component in self.force_components

    def _cleanup_intermediate_files(self, directory: Path):
        """Delete intermediate .tar.gz and other temporary compressed files to save space.
        
        Only removes temporary archive files (.tar.gz, .tar.bz2, .tar.xz).
        All analysis outputs are preserved: .csv, .mat, .txt, .fib.gz, .src.gz, .json, .html
        """
        if not directory.exists():
            return
        
        patterns = ['*.tar.gz', '*.tar.bz2', '*.tar.xz']
        deleted_count = 0
        
        for pattern in patterns:
            for file in directory.rglob(pattern):
                try:
                    size_mb = file.stat().st_size / (1024 * 1024)
                    file.unlink()
                    self.logger.info(f"Deleted intermediate file: {file.name} ({size_mb:.1f} MB)")
                    deleted_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to delete {file}: {e}")
        
        if deleted_count > 0:
            self.logger.info(f"Cleanup: Removed {deleted_count} intermediate files")

    def _shorten_filename(self, original_name: str, sub_id: str, ses_id: str = "", file_type: str = "") -> str:
        """Create a shortened filename while preserving key information for Windows compatibility.
        
        Pattern: {sub}_{ses}_{type}_{param}.{ext}
        Example: sub1292092_ses3_fib_q1p25.fib.gz instead of sub-1292092_ses-3_desc-long_name.fib.gz
        """
        # Extract key components
        sub_short = sub_id.replace('sub-', '')
        ses_short = ses_id.replace('ses-', '') if ses_id and ses_id != 'ses-1' else ""
        
        # Get file extension
        parts = original_name.rsplit('.', 2)  # Handle .fib.gz, .src.gz, etc
        if len(parts) >= 2:
            ext = '.'.join(parts[-2:]) if parts[-1] in ['gz', 'sz'] else parts[-1]
        else:
            ext = parts[-1] if parts else 'file'
        
        # Create method abbreviation
        method_abbr = f"m{self.method}" if self.method else "m4"
        param_abbr = f"p{self.param0.replace('.', '')}".replace('.', '') if self.param0 else "p125"
        
        # Build short name
        base_parts = [f"s{sub_short}"]  # s = subject
        if ses_short:
            base_parts.append(f"e{ses_short}")  # e = session
        if file_type:
            base_parts.append(file_type[:3])  # f=fib, s=src, d=diff
        base_parts.append(f"{method_abbr}{param_abbr}")
        
        short_name = '_'.join(base_parts) + '.' + ext
        return short_name

    def _decompress_sz(self, zipped_path: Path) -> Optional[Path]:
        """Convert a .sz archive produced by DSI Studio into the base file."""
        if not zipped_path.exists():
            return None
        dest = zipped_path.with_suffix("")
        try:
            with gzip.open(zipped_path, 'rb') as inf, open(dest, 'wb') as outf:
                shutil.copyfileobj(inf, outf)
            zipped_path.unlink()
            return dest
        except Exception as exc:
            self.logger.error(f"Failed to decompress {zipped_path}: {exc}")
            return None

    def _collect_reconstruction_outputs(self, src_file: Path, search_dir: Optional[Path] = None) -> List[Path]:
        """Gather reconstructed delta outputs (.fib or .odf) and decompress .sz archives."""
        outputs = []
        prefix = src_file.name.split(".src")[0]
        parent = search_dir if search_dir is not None else src_file.parent
        patterns = [f"{prefix}*.fib.gz", f"{prefix}*.odf.*"]

        for pattern in patterns:
            for match in parent.glob(pattern):
                candidate = match
                if match.suffix == ".sz":
                    if self.dry_run:
                        self.logger.info(f"Dry run: would decompress {match}")
                        continue
                    decompressed = self._decompress_sz(match)
                    if not decompressed:
                        continue
                    candidate = decompressed
                if candidate not in outputs:
                    outputs.append(candidate)
        return outputs

    def run_command(self, cmd: List[str], log_command: bool = False):
        """Run a shell command and log output.
        
        Args:
            cmd: Command to run as list of strings
            log_command: If True, log the command before running (default False, caller should log instead)
        """
        if log_command:
            self.logger.info(f"Running: {' '.join(cmd)}")
        if self.dry_run:
            return True
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if result.stdout:
                self.logger.debug(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            self.logger.error(f"Command failed with error: {err}")
            return False

    def validate_fib_file(self, fib_path: Path) -> Dict[str, any]:
        """Validate FIB file after generation.
        
        Checks:
        - File exists and has non-zero size
        - Can be loaded with scipy (basic integrity check)
        - Contains expected metrics
        """
        result = {
            "valid": False,
            "path": str(fib_path),
            "size_mb": 0,
            "exists": False,
            "metrics_found": [],
            "errors": []
        }
        
        if not fib_path.exists():
            result["errors"].append(f"File does not exist: {fib_path}")
            return result
        
        result["exists"] = True
        size_mb = fib_path.stat().st_size / (1024 * 1024)
        result["size_mb"] = round(size_mb, 2)
        
        if size_mb < 1:
            result["errors"].append(f"File is suspiciously small ({size_mb:.2f} MB), may be corrupt")
            return result
        
        # Try to load and check structure
        try:
            import gzip
            with gzip.open(fib_path, 'rb') as f:
                import scipy.io
                mat = scipy.io.loadmat(f)
                
                # Check for key metrics
                expected_metrics = ['fa0', 'fa1', 'fa2', 'gfa', 'dti_fa', 'md', 'ad', 'rd']
                found = [m for m in expected_metrics if m in mat]
                result["metrics_found"] = found
                
                if not found:
                    result["errors"].append(f"No standard metrics found in file")
                    return result
                
                result["valid"] = True
                
        except Exception as e:
            result["errors"].append(f"Failed to load FIB file: {str(e)}")
        
        return result

    def print_progress_summary(self, current: int, total: int):
        """Print a formatted progress summary to terminal"""
        percent = (current / total * 100) if total > 0 else 0
        bar_length = 20
        filled = int(bar_length * current / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_length - filled)
        
        summary = f"[{bar}] {percent:5.1f}% ({current}/{total}) | Proc: {self.stats['processed']:>3} | SRC: {self.stats['src_ok']:>3} | FIB: {self.stats['fib_ok']:>3}"
        self.logger.info(summary)

    def _parse_sub_ses(self, path: Path) -> Tuple[str, str]:
        parts = path.name.split("_")
        sub = next((p for p in parts if p.startswith("sub-")), "")
        ses = next((p for p in parts if p.startswith("ses-")), "")
        if ses:
            ses = ses.split(".")[0] # Clean 'ses-1.odf.qsdr.fz' to 'ses-1'
        return sub, ses

    @staticmethod
    def _normalize_bids_filter(value: Optional[str], prefix: str) -> Optional[str]:
        """Turn a user-supplied filter value into a bare entity value to
        compare against, or None for 'no filter'. Accepts 'all'/empty (no
        filter) and either the bare value or the prefixed entity ('ses-1'
        or '1') so the web UI and CLI can both pass through whatever the
        dropdown/glob gave them.
        """
        if not value:
            return None
        v = value.strip()
        if not v or v.lower() == "all":
            return None
        if v.lower().startswith(f"{prefix}-"):
            v = v[len(prefix) + 1:]
        return v

    @staticmethod
    def _parse_bids_entities(path: Path) -> Dict[str, str]:
        """Parse 'key-value' BIDS entities out of a filename, e.g.
        'sub-1_ses-1_acq-multi_space-ACPC_desc-preproc_dwi.nii.gz' ->
        {'sub': '1', 'ses': '1', 'acq': 'multi', 'space': 'ACPC', 'desc': 'preproc'}.
        Non-entity parts (no '-', e.g. the trailing 'dwi.nii.gz') are skipped.
        """
        entities = {}
        for part in path.name.split("_"):
            if "-" in part:
                key, _, value = part.partition("-")
                entities[key] = value
        return entities

    def _matches_bids_filters(self, path: Path) -> bool:
        if not (self.session_filter or self.acq_filter or self.space_filter):
            return True
        entities = self._parse_bids_entities(path)
        if self.session_filter and entities.get("ses", "").lower() != self.session_filter.lower():
            return False
        if self.acq_filter and entities.get("acq", "").lower() != self.acq_filter.lower():
            return False
        if self.space_filter and entities.get("space", "").lower() != self.space_filter.lower():
            return False
        return True

    def verify_raw_vs_qsiprep(self):
        """Cross-check rawdata subjects/sessions against qsiprep outputs."""
        if not self.rawdata_dir.exists():
            self.logger.warning(f"Rawdata directory not found: {self.rawdata_dir}; skipping raw-vs-qsiprep check.")
            return

        raw_dwi = list(self.rawdata_dir.glob("sub-*/dwi/*_dwi.nii.gz"))
        raw_dwi += list(self.rawdata_dir.glob("sub-*/ses-*/dwi/*_dwi.nii.gz"))
        raw_keys = set()
        for f in raw_dwi:
            sub, ses = self._parse_sub_ses(f)
            raw_keys.add((sub, ses))

        qsi_dwi = list(self.qsiprep_dir.glob("sub-*/dwi/*_desc-preproc_dwi.nii.gz"))
        qsi_dwi += list(self.qsiprep_dir.glob("sub-*/ses-*/dwi/*_desc-preproc_dwi.nii.gz"))
        qsi_keys = set()
        for f in qsi_dwi:
            sub, ses = self._parse_sub_ses(f)
            qsi_keys.add((sub, ses))

        missing = raw_keys - qsi_keys
        extra = qsi_keys - raw_keys

        if missing:
            self.logger.warning(f"Rawdata subjects/sessions missing in qsiprep: {sorted(missing)}")
        if extra:
            self.logger.info(f"qsiprep has extra subjects/sessions not in rawdata: {sorted(extra)}")
        if not missing:
            self.logger.info("Rawdata vs qsiprep check: all raw subjects/sessions present in qsiprep outputs.")

    def find_qsiprep_files(self):
        """Find and validate preprocessed DWI files in qsiprep directory"""
        all_dwi = list(self.qsiprep_dir.glob("sub-*/dwi/*_desc-preproc_dwi.nii.gz"))
        all_dwi += list(self.qsiprep_dir.glob("sub-*/ses-*/dwi/*_desc-preproc_dwi.nii.gz"))
        if not all_dwi:
            all_dwi = list(self.qsiprep_dir.glob("*_desc-preproc_dwi.nii.gz"))

        if self.session_filter or self.acq_filter or self.space_filter:
            before = len(all_dwi)
            all_dwi = [f for f in all_dwi if self._matches_bids_filters(f)]
            self.logger.info(
                f"BIDS filter (session={self.session_filter or 'all'}, "
                f"acq={self.acq_filter or 'all'}, space={self.space_filter or 'all'}): "
                f"{len(all_dwi)}/{before} files matched"
            )

        # In pilot mode, narrow down to one randomly chosen subject's files
        # *before* fetching any DataLad content below - filenames alone are
        # enough to pick a subject (glob lists them regardless of whether
        # their content has been fetched yet), so doing this first avoids
        # 'datalad get' being attempted for every other subject just to
        # determine which single one to actually keep.
        if self.pilot:
            subjects = {dwi.name.split('_')[0] for dwi in all_dwi}
            if subjects:
                selected_sub = random.choice(list(subjects))
                all_dwi = [f for f in all_dwi if f.name.startswith(selected_sub + "_")]
                self.logger.info(f"PILOT MODE: Randomly selected subject {selected_sub} ({len(all_dwi)} files)")

        valid_dwi = []
        current_time = datetime.now().timestamp()

        for dwi in all_dwi:
            bval = dwi.with_suffix('').with_suffix('').with_suffix('.bval')
            bvec = dwi.with_suffix('').with_suffix('').with_suffix('.bvec')
            self._ensure_datalad_content([dwi, bval, bvec])
            if not (bval.exists() and bvec.exists()):
                self.logger.warning(f"Skipping {dwi.name}: Missing .bval or .bvec")
                self.stats["skipped_missing"] += 1
                continue
            # Sanity check file sizes
            if dwi.stat().st_size == 0 or bval.stat().st_size == 0 or bvec.stat().st_size == 0:
                self.logger.warning(f"Skipping {dwi.name}: One of the files is empty (nii/bval/bvec)")
                self.stats["skipped_missing"] += 1
                continue
            
            # Check if files are recent (possibly still being written by qsiprep).
            # Skipped entirely under --qsiprep_datalad: this data was already
            # committed to the source dataset, so it can't still be "being
            # written" - and current_time was captured once before this loop,
            # while _ensure_datalad_content() above can take real wall-clock
            # time to fetch a file, so its mtime (set at fetch completion) can
            # end up *after* current_time, giving a bogus negative age that's
            # always < min_file_age and skips every freshly-fetched file.
            if self.min_file_age > 0 and not self.qsiprep_datalad:
                dwi_age = current_time - dwi.stat().st_mtime
                bval_age = current_time - bval.stat().st_mtime
                bvec_age = current_time - bvec.stat().st_mtime
                min_age = min(dwi_age, bval_age, bvec_age)
                
                if min_age < self.min_file_age:
                    self.logger.info(f"Skipping {dwi.name}: Files too recent (age: {min_age:.0f}s < {self.min_file_age}s, likely still being written)")
                    self.stats["skipped_missing"] += 1
                    continue
            
            valid_dwi.append(dwi)

        if not valid_dwi:
            self.logger.error("No valid DWI files (with bval/bvec) found!")
            self.stats["found"] = 0
            return []

        self.stats["found"] = len(valid_dwi)
        self.logger.info(f"Found {len(valid_dwi)} valid DWI files")
        return valid_dwi

    def find_existing_fib_files(self) -> List[Path]:
        """Find already-generated FIB files for connectivity-only mode.

        Mirrors the search logic in reconstruct_fib() (same per-method
        filename suffix, same datalad-vs-flat directory layout) but reads
        instead of generates. FIB filenames only carry sub-/ses- entities
        (acq/space are dropped in generate_src's base_id), so only the
        session filter can be applied here.
        """
        method_suffixes = {
            '4': ['.odf.gqi.fz'],  # GQI
            '7': ['.odf.qsdr.fz'],  # QSDR
        }
        method_key = str(self.method) if self.method else '4'
        expected_suffixes = method_suffixes.get(method_key, ['*'])

        if self.acq_filter or self.space_filter:
            self.logger.warning(
                "connectivity_only: acq/space filters are ignored - FIB filenames don't retain those BIDS entities."
            )

        all_fibs: List[Path] = []
        for suffix in expected_suffixes:
            all_fibs.extend(self.output_dir.glob(f"sub-*/fib/*{suffix}"))

        if self.session_filter:
            before = len(all_fibs)
            all_fibs = [f for f in all_fibs if self._parse_sub_ses(f)[1].lower() == f"ses-{self.session_filter}".lower()]
            self.logger.info(f"Session filter (ses-{self.session_filter}): {len(all_fibs)}/{before} FIB files matched")

        if not all_fibs:
            self.logger.error(f"No existing FIB files found (method {self.method}) for connectivity-only mode!")
            return []

        all_fibs.sort()
        if self.pilot:
            subjects = {self._parse_sub_ses(f)[0] for f in all_fibs}
            subjects.discard("")
            if subjects:
                selected_sub = random.choice(list(subjects))
                selected = [f for f in all_fibs if f.name.startswith(selected_sub + "_")]
                self.logger.info(f"PILOT MODE: Randomly selected subject {selected_sub} ({len(selected)} FIB files)")
                return selected

        self.logger.info(f"Found {len(all_fibs)} existing FIB files for connectivity-only mode")
        return all_fibs

    def generate_src(self, dwi_file: Path):
        """Generate SRC file from DWI, bval, and bvec, including T1w if available"""
        subject_id = dwi_file.name.split('_')[0]
        # Handle session if present
        session_id = ""
        if "_ses-" in dwi_file.name:
            session_id = "_" + dwi_file.name.split('_')[1]
        
        base_id = f"{subject_id}{session_id}"

        bval_file = dwi_file.with_suffix('').with_suffix('').with_suffix('.bval')
        bvec_file = dwi_file.with_suffix('').with_suffix('').with_suffix('.bvec')

        if not bval_file.exists() or not bvec_file.exists():
            self.logger.warning(f"Missing bval/bvec for {dwi_file.name}, skipping.")
            return None

        subject_dir = self._ensure_subject_dir(subject_id)
        src_subdir = subject_dir / "src"
        src_subdir.mkdir(parents=True, exist_ok=True)
        output_src = src_subdir / f"{base_id}.src.gz"

        dsi_args = [
            "--action=src",
            f"--source={dwi_file}",
            f"--bval={bval_file}",
            f"--bvec={bvec_file}",
            f"--output={output_src}"
        ]

        # Try to find T1w image in anat directory. For session-layout qsiprep
        # output (sub-X/ses-Y/dwi/...), the per-session anat dir often holds
        # only transforms - qsiprep shares one T1w across sessions in the
        # subject-level anat dir (sub-X/anat/) when run with a shared
        # anatomical workflow. Check session-level first, fall back to
        # subject-level.
        # .exists() (not just glob matching the filename) matters here: a
        # DataLad/git-annex file whose content hasn't been fetched yet is a
        # broken symlink - it matches the glob pattern but isn't readable.
        # Passing that path to dsi_studio would fail silently/confusingly
        # inside the container instead of a clear "missing" message.
        anat_dirs = [dwi_file.parents[1] / "anat"]
        if session_id and len(dwi_file.parents) > 2:
            anat_dirs.append(dwi_file.parents[2] / "anat")
        t1w_files = []
        for anat_dir in anat_dirs:
            candidates = list(anat_dir.glob(f"{subject_id}*_desc-preproc_T1w.nii.gz"))
            self._ensure_datalad_content(candidates)
            t1w_files = [f for f in candidates if f.exists()]
            if t1w_files:
                break
        if t1w_files:
            dsi_args.append(f"--t1w={t1w_files[0]}")
            self.logger.info(f"Found T1w for {base_id}: {t1w_files[0].name}")
        elif any(anat_dir.glob(f"{subject_id}*_desc-preproc_T1w.nii.gz") for anat_dir in anat_dirs):
            self.logger.warning(
                f"T1w for {base_id} matched but content isn't fetched (broken DataLad/git-annex "
                f"symlink) - run 'datalad get' on it first, or pass --qsiprep_datalad to fetch "
                f"it automatically. Treating as missing."
            )

        # Try to find mask
        mask_candidates = list(dwi_file.parent.glob(f"{base_id}*_desc-brain_mask.nii.gz"))
        self._ensure_datalad_content(mask_candidates)
        mask_files = [f for f in mask_candidates if f.exists()]
        if mask_files:
            dsi_args.append(f"--mask={mask_files[0]}")
            self.logger.info(f"Found mask for {base_id}: {mask_files[0].name}")
        elif self.require_mask:
            self.logger.warning(f"Missing mask for {base_id}; skipping due to --require-mask")
            return None

        if self.require_t1w and not t1w_files:
            self.logger.warning(f"Missing T1w for {base_id}; skipping due to --require-t1w")
            return None

        # Check for existing SRC (could be .src.gz or .src.gz.sz)
        src_exists = output_src.exists() or Path(f"{output_src}.sz").exists()
        actual_src = output_src if output_src.exists() else Path(f"{output_src}.sz") if Path(f"{output_src}.sz").exists() else None

        if src_exists and self.skip_existing and not self._should_force('src'):
            self.logger.info(f"SRC exists, skipping generation: {actual_src.name if actual_src else output_src.name}")
            return actual_src or output_src
        elif src_exists and self._should_force('src'):
            self.logger.info(f"SRC exists but --force src is set, will overwrite: {actual_src.name if actual_src else output_src.name}")

        if self.use_datalad:
            success = self._datalad_run(subject_dir, dsi_args, f"src/{base_id}.src.gz*", f"src: {base_id}")
        else:
            cmd = [self.dsi_studio_cmd] + dsi_args
            self.logger.info(f"Running: {' '.join(cmd)}")
            success = self.run_command(cmd)

        if success:
            # DSI Studio may create .src.gz.sz instead of .src.gz
            if not output_src.exists():
                zipped_src = Path(f"{output_src}.sz")
                if zipped_src.exists():
                    output_src = zipped_src
                else:
                    self.logger.error(f"SRC file not found at {output_src} or {zipped_src}")
                    return None
            self.stats["src_ok"] += 1
            return output_src
        return None

    def reconstruct_fib(self, src_file: Path):
        """Reconstruct FIB file from SRC"""
        subject_prefix = src_file.name.split('.src')[0]
        subject_id = subject_prefix.split('_')[0]

        # Check for method-specific existing FIB files
        # FIB files: modern format is .fz (not .gz or .sz)
        # SRC files: modern format is .sz (compressed)
        method_suffixes = {
            '4': ['.odf.gqi.fz'],  # GQI (method 4)
            '7': ['.odf.qsdr.fz'],  # QSDR (method 7)
        }

        method_key = str(self.method) if self.method else '4'
        expected_suffixes = method_suffixes.get(method_key, ['*'])

        subject_dir = self._ensure_subject_dir(subject_id)
        fib_subdir = subject_dir / "fib"
        fib_subdir.mkdir(parents=True, exist_ok=True)

        existing_fib = []
        for suffix in expected_suffixes:
            pattern = f"{subject_prefix}{suffix}"
            matches = list(fib_subdir.glob(pattern))
            existing_fib.extend(matches)

        if existing_fib and self.skip_existing and not self._should_force('fib'):
            self.logger.info(f"FIB (method {self.method}) exists, skipping: {existing_fib[0].name}")
            self.stats["skipped_existing"] += 1
            return existing_fib[0]
        elif existing_fib and self._should_force('fib'):
            self.logger.info(f"FIB (method {self.method}) exists but --force fib is set, will overwrite: {existing_fib[0].name}")
        elif self.skip_existing:
            # Check if other method files exist (but requested method doesn't)
            all_fibs = list(fib_subdir.glob(f"{subject_prefix}*"))
            other_method_fibs = [f for f in all_fibs if f not in existing_fib]
            if other_method_fibs:
                self.logger.info(f"Found {len(other_method_fibs)} FIB(s) with different method, generating method {self.method}: {other_method_fibs[0].name}")

        dsi_args = [
            "--action=rec",
            f"--source={src_file}",
            f"--method={self.method}",
            f"--param={self.param0}",
            f"--thread_count={self.threads}",
            '--cmd=[Step T2][B-table][flip by]',
            "--other_output=all"
        ]

        if self.use_datalad:
            # Write directly into this subject's fib/ subdir instead of next to
            # the SRC file, so the declared datalad output and the final
            # location are the same path - no separate move step afterward.
            dsi_args.append(f"--output={fib_subdir / subject_prefix}")
            success = self._datalad_run(subject_dir, dsi_args, f"fib/{subject_prefix}*", f"rec: {subject_prefix} method={self.method}")
            if success:
                generated_fib = self._collect_reconstruction_outputs(src_file, search_dir=fib_subdir)
                for dest in generated_fib:
                    validation = self.validate_fib_file(dest)
                    if validation["valid"]:
                        self.logger.info(f"✓ FIB validated: {dest.name} ({validation['size_mb']} MB, metrics: {len(validation['metrics_found'])})")
                        self.stats["fib_ok"] += 1
                    else:
                        self.logger.warning(f"✗ FIB validation failed: {dest.name}")
                        for error in validation["errors"]:
                            self.logger.warning(f"  → {error}")
                return generated_fib[0] if generated_fib else None
            return None

        cmd = [self.dsi_studio_cmd] + dsi_args
        self.logger.info(f"Running: {' '.join(cmd)}")
        if self.run_command(cmd):
            generated_fib = self._collect_reconstruction_outputs(src_file)
            if generated_fib:
                moved = []
                for fib in generated_fib:
                    dest = fib_subdir / fib.name
                    if not self.dry_run:
                        fib.rename(dest)
                        # Validate the FIB file
                        validation = self.validate_fib_file(dest)
                        if validation["valid"]:
                            self.logger.info(f"✓ FIB validated: {dest.name} ({validation['size_mb']} MB, metrics: {len(validation['metrics_found'])})")
                            self.stats["fib_ok"] += 1
                        else:
                            self.logger.warning(f"✗ FIB validation failed: {dest.name}")
                            for error in validation["errors"]:
                                self.logger.warning(f"  → {error}")
                    else:
                        self.logger.info(f"Dry run: would move {fib} to {dest}")
                    moved.append(dest)
                return moved[0] if moved else None
        return None

    def generate_longitudinal_diff(self, baseline_fib: Path, followup_fib: Path) -> Optional[Path]:
        """Compute longitudinal change between two sessions using a custom script that merges diff into baseline template."""
        sub_b, ses_b = self._parse_sub_ses(baseline_fib)
        sub_f, ses_f = self._parse_sub_ses(followup_fib)
        
        if not ses_b or not ses_f:
            self.logger.warning(f"Could not determine sessions for {baseline_fib.name} or {followup_fib.name}")
            return None

        output_name = f"{sub_f}_{ses_f}_minus_{ses_b}.fib.gz"
        diff_subdir = self._ensure_subject_dir(sub_f) / "diff"
        diff_subdir.mkdir(parents=True, exist_ok=True)
        output_path = diff_subdir / output_name

        if output_path.exists() and self.skip_existing and not self._should_force('diffs'):
            # Check if file has content (not 0 bytes from previous failed attempt)
            if output_path.stat().st_size > 0:
                self.logger.info(f"Diff already exists, skipping: {output_name}")
                return output_path
            else:
                self.logger.warning(f"Diff file exists but is empty (0 bytes), will regenerate: {output_name}")
                output_path.unlink()  # Delete the empty file
        elif output_path.exists() and self._should_force('diffs'):
            self.logger.info(f"Diff file exists but --force diffs is set, will overwrite: {output_name}")

        # Use the specialized create_differential_fib.py script
        script_path = Path(__file__).parent / "create_differential_fib.py"
        cmd = [
            "python3",
            str(script_path),
            "--baseline", str(baseline_fib),
            "--followup", str(followup_fib),
            "--output", str(output_path),
            "--method", str(self.method)
        ]
        
        self.logger.info(f"Computing longitudinal change (v2): {ses_f} - {ses_b} for {sub_f}")
        if self.run_command(cmd):
            # Verify output file was created and has content
            if output_path.exists() and output_path.stat().st_size > 0:
                if self.use_datalad and not self.dry_run:
                    # This goes through create_differential_fib.py rather than
                    # dsi_studio directly, so it isn't a containers-run record -
                    # just commit the resulting file so the dataset stays clean.
                    subprocess.run(
                        ["datalad", "save", "-d", str(self.project_root), "-m", f"diff: {output_name}"],
                        capture_output=True, text=True
                    )
                return output_path
            else:
                self.logger.error(f"Differential FIB creation failed or produced empty file: {output_path}")
                # Clean up empty file if it exists
                if output_path.exists():
                    output_path.unlink()
                return None
        return None

    def _validate_connectivity_setup(self) -> bool:
        """Validate connectivity extraction setup (atlases, DSI Studio, config)."""
        self.logger.info("Validating connectivity extraction setup...")
        
        # Check DSI Studio
        try:
            result = subprocess.run([self.dsi_studio_cmd, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                self.logger.error(f"DSI Studio not working: {self.dsi_studio_cmd}")
                return False
        except Exception as e:
            self.logger.error(f"DSI Studio check failed: {e}")
            return False
        
        # Check config file and load atlases
        if self.connectivity_config:
            if not self.connectivity_config.exists():
                self.logger.error(f"Connectivity config not found: {self.connectivity_config}")
                return False
            
            try:
                import json
                with open(self.connectivity_config, 'r') as f:
                    config = json.load(f)
                atlases = config.get('atlases', [])
                if not atlases:
                    self.logger.warning("No atlases specified in connectivity config")
                    return True
                
                # Check if atlases exist in DSI Studio (use the real install dir for
                # this check even in --apptainer mode, since dsi_studio_cmd then
                # points at the container wrapper script, not an actual install).
                atlas_dir = self.atlas_reference_dir / "atlas" / "human"
                if not atlas_dir.exists():
                    self.logger.warning(f"DSI Studio atlas directory not found: {atlas_dir}")
                    return True
                
                missing_atlases = []
                for atlas in atlases:
                    atlas_file = atlas_dir / f"{atlas}.nii.gz"
                    atlas_txt = atlas_dir / f"{atlas}.txt"
                    if not atlas_file.exists():
                        missing_atlases.append(f"{atlas} (missing {atlas}.nii.gz)")
                    elif not atlas_txt.exists():
                        missing_atlases.append(f"{atlas} (missing {atlas}.txt)")
                
                if missing_atlases:
                    self.logger.error(f"Missing atlases in {atlas_dir}:")
                    for missing in missing_atlases:
                        self.logger.error(f"  - {missing}")
                    return False
                
                self.logger.info(f"✓ All {len(atlases)} atlases found: {', '.join(atlases)}")
                
                # Check reconstruction method compatibility
                config_recon_method = config.get('reconstruction_method', None)
                if config_recon_method is not None:
                    pipeline_method = int(self.method) if self.method else 4
                    if config_recon_method != pipeline_method:
                        self.logger.info(f"ℹ️  Config specifies method {config_recon_method}, but CLI using method {pipeline_method}")
                        self.logger.info(f"   Method {pipeline_method} ({['GQI (native)' if pipeline_method==4 else 'QSDR (MNI)' if pipeline_method==7 else f'method{pipeline_method}'][0]}) will be passed to extractor (CLI overrides config)")
                    else:
                        self.logger.info(f"✓ Reconstruction method matches: {pipeline_method} ({['GQI' if pipeline_method==4 else 'QSDR' if pipeline_method==7 else f'method{pipeline_method}'][0]})")
                
            except Exception as e:
                self.logger.error(f"Error validating connectivity config: {e}")
                return False
        
        return True
    
    def run_connectivity_extraction(self, fib_files: List[Path]):
        """Run connectivity matrix extraction on generated FIB files."""
        if not fib_files:
            self.logger.warning("No FIB files available for connectivity extraction")
            return
        extractor = Path(__file__).resolve().parents[1] / "connectivity" / "extract_connectivity_matrices.py"
        if not extractor.exists():
            self.logger.error(f"Connectivity extractor not found at {extractor}")
            return
        if self.connectivity_config and not self.connectivity_config.exists():
            self.logger.error(f"Connectivity config not found: {self.connectivity_config}")
            return
        
        # Run validation (works in both normal and dry-run mode)
        if not self._validate_connectivity_setup():
            self.logger.error("Connectivity validation failed - skipping extraction")
            return

        for fib in fib_files:
            cmd = ["python3", str(extractor)]
            if self.connectivity_config:
                cmd += ["--config", str(self.connectivity_config)]
            if self.connectivity_threads:
                cmd += ["--threads", str(self.connectivity_threads)]
            # Pass DSI Studio path to extractor
            cmd += ["--dsi_studio_cmd", self.dsi_studio_cmd]
            # Pass reconstruction method from CLI to override config
            cmd += ["--reconstruction_method", str(self.method)]
            cmd += [str(fib), str(self.connectivity_output_dir)]
            self.logger.info(f"Launching connectivity extraction for {fib.name}")
            self.run_command(cmd)

    def create_database(self, fib_files: List[Path], output_db: Optional[Path] = None, index_name: Optional[str] = None):
        """Create connectometry database from FIB files"""
        if not fib_files:
            self.logger.error("No FIB files found to create database")
            return
        
        # Filter out invalid files (0 bytes or non-existent)
        valid_fibs = []
        for fib in fib_files:
            if not fib.exists():
                self.logger.warning(f"FIB file does not exist, skipping: {fib}")
                continue
            if fib.stat().st_size == 0:
                self.logger.warning(f"FIB file is empty (0 bytes), skipping: {fib}")
                continue
            valid_fibs.append(fib)
        
        if not valid_fibs:
            self.logger.error("No valid FIB files found for database creation (all files were invalid or missing)")
            return
        
        if len(valid_fibs) < len(fib_files):
            self.logger.warning(f"Database creation: {len(fib_files) - len(valid_fibs)} invalid files were skipped")
        
        if output_db is None:
            output_db = self.output_dir / self.db_name
        
        fib_list = ",".join([str(f) for f in valid_fibs])

        dsi_args = [
            "--action=db",
            f"--source={fib_list}",
            f"--output={output_db}"
        ]

        if index_name:
            dsi_args.append(f"--index_name={index_name}")

        if self.use_datalad:
            # Spans multiple subjects' FIBs, so this is a project-level
            # (superdataset) artifact rather than something owned by one
            # subject's nested dataset.
            rel_output = output_db.relative_to(self.project_root)
            self._datalad_run(self.project_root, dsi_args, str(rel_output), f"db: {output_db.name}")
        else:
            cmd = [self.dsi_studio_cmd] + dsi_args
            self.run_command(cmd)
        self.logger.info(f"Database created at {output_db}")

    def _generate_html_report(self, subject_id: str, sessions: List[Dict]):
        """Generate HTML report for a subject containing all sessions."""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>DSI Studio Processing Report - {subject_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        .session {{ background: #ecf0f1; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #3498db; }}
        .success {{ color: #27ae60; font-weight: bold; }}
        .error {{ color: #e74c3c; font-weight: bold; }}
        .info {{ color: #7f8c8d; font-size: 0.9em; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #34495e; color: white; }}
        .timestamp {{ color: #95a5a6; font-size: 0.85em; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>DSI Studio Processing Report: {subject_id}</h1>
        <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        
        <h2>Processing Summary</h2>
        <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Total Sessions</td><td>{len(sessions)}</td></tr>
            <tr><td>Successful</td><td class="success">{sum(1 for s in sessions if s['status'] == 'success')}</td></tr>
            <tr><td>Failed</td><td class="error">{sum(1 for s in sessions if s['status'] == 'failed')}</td></tr>
        </table>
        
        <h2>Session Details</h2>
"""
        
        for session in sessions:
            status_class = 'success' if session['status'] == 'success' else 'error'
            html_content += f"""
        <div class="session">
            <h3>Session: {session['session_id']}</h3>
            <p><strong>Status:</strong> <span class="{status_class}">{session['status'].upper()}</span></p>
            <p class="info">DWI: {session['dwi_file']}</p>
            <p class="info">SRC: {session.get('src_file', 'N/A')}</p>
            <p class="info">FIB: {session.get('fib_file', 'N/A')}</p>
            <p class="info">Method: GQI (param0={session.get('param0', 'N/A')})</p>
            <p class="timestamp">Processed: {session.get('timestamp', 'N/A')}</p>
        </div>
"""
        
        html_content += """
    </div>
</body>
</html>
"""
        
        reports_subdir = self._ensure_subject_dir(subject_id) / "reports"
        reports_subdir.mkdir(parents=True, exist_ok=True)
        report_file = reports_subdir / f"{subject_id}_report.html"
        with open(report_file, 'w') as f:
            f.write(html_content)
        self.logger.info(f"Generated report: {report_file}")

    def _load_participants_tsv(self) -> List[str]:
        """Load expected subjects from participants.tsv"""
        participants_file = self.qsiprep_dir.parent.parent / "participants.tsv"
        if not participants_file.exists():
            # Try rawdata location
            if hasattr(self, 'rawdata_dir') and self.rawdata_dir.exists():
                participants_file = self.rawdata_dir / "participants.tsv"
        
        if not participants_file.exists():
            self.logger.warning("participants.tsv not found, skipping completeness check")
            return []
        
        subjects = []
        with open(participants_file, 'r') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                subjects.append(row['participant_id'])
        return subjects

    def run(self):
        if self.verify_rawdata:
            self.verify_raw_vs_qsiprep()

        if self.connectivity_only:
            self.logger.info(
                "connectivity_only: skipping SRC/FIB generation and the longitudinal diff step; "
                "using FIB files that already exist."
            )
            dwi_files = []
            fib_files = self.find_existing_fib_files()
            self.stats["found"] = len(fib_files)
        else:
            dwi_files = self.find_qsiprep_files()
            fib_files = []

            for idx, dwi in enumerate(dwi_files, 1):
                # Show progress
                self.print_progress_summary(idx - 1, len(dwi_files))
                self.logger.info(f"Processing {dwi.name} [{idx}/{len(dwi_files)}]")

                # Extract subject and session IDs
                sub_id = dwi.name.split('_')[0]
                ses_id = dwi.name.split('_')[1] if '_ses-' in dwi.name else 'ses-1'

                session_info = {
                    'session_id': ses_id,
                    'dwi_file': dwi.name,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'param0': self.param0,
                    'status': 'failed'
                }

                src = self.generate_src(dwi)
                if src:
                    session_info['src_file'] = src.name
                    # Validate SRC
                    if src.exists() and src.stat().st_size > 0:
                        self.logger.info(f"✓ SRC validated: {src.name} ({src.stat().st_size / (1024*1024):.1f} MB)")

                    fib = self.reconstruct_fib(src)
                    self.logger.debug(f"reconstruct_fib returned: {fib}")
                    if fib:
                        fib_files.append(fib)
                        self.logger.debug(f"Added FIB to list: {fib.name}, total now: {len(fib_files)}")
                        session_info['fib_file'] = fib.name
                        session_info['status'] = 'success'
                        self.stats["processed"] += 1
                    else:
                        self.logger.debug(f"reconstruct_fib returned None/False")
                else:
                    # If SRC generation was skipped, check if FIB already exists
                    if self.skip_existing:
                        # Use the same base_id logic as generate_src
                        dwi_subject_id = dwi.name.split('_')[0]
                        dwi_session_id = ""
                        if "_ses-" in dwi.name:
                            dwi_session_id = "_" + dwi.name.split('_')[1]
                        subject_prefix = f"{dwi_subject_id}{dwi_session_id}"

                        method_suffixes = {
                            '4': ['.odf.gqi.fz'],  # GQI
                            '7': ['.odf.qsdr.fz'],  # QSDR
                        }
                        method_key = str(self.method) if self.method else '4'
                        expected_suffixes = method_suffixes.get(method_key, ['*'])
                        search_dir = self.output_dir / dwi_subject_id / "fib"

                        for suffix in expected_suffixes:
                            pattern = f"{subject_prefix}{suffix}"
                            matches = list(search_dir.glob(pattern))
                            self.logger.debug(f"Looking for FIB: {pattern} in {search_dir} -> {len(matches)} matches")
                            if matches:
                                fib = matches[0]
                                fib_files.append(fib)
                                self.logger.debug(f"Added FIB file: {fib.name}")
                                session_info['fib_file'] = fib.name
                                session_info['status'] = 'success'
                                self.stats["skipped_existing"] += 1
                                break
                    else:
                        self.stats["skipped_missing"] += 1

                # Track for HTML report
                if sub_id not in self.subject_details:
                    self.subject_details[sub_id] = []
                self.subject_details[sub_id].append(session_info)

        if self.run_connectivity:
            self.logger.info(f"Starting connectivity extraction step (found {len(fib_files)} FIB files)")
            if not fib_files:
                self.logger.warning("No FIB files found for connectivity extraction!")
            self.run_connectivity_extraction(fib_files)

        if self.connectivity_only:
            self._finalize_run(dwi_files, fib_files)
            return

        # --- Longitudinal Processing ---
        # Group FIBs by subject
        subject_fibs = {}
        for fib in fib_files:
            sub_id, ses_id = self._parse_sub_ses(fib)
            if not sub_id: continue
            if sub_id not in subject_fibs:
                subject_fibs[sub_id] = {}
            subject_fibs[sub_id][ses_id] = fib

        # Compute differences
        diff_groups = {} # key: "ses-2_minus_ses-1", value: [diff_fib1, diff_fib2...]

        # GQI (method 4) reconstructs in native space — each session has its own brain mask,
        # so voxel arrays are not index-aligned and cannot be subtracted directly.
        if str(self.method) == '4':
            self.logger.info("Skipping longitudinal diff: GQI (method 4) is native space — brain masks differ per session, subtraction is not valid.")
        else:
            self.logger.info("Checking for longitudinal data...")
            for sub_id, sessions in subject_fibs.items():
                if len(sessions) < 2:
                    continue

                # Sort sessions to identify baseline (earliest)
                sorted_sessions = sorted(sessions.keys())
                baseline_ses = sorted_sessions[0]
                baseline_fib = sessions[baseline_ses]

                self.logger.info(f"Subject {sub_id} has {len(sessions)} sessions. Baseline: {baseline_ses}")

                for followup_ses in sorted_sessions[1:]:
                    diff_fib = self.generate_longitudinal_diff(baseline_fib, sessions[followup_ses])
                    if diff_fib:
                        self.stats["diff_ok"] += 1
                        group_key = f"{followup_ses}_minus_{baseline_ses}"
                        if group_key not in diff_groups:
                            diff_groups[group_key] = []
                        diff_groups[group_key].append(diff_fib)
                    else:
                        self.stats["diff_failed"] += 1
                        error_msg = f"Differential FIB failed: {sub_id} {followup_ses} - {baseline_ses}"
                        self.stats["errors"].append(error_msg)
                        self.logger.warning(error_msg)

        # Create longitudinal databases
        for group_key, group_fibs in diff_groups.items():
            db_path = self.output_dir / f"longitudinal_{group_key}.db.fib.gz"
            
            # Check if database already exists
            if db_path.exists() and self.skip_existing and not self._should_force('database'):
                self.logger.info(f"Longitudinal database exists, skipping: {db_path.name}")
                continue
            elif db_path.exists() and self._should_force('database'):
                self.logger.info(f"Longitudinal database exists but --force database is set, will overwrite: {db_path.name}")
            
            self.logger.info(f"Creating longitudinal database for {group_key} ({len(group_fibs)} subjects)")
            self.create_database(group_fibs, output_db=db_path, index_name="qa")

        # --- Final Database Check ---
        # Check if all subjects from participants.tsv are processed
        if not self.pilot:
            expected_subjects = self._load_participants_tsv()
            if expected_subjects:
                processed_subjects = set(self.subject_details.keys())
                expected_set = set(expected_subjects)
                missing = expected_set - processed_subjects
                
                if missing:
                    self.logger.warning(f"Not all subjects processed. Missing: {sorted(missing)}")
                    self.logger.warning("Skipping database creation until all subjects are complete.")
                elif fib_files:
                    self.logger.info("All subjects from participants.tsv processed successfully!")
                    self.create_database(fib_files)
            elif fib_files:
                # No participants.tsv, create database anyway
                self.create_database(fib_files)
        
        self._finalize_run(dwi_files, fib_files)

    def _push_derivatives(self):
        """Push *only* this run's own output subtree to its remote - never
        touches qsiprep or any other sibling dataset (e.g. mriqc), since the
        push path is scoped to output_dir specifically. Opt-in via
        --push_derivatives; this never runs unless the user explicitly asks
        for it on this invocation, since it writes to a remote that may be
        shared with other pipelines/users.
        """
        if not self.use_datalad:
            self.logger.warning("--push_derivatives has no effect without --datalad (nothing was committed locally to push).")
            return
        # Absolute path, not a path relative to project_root: datalad resolves
        # a relative push path against the process's cwd (not -d's dataset),
        # which would silently push the wrong thing (or fail) if the pipeline
        # was invoked from anywhere other than project_root itself.
        push_path = str(self.output_dir)
        if self.dry_run:
            self.logger.info(f"[dry-run] would push {push_path} (under {self.project_root}) to its remote")
            return
        self.logger.info(f"Pushing derivatives ({push_path}) to remote - qsiprep and other sibling datasets are not touched...")
        result = subprocess.run(
            ["datalad", "push", "-d", str(self.project_root), "--to", "origin", "-r", push_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()[-2000:]
            self.logger.error(f"datalad push failed: {err}")
        else:
            self.logger.info("Push complete.")

    def _finalize_run(self, dwi_files: List[Path], fib_files: List[Path]):
        """Cleanup phase + final summary, shared by the full pipeline and connectivity-only mode."""
        # --- Cleanup Phase ---
        # Delete intermediate .tar.gz and other temporary files to free space
        self.logger.info("🧹 Starting cleanup of intermediate files...")
        # Recursive, so this covers every subject's nested src/fib/diff/
        # reports subfolders under output_dir in one pass.
        self._cleanup_intermediate_files(self.output_dir)
        self._cleanup_intermediate_files(self.connectivity_output_dir)
        self.logger.info("✓ Cleanup complete")

        # --- Final Summary ---
        self.print_progress_summary(len(dwi_files), len(dwi_files))

        # Build summary with error checking
        if self.use_datalad and not self.dry_run:
            self.logger.info("Rolling up subject dataset updates into the project superdataset...")
            # Scoped to output_dir, not the whole project_root - project_root
            # may be a large, pre-existing study root with unrelated content
            # (other pipelines' rawdata/derivatives); an unscoped recursive
            # save would sweep all of that into the dataset too.
            subprocess.run(
                ["datalad", "save", "-d", str(self.project_root), "-r", "-m", "Update subject datasets for this pipeline run",
                 str(self.output_dir.relative_to(self.project_root))],
                capture_output=True, text=True
            )

        if self.push_derivatives:
            self._push_derivatives()

        summary_lines = [
            "╔══════════════════════════════════════════════════════════════╗",
        ]

        # Check if there were any errors. In connectivity_only mode, "processed"
        # never increments (no SRC/FIB generation happens), so that check would
        # always misreport issues - judge success by FIB availability instead.
        has_issues = (
            self.stats['diff_failed'] > 0
            or (self.connectivity_only and not fib_files)
            or (not self.connectivity_only and self.stats['processed'] == 0)
        )
        if has_issues:
            summary_lines.append("║ ⚠️  PIPELINE COMPLETE WITH ISSUES                              ║")
        else:
            summary_lines.append("║ ✓ PIPELINE COMPLETE                                          ║")

        found_label = "Existing FIB files used" if self.connectivity_only else "Total DWI files found"
        summary_lines.extend([
            "╠══════════════════════════════════════════════════════════════╣",
            f"║ {found_label}:{' ' * (27 - len(found_label))}{self.stats.get('found', len(dwi_files)):>6}",
            f"║ Successfully processed:   {self.stats['processed']:>6}",
            f"║ SRC files validated:      {self.stats['src_ok']:>6}",
            f"║ FIB files validated:      {self.stats['fib_ok']:>6}",
            f"║ Skipped (existing):       {self.stats['skipped_existing']:>6}",
            f"║ Skipped (other reasons):  {self.stats['skipped_missing']:>6}",
        ])

        # Add differential FIB stats if longitudinal processing was done
        if self.stats['diff_ok'] > 0 or self.stats['diff_failed'] > 0:
            summary_lines.extend([
                "╠══════════════════════════════════════════════════════════════╣",
                f"║ Differential FIB OK:      {self.stats['diff_ok']:>6}",
                f"║ Differential FIB failed:  {self.stats['diff_failed']:>6}",
            ])

        summary_lines.append("╚══════════════════════════════════════════════════════════════╝")

        summary = "\n".join(summary_lines)
        self.logger.info(summary)

        # Log errors if any occurred
        if self.stats['errors']:
            self.logger.warning(f"\n⚠️  {len(self.stats['errors'])} ERROR(S) ENCOUNTERED:")
            for error in self.stats['errors'][:10]:  # Show first 10 errors
                self.logger.warning(f"  - {error}")
            if len(self.stats['errors']) > 10:
                self.logger.warning(f"  ... and {len(self.stats['errors']) - 10} more errors")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DSI Studio Preprocessing Pipeline")
    parser.add_argument("--qsiprep_dir", required=True, help="Path to qsiprep output directory")
    parser.add_argument("--qsiprep_datalad", action="store_true", help="Treat --qsiprep_dir as a read-only, (per-subject) nested DataLad dataset: install subject subdataset registrations and 'datalad get' DWI/bval/bvec/T1w/mask content on demand, right before it's needed. Never writes back to this source (no save/push/unlock/drop).")
    parser.add_argument("--qsiprep_datalad_source", help="If --qsiprep_dir doesn't exist yet (or isn't a DataLad dataset), clone it from this URL first, e.g. 'ssh://user@host/path/to/qsiprep' or a study-level superdataset URL if --qsiprep_dir lives inside --project_root (e.g. <project_root>/derivatives/qsiprep). Implies --qsiprep_datalad.")
    parser.add_argument("--qsiprep_datalad_branch", default="derivatives", help="Branch to checkout for the qsiprep source, wherever it exists - both at the top level of the clone and independently inside every nested subdataset (some labs keep real processed data on a dedicated branch rather than the default one, and a subdataset's pinned commit can be stale even once that branch has real data). Default: 'derivatives'.")
    parser.add_argument("--push_derivatives", action="store_true", help="After this run, 'datalad push' ONLY this run's own output subtree (--output_dir) to its remote - never touches qsiprep or any other sibling dataset. Requires --datalad. Opt-in per run; never happens automatically.")
    parser.add_argument("--output_dir", required=True, help="Path to output directory. Per-subject output is always nested as <output_dir>/<subject_id>/{src,fib,diff,reports}/... (mirrors qsiprep/DataLad's own per-subject layout); --datalad additionally makes each subject folder its own nested DataLad dataset.")
    parser.add_argument("--project_root", help="BIDS-style dataset root (default: inferred from output_dir, e.g. <root>/derivatives/<pipeline> -> <root>). Project-level state (like the Apptainer image pin) lives under <project_root>/code/dsistudio/")
    parser.add_argument("--dsi_studio_cmd", default="/data/local/software/dsi-studio/2025.04.16/dsi-studio/dsi_studio", help="Path to dsi_studio executable")
    parser.add_argument("--dsi_studio_path", help="Path to DSI Studio installation folder (containing dsi_studio executable)")
    parser.add_argument("--apptainer", action="store_true", help="Run DSI Studio via the GPU-capable Apptainer image instead of a bare-metal install (see installation/apptainer/)")
    parser.add_argument("--apptainer_image", help="Path to a specific .sif image (default: installation/apptainer's dsi_studio_latest.sif)")
    parser.add_argument("--apptainer_bind", help="Extra bind path(s) for the container (default: /data/local)")
    parser.add_argument("--datalad", action="store_true", help="Run every DSI Studio call via 'datalad containers-run' (implies --apptainer): project_root becomes a DataLad superdataset, each subject a nested dataset, and every SRC/FIB call becomes a single replayable, provenance-recording commit. Only bootstraps brand-new projects.")
    parser.add_argument("--method", default="4", help="Reconstruction method (4=GQI, 7=QSDR)")
    parser.add_argument("--param0", default="1.25", help="Diffusion sampling length ratio")
    parser.add_argument("--threads", default="8", help="Number of threads")
    parser.add_argument("--db_name", default="connectometry.db.fib.gz", help="Name of the output database file")
    parser.add_argument("--rawdata_dir", help="Path to raw BIDS data (for verification)")
    parser.add_argument("--verify_rawdata", action="store_true", help="Cross-check rawdata vs qsiprep outputs")
    parser.add_argument("--require_mask", action="store_true", help="Skip subjects without brain mask")
    parser.add_argument("--require_t1w", action="store_true", help="Skip subjects without T1w")
    parser.add_argument("--skip_existing", action="store_true", help="Skip subjects if SRC/FIB already exist")
    parser.add_argument("--force", nargs='?', const='all', help="Force overwrite: 'database' (only db), 'diffs', 'src', 'fib', 'all' (default: all)")
    parser.add_argument("--min_file_age", type=int, default=300, help="Minimum file age in seconds (default: 300s/5min) to avoid processing files still being written. Ignored under --qsiprep_datalad, where files are freshly fetched on demand and their local mtime reflects fetch time, not whether the source data is still being written.")
    parser.add_argument("--session", default="all", help="BIDS session to process, e.g. 'ses-1' or '1' (default: all sessions)")
    parser.add_argument("--acq", default="all", help="BIDS acq tag to filter by, e.g. 'multi' (default: all)")
    parser.add_argument("--space", default="all", help="BIDS space tag to filter by, e.g. 'ACPC' (default: all)")
    parser.add_argument("--pilot", action="store_true", help="Process only one randomly chosen subject")
    parser.add_argument("--dry_run", action="store_true", help="Show commands without running them")
    parser.add_argument("--run_connectivity", action="store_true", help="Run connectivity extraction after FIB generation")
    parser.add_argument("--connectivity_only", action="store_true", help="Skip SRC/FIB (re)generation and the longitudinal diff step; run connectivity extraction directly on FIB files that already exist (implies --run_connectivity)")
    parser.add_argument("--connectivity_config", help="Path to connectivity extractor JSON config (e.g., graph_analysis_config.json)")
    parser.add_argument("--connectivity_output_dir", help="Directory for connectivity outputs (default: output_dir/connectivity)")
    parser.add_argument("--connectivity_threads", type=int, help="Thread override for connectivity extraction")
    
    args = parser.parse_args()
    
    pipeline = DSIStudioPipeline(args)
    pipeline.run()
