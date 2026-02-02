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
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from waitress import serve

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
LOG_DIR = BASE_DIR / "web_logs"
SETTINGS_DIR = BASE_DIR / "web_settings"

LOG_DIR.mkdir(exist_ok=True)
SETTINGS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webui")

jobs_lock = threading.Lock()
jobs: Dict[str, Dict] = {}


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
        jobs[job_id]["ended_at"] = datetime.utcnow().isoformat()
        jobs[job_id]["duration_sec"] = round(end_ts - start_ts, 2)


def launch_job(cmd: List[str], job_type: str, cwd: Optional[Path] = None) -> Dict[str, str]:
    """Launch a subprocess in a background thread and track it."""
    job_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    log_file = LOG_DIR / f"{job_type}_{job_id}.log"
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "cmd": cmd,
            "cwd": str(cwd) if cwd else None,
            "log_file": str(log_file),
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
        }
    thread = threading.Thread(target=_run_job, args=(job_id, cmd, cwd), daemon=True)
    thread.start()
    return {"job_id": job_id, "log_file": str(log_file)}


def build_pipeline_command(payload: Dict) -> List[str]:
    required = ["qsiprep_dir", "output_dir"]
    for key in required:
        if not payload.get(key):
            raise ValueError(f"Missing required field: {key}")

    cmd = [sys.executable, str(BASE_DIR / "dsi_studio_pipeline.py"), "--qsiprep_dir", payload["qsiprep_dir"], "--output_dir", payload["output_dir"]]

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
            cmd.append(f"--{flag.replace('_', '-')}")

    return cmd


def build_connectometry_command(payload: Dict) -> List[str]:
    if not payload.get("config"):
        raise ValueError("Missing required field: config")

    cmd = [sys.executable, str(BASE_DIR / "run_connectometry_batch.py"), "--config", payload["config"]]

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

    cmd = [sys.executable, str(BASE_DIR / "generate_interactive_viewer.py"), payload["input_folder"]]

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
    return render_template("webui.html")


@app.route("/api/run/pipeline", methods=["POST"])
def api_run_pipeline():
    payload = request.get_json(force=True)
    try:
        cmd = build_pipeline_command(payload)
        job = launch_job(cmd, job_type="pipeline", cwd=BASE_DIR)
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/run/connectometry", methods=["POST"])
def api_run_connectometry():
    payload = request.get_json(force=True)
    try:
        cmd = build_connectometry_command(payload)
        job = launch_job(cmd, job_type="connectometry", cwd=BASE_DIR)
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/run/viewer", methods=["POST"])
def api_run_viewer():
    payload = request.get_json(force=True)
    try:
        cmd = build_viewer_command(payload)
        job = launch_job(cmd, job_type="viewer", cwd=BASE_DIR)
        return jsonify({"ok": True, "job": job, "cmd": cmd})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    with jobs_lock:
        return jsonify(list(jobs.values()))


@app.route("/api/save_settings", methods=["POST"])
def api_save_settings():
    payload = request.get_json(force=True)
    filename = payload.get("filename") or f"settings_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"
    target = SETTINGS_DIR / filename
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload.get("settings", {}), fh, indent=2)
    return jsonify({"ok": True, "saved_to": str(target)})


@app.route("/api/list_settings", methods=["GET"])
def api_list_settings():
    files = []
    for path in sorted(SETTINGS_DIR.glob("*.json")):
        files.append({"name": path.name, "path": str(path), "modified": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat()})
    return jsonify(files)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Flask + Waitress UI for DSI Studio helpers")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Preferred port (auto-increment if busy)")
    args = parser.parse_args()

    port = find_free_port(args.port)
    if port != args.port:
        logger.info(f"Port {args.port} in use, using {port} instead")
    logger.info(f"Starting server on http://{args.host}:{port}")
    serve(app, host=args.host, port=port)


if __name__ == "__main__":
    main()
