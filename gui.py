#!/usr/bin/env python3
"""
Lightweight Flask + Waitress web UI to run the repo's scripts:
- Pipeline (dsi_studio_pipeline.py)
- Connectometry (run_connectometry_batch.py)
- Interactive viewer generation (generate_interactive_viewer.py)

Features
- Simple forms for each task
- Save current form values to a JSON preset
- Background job runner with log files
- Auto-select a free port if the preferred one is taken
- Cross-platform (no Linux-only assumptions)
"""

import json
import logging
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import atexit
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlopen

from flask import Flask, jsonify, render_template, request, redirect, url_for, send_file
from waitress import serve

REPO_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_DIR / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR / "qa"))
from src_thumbnail import parse_sub_ses as _parse_thumbnail_sub_ses  # noqa: E402
TEMPLATES_DIR = REPO_DIR / "templates"
LOG_DIR = SCRIPTS_DIR / "web_logs"
SETTINGS_DIR = SCRIPTS_DIR / "web_settings"
SERVER_STATE_FILE = SETTINGS_DIR / "webui_server_state.json"
# Machine-local index of known projects (name/project_root/profile_path only)
# so the UI can list and re-open them - the actual project data lives in each
# project's own <project_root>/code/dsistudio/project.json, never here.
PROJECTS_REGISTRY_FILE = SETTINGS_DIR / "projects_registry.json"
APP_SIGNATURE = "dsi-studio-webui"
# Matches the default in scripts/pipeline/dsi_studio_pipeline.py's --dsi_studio_cmd
# argument, so atlas discovery looks in the same place the pipeline actually runs.
DEFAULT_DSI_STUDIO_CMD = "/data/local/software/dsi-studio/2025.04.16/dsi-studio/dsi_studio"
# installation/install_git_annex.sh's default install location. Job
# subprocesses need this prepended to PATH explicitly (see launch_job) rather
# than relying on inherited PATH: the web server's own process environment is
# whatever it was at server *startup*, which predates this install for any
# server instance already running when install_git_annex.sh is (re-)run -
# restarting the server isn't required for jobs to pick up the fix.
GIT_ANNEX_STANDALONE_BIN = Path("/data/local/software/git-annex-standalone/git-annex.linux")
# This lab's shared study repository - prefilled as a starting point when
# browsing for a qsiprep DataLad source on the New Project page, so users
# don't have to already know/retype the exact host and base path by hand.
# "host" is an SSH config alias (see installation/SETUP.md), not the bare
# hostname - DataLad's own SSH wrapper (datalad sshrun) expects a
# "[user@]hostname" login target with a single '@', which breaks for this
# lab's email-style accounts (user@domain.tld) if the user is embedded
# directly in the URL as user@domain.tld@host. The alias keeps the URL
# DataLad ever sees free of a second '@'; plain SSH (used for browsing below)
# works fine either way since it isn't affected by that parsing bug.
DEFAULT_QSIPREP_REMOTE = {
    "user": "",
    "host": "mri-it035016",
    "path": "/datalad/mri/MRI-Lab_Repository",
}

LOG_DIR.mkdir(exist_ok=True)
SETTINGS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webui")

jobs_lock = threading.Lock()
jobs: Dict[str, Dict] = {}
job_processes: Dict[str, subprocess.Popen] = {}


