#!/usr/bin/env python3
"""Self-check for extract_connectivity_matrices.py's log-directory choice
(previously always wrote to a bare 'logs' relative to the process's CWD -
which, for a job launched by the web UI or the pipeline, is often not the
repo or the run's output dir, scattering log files wherever the launcher's
CWD happened to be - see #8 in the repo assessment). Run directly:
python3 test_extract_connectivity_matrices.py
"""
import json
import tempfile
import unittest
from pathlib import Path

import extract_connectivity_matrices as ecm

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
# These share extract_connectivity_matrices.py's config shape (atlases/
# connectivity_values/tracking_parameters/...). connectometry_config.json and
# poly_dance.json are a different tool's (run_connectometry_batch.py) config
# format and don't belong here.
EXTRACTION_CONFIGS = [
    "example_config.json",
    "graph_analysis_config.json",
    "graph_analysis_simple_config.json",
    "connectivity_config.json",
    "research_config.json",
]


class ShippedConfigTemplatesTest(unittest.TestCase):
    """Regression check for the configs/ templates: catches a template that
    no longer parses, or that (after merging with DEFAULT_CONFIG) is missing
    a key the extractor actually reads - the kind of silent breakage a
    hand-edit of these JSON files (e.g. repointing dsi_studio_cmd) could
    introduce without any other test catching it before someone hits it at
    run time, hours into a batch job.
    """

    def test_each_shipped_config_parses_and_merges_cleanly(self):
        for name in EXTRACTION_CONFIGS:
            with self.subTest(config=name):
                path = CONFIGS_DIR / name
                self.assertTrue(path.is_file(), f"missing: {path}")
                raw = json.loads(path.read_text())
                with tempfile.TemporaryDirectory() as tmp:
                    # Constructing with this config is exactly what running
                    # the script for real does before it ever touches DSI
                    # Studio (that check only happens inside
                    # validate_configuration(), not __init__/_merge_config).
                    extractor = ecm.ConnectivityExtractor(raw, output_dir=tmp)
                merged = extractor.config
                self.assertTrue(merged.get("atlases"), "no atlases after merge")
                self.assertTrue(merged.get("connectivity_values"), "no connectivity_values after merge")
                self.assertIsInstance(merged.get("tracking_parameters"), dict)
                self.assertTrue(merged.get("dsi_studio_cmd"), "no dsi_studio_cmd after merge")


class ResolveLogsDirTest(unittest.TestCase):
    def test_scopes_logs_under_output_dir_when_given(self):
        self.assertEqual(
            ecm._resolve_logs_dir("/data/study/output"),
            "/data/study/output/logs",
        )

    def test_falls_back_to_bare_logs_when_output_dir_unknown(self):
        self.assertEqual(ecm._resolve_logs_dir(None), "logs")


if __name__ == "__main__":
    unittest.main()
