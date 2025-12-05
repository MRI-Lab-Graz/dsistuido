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
from typing import List, Dict, Any, Optional
import itertools
import time
import shutil
import concurrent.futures
import random

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
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "connectometry_results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        
        # Setup logging
        self._setup_logging()
        
        # Extract DSI Studio command
        self.dsi_studio_cmd = self.config.get('dsi_studio_cmd', 'dsi_studio')
        
        # Validate DSI Studio installation
        self._validate_dsi_studio()
        
        self.results = []
        
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
                timeout=10
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
        
        # Demographics file (required)
        if 'demo' not in params and 'demo' in core_params:
            demo = core_params['demo'].get('value')
            if demo:
                cmd.append(f'--demo={demo}')
        elif 'demo' in params:
            cmd.append(f'--demo={params["demo"]}')
        
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
                timeout=7200  # 2 hour timeout
            )
            
            end = time.time()
            result['end_time'] = datetime.now().isoformat()
            result['duration'] = end - start
            result['return_code'] = process.returncode
            result['stdout'] = process.stdout
            result['stderr'] = process.stderr
            
            if process.returncode == 0:
                result['status'] = 'success'
                self.logger.info(f"✓ Analysis completed successfully in {result['duration']:.2f}s")
                
                # Parse results for significance
                try:
                    # Extract output prefix from command
                    output_arg = next((arg for arg in cmd if arg.startswith('--output=')), None)
                    if output_arg:
                        output_prefix = output_arg.split('=', 1)[1]
                        
                        # Check for positive/increased findings
                        inc_track = Path(f"{output_prefix}.inc.tt.gz")
                        if inc_track.exists() and inc_track.stat().st_size > 1000: # Check if file is not empty/trivial
                            result['findings_increased'] = True
                            self.logger.info("  -> Found INCREASED connectivity findings")
                        else:
                            result['findings_increased'] = False
                            
                        # Check for negative/decreased findings
                        dec_track = Path(f"{output_prefix}.dec.tt.gz")
                        if dec_track.exists() and dec_track.stat().st_size > 1000:
                            result['findings_decreased'] = True
                            self.logger.info("  -> Found DECREASED connectivity findings")
                        else:
                            result['findings_decreased'] = False
                except Exception as e:
                    self.logger.warning(f"Failed to parse results: {e}")
                    
            else:
                result['status'] = 'failed'
                self.logger.error(f"✗ Analysis failed with return code {process.returncode}")
                self.logger.error(f"STDERR: {process.stderr}")
                
                # Check for headless/Qt error
                if "qt.qpa" in process.stderr or process.returncode == -6 or process.returncode == 134:
                    self.logger.error("\n" + "!"*80)
                    self.logger.error("HEADLESS SERVER ERROR DETECTED")
                    self.logger.error("!"*80)
                    self.logger.error("DSI Studio 'cnt' action requires a display or xvfb.")
                    self.logger.error("Please ask your administrator to install 'xvfb' and run:")
                    self.logger.error(f"  xvfb-run -a {result['command']}")
                    self.logger.error("!"*80 + "\n")
            
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
    sys.exit(main())
