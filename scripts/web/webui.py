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

from flask import Flask, jsonify, render_template, request, redirect, url_for
from waitress import serve

WEB_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = WEB_DIR.parent
REPO_DIR = SCRIPTS_DIR.parent
TEMPLATES_DIR = REPO_DIR / "templates"
LOG_DIR = SCRIPTS_DIR / "web_logs"
SETTINGS_DIR = SCRIPTS_DIR / "web_settings"
SERVER_STATE_FILE = SETTINGS_DIR / "webui_server_state.json"
APP_SIGNATURE = "dsi-studio-webui"

LOG_DIR.mkdir(exist_ok=True)
SETTINGS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webui")

jobs_lock = threading.Lock()
jobs: Dict[str, Dict] = {}


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


def _run_job(job_id: str, cmd: List[str], cwd: Optional[Path]):
    start_ts = time.time()
    log_file = Path(jobs[job_id]["log_file"])
    try:
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write(f"Command: {' '.join(cmd)}\n")
            fh.flush()
            result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=fh, stderr=fh)
            rc = result.returncode
    except Exception as exc:  # noqa: BLE001
        rc = -1
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(f"Exception: {exc}\n")
    end_ts = time.time()
    with jobs_lock:
        jobs[job_id]["status"] = "completed" if rc == 0 else "failed"
        jobs[job_id]["return_code"] = rc
        jobs[job_id]["ended_at"] = datetime.now(timezone.utc).isoformat()
        jobs[job_id]["duration_sec"] = round(end_ts - start_ts, 2)


def launch_job(cmd: List[str], job_type: str, cwd: Optional[Path] = None) -> Dict[str, str]:
    """Launch a subprocess in a background thread and track it."""
    # Use UUID-based job IDs to avoid collisions for rapid consecutive runs.
    job_id = uuid.uuid4().hex
    log_file = LOG_DIR / f"{job_type}_{job_id}.log"
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
        "dsi_studio_cmd": "--dsi_studio_cmd",
        "dsi_studio_path": "--dsi_studio_path",
        "method": "--method",
        "param0": "--param0",
        "threads": "--threads",
        "db_name": "--db_name",
        "rawdata_dir": "--rawdata_dir",
        "min_file_age": "--min_file_age",
        "connectivity_config": "--connectivity_config",
        "connectivity_output_dir": "--connectivity_output_dir",
        "connectivity_threads": "--connectivity_threads",
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
    ]
    for flag in bool_flags:
        if payload.get(flag):
            # dsi_studio_pipeline.py defines these flags with underscores.
            cmd.append(f"--{flag}")

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


@app.route("/viewer")
def viewer_page():
    return render_template("viewer.html", active_page="viewer")


@app.route("/api/run/pipeline", methods=["POST"])
def api_run_pipeline():
    try:
        payload = _get_json_payload()
        cmd = build_pipeline_command(payload)
        job = launch_job(cmd, job_type="pipeline", cwd=REPO_DIR)
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


@app.route("/api/run/connectometry", methods=["POST"])
def api_run_connectometry():
    try:
        payload = _get_json_payload()
        cmd = build_connectometry_command(payload)
        job = launch_job(cmd, job_type="connectometry", cwd=REPO_DIR)
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
        job = launch_job(cmd, job_type="viewer", cwd=REPO_DIR)
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 400)


@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values()))


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

        target = (SETTINGS_DIR / filename).resolve()
        settings_root = SETTINGS_DIR.resolve()
        if target.parent != settings_root:
            return _json_error("Invalid filename", 400)
        if target.suffix.lower() != ".json":
            return _json_error("Filename must end with .json", 400)

        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload.get("settings", {}), fh, indent=2)
        return jsonify({"ok": True, "saved_to": str(target)})
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), 500)


@app.route("/api/list_settings", methods=["GET"])
def api_list_settings():
    files = []
    for path in sorted(SETTINGS_DIR.glob("*.json")):
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

    target = (SETTINGS_DIR / filename).resolve()
    if target.parent != SETTINGS_DIR.resolve() or not target.exists():
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
