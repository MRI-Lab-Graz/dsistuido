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
from urllib.request import Request, urlopen

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
# Host-side atlas library, independent of whichever DSI Studio binary/Apptainer
# image is currently pinned (image rebuilds have changed the bundled atlas set
# before). See /data/local/software/dsi_studio_atlases/SOURCES.md.
SHARED_ATLAS_DIR = Path("/data/local/software/dsi_studio_atlases/human")
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
# Snapshot of `jobs` on disk, so a job launched before a `--restart` (the
# default - see main()) is still visible/stoppable after the new server
# process comes up, instead of silently vanishing from the UI while the
# subprocess (which outlives the restart - see start_new_session in
# _run_job) keeps running unseen.
JOBS_STATE_FILE = SETTINGS_DIR / "jobs_state.json"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ntfy.sh push notification config, same pattern as build_apptainer's
# notify_ntfy.sh: a gitignored, machine-local env file holding the topic
# (an unauthenticated shared secret) - see scripts/web_settings/ntfy.env.example.
# Absent by default, so notifications are opt-in and silently skipped until set up.
NTFY_ENV_FILE = SETTINGS_DIR / "ntfy.env"


def _save_jobs_snapshot(jobs_dict: Dict[str, Dict], path: Path = None) -> None:
    """Best-effort write of the jobs dict to disk. Never raises - a failed
    snapshot write shouldn't break the job it's tracking."""
    path = path or JOBS_STATE_FILE
    try:
        path.write_text(json.dumps(jobs_dict, indent=2))
    except OSError:
        pass


def _load_jobs_snapshot(path: Path = None) -> Dict[str, Dict]:
    """Read back the jobs snapshot. Missing/corrupt file -> {} (same
    "not configured yet" treatment as _load_ntfy_config)."""
    path = path or JOBS_STATE_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _persist_jobs() -> None:
    """Snapshot the current `jobs` dict to disk. Call after any mutation
    under jobs_lock (copy the dict while holding the lock, write outside it
    so file IO doesn't block other job operations)."""
    with jobs_lock:
        snapshot = dict(jobs)
    _save_jobs_snapshot(snapshot)


def _reconcile_persisted_jobs(jobs_dict: Dict[str, Dict], is_alive) -> Dict[str, Dict]:
    """After loading a jobs snapshot at startup, any job still marked
    "running" whose pid is no longer alive was orphaned by a server
    restart (or crash) - there's no thread left to ever mark it
    completed/failed, so it would otherwise show as "running" forever.
    Mark it "interrupted" instead; leave everything else untouched."""
    for job in jobs_dict.values():
        if job.get("status") == "running" and not is_alive(job.get("pid")):
            job["status"] = "interrupted"
    return jobs_dict


