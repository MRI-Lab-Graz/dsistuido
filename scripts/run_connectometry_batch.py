#!/usr/bin/env python3
"""
DSI Studio Connectometry Batch Analysis Script

This script performs multiple connectometry analyses using DSI Studio's --action=cnt.
It reads configuration from a JSON file and executes analyses with various parameter combinations.

Author: Generated for connectometry batch processing
Usage: python run_connectometry_batch.py --config connectometry_config.json [options]
"""

import os
import sys
import subprocess
import argparse
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Sequence
import itertools
import time
import shutil
import concurrent.futures
import random


def _sanitize_dsi_environment() -> Dict[str, str]:
    """Return a subprocess environment safe for DSI Studio execution.

    Removes legacy MATLAB MCR paths from LD_LIBRARY_PATH to avoid Qt/libstdc++
    conflicts that can break DSI Studio startup or figure export.
    """
    env = os.environ.copy()
    ld_lib = env.get('LD_LIBRARY_PATH', '')
    if not ld_lib:
        return env

    parts = [p for p in ld_lib.split(':') if p]
    filtered = [p for p in parts if 'conn_standalone/MCR' not in p and '/MCR/' not in p]

    if filtered:
        env['LD_LIBRARY_PATH'] = ':'.join(filtered)
    else:
        env.pop('LD_LIBRARY_PATH', None)
    return env

# Force Qt to use minimal platform for headless execution
# This avoids "Could not find the Qt platform plugin" errors on servers without display
# os.environ['QT_QPA_PLATFORM'] = 'minimal'