def _is_process_running(pid: int) -> bool:
    """Return True if a process with this PID exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _public_host_for_url(host: str) -> str:
    """Map bind host to a browser-friendly host."""
    if host in ("0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def _build_ui_url(host: str, port: int) -> str:
    return f"http://{_public_host_for_url(host)}:{port}"


def _url_reachable(url: str, timeout: float = 1.5) -> bool:
    """Return True if an HTTP endpoint responds."""
    try:
        with urlopen(url, timeout=timeout) as resp:  # nosec B310 - local loopback/UI check only
            return int(getattr(resp, "status", 0)) in {200, 301, 302, 303, 307, 308}
    except URLError:
        return False
    except Exception:
        return False


def _url_has_expected_ui(url: str, timeout: float = 1.5) -> bool:
    """Return True when the URL appears to be this Web UI instance."""
    probe_url = f"{url.rstrip('/')}/api/health"
    try:
        with urlopen(probe_url, timeout=timeout) as resp:  # nosec B310 - local loopback/UI check only
            if int(getattr(resp, "status", 0)) != 200:
                return False
            payload = json.load(resp)
            return payload.get("app") == APP_SIGNATURE
    except Exception:
        return False


def _load_server_state() -> Optional[Dict]:
    if not SERVER_STATE_FILE.exists():
        return None
    try:
        with open(SERVER_STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_server_state(pid: int, host: str, port: int):
    state = {
        "pid": pid,
        "host": host,
        "port": port,
        "url": _build_ui_url(host, port),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(SERVER_STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _clear_server_state_if_owned(owner_pid: int):
    """Remove state file only if this process wrote it."""
    state = _load_server_state()
    if state and state.get("pid") == owner_pid and SERVER_STATE_FILE.exists():
        try:
            SERVER_STATE_FILE.unlink()
        except OSError:
            pass


def _open_browser(url: str):
    """Open browser best-effort without failing server startup."""
    try:
        webbrowser.open_new_tab(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not auto-open browser: {exc}")


def _resolve_input_path(path_value: Optional[str]) -> Path:
    """Resolve user-provided path for filesystem browsing."""
    if not path_value:
        return REPO_DIR
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = (REPO_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _atlas_human_dir(dsi_studio_cmd: Optional[str]) -> Path:
    """DSI Studio ships its bundled atlases at <install_dir>/atlas/human -
    same lookup dsi_studio_pipeline.py uses to validate a connectivity
    config's atlas list before a run.
    """
    cmd = (dsi_studio_cmd or "").strip() or DEFAULT_DSI_STUDIO_CMD
    return _resolve_input_path(cmd).parent / "atlas" / "human"


def _resolve_project_settings_dir(project_root_value: Optional[str]) -> Optional[Path]:
    """Project-scoped presets dir (<project_root>/code/dsistudio/presets) if
    project_root points at a real, existing directory; None otherwise so
    callers can fall back to the shared SETTINGS_DIR.
    """
    if not project_root_value:
        return None
    candidate = _resolve_input_path(str(project_root_value))
    if not candidate.exists() or not candidate.is_dir():
        return None
    return candidate / "code" / "dsistudio" / "presets"


def _settings_search_dirs(project_root_value: Optional[str]) -> List[Path]:
    """Directories to look for presets in, project-scoped dir first (when
    valid) so newer per-project saves take precedence over the legacy shared
    location, but old presets saved before project-scoping remain visible.
    """
    project_dir = _resolve_project_settings_dir(project_root_value)
    return [project_dir, SETTINGS_DIR] if project_dir else [SETTINGS_DIR]


def _load_projects_registry() -> List[Dict]:
    if not PROJECTS_REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(PROJECTS_REGISTRY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_projects_registry(entries: List[Dict]):
    PROJECTS_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_REGISTRY_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _upsert_project_registry_entry(entry: Dict):
    """Add or update this machine's project index by project_root - the
    stable identity for a project. Only a pointer (name/project_root/
    profile_path) is stored here; the actual profile lives in that project's
    own folder, never duplicated into the registry.
    """
    entries = _load_projects_registry()
    entries = [e for e in entries if e.get("project_root") != entry["project_root"]]
    entries.append(entry)
    entries.sort(key=lambda e: (e.get("name") or e.get("project_root") or "").lower())
    _save_projects_registry(entries)


def _list_directory_entries(path: Path, mode: str) -> Dict:
    """Return sorted directory entries for file/folder picker."""
    target = path
    selected_file = None

    if target.exists() and target.is_file():
        selected_file = str(target)
        target = target.parent

    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {target}")

    entries = []
    for child in target.iterdir():
        try:
            is_dir = child.is_dir()
        except OSError:
            continue

        if mode == "dir" and not is_dir:
            continue

        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "is_dir": is_dir,
            }
        )

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "cwd": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "mode": mode,
        "selected_file": selected_file,
        "entries": entries,
        "roots": [
            str(REPO_DIR),
            str(Path.home()),
            "/",
        ],
    }


def _json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _get_json_payload() -> Dict:
    """Parse request JSON as an object and raise ValueError on bad input."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Invalid JSON body; expected an object")
    return payload


def find_free_port(start_port: int) -> int:
    """Return the first free port at or above start_port."""
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(("0.0.0.0", port)) != 0:
                return port
        port += 1


def _job_env() -> Dict[str, str]:
    """Environment for job subprocesses: inherits this server process's own
    environment, plus the standalone git-annex build prepended to PATH (see
    GIT_ANNEX_STANDALONE_BIN) so --qsiprep_datalad works regardless of
    whether this particular server instance was started before or after
    installation/install_git_annex.sh was (re-)run.
    """
    env = os.environ.copy()
    if GIT_ANNEX_STANDALONE_BIN.is_dir():
        env["PATH"] = f"{GIT_ANNEX_STANDALONE_BIN}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run_job(job_id: str, cmd: List[str], cwd: Optional[Path]):
    start_ts = time.time()
    log_file = Path(jobs[job_id]["log_file"])
    rc = -1
    try:
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write(f"Command: {' '.join(cmd)}\n")
            fh.flush()
            # start_new_session puts the whole process tree (this wrapper,
            # apptainer, dsi_studio inside it) in its own process group, so
            # /api/jobs/<id>/stop can kill all of it together via killpg -
            # killing just this top pid would leave the container running.
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None, stdout=fh, stderr=fh,
                start_new_session=True, env=_job_env(),
            )
            with jobs_lock:
                job_processes[job_id] = proc
            rc = proc.wait()
    except Exception as exc:  # noqa: BLE001
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(f"Exception: {exc}\n")
    finally:
        with jobs_lock:
            job_processes.pop(job_id, None)
    end_ts = time.time()
    with jobs_lock:
        if jobs[job_id]["status"] != "stopped":
            jobs[job_id]["status"] = "completed" if rc == 0 else "failed"
        jobs[job_id]["return_code"] = rc
        jobs[job_id]["ended_at"] = datetime.now(timezone.utc).isoformat()
        jobs[job_id]["duration_sec"] = round(end_ts - start_ts, 2)