def _is_process_running(pid: int) -> bool:
    """Return True if a process with this PID exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_loopback_host(host: str) -> bool:
    """True if `host` only ever resolves to this machine (127.0.0.1,
    ::1, localhost). Used to gate --host: the /api/fs/*, /api/run/* and
    job-log routes have no auth and can read/write/execute anywhere this
    process can, on request from anyone who can reach the port - fine on
    loopback (only local users), not fine on a LAN/0.0.0.0 bind without an
    explicit opt-in.
    """
    import ipaddress
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # not a literal IP (e.g. "0.0.0.0" IS one and returns above; a hostname isn't guaranteed loopback)


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


def _kill_existing_instance(timeout: float = 5.0) -> bool:
    """Stop whatever process the saved server state points at, so --restart
    actually replaces it on the same port instead of --new-instance's
    behavior of leaving it running and silently binding a different port
    (find_free_port auto-increments) - the two look identical from the
    terminal, but only one actually picks up new code. Returns True if a live
    process was found and stopped, False if there was nothing to kill."""
    state = _load_server_state()
    pid = int(state.get("pid", -1)) if state else -1
    if not _is_process_running(pid):
        if state and SERVER_STATE_FILE.exists():
            try:
                SERVER_STATE_FILE.unlink()
            except OSError:
                pass
        return False

    logger.info(f"Stopping existing Web UI instance (pid {pid}) before starting a fresh one...")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_process_running(pid):
            break
        time.sleep(0.2)
    else:
        if _is_process_running(pid):
            logger.warning(f"pid {pid} did not exit after {timeout}s, sending SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(0.5)

    if SERVER_STATE_FILE.exists():
        try:
            SERVER_STATE_FILE.unlink()
        except OSError:
            pass
    return True


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


def _atlas_human_dir(dsi_studio_cmd: Optional[str] = None) -> Path:
    """Atlases live in SHARED_ATLAS_DIR, not <dsi_studio install>/atlas/human -
    that install-local path changes contents (and breaks entirely for the
    Apptainer wrapper) every time the pinned image is rebuilt. dsi_studio_cmd
    is accepted for API-compatibility with existing callers but no longer
    used; dsi_studio_pipeline.py's atlas validation uses the same SHARED_ATLAS_DIR.
    """
    return SHARED_ATLAS_DIR


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


def _load_ntfy_config() -> Optional[Dict[str, str]]:
    """Read NTFY_TOPIC (required) / NTFY_SERVER (optional) from ntfy.env,
    the same KEY=VALUE format as build_apptainer's config/ntfy.env. Returns
    None if the file doesn't exist yet or has no topic set, so callers can
    treat "not configured" as a silent no-op rather than an error.
    """
    if not NTFY_ENV_FILE.is_file():
        return None
    config: Dict[str, str] = {}
    for line in NTFY_ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        config[key.strip()] = value.strip()
    topic = config.get("NTFY_TOPIC")
    if not topic:
        return None
    server = (config.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    return {"topic": topic, "server": server}


def _send_ntfy(title: str, message: str, priority: str = "default") -> None:
    """Best-effort ntfy.sh push notification. Never raises - a notification
    failure shouldn't affect job status or the response to the UI.
    """
    config = _load_ntfy_config()
    if not config:
        return
    url = f"{config['server']}/{config['topic']}"
    req = Request(
        url,
        data=message.encode("utf-8"),
        method="POST",
        headers={"Title": title, "Priority": priority},
    )
    try:
        urlopen(req, timeout=10)
    except (URLError, OSError) as exc:
        logger.warning(f"ntfy notification failed: {exc}")


def _tail_log_text(log_file: Path, max_lines: int = 20) -> str:
    if not log_file.is_file():
        return ""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = _ANSI_RE.sub("", text)
    lines = text.splitlines()[-max_lines:]
    return "\n".join(lines)


def _notify_job_finished(job: Dict) -> None:
    """Push an ntfy notification for a finished job. Only called for jobs
    launched with notify=True (see launch_job) - i.e. "full" pipeline runs
    (not --dry_run/--pilot) and "full" connectometry runs (not
    --dry-run/--test), per the same "only tell me about the real thing"
    rule build_apptainer's autobuild notifications follow.
    """
    status = job.get("status", "unknown")
    duration = job.get("duration_sec")
    duration_str = f"{duration:.0f}s" if isinstance(duration, (int, float)) else "?"
    lines = [
        f"Host: {socket.gethostname()}",
        f"Status: {status} (exit {job.get('return_code')})",
        f"Duration: {duration_str}",
    ]
    if job.get("label"):
        lines.append(f"Target: {job['label']}")
    if status != "completed":
        tail = _tail_log_text(Path(job["log_file"]))
        if tail:
            lines.append("")
            lines.append("Last log lines:")
            lines.append(tail)
    title = f"DSI Studio {job.get('type')}: {status}"
    priority = "default" if status == "completed" else "high"
    _send_ntfy(title, "\n".join(lines), priority=priority)


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
                jobs[job_id]["pid"] = proc.pid
            _persist_jobs()
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
        job_snapshot = dict(jobs[job_id])
    _persist_jobs()

    # Manual stops are excluded - the user is already at the controls when
    # they hit "stop job", so a push notification would be noise.
    if job_snapshot.get("notify") and job_snapshot["status"] in ("completed", "failed"):
        try:
            _notify_job_finished(job_snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ntfy notification failed: {exc}")


def _resolve_log_dir(project_root_value: Optional[str]) -> Path:
    """<project_root>/code/dsistudio/logs/ when a valid project root is given,
    else the shared scripts/web_logs/ dir (same fallback rule as presets).
    """
    if project_root_value:
        candidate = _resolve_input_path(str(project_root_value))
        if candidate.exists() and candidate.is_dir():
            return candidate / "code" / "dsistudio" / "logs"
    return LOG_DIR


def launch_job(
    cmd: List[str],
    job_type: str,
    cwd: Optional[Path] = None,
    project_root: Optional[str] = None,
    notify: bool = False,
    label: Optional[str] = None,
) -> Dict[str, str]:
    """Launch a subprocess in a background thread and track it.

    notify: send an ntfy push notification (see _notify_job_finished) when
    this job finishes. Callers set this only for "full" runs - see the
    api_run_pipeline/api_run_connectometry routes for the dry_run/pilot/test
    checks that decide it.
    label: short human-readable target (e.g. output_dir or config path) to
    include in that notification.
    """
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
            "notify": notify,
            "label": label,
        }
    _persist_jobs()
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
        "flagged_subjects_out": "--flagged_subjects_out",
        "qsiprep_dir": "--qsiprep_dir",
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


@app.route("/qc-dashboard")
def qc_dashboard_page():
    return render_template("qc_dashboard.html", active_page="pipeline")


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
        # Only notify for full runs - --dry_run and --pilot are for checking
        # a command/single subject before committing to the real thing, not
        # something worth an unattended push notification.
        is_full_run = not payload.get("dry_run") and not payload.get("pilot")
        job = launch_job(
            cmd, job_type="pipeline", cwd=REPO_DIR, project_root=payload.get("project_root"),
            notify=is_full_run, label=payload.get("output_dir"),
        )
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

    Also cross-references <output_dir>/reports/qc_flags.json (written by
    run_qc.py's --qsiprep_dir/SRC/FIB passes, keyed by "sub-X_ses-Y") so a
    subject/session that numeric QC flagged shows up right on its thumbnail
    card - a b0 slice alone can look like a perfectly normal brain even when
    every b>0 shell is crushed (see src_thumbnail.py's docstring), so the
    image by itself isn't enough; the flag has to ride along with it.
    """
    output_dir_value = (request.args.get("output_dir") or "").strip()
    if not output_dir_value:
        return _json_error("Missing output_dir", 400)
    output_dir = _resolve_input_path(output_dir_value)
    thumbnails_dir = output_dir / "reports" / "thumbnails"
    if not thumbnails_dir.is_dir():
        return jsonify({"ok": True, "thumbnails": [], "count": 0})

    qc_flags = {}
    flags_path = output_dir / "reports" / "qc_flags.json"
    if flags_path.is_file():
        try:
            qc_flags = json.loads(flags_path.read_text())
        except (OSError, ValueError):
            qc_flags = {}

    entries = []
    for png in sorted(thumbnails_dir.glob("*.png")):
        sub, ses = _parse_thumbnail_sub_ses(png.stem)
        key = f"{sub}_{ses}" if sub and ses else (sub or png.stem)
        entries.append({
            "name": png.stem,
            "subject": sub or None,
            "session": ses or None,
            "url": url_for("api_qc_thumbnail_file", output_dir=str(output_dir), name=png.name),
            "flags": qc_flags.get(key, []),
        })
    return jsonify({"ok": True, "thumbnails": entries, "count": len(entries)})


@app.route("/api/qc/thumbnail_file", methods=["GET"])
def api_qc_thumbnail_file():
    """Serve one thumbnail PNG. `name` is constrained to a bare filename
    (no path separators), which rules out walking outside
    <output_dir>/reports/thumbnails by construction - output_dir itself is
    user-supplied (same trust model as the rest of this local-only tool's
    path picker). Deliberately does not additionally require
    target.resolve().parent == thumbnails_dir.resolve(): thumbnail PNGs are
    frequently git-annex symlinks whose resolved target lives under
    .git/annex/objects/ outside this folder, so that check would reject
    every annexed thumbnail as "not found".
    """
    output_dir_value = (request.args.get("output_dir") or "").strip()
    name = (request.args.get("name") or "").strip()
    if not output_dir_value or not name or "/" in name or "\\" in name:
        return _json_error("Invalid output_dir or name", 400)
    thumbnails_dir = _resolve_input_path(output_dir_value) / "reports" / "thumbnails"
    target = thumbnails_dir / name
    if not target.is_file():
        return _json_error("Thumbnail not found", 404)
    return send_file(target, mimetype="image/png")


@app.route("/api/qc/metrics", methods=["GET"])
def api_qc_metrics():
    """Serve <output_dir>/reports/qc_metrics.json (written by run_qc.py) for
    the QC dashboard's group scatter/box plots - one metric's full cohort
    distribution per entry, every session that has a value regardless of
    severity tier (unlike qc_flags.json, which only carries non-"ok" ones).
    """
    output_dir_value = (request.args.get("output_dir") or "").strip()
    if not output_dir_value:
        return _json_error("Missing output_dir", 400)
    output_dir = _resolve_input_path(output_dir_value)
    metrics_path = output_dir / "reports" / "qc_metrics.json"
    if not metrics_path.is_file():
        return jsonify({"ok": True, "metrics": {}})
    try:
        metrics = json.loads(metrics_path.read_text())
    except (OSError, ValueError):
        return _json_error("Could not parse qc_metrics.json", 500)
    return jsonify({"ok": True, "metrics": metrics})


@app.route("/api/run/connectometry", methods=["POST"])
def api_run_connectometry():
    try:
        payload = _get_json_payload()
        cmd = build_connectometry_command(payload)
        # Same "only the real thing" rule as the pipeline: --dry-run and
        # --test (single test_run config) are for checking the batch before
        # committing to it, not worth a push notification.
        is_full_run = not payload.get("dry_run") and not payload.get("test")
        job = launch_job(
            cmd, job_type="connectometry", cwd=REPO_DIR, project_root=payload.get("project_root"),
            notify=is_full_run, label=payload.get("config"),
        )
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
    # proc is only set for jobs launched by *this* server instance. A job
    # recovered from JOBS_STATE_FILE after a --restart (see main()) has no
    # proc here even though its subprocess is genuinely still running -
    # fall back to the persisted pid so it stays stoppable across restarts.
    target_pid = proc.pid if proc is not None else job.get("pid")
    if job["status"] != "running" or not target_pid:
        return _json_error("Job is not running", 400)

    with jobs_lock:
        jobs[job_id]["status"] = "stopped"
    _persist_jobs()

    try:
        pgid = os.getpgid(target_pid)
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
            if _is_process_running(target_pid):
                os.killpg(os.getpgid(target_pid), signal.SIGKILL)
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
    """List atlases available in the shared atlas library (matching .nii.gz +
    .txt label file pairs), so the connectivity settings UI only offers
    atlases that will actually be found at run time instead of a hardcoded,
    possibly stale, name list.
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

    # Plain `./gui.py` with no flags now defaults to --no-open --restart: no
    # browser popup (this is normally left running in a terminal/tmux, not
    # launched fresh each time) and always replace whatever instance was
    # previously tracked so code changes actually take effect. Without this,
    # re-running the script after an edit silently did nothing useful - the
    # old process kept serving stale code (plain reuse) or a second instance
    # started on a different auto-incremented port while the stale one kept
    # running on the original port (--new-instance) - either way the browser
    # tab pointed at the original port never saw the update. --open and
    # --no-restart opt back into the old behaviors if ever wanted.
    parser = argparse.ArgumentParser(description="Flask + Waitress UI for DSI Studio helpers")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Preferred port (auto-increment if busy)")
    parser.add_argument("--allow-network", action="store_true", help="Required alongside a non-loopback --host: the /api/fs/*, /api/run/* etc. routes have no authentication, so binding off 127.0.0.1/localhost needs an explicit opt-in.")
    parser.add_argument("--open", action="store_true", help="Auto-open the UI URL in a browser after starting (default: off)")
    parser.add_argument("--no-open", action="store_true", help="No-op: browser auto-open is off by default now. Kept only so old invocations don't error.")
    parser.add_argument("--new-instance", action="store_true", help="Start a new server even if an existing instance is already running (does NOT stop it - if the preferred port is taken, this binds a different one instead, so the old instance keeps serving its old code). Rarely what you want - see --restart.")
    parser.add_argument("--restart", action=argparse.BooleanOptionalAction, default=True, help="Stop any existing tracked instance (from a previous run) and start fresh on the same port, so code changes actually take effect (default: on). Pass --no-restart to instead reuse a healthy existing instance if one is running.")
    args = parser.parse_args()

    if not _is_loopback_host(args.host) and not args.allow_network:
        parser.error(
            f"--host {args.host} is not loopback, and the fs/job/run API routes have no "
            f"authentication - anyone who can reach this port could read/write files or launch "
            f"jobs as this user. Pass --allow-network to bind it anyway."
        )

    if args.restart:
        _kill_existing_instance()
        args.new_instance = True

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
                if args.open:
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

    # Recover jobs tracked by a previous instance of this server (e.g. the
    # one --restart just stopped) so a job that was launched before the
    # restart doesn't just vanish from the UI while its subprocess (which
    # outlives the restart - see start_new_session in _run_job) keeps
    # running unseen and unstoppable from here.
    with jobs_lock:
        jobs.update(_reconcile_persisted_jobs(
            _load_jobs_snapshot(),
            is_alive=lambda pid: bool(pid) and _is_process_running(pid),
        ))

    if args.open:
        # Open after startup begins; non-blocking and best-effort.
        threading.Timer(1.2, _open_browser, args=[url]).start()

    serve(app, host=args.host, port=port)


if __name__ == "__main__":
    main()