class ConnectometryBatchAnalysis:
    """Handles batch connectometry analysis with multiple parameter combinations"""
    
    def __init__(self, config_file: str, output_dir: Optional[str] = None, workers: int = 1):
        """
        Initialize the batch analysis
        
        Args:
            config_file: Path to JSON configuration file
            output_dir: Output directory for results (optional)
            workers: Number of parallel workers (default: 1)
        """
        self.config_file = Path(config_file)
        self.config = self._load_config()

        # Always use an absolute output directory so that DSI Studio
        # can reliably write result files and figures even if it
        # changes its current working directory internally.
        if output_dir:
            self.output_dir = Path(output_dir).expanduser().resolve()
        else:
            self.output_dir = (Path.cwd() / "connectometry_results").resolve()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        
        # Setup logging
        self._setup_logging()
        
        # Extract DSI Studio command
        self.dsi_studio_cmd = self.config.get('dsi_studio_cmd', 'dsi_studio')
        
        # Validate DSI Studio installation
        self._validate_dsi_studio()
        
        self.results = []

    def _extract_output_prefix(self, cmd: List[str]) -> Optional[Path]:
        """Extract the output prefix path from a DSI Studio command."""
        output_arg = next((arg for arg in cmd if arg.startswith('--output=')), None)
        if not output_arg:
            return None
        return Path(output_arg.split('=', 1)[1])

    def _collect_tract_outputs(self, output_prefix: Optional[Path]) -> List[Path]:
        """Return non-trivial tract outputs produced by a connectometry run."""
        if output_prefix is None:
            return []

        tract_paths = [
            Path(f"{output_prefix}.inc.tt.gz"),
            Path(f"{output_prefix}.dec.tt.gz"),
        ]
        return [path for path in tract_paths if path.exists() and path.stat().st_size > 1000]

    def _expected_jpg_path(self, tt_path: Path) -> Path:
        """Map xxx.tt.gz -> xxx.jpg for connectometry tract outputs."""
        name = tt_path.name
        if name.endswith('.tt.gz'):
            return tt_path.with_name(name[:-6] + '.jpg')
        return tt_path.with_suffix('.jpg')

    def _populate_findings(self, result: Dict[str, Any], output_prefix: Optional[Path]) -> None:
        """Annotate result with increased/decreased tract findings."""
        if output_prefix is None:
            return

        inc_track = Path(f"{output_prefix}.inc.tt.gz")
        result['findings_increased'] = inc_track.exists() and inc_track.stat().st_size > 1000
        if result['findings_increased']:
            self.logger.info("  -> Found INCREASED connectivity findings")

        dec_track = Path(f"{output_prefix}.dec.tt.gz")
        result['findings_decreased'] = dec_track.exists() and dec_track.stat().st_size > 1000
        if result['findings_decreased']:
            self.logger.info("  -> Found DECREASED connectivity findings")

    def _recover_missing_jpgs(self, output_prefix: Optional[Path]) -> Dict[str, Any]:
        """Render missing JPG previews from tract outputs using the fallback helper script."""
        tract_paths = self._collect_tract_outputs(output_prefix)
        if not tract_paths:
            return {
                'attempted': False,
                'success': False,
                'message': 'No tract outputs available for JPG recovery.'
            }

        missing_jpgs = [
            self._expected_jpg_path(tt_path)
            for tt_path in tract_paths
            if not self._expected_jpg_path(tt_path).exists()
            or self._expected_jpg_path(tt_path).stat().st_size == 0
        ]
        if not missing_jpgs:
            return {
                'attempted': False,
                'success': True,
                'message': 'All expected JPG previews already exist.'
            }

        helper_script = Path(__file__).with_name('generate_jpgs_from_tt.py')
        if not helper_script.exists():
            return {
                'attempted': False,
                'success': False,
                'message': f'JPG helper script not found: {helper_script}'
            }

        cmd = [
            sys.executable,
            str(helper_script),
            str(output_prefix.parent),
            '--dsi-studio',
            self.dsi_studio_cmd,
            '--jobs',
            '1',
            '--quiet',
        ]

        if sys.platform.startswith('linux') and os.environ.get('DISPLAY') is None and shutil.which('xvfb-run'):
            cmd.append('--xvfb')

        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
                env=_sanitize_dsi_environment(),
            )
        except subprocess.TimeoutExpired:
            return {
                'attempted': True,
                'success': False,
                'command': ' '.join(cmd),
                'message': 'Timed out while generating fallback JPG previews.'
            }
        except Exception as exc:
            return {
                'attempted': True,
                'success': False,
                'command': ' '.join(cmd),
                'message': f'Failed to launch JPG recovery: {exc}'
            }

        remaining_missing = [
            str(jpg_path)
            for jpg_path in missing_jpgs
            if not Path(jpg_path).exists() or Path(jpg_path).stat().st_size == 0
        ]
        success = process.returncode == 0 and not remaining_missing
        output_text = (process.stdout or '').strip()
        error_text = (process.stderr or '').strip()
        message_parts = []
        if output_text:
            message_parts.append(output_text[-500:])
        if error_text:
            message_parts.append(error_text[-500:])
        if remaining_missing:
            message_parts.append('Missing JPGs: ' + ', '.join(remaining_missing))

        return {
            'attempted': True,
            'success': success,
            'command': ' '.join(cmd),
            'message': ' | '.join(message_parts) if message_parts else 'JPG recovery finished without additional output.'
        }

    def _is_recoverable_figure_export_failure(
        self,
        return_code: int,
        stdout_text: str,
        stderr_text: str,
        output_prefix: Optional[Path],
    ) -> bool:
        """Return True when DSI Studio crashed after writing tract outputs but before saving screenshots."""
        if return_code == 0 or not self._collect_tract_outputs(output_prefix):
            return False

        combined = f"{stdout_text}\n{stderr_text}".lower()
        return (
            'create tract figures' in combined
            and 'qwidget: cannot create a qwidget without qapplication' in combined
        )

    def _extract_failure_snippet(
        self,
        stdout_text: str,
        stderr_text: str,
        max_lines: int = 12,
        max_chars: int = 1200,
    ) -> str:
        """Return the most relevant DSI Studio failure lines for logging."""
        combined = []
        if stdout_text:
            combined.extend(("STDOUT", line) for line in stdout_text.splitlines())
        if stderr_text:
            combined.extend(("STDERR", line) for line in stderr_text.splitlines())

        if not combined:
            return "(no DSI Studio output captured)"

        keywords = (
            'create tract figures',
            'qwidget',
            'qapplication',
            'qt.qpa',
            'cannot save',
            'abort',
            'aborted',
            'segmentation fault',
            'core dumped',
        )
        match_indexes = [
            idx for idx, (_, line) in enumerate(combined)
            if any(keyword in line.lower() for keyword in keywords)
        ]

        if match_indexes:
            first = max(0, match_indexes[0] - 2)
            last = min(len(combined), match_indexes[-1] + 3)
            snippet_lines = [f"{stream}: {line}" for stream, line in combined[first:last]]
        else:
            snippet_lines = [f"{stream}: {line}" for stream, line in combined[-max_lines:]]

        snippet = "\n".join(snippet_lines)
        if len(snippet) > max_chars:
            snippet = snippet[-max_chars:]
        return snippet

    def _command_uses_xvfb(self, cmd: Sequence[str]) -> bool:
        """Return True when the command is already wrapped in xvfb-run."""
        return any(Path(part).name == 'xvfb-run' for part in cmd)

    def _log_qt_failure_diagnosis(
        self,
        cmd: Sequence[str],
        return_code: int,
        stdout_text: str,
        stderr_text: str,
    ) -> None:
        """Log actionable Qt/headless diagnostics for failed DSI Studio runs."""
        snippet = self._extract_failure_snippet(stdout_text, stderr_text)
        self.logger.error("\n" + "!" * 80)
        self.logger.error("DSI STUDIO QT/FIGURE EXPORT FAILURE DETECTED")
        self.logger.error("!" * 80)
        self.logger.error(f"Return code: {return_code}")
        self.logger.error("Relevant DSI Studio output:")
        for line in snippet.splitlines():
            self.logger.error(f"  {line}")

        if self._command_uses_xvfb(cmd):
            self.logger.error(
                "DSI Studio was already running under xvfb-run, so this is not a missing-display setup issue."
            )
            self.logger.error(
                "The failure is consistent with DSI Studio crashing inside its Qt-based tract figure export path."
            )
        else:
            self.logger.error("DSI Studio was not wrapped in xvfb-run for this failed command.")
            self.logger.error("If this is a headless Linux host, install xvfb and rerun with:")
            self.logger.error(f"  xvfb-run -a {' '.join(cmd)}")

        self.logger.error("!" * 80 + "\n")
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        if not self.config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")
        
        with open(self.config_file, 'r') as f:
            config = json.load(f)
        
        return config
    
    def _setup_logging(self):
        """Setup logging configuration"""
        log_file = self.output_dir / f"connectometry_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Logging to: {log_file}")
    
    def _validate_dsi_studio(self):
        """Validate DSI Studio installation"""
        try:
            # Use --version instead of --help as --help might trigger GUI/display requirement
            result = subprocess.run(
                [self.dsi_studio_cmd, '--version'],
                capture_output=True,
                timeout=10,
                env=_sanitize_dsi_environment(),
            )
            if result.returncode != 0:
                # Fallback to checking if file exists and is executable if --version fails
                if Path(self.dsi_studio_cmd).exists() and os.access(self.dsi_studio_cmd, os.X_OK):
                    self.logger.warning(f"DSI Studio command returned non-zero exit code but executable exists: {self.dsi_studio_cmd}")
                else:
                    raise RuntimeError(f"DSI Studio command failed: {self.dsi_studio_cmd}")
            self.logger.info(f"✓ DSI Studio validated: {self.dsi_studio_cmd}")
        except FileNotFoundError:
            raise RuntimeError(f"DSI Studio not found: {self.dsi_studio_cmd}")
        except Exception as e:
            raise RuntimeError(f"Error validating DSI Studio: {e}")
    
    def _build_command(self, analysis_name: str, params: Dict[str, Any]) -> List[str]:
        """
        Build DSI Studio connectometry command
        
        Args:
            analysis_name: Name of the analysis
            params: Dictionary of parameters
            
        Returns:
            Command as list of strings
        """
        cmd = []
        
        # Automatically prepend xvfb-run if on Linux, no display, and xvfb-run is available
        if sys.platform.startswith('linux') and os.environ.get('DISPLAY') is None:
            xvfb_path = shutil.which('xvfb-run')
            if xvfb_path:
                cmd.extend([xvfb_path, '-a'])
        
        cmd.extend([self.dsi_studio_cmd, '--action=cnt'])
        
        # Add core parameters from config
        core_params = self.config.get('core_parameters', {})
        
        # Source file (required)
        if 'source' not in params and 'source' in core_params:
            source = core_params['source'].get('value')
            if source:
                cmd.append(f'--source={source}')
        elif 'source' in params:
            cmd.append(f'--source={params["source"]}')
        
        # Demographics file (optional - skip if empty, placeholder, or not a real path)
        if 'demo' not in params and 'demo' in core_params:
            demo = core_params['demo'].get('value', '').strip()
            if demo and not demo.startswith('/path/to') and Path(demo).exists():
                cmd.append(f'--demo={demo}')
        elif 'demo' in params:
            demo = str(params['demo']).strip()
            if demo and not demo.startswith('/path/to') and Path(demo).exists():
                cmd.append(f'--demo={demo}')
        
        # Variable list (required)
        if 'variable_list' not in params and 'variable_list' in core_params:
            var_list = core_params['variable_list'].get('value')
            if var_list:
                cmd.append(f'--variable_list={var_list}')
        elif 'variable_list' in params:
            cmd.append(f'--variable_list={params["variable_list"]}')
        
        # Variable of interest (required)
        if 'voi' not in params and 'voi' in core_params:
            voi = core_params['voi'].get('value')
            if voi is not None:
                cmd.append(f'--voi={voi}')
        elif 'voi' in params:
            cmd.append(f'--voi={params["voi"]}')
        
        # Add all other parameters
        param_mapping = {
            'index_name': '--index_name',
            't_threshold': '--t_threshold',
            'effect_size': '--effect_size',
            'length_threshold': '--length_threshold',
            'fdr_threshold': '--fdr_threshold',
            'permutation': '--permutation',
            'thread_count': '--thread_count',
            'exclude_cb': '--exclude_cb',
            'normalize_iso': '--normalize_iso',
            'tip_iteration': '--tip_iteration',
            'region_pruning': '--region_pruning',
            'no_tractogram': '--no_tractogram',
            'output': '--output',
            'select': '--select',
            'seed': '--seed',
            'roi': '--roi',
            'roa': '--roa'
        }
        
        for param_key, param_value in params.items():
            if param_key in param_mapping and param_value is not None and param_value != "":
                flag = param_mapping[param_key]
                cmd.append(f'{flag}={param_value}')
        
        # Set output prefix if not specified
        has_output = any(arg.startswith('--output=') for arg in cmd)
        if not has_output:
            # Create subfolder for this analysis
            analysis_dir = self.output_dir / analysis_name
            analysis_dir.mkdir(parents=True, exist_ok=True)
            output_prefix = analysis_dir / analysis_name
            cmd.append(f'--output={output_prefix}')
        
        return cmd
    
    def _parse_range_string(self, value: Any) -> Optional[List[Any]]:
        """
        Parse MATLAB-style range string 'start:step:end' or 'start:end'
        Returns list of values if successful, None otherwise.
        """
        if not isinstance(value, str) or ':' not in value:
            return None
            
        # Check if it looks like a number range (simple check)
        # It should only contain numbers, dots, minus signs, and colons
        if not all(c.isdigit() or c in '.:-' for c in value):
            return None

        try:
            parts = [float(x) for x in value.split(':')]
            if len(parts) == 3:
                start, step, end = parts
            elif len(parts) == 2:
                start, end = parts
                step = 1.0
            else:
                return None
                
            values = []
            current = start
            # Use epsilon for float comparison
            epsilon = abs(step) * 1e-5 if step != 0 else 1e-5
            
            if step > 0:
                while current <= end + epsilon:
                    values.append(current)
                    current += step
            elif step < 0:
                while current >= end - epsilon:
                    values.append(current)
                    current += step
            else:
                return [start]
                
            # Round to reasonable precision (10 decimal places) to handle float errors
            return [round(x, 10) if isinstance(x, float) else x for x in values]
        except ValueError:
            return None

    def _expand_parameter_grid(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Expand parameters that are lists into a grid of configurations
        
        Args:
            params: Dictionary with parameters (some may be lists)
            
        Returns:
            List of parameter dictionaries (one per combination)
        """
        # Separate list parameters from single values
        list_params = {}
        single_params = {}
        
        for key, value in params.items():
            # Check for range string first
            range_vals = self._parse_range_string(value)
            if range_vals is not None:
                list_params[key] = range_vals
            elif isinstance(value, list):
                list_params[key] = value
            else:
                single_params[key] = value
        
        # If no list parameters, return single configuration
        if not list_params:
            return [params]
        
        # Generate all combinations
        keys = list(list_params.keys())
        values = [list_params[k] for k in keys]
        combinations = list(itertools.product(*values))
        
        # Create parameter dictionaries for each combination
        result = []
        for combo in combinations:
            param_dict = single_params.copy()
            for key, value in zip(keys, combo):
                param_dict[key] = value
            result.append(param_dict)
        
        return result
    
    def run_single_analysis(self, analysis_name: str, params: Dict[str, Any], 
                           analysis_index: int = 0, total_analyses: int = 1) -> Dict[str, Any]:
        """
        Run a single connectometry analysis
        
        Args:
            analysis_name: Name of the analysis
            params: Dictionary of parameters
            analysis_index: Current analysis number
            total_analyses: Total number of analyses
            
        Returns:
            Dictionary with analysis results
        """
        result = {
            'name': analysis_name,
            'params': params,
            'status': 'pending',
            'start_time': None,
            'end_time': None,
            'duration': None,
            'command': None,
            'stdout': None,
            'stderr': None,
            'return_code': None
        }
        
        try:
            # Build command
            cmd = self._build_command(analysis_name, params)
            output_prefix = self._extract_output_prefix(cmd)
            result['command'] = ' '.join(cmd)
            
            self.logger.info(f"\n{'='*80}")
            self.logger.info(f"Analysis {analysis_index + 1}/{total_analyses}: {analysis_name}")
            self.logger.info(f"{'='*80}")
            self.logger.info(f"Parameters: {json.dumps(params, indent=2)}")
            self.logger.info(f"Command: {result['command']}")
            self.logger.info(f"{'='*80}\n")
            
            # Add random delay if running in parallel to avoid xvfb race conditions
            if self.workers > 1:
                delay = random.uniform(2.0, 30.0)
                self.logger.info(f"Waiting {delay:.2f}s before starting to avoid race conditions...")
                time.sleep(delay)

            # Run analysis
            result['start_time'] = datetime.now().isoformat()
            start = time.time()
            
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,  # 2 hour timeout
                env=_sanitize_dsi_environment(),
            )
            
            end = time.time()
            result['end_time'] = datetime.now().isoformat()
            result['duration'] = end - start
            result['return_code'] = process.returncode
            result['stdout'] = process.stdout
            result['stderr'] = process.stderr
            stdout_text = result['stdout'] or ""
            stderr_text = result['stderr'] or ""
            
            if process.returncode == 0:
                # Check for DSI Studio specific error messages in stdout even if return code is 0.
                # Some "❌ cannot save ...jpg" messages are non-fatal (figure export only) and
                # should not cause the whole analysis to be marked as failed.

                stdout_lower = stdout_text.lower()

                # Lines that contain the red-cross marker
                cross_lines = [ln for ln in stdout_text.splitlines() if "❌" in ln]

                def _is_non_fatal_figure_error(line: str) -> bool:
                    """Return True for known non-fatal DSI Studio JPG export issues."""
                    l = line.lower()
                    # Known non-critical messages we want to tolerate:
                    # - cannot save mapping to ...dec_map2.jpg
                    # - cannot save screen to ...pos_neg.jpg
                    return (
                        ("cannot save mapping" in l and "dec_map2.jpg" in l)
                        or ("cannot save screen" in l and "pos_neg.jpg" in l)
                    )

                has_non_fatal_cross_only = bool(cross_lines) and all(
                    _is_non_fatal_figure_error(ln) for ln in cross_lines
                )

                # Detect real problems
                cannot_find_lines = [ln for ln in stdout_text.splitlines() if "cannot find" in ln.lower()]
                # "cannot find index: t1w_template" is known to be non-fatal for
                # our use case (core outputs and JPGs are still created), so we
                # tolerate it as a warning.
                def _is_non_fatal_cannot_find(line: str) -> bool:
                    return "cannot find index" in line.lower() and "t1w_template" in line.lower()

                has_cannot_find_fatal = bool(cannot_find_lines) and not all(
                    _is_non_fatal_cannot_find(ln) for ln in cannot_find_lines
                )
                has_cross = bool(cross_lines)

                fatal_output_issue = (
                    has_cannot_find_fatal
                    or (has_cross and not has_non_fatal_cross_only)
                )

                if fatal_output_issue:
                    result['status'] = 'failed'
                    self.logger.error(f"✗ Analysis failed (detected error in output)")
                    self.logger.error(f"Output snippet: {stdout_text[-500:]}")  # Log last 500 chars
                else:
                    result['status'] = 'success'
                    self.logger.info(f"✓ Analysis completed successfully in {result['duration']:.2f}s")
                    try:
                        self._populate_findings(result, output_prefix)
                    except Exception as e:
                        self.logger.warning(f"Failed to parse results: {e}")

                    jpg_result = self._recover_missing_jpgs(output_prefix)
                    result['jpg_generation'] = jpg_result
                    if jpg_result['attempted']:
                        if jpg_result['success']:
                            self.logger.info("  -> Recovered missing JPG previews after connectometry run")
                        else:
                            result['status'] = 'failed'
                            self.logger.error("✗ Analysis completed but JPG recovery failed")
                            self.logger.error(jpg_result['message'])
                    
            else:
                if self._is_recoverable_figure_export_failure(
                    process.returncode,
                    stdout_text,
                    stderr_text,
                    output_prefix,
                ):
                    self.logger.warning(
                        "DSI Studio crashed during built-in tract figure export after writing tract outputs; "
                        "attempting JPG recovery."
                    )
                    self.logger.warning("Relevant DSI Studio output:")
                    for line in self._extract_failure_snippet(stdout_text, stderr_text).splitlines():
                        self.logger.warning(f"  {line}")
                    jpg_result = self._recover_missing_jpgs(output_prefix)
                    result['jpg_generation'] = jpg_result

                    if jpg_result['success']:
                        result['status'] = 'success'
                        result['recovered_after_figure_export_crash'] = True
                        self.logger.warning(
                            f"Recovered analysis outputs despite DSI Studio returning {process.returncode}"
                        )
                        try:
                            self._populate_findings(result, output_prefix)
                        except Exception as e:
                            self.logger.warning(f"Failed to parse recovered results: {e}")
                    else:
                        result['status'] = 'failed'
                        self.logger.error(f"✗ Analysis failed with return code {process.returncode}")
                        self.logger.error(jpg_result['message'])
                else:
                    result['status'] = 'failed'
                    self.logger.error(f"✗ Analysis failed with return code {process.returncode}")
                    self.logger.error(f"STDERR: {process.stderr}")
                
                # Check for headless/Qt error only if the run is still failed after recovery.
                if result['status'] != 'success' and (
                    "qt.qpa" in stderr_text.lower()
                    or "qwidget: cannot create a qwidget without qapplication" in stdout_text.lower()
                    or "qwidget: cannot create a qwidget without qapplication" in stderr_text.lower()
                    or process.returncode == -6
                    or process.returncode == 134
                ):
                    self._log_qt_failure_diagnosis(
                        cmd=cmd,
                        return_code=process.returncode,
                        stdout_text=stdout_text,
                        stderr_text=stderr_text,
                    )
            
        except subprocess.TimeoutExpired:
            result['status'] = 'timeout'
            result['end_time'] = datetime.now().isoformat()
            self.logger.error(f"✗ Analysis timed out")
            
        except Exception as e:
            result['status'] = 'error'
            result['end_time'] = datetime.now().isoformat()
            result['stderr'] = str(e)
            self.logger.error(f"✗ Analysis error: {e}")
        
        return result
    
    def _get_config_defaults(self) -> Dict[str, Any]:
        """Extract default values from config sections"""
        defaults = {}
        
        # Helper to extract values from sections
        def extract_from_section(section_name):
            section = self.config.get(section_name, {})
            for key, item in section.items():
                if isinstance(item, dict) and 'value' in item:
                    defaults[key] = item['value']
                elif not isinstance(item, dict):
                    defaults[key] = item
                    
        # Extract from all parameter sections
        extract_from_section('core_parameters')
        extract_from_section('threshold_parameters')
        extract_from_section('analysis_parameters')
        extract_from_section('optional_parameters')
        
        return defaults

    def _generate_analysis_name(self, params: Dict[str, Any], base_name: str, index: int) -> str:
        """
        Generate a descriptive analysis name based on parameters
        Format: {index_name}_{effect_size}_{length_threshold}_{permutation}
        """
        try:
            # Extract key parameters
            index_name = params.get('index_name')
            effect_size = params.get('effect_size')
            length = params.get('length_threshold')
            perm = params.get('permutation')
            
            # If we have all key parameters, create descriptive name
            if all(v is not None for v in [index_name, effect_size, length, perm]):
                # Format numbers nicely (remove trailing zeros if integer)
                def fmt(v):
                    if isinstance(v, float) and v.is_integer():
                        return str(int(v))
                    return str(v)
                
                return f"{index_name}_{fmt(effect_size)}_{fmt(length)}_{fmt(perm)}"
        except Exception:
            pass
            
        # Fallback to generic name
        return f"{base_name}_combo_{index+1}"

    def run_batch_configuration(self, batch_config: Dict[str, Any], 
                               batch_index: int = 0, total_batches: int = 1):
        """
        Run a batch configuration with parameter grid expansion
        
        Args:
            batch_config: Batch configuration dictionary
            batch_index: Current batch number
            total_batches: Total number of batches
        """
        name = batch_config.get('name', f'batch_{batch_index}')
        description = batch_config.get('description', '')
        params = batch_config.get('parameters', {})
        
        self.logger.info(f"\n{'#'*80}")
        self.logger.info(f"BATCH {batch_index + 1}/{total_batches}: {name}")
        self.logger.info(f"Description: {description}")
        self.logger.info(f"{'#'*80}\n")
        
        # Get global defaults
        defaults = self._get_config_defaults()
        
        # Expand parameter grid
        param_combinations = self._expand_parameter_grid(params)
        self.logger.info(f"Generated {len(param_combinations)} parameter combination(s)")
        
        # Run each combination
        if self.workers > 1:
            self.logger.info(f"Running with {self.workers} parallel workers")
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = []
                for i, combo_params in enumerate(param_combinations):
                    # Merge defaults with combo params
                    full_params = defaults.copy()
                    full_params.update(combo_params)
                    
                    analysis_name = self._generate_analysis_name(full_params, name, i)
                    
                    futures.append(
                        executor.submit(
                            self.run_single_analysis,
                            analysis_name,
                            full_params,
                            i,
                            len(param_combinations)
                        )
                    )
                
                # Collect results as they complete
                for future in concurrent.futures.as_completed(futures):
                    try:
                        result = future.result()
                        self.results.append(result)
                    except Exception as e:
                        self.logger.error(f"Parallel execution error: {e}")
        else:
            # Sequential execution
            for i, combo_params in enumerate(param_combinations):
                # Merge defaults with combo params
                full_params = defaults.copy()
                full_params.update(combo_params)
                
                analysis_name = self._generate_analysis_name(full_params, name, i)
                
                result = self.run_single_analysis(
                    analysis_name, 
                    full_params,
                    i,
                    len(param_combinations)
                )
                self.results.append(result)
    
    def run_all_batches(self, exclude_names: List[str] = None):
        """
        Run all batch configurations defined in the config file
        
        Args:
            exclude_names: List of batch names to exclude
        """
        batch_configs = self.config.get('batch_configurations', [])
        
        if exclude_names:
            batch_configs = [b for b in batch_configs if b.get('name') not in exclude_names]
        
        if not batch_configs:
            self.logger.warning("No batch configurations found (after filtering)")
            return
        
        self.logger.info(f"Starting batch processing: {len(batch_configs)} batch(es) selected")
        
        for i, batch_config in enumerate(batch_configs):
            self.run_batch_configuration(batch_config, i, len(batch_configs))
    
    def run_custom_analysis(self, params: Dict[str, Any], name: Optional[str] = None):
        """
        Run a custom analysis with specified parameters
        
        Args:
            params: Dictionary of parameters
            name: Optional name for the analysis
        """
        if name is None:
            name = f"custom_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        result = self.run_single_analysis(name, params)
        self.results.append(result)

    def retry_failed_analyses(self, summary_file: str):
        """
        Retry failed analyses from a previous run summary
        
        Args:
            summary_file: Path to the analysis summary JSON file
        """
        summary_path = Path(summary_file)
        if not summary_path.exists():
            self.logger.error(f"Summary file not found: {summary_path}")
            return

        with open(summary_path, 'r') as f:
            summary = json.load(f)
            
        failed_analyses = [a for a in summary.get('analyses', []) if a.get('status') == 'failed']
        
        if not failed_analyses:
            self.logger.info("No failed analyses found in summary.")
            return
            
        self.logger.info(f"Found {len(failed_analyses)} failed analyses. Retrying...")
        
        # Use the same parallel execution logic if workers > 1
        if self.workers > 1:
            self.logger.info(f"Retrying with {self.workers} parallel workers")
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = []
                for i, analysis in enumerate(failed_analyses):
                    name = analysis.get('name')
                    params = analysis.get('params')
                    
                    futures.append(
                        executor.submit(
                            self.run_single_analysis,
                            name,
                            params,
                            i,
                            len(failed_analyses)
                        )
                    )
                
                for future in concurrent.futures.as_completed(futures):
                    try:
                        result = future.result()
                        self.results.append(result)
                    except Exception as e:
                        self.logger.error(f"Retry execution error: {e}")
        else:
            for i, analysis in enumerate(failed_analyses):
                name = analysis.get('name')
                params = analysis.get('params')
                
                result = self.run_single_analysis(
                    name,
                    params,
                    i,
                    len(failed_analyses)
                )
                self.results.append(result)
    
    def save_summary(self):
        """Save summary of all analyses to JSON file"""
        summary_file = self.output_dir / f"analysis_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        summary = {
            'config_file': str(self.config_file),
            'total_analyses': len(self.results),
            'successful': sum(1 for r in self.results if r['status'] == 'success'),
            'failed': sum(1 for r in self.results if r['status'] == 'failed'),
            'timeout': sum(1 for r in self.results if r['status'] == 'timeout'),
            'error': sum(1 for r in self.results if r['status'] == 'error'),
            'analyses': self.results
        }
        
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info("SUMMARY")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"Total analyses: {summary['total_analyses']}")
        self.logger.info(f"Successful: {summary['successful']}")
        self.logger.info(f"Failed: {summary['failed']}")
        self.logger.info(f"Timeout: {summary['timeout']}")
        self.logger.info(f"Error: {summary['error']}")
        self.logger.info(f"Summary saved to: {summary_file}")
        self.logger.info(f"{'='*80}\n")
        
        return summary


def main():
    """Main entry point for the script"""
    parser = argparse.ArgumentParser(
        description='Run batch connectometry analyses with DSI Studio',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all batch configurations from config file
  python run_connectometry_batch.py --config connectometry_config.json
  
  # Run with custom output directory
  python run_connectometry_batch.py --config connectometry_config.json --output ./results
  
  # Run a specific batch by index
  python run_connectometry_batch.py --config connectometry_config.json --batch 0
  
  # Run custom analysis with parameters
  python run_connectometry_batch.py --config connectometry_config.json --custom '{"index_name":"qa","effect_size":0.3}'
  
    # Run detached in background (nohup)
    python run_connectometry_batch.py --nohup [your options]
        """
    )
    
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='Path to connectometry configuration JSON file'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output directory for results (default: ./connectometry_results)'
    )
    
    parser.add_argument(
        '--batch',
        type=int,
        default=None,
        help='Run only specific batch by index (0-based)'
    )
    
    parser.add_argument(
        '--workers',
        type=int,
        default=1,
        help='Number of parallel workers (default: 1)'
    )
    
    parser.add_argument(
        '--custom',
        type=str,
        default=None,
        help='Run custom analysis with JSON parameters'
    )

    parser.add_argument(
        '--retry-failed',
        type=str,
        default=None,
        help='Retry failed analyses from a summary JSON file'
    )
    
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run only the "test_run" configuration'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show commands without executing them'
    )
    
    parser.add_argument(
        '--nohup',
        action='store_true',
        help='Run detached in background automatically'
    )

    args = parser.parse_args()
    
    try:
        # Initialize batch analysis
        batch = ConnectometryBatchAnalysis(args.config, args.output, args.workers)
        
        if args.dry_run:
            batch.logger.info("DRY RUN MODE - Commands will be shown but not executed")
            # TODO: Implement dry-run mode
            return
        
        # Run custom analysis
        if args.custom:
            custom_params = json.loads(args.custom)
            batch.run_custom_analysis(custom_params)

        # Retry failed analyses
        elif args.retry_failed:
            batch.retry_failed_analyses(args.retry_failed)
            
        # Run test run
        elif args.test:
            batch_configs = batch.config.get('batch_configurations', [])
            test_batch = next((b for b in batch_configs if b.get('name') == 'test_run'), None)
            if test_batch:
                batch.run_batch_configuration(test_batch, 0, 1)
            else:
                batch.logger.error("No batch named 'test_run' found in configuration")
                return 1
        
        # Run specific batch
        elif args.batch is not None:
            batch_configs = batch.config.get('batch_configurations', [])
            if 0 <= args.batch < len(batch_configs):
                batch.run_batch_configuration(batch_configs[args.batch], args.batch, len(batch_configs))
            else:
                batch.logger.error(f"Invalid batch index: {args.batch}")
                return
        
        # Run all batches (default)
        else:
            # Exclude 'test_run' from default execution as requested
            batch.run_all_batches(exclude_names=['test_run'])
        
        # Save summary
        batch.save_summary()
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 1:
        print("\nUsage: python run_connectometry_batch.py --config CONFIG [options]\n")
        print("For full help, run: python run_connectometry_batch.py --help\n")
        sys.exit(0)
        
    if '--nohup' in sys.argv:
        # Remove --nohup from arguments
        cmd_args = [arg for arg in sys.argv if arg != '--nohup']
        
        # Log file
        log_filename = "batch_detached.log"
        
        print(f"\nLaunching detached process...")
        print(f"Output logging to: {log_filename}")
        
        try:
            # Open file for appending
            with open(log_filename, 'a') as log_file:
                # Write a header to the log
                log_file.write(f"\n{'='*40}\n")
                log_file.write(f"Detached run started at {datetime.now().isoformat()}\n")
                log_file.write(f"Command: {' '.join([sys.executable] + cmd_args)}\n")
                log_file.write(f"{'='*40}\n")
                log_file.flush()
                
                # Launch process
                # start_new_session=True is equivalent to setsid, detaching from terminal
                subprocess.Popen(
                    [sys.executable] + cmd_args,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )
                
            print(f"Process launched successfully.")
            print(f"You can now close this terminal.")
            print(f"Monitor progress with: tail -f {log_filename}\n")
            sys.exit(0)
        except Exception as e:
            print(f"Failed to launch detached process: {e}")
            sys.exit(1)

    sys.exit(main())