def _resolve_log_dir(project_root_value: Optional[str]) -> Path:
    """<project_root>/code/dsistudio/logs/ when a valid project root is given,
    else the shared scripts/web_logs/ dir (same fallback rule as presets).
    """
    if project_root_value:
        candidate = _resolve_input_path(str(project_root_value))
        if candidate.exists() and candidate.is_dir():
            return candidate / "code" / "dsistudio" / "logs"
    return LOG_DIR


def launch_job(cmd: List[str], job_type: str, cwd: Optional[Path] = None, project_root: Optional[str] = None) -> Dict[str, str]:
    """Launch a subprocess in a background thread and track it."""
    # Use UUID-based job IDs to avoid collisions for rapid consecutive runs.
    job_id = uuid.uuid4().hex
    log_dir = _resolve_log_dir(project_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{job_type}_{job_id}.log"
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "cmd": cmd,
            "cwd": str(cwd) if cwd else None,
            "log_file": str(log_file),
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    thread = threading.Thread(target=_run_job, args=(job_id, cmd, cwd), daemon=True)
    thread.start()
    return {"job_id": job_id, "log_file": str(log_file)}


def build_pipeline_command(payload: Dict) -> List[str]:
    required = ["qsiprep_dir", "output_dir"]
    for key in required:
        if not payload.get(key):
            raise ValueError(f"Missing required field: {key}")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "pipeline" / "dsi_studio_pipeline.py"),
        "--qsiprep_dir",
        payload["qsiprep_dir"],
        "--output_dir",
        payload["output_dir"],
    ]

    optional_args = {
        "project_root": "--project_root",
        "dsi_studio_cmd": "--dsi_studio_cmd",
        "dsi_studio_path": "--dsi_studio_path",
        "method": "--method",
        "param0": "--param0",
        "threads": "--threads",
        "db_name": "--db_name",
        "rawdata_dir": "--rawdata_dir",
        "qsiprep_datalad_source": "--qsiprep_datalad_source",
        "qsiprep_datalad_branch": "--qsiprep_datalad_branch",
        "min_file_age": "--min_file_age",
        "connectivity_config": "--connectivity_config",
        "connectivity_output_dir": "--connectivity_output_dir",
        "connectivity_threads": "--connectivity_threads",
        "force": "--force",
        "apptainer_image": "--apptainer_image",
        "apptainer_bind": "--apptainer_bind",
        "subject": "--subject",
        "session": "--session",
        "acq": "--acq",
        "space": "--space",
    }

    for field, flag in optional_args.items():
        value = payload.get(field)
        if value not in (None, ""):
            cmd.extend([flag, str(value)])

    bool_flags = [
        "require_mask",
        "require_t1w",
        "skip_existing",
        "verify_rawdata",
        "pilot",
        "dry_run",
        "run_connectivity",
        "connectivity_only",
        "apptainer",
        "datalad",
        "qsiprep_datalad",
        "push_derivatives",
    ]
    for flag in bool_flags:
        if payload.get(flag):
            # dsi_studio_pipeline.py defines these flags with underscores.
            cmd.append(f"--{flag}")

    return cmd


def build_qa_command(payload: Dict) -> List[str]:
    if not payload.get("source_dir"):
        raise ValueError("Missing required field: source_dir")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "qa" / "run_qc.py"),
        payload["source_dir"],
    ]

    optional_args = {
        "output_dir": "--output_dir",
        "dsi_studio_cmd": "--dsi_studio_cmd",
        "apptainer_image": "--apptainer_image",
        "min_coherence": "--min_coherence",
        "min_r2": "--min_r2",
        "borderline_margin": "--borderline_margin",
        "flagged_subjects_out": "--flagged_subjects_out",
    }
    for field, flag in optional_args.items():
        value = payload.get(field)
        if value not in (None, ""):
            cmd.extend([flag, str(value)])

    if payload.get("check_btable") in (0, 1, "0", "1", False, True):
        cmd.extend(["--check_btable", "1" if payload["check_btable"] else "0"])

    for flag in ["apptainer", "skip_src", "skip_fib"]:
        if payload.get(flag):
            cmd.append(f"--{flag}")

    return cmd


