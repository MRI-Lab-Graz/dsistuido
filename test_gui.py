#!/usr/bin/env python3
"""Self-check for gui.py's job-persistence logic (survives a --restart of
the web UI while background jobs are still running - see #1 in the repo
assessment) and its non-loopback bind guard (see #10 - the /api/fs/* routes
have no auth and can read/write anywhere the server process can, so binding
off loopback needs an explicit opt-in). Run directly: python3 test_gui.py
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import gui


class ReconcilePersistedJobsTest(unittest.TestCase):
    def test_marks_dead_running_job_as_interrupted(self):
        jobs = {"a": {"job_id": "a", "status": "running", "pid": 999999}}
        result = gui._reconcile_persisted_jobs(jobs, is_alive=lambda pid: False)
        self.assertEqual(result["a"]["status"], "interrupted")

    def test_leaves_running_job_alone_if_process_still_alive(self):
        jobs = {"a": {"job_id": "a", "status": "running", "pid": 123}}
        result = gui._reconcile_persisted_jobs(jobs, is_alive=lambda pid: True)
        self.assertEqual(result["a"]["status"], "running")

    def test_leaves_terminal_status_jobs_unchanged(self):
        jobs = {"a": {"job_id": "a", "status": "completed", "pid": 999999}}
        result = gui._reconcile_persisted_jobs(jobs, is_alive=lambda pid: False)
        self.assertEqual(result["a"]["status"], "completed")


class JobsSnapshotIOTest(unittest.TestCase):
    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs_state.json"
            jobs = {"a": {"job_id": "a", "status": "running", "pid": 123}}
            gui._save_jobs_snapshot(jobs, path)
            self.assertEqual(gui._load_jobs_snapshot(path), jobs)

    def test_load_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.json"
            self.assertEqual(gui._load_jobs_snapshot(path), {})

    def test_load_corrupt_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs_state.json"
            path.write_text("{not valid json")
            self.assertEqual(gui._load_jobs_snapshot(path), {})


class IsLoopbackHostTest(unittest.TestCase):
    def test_127_0_0_1_is_loopback(self):
        self.assertTrue(gui._is_loopback_host("127.0.0.1"))

    def test_localhost_is_loopback(self):
        self.assertTrue(gui._is_loopback_host("localhost"))

    def test_ipv6_loopback_is_loopback(self):
        self.assertTrue(gui._is_loopback_host("::1"))

    def test_0_0_0_0_is_not_loopback(self):
        self.assertFalse(gui._is_loopback_host("0.0.0.0"))

    def test_lan_address_is_not_loopback(self):
        self.assertFalse(gui._is_loopback_host("192.168.1.5"))


if __name__ == "__main__":
    unittest.main()