def build_thumbnails_command(payload: Dict) -> List[str]:
    if not payload.get("output_dir"):
        raise ValueError("Missing required field: output_dir")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "qa" / "src_thumbnail.py"),
        payload["output_dir"],
    ]
    if payload.get("thumbnails_dir"):
        cmd.extend(["--thumbnails_dir", payload["thumbnails_dir"]])
    if payload.get("slice_frac") not in (None, ""):
        cmd.extend(["--slice_frac", str(payload["slice_frac"])])
    if payload.get("force"):
        cmd.append("--force")

    return cmd


def build_connectometry_command(payload: Dict) -> List[str]:
    if not payload.get("config"):
        raise ValueError("Missing required field: config")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "connectivity" / "run_connectometry_batch.py"),
        "--config",
        payload["config"],
    ]

    if payload.get("output"):
        cmd.extend(["--output", payload["output"]])
    if payload.get("batch") not in (None, ""):
        cmd.extend(["--batch", str(payload["batch"])])
    if payload.get("workers"):
        cmd.extend(["--workers", str(payload["workers"])])
    if payload.get("custom"):
        cmd.extend(["--custom", payload["custom"]])
    if payload.get("retry_failed"):
        cmd.extend(["--retry-failed", payload["retry_failed"]])
    if payload.get("test"):
        cmd.append("--test")
    if payload.get("dry_run"):
        cmd.append("--dry-run")
    if payload.get("nohup"):
        cmd.append("--nohup")

    return cmd


def build_viewer_command(payload: Dict) -> List[str]:
    if not payload.get("input_folder"):
        raise ValueError("Missing required field: input_folder")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "visualization" / "generate_interactive_viewer.py"),
        payload["input_folder"],
    ]

    if payload.get("output"):
        cmd.extend(["--output", payload["output"]])
    if payload.get("output_name"):
        cmd.extend(["--output-name", payload["output_name"]])
    if payload.get("jpeg_quality"):
        cmd.extend(["--jpeg-quality", str(payload["jpeg_quality"])] )
    if payload.get("tt_min_bytes"):
        cmd.extend(["--tt-min-bytes", str(payload["tt_min_bytes"])])
    if payload.get("max_width") is not None and payload.get("max_width") != "":
        cmd.extend(["--max-width", str(payload["max_width"])])
    if payload.get("placeholder"):
        cmd.extend(["--placeholder", payload["placeholder"]])
    if payload.get("placeholder_quality"):
        cmd.extend(["--placeholder-quality", str(payload["placeholder_quality"])] )
    if payload.get("require_tt") is False:
        cmd.append("--no-require-tt")
    if payload.get("enable_placeholder") is False:
        cmd.append("--no-placeholder")

    return cmd


@app.route("/")
def index():
    # Force a single canonical entry page to avoid root-path rendering inconsistencies.
    return redirect(url_for("projects_page"))


@app.after_request
def add_no_cache_headers(response):
    """Prevent stale UI content from being served by browser/editor caches."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/projects")
def projects_page():
    try:
        return render_template("projects.html", active_page="projects")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to render webui template")
        return (
            "<h1>DSI Studio Web UI Template Error</h1>"
            f"<p>{exc}</p>"
            f"<p>Templates path: {TEMPLATES_DIR}</p>",
            500,
        )


@app.route("/pipeline")
def pipeline_page():
    return render_template("pipeline.html", active_page="pipeline")


@app.route("/connectometry")
def connectometry_page():
    return render_template("connectometry.html", active_page="connectometry")


@app.route("/connectivity-settings")
def connectivity_settings_page():
    return render_template("connectivity_settings.html", active_page="pipeline")


@app.route("/viewer")
def viewer_page():
    return render_template("viewer.html", active_page="viewer")


@app.route("/api/project/create", methods=["POST"])
def api_project_create():
    """Create a new project: makes project_root/output_dir/connectivity
    output_dir on disk (they're this pipeline's own writable locations) and
    writes a canonical profile to <project_root>/code/dsistudio/project.json
    so the project can be reloaded later (e.g. on another machine, or after
    clearing browser storage) instead of living only in browser localStorage.

    qsiprep_dir and rawdata_dir are treated as read-only *inputs* - they are
    never created, only checked for existence, since silently mkdir'ing them
    would mask a genuinely missing/misspelled source dataset.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    project_root_value = str(payload.get("project_root", "")).strip()
    if not project_root_value:
        return _json_error("Missing project_root", 400)

    project_root = _resolve_input_path(project_root_value)
    created = []
    warnings = []

    try:
        project_root.mkdir(parents=True, exist_ok=True)
        created.append(str(project_root))

        output_dir_value = str(payload.get("output_dir", "")).strip()
        if output_dir_value:
            output_dir = _resolve_input_path(output_dir_value)
            output_dir.mkdir(parents=True, exist_ok=True)
            created.append(str(output_dir))

        connectivity_output_value = str(payload.get("connectivity_output_dir", "")).strip()
        if connectivity_output_value:
            connectivity_output_dir = _resolve_input_path(connectivity_output_value)
            connectivity_output_dir.mkdir(parents=True, exist_ok=True)
            created.append(str(connectivity_output_dir))
    except OSError as exc:
        return _json_error(f"Could not create project folders: {exc}", 500)

    qsiprep_dir_value = str(payload.get("qsiprep_dir", "")).strip()
    qsiprep_datalad_source = str(payload.get("qsiprep_datalad_source", "")).strip()
    if qsiprep_dir_value:
        qsiprep_dir = _resolve_input_path(qsiprep_dir_value)
        if not qsiprep_dir.exists() and not qsiprep_datalad_source:
            warnings.append(f"QSIPrep directory does not exist yet: {qsiprep_dir}")

    rawdata_dir_value = str(payload.get("rawdata_dir", "")).strip()
    if rawdata_dir_value and not _resolve_input_path(rawdata_dir_value).exists():
        warnings.append(f"Rawdata directory does not exist yet: {rawdata_dir_value}")

    profile_fields = [
        "name", "project_root", "qsiprep_dir", "qsiprep_datalad", "qsiprep_datalad_source",
        "qsiprep_datalad_branch", "output_dir", "rawdata_dir", "connectivity_config",
        "connectivity_output_dir",
    ]
    profile = {field: payload.get(field, "") for field in profile_fields}
    profile["created_at"] = datetime.now(timezone.utc).isoformat()

    profile_dir = project_root / "code" / "dsistudio"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / "project.json"
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)

    _upsert_project_registry_entry({
        "name": profile.get("name") or project_root.name,
        "project_root": str(project_root),
        "profile_path": str(profile_path),
        "created_at": profile["created_at"],
    })

    return jsonify({
        "ok": True,
        "created": created,
        "warnings": warnings,
        "profile_path": str(profile_path),
        "profile": profile,
    })


@app.route("/api/projects/list", methods=["GET"])
def api_projects_list():
    """List projects known on this machine, from the shared registry file -
    this is only a pointer list (name/project_root/profile_path); the actual
    project data lives in each project's own project.json. Entries whose
    profile file has since disappeared (folder moved/deleted) are pruned
    automatically so the list doesn't accumulate dead links.
    """
    entries = _load_projects_registry()
    kept = [e for e in entries if e.get("profile_path") and Path(e["profile_path"]).exists()]
    if len(kept) != len(entries):
        _save_projects_registry(kept)
    return jsonify(kept)


@app.route("/api/projects/register", methods=["POST"])
def api_projects_register():
    """Add an existing project (already has its own project.json somewhere)
    to this machine's registry, without creating any folders - used when a
    profile is loaded from an arbitrary file path so it becomes easy to find
    again next time, instead of only living wherever it was browsed from.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    project_root_value = str(payload.get("project_root", "")).strip()
    profile_path_value = str(payload.get("profile_path", "")).strip()
    if not project_root_value or not profile_path_value:
        return _json_error("Missing project_root or profile_path", 400)

    _upsert_project_registry_entry({
        "name": str(payload.get("name") or "").strip() or Path(project_root_value).name,
        "project_root": str(_resolve_input_path(project_root_value)),
        "profile_path": str(_resolve_input_path(profile_path_value)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return jsonify({"ok": True})


@app.route("/api/projects/forget", methods=["POST"])
def api_projects_forget():
    """Remove a project from this machine's registry list only - never
    touches the project's own files (project.json, data, output, etc), so
    it's always recoverable by loading/creating the project again.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    project_root_value = str(payload.get("project_root", "")).strip()
    if not project_root_value:
        return _json_error("Missing project_root", 400)

    entries = _load_projects_registry()
    remaining = [e for e in entries if e.get("project_root") != project_root_value]
    _save_projects_registry(remaining)
    return jsonify({"ok": True, "count": len(remaining)})


@app.route("/api/run/pipeline", methods=["POST"])
def api_run_pipeline():
    try:
        payload = _get_json_payload()
        cmd = build_pipeline_command(payload)
        job = launch_job(cmd, job_type="pipeline", cwd=REPO_DIR, project_root=payload.get("project_root"))
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({
        "ok": True,
        "app": APP_SIGNATURE,
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/run/qa", methods=["POST"])
def api_run_qa():
    try:
        payload = _get_json_payload()
        cmd = build_qa_command(payload)
        job = launch_job(cmd, job_type="qa", cwd=REPO_DIR, project_root=payload.get("project_root"))
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/run/thumbnails", methods=["POST"])
def api_run_thumbnails():
    """Backfill slice thumbnails for every SRC file already processed in a
    project (dsi_studio_pipeline.py only renders one for SRC files it
    generates itself going forward - this covers subjects processed before
    that hook existed, or after --force regenerated a SRC in place).
    """
    try:
        payload = _get_json_payload()
        cmd = build_thumbnails_command(payload)
        job = launch_job(cmd, job_type="thumbnails", cwd=REPO_DIR, project_root=payload.get("project_root"))
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/qc/thumbnails", methods=["GET"])
def api_qc_thumbnails():
    """List slice thumbnails available for a project's output_dir, for the
    Visual QC gallery panel. Scans <output_dir>/reports/thumbnails directly
    (rather than trusting a possibly-stale manifest.json) so newly-rendered
    thumbnails show up without needing a separate backfill run first.
    """
    output_dir_value = (request.args.get("output_dir") or "").strip()
    if not output_dir_value:
        return _json_error("Missing output_dir", 400)
    output_dir = _resolve_input_path(output_dir_value)
    thumbnails_dir = output_dir / "reports" / "thumbnails"
    if not thumbnails_dir.is_dir():
        return jsonify({"ok": True, "thumbnails": [], "count": 0})

    entries = []
    for png in sorted(thumbnails_dir.glob("*.png")):
        sub, ses = _parse_thumbnail_sub_ses(png.stem)
        entries.append({
            "name": png.stem,
            "subject": sub or None,
            "session": ses or None,
            "url": url_for("api_qc_thumbnail_file", output_dir=str(output_dir), name=png.name),
        })
    return jsonify({"ok": True, "thumbnails": entries, "count": len(entries)})


@app.route("/api/qc/thumbnail_file", methods=["GET"])
def api_qc_thumbnail_file():
    """Serve one thumbnail PNG. `name` is constrained to a bare filename
    (no path separators) and the resolved path is checked to stay inside
    <output_dir>/reports/thumbnails - output_dir itself is user-supplied
    (same trust model as the rest of this local-only tool's path picker),
    but this still guards against a crafted `name` walking outside that
    one subfolder.
    """
    output_dir_value = (request.args.get("output_dir") or "").strip()
    name = (request.args.get("name") or "").strip()
    if not output_dir_value or not name or "/" in name or "\\" in name:
        return _json_error("Invalid output_dir or name", 400)
    thumbnails_dir = _resolve_input_path(output_dir_value) / "reports" / "thumbnails"
    target = (thumbnails_dir / name).resolve()
    if target.parent != thumbnails_dir.resolve() or not target.is_file():
        return _json_error("Thumbnail not found", 404)
    return send_file(target, mimetype="image/png")


@app.route("/api/run/connectometry", methods=["POST"])
def api_run_connectometry():
    try:
        payload = _get_json_payload()
        cmd = build_connectometry_command(payload)
        job = launch_job(cmd, job_type="connectometry", cwd=REPO_DIR, project_root=payload.get("project_root"))
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/run/viewer", methods=["POST"])
def api_run_viewer():
    try:
        payload = _get_json_payload()
        cmd = build_viewer_command(payload)
        job = launch_job(cmd, job_type="viewer", cwd=REPO_DIR, project_root=payload.get("project_root"))
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values()))


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@app.route("/api/jobs/<job_id>/log", methods=["GET"])
def api_job_log(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return _json_error("Unknown job_id", 404)
    log_file = Path(job["log_file"])
    if not log_file.exists():
        return jsonify({"content": "", "status": job["status"]})
    max_bytes = 20_000
    size = log_file.stat().st_size
    with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            content = "... (truncated)\n" + fh.read()
        else:
            content = fh.read()
    return jsonify({"content": _ANSI_RE.sub("", content), "status": job["status"]})


@app.route("/api/jobs/<job_id>/stop", methods=["POST"])
def api_job_stop(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        proc = job_processes.get(job_id)
    if not job:
        return _json_error("Unknown job_id", 404)
    if job["status"] != "running" or proc is None:
        return _json_error("Job is not running", 400)

    with jobs_lock:
        jobs[job_id]["status"] = "stopped"

    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return jsonify({"ok": True})

    def _force_kill():
        # dsi_studio_pipeline.py catches SIGTERM and runs a best-effort
        # 'datalad save' rollup before exiting (see _safety_save() there) so
        # a stopped job doesn't leave its output untracked - give it real
        # time to finish that before escalating to SIGKILL, which can't be
        # caught or cleaned up after.
        time.sleep(30)
        try:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    threading.Thread(target=_force_kill, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/save_settings", methods=["POST"])
def api_save_settings():
    try:
        payload = _get_json_payload()
        default_name = f"settings_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
        filename = str(payload.get("filename") or default_name).strip()
        if not filename:
            return _json_error("Missing filename", 400)
        if "/" in filename or "\\" in filename:
            return _json_error("Invalid filename", 400)
        if not filename.lower().endswith(".json"):
            return _json_error("Filename must end with .json", 400)

        # Prefer the current project's own code/dsistudio/presets/ folder so
        # presets live alongside the project they belong to; fall back to the
        # shared scripts/web_settings/ dir when no (valid) project is set.
        target_dir = _resolve_project_settings_dir(payload.get("project_root")) or SETTINGS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename

        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload.get("settings", {}), fh, indent=2)
        return jsonify({"ok": True, "saved_to": str(target)})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)


@app.route("/api/list_settings", methods=["GET"])
def api_list_settings():
    project_root = request.args.get("project_root", "")
    seen = set()
    files = []
    for dir_path in _settings_search_dirs(project_root):
        if not dir_path.exists():
            continue
        for path in sorted(dir_path.glob("*.json")):
            if path.name in seen:
                continue
            seen.add(path.name)
            files.append({
                "name": path.name,
                "path": str(path),
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            })
    return jsonify(files)


@app.route("/api/settings/read", methods=["POST"])
def api_read_settings():
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    filename = str(payload.get("filename", "")).strip()
    if not filename:
        return _json_error("Missing filename", 400)
    if "/" in filename or "\\" in filename:
        return _json_error("Invalid filename", 400)

    target = None
    for dir_path in _settings_search_dirs(payload.get("project_root")):
        candidate = dir_path / filename
        if candidate.exists():
            target = candidate
            break
    if target is None:
        return _json_error("Preset not found", 404)

    try:
        with open(target, "r", encoding="utf-8") as fh:
            content = json.load(fh)
        return jsonify({"ok": True, "settings": content})
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)


@app.route("/api/fs/list", methods=["POST"])
def api_fs_list():
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    mode = str(payload.get("mode", "any")).strip().lower()
    if mode not in {"any", "file", "dir"}:
        return _json_error("Invalid mode; use any|file|dir", 400)

    requested_path = str(payload.get("path", "")).strip()
    try:
        resolved = _resolve_input_path(requested_path)
        result = _list_directory_entries(resolved, mode)
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/fs/mkdir", methods=["POST"])
def api_fs_mkdir():
    """Create a new folder from the path picker, so choosing a brand-new
    project root/output dir doesn't require dropping out to a terminal first.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    name = str(payload.get("name", "")).strip()
    if not name:
        return _json_error("Missing folder name", 400)
    if "/" in name or "\\" in name:
        return _json_error("Folder name cannot contain path separators", 400)

    parent = _resolve_input_path(str(payload.get("path", "")).strip())
    target = parent / name
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _json_error(f"Could not create folder: {exc}", 500)

    return jsonify({"ok": True, "path": str(target)})


@app.route("/api/remote/ssh_default", methods=["GET"])
def api_remote_ssh_default():
    """This lab's known-good default SSH source (host/user/base path), so
    the New Project page can prefill the remote browser instead of everyone
    re-typing the same host and path by hand.
    """
    return jsonify({"ok": True, **DEFAULT_QSIPREP_REMOTE})


@app.route("/api/remote/ssh_list", methods=["POST"])
def api_remote_ssh_list():
    """List directories at a path on a remote host over SSH (read-only) -
    lets the New Project page browse e.g. MRI-Lab_Repository for the right
    study without already knowing the exact folder name. Requires
    passwordless (key-based) SSH access to already be set up; this only
    ever runs a read-only 'find', never writes anything remotely.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    user = str(payload.get("user", "")).strip()
    host = str(payload.get("host", "")).strip()
    path = str(payload.get("path", "")).strip() or "/"
    if not host:
        return _json_error("Missing host", 400)

    # user is optional: an SSH config alias (Host block) can supply it
    # instead, which is required anyway for DataLad's own SSH wrapper to
    # work with email-style ("user@domain.tld") logins - see SETUP.md.
    target = f"{user}@{host}" if user else host
    remote_cmd = f"find {shlex.quote(path)} -mindepth 1 -maxdepth 1 -printf '%f\\t%y\\n' 2>/dev/null | sort"
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, remote_cmd],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return _json_error(f"SSH to {target} timed out", 504)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)

    if result.returncode != 0:
        return _json_error(f"SSH to {target} failed: {(result.stderr or result.stdout).strip()[-500:]}", 502)

    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name, ftype = parts
        entries.append({"name": name, "is_dir": ftype == "d"})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    return jsonify({"ok": True, "user": user, "host": host, "path": path, "entries": entries})


@app.route("/api/fs/read_json", methods=["POST"])
def api_fs_read_json():
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    requested_path = str(payload.get("path", "")).strip()
    if not requested_path:
        return _json_error("Missing path", 400)

    target = _resolve_input_path(requested_path)
    if not target.exists() or not target.is_file():
        return _json_error(f"File not found: {target}", 404)

    try:
        with open(target, "r", encoding="utf-8") as fh:
            content = json.load(fh)
        return jsonify({"ok": True, "content": content})
    except json.JSONDecodeError as exc:
        return _json_error(f"Invalid JSON in {target}: {exc}", 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)


@app.route("/api/fs/write_json", methods=["POST"])
def api_fs_write_json():
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    requested_path = str(payload.get("path", "")).strip()
    if not requested_path:
        return _json_error("Missing path", 400)
    if not requested_path.lower().endswith(".json"):
        return _json_error("Path must end with .json", 400)

    target = _resolve_input_path(requested_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload.get("content", {}), fh, indent=2)
        return jsonify({"ok": True, "saved_to": str(target)})
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)


@app.route("/api/atlases/list", methods=["GET"])
def api_atlases_list():
    """List atlases actually installed on this machine (matching .nii.gz +
    .txt label file pairs under DSI Studio's atlas/human dir), so the
    connectivity settings UI only offers atlases that will actually be found
    at run time instead of a hardcoded, possibly stale, name list.
    """
    dsi_studio_cmd = request.args.get("dsi_studio_cmd", "")
    atlas_dir = _atlas_human_dir(dsi_studio_cmd)
    if not atlas_dir.is_dir():
        return jsonify({"ok": True, "atlas_dir": str(atlas_dir), "found": False, "atlases": []})

    atlases = sorted(
        p.name[: -len(".nii.gz")]
        for p in atlas_dir.glob("*.nii.gz")
        if (atlas_dir / f"{p.name[:-len('.nii.gz')]}.txt").exists()
    )
    return jsonify({"ok": True, "atlas_dir": str(atlas_dir), "found": True, "atlases": atlases})


@app.route("/api/bids/entities", methods=["POST"])
def api_bids_entities():
    """Scan a qsiprep dir's preprocessed DWI filenames for the BIDS entities
    (session/acq/space) actually present, so the UI can offer real choices
    instead of free text.
    """
    try:
        payload = _get_json_payload()
    except ValueError as exc:
        return _json_error(str(exc), 400)

    qsiprep_dir = str(payload.get("qsiprep_dir", "")).strip()
    if not qsiprep_dir:
        return _json_error("Missing qsiprep_dir", 400)
    root = Path(qsiprep_dir).expanduser()
    if not root.is_dir():
        return _json_error(f"Not a directory: {qsiprep_dir}", 400)

    dwi_files = list(root.glob("sub-*/dwi/*_desc-preproc_dwi.nii.gz"))
    dwi_files += list(root.glob("sub-*/ses-*/dwi/*_desc-preproc_dwi.nii.gz"))
    if not dwi_files:
        dwi_files = list(root.glob("*_desc-preproc_dwi.nii.gz"))

    sessions, acqs, spaces = set(), set(), set()
    for f in dwi_files:
        for part in f.name.split("_"):
            if part.startswith("ses-"):
                sessions.add(part[len("ses-"):])
            elif part.startswith("acq-"):
                acqs.add(part[len("acq-"):])
            elif part.startswith("space-"):
                spaces.add(part[len("space-"):])

    return jsonify({
        "ok": True,
        "file_count": len(dwi_files),
        "sessions": sorted(sessions),
        "acqs": sorted(acqs),
        "spaces": sorted(spaces),
    })


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Flask + Waitress UI for DSI Studio helpers")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Preferred port (auto-increment if busy)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the UI URL in a browser")
    parser.add_argument("--new-instance", action="store_true", help="Start a new server even if an existing instance is already running")
    args = parser.parse_args()

    # Reuse an existing running instance by default to avoid repeated port/open prompts.
    if not args.new_instance:
        state = _load_server_state()
        state_pid = -1
        if state:
            try:
                state_pid = int(state.get("pid", -1))
            except (TypeError, ValueError):
                state_pid = -1
        if state and _is_process_running(state_pid):
            saved_host = state.get("host", args.host)
            try:
                saved_port = int(state.get("port", args.port))
            except (TypeError, ValueError):
                saved_port = args.port
            url = state.get("url") or _build_ui_url(saved_host, saved_port)
            if _url_reachable(url) and _url_has_expected_ui(url):
                logger.info(f"Web UI already running at {url} (pid {state.get('pid')})")
                if not args.no_open:
                    _open_browser(url)
                return
            logger.warning("Saved Web UI instance did not match expected app signature; starting a fresh instance.")
        if state and SERVER_STATE_FILE.exists():
            # Remove stale state from a dead process.
            try:
                SERVER_STATE_FILE.unlink()
            except OSError:
                pass

    port = find_free_port(args.port)
    if port != args.port:
        logger.info(f"Port {args.port} in use, using {port} instead")

    _save_server_state(os.getpid(), args.host, port)
    atexit.register(_clear_server_state_if_owned, os.getpid())

    url = _build_ui_url(args.host, port)
    logger.info("Starting DSI Studio Web UI...")
    logger.info(f"Web UI available at: {url}")
    logger.info("Press Ctrl+C to stop")
    if not args.no_open:
        # Open after startup begins; non-blocking and best-effort.
        threading.Timer(1.2, _open_browser, args=[url]).start()

    serve(app, host=args.host, port=port)


if __name__ == "__main__":
    main()
