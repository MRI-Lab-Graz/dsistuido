#!/usr/bin/env python3
"""
DSI Studio Connectivity Matrix Extraction Script

This script extracts connectivity matrices for multiple atlases from DSI Studio fiber files.
It provides batch processing capabilities and detailed logging.

Author: Generated for connectivity analysis
Usage: python extract_connectivity_matrices.py [options] input_file output_dir
"""

import os
import sys
import subprocess
import argparse
import logging
from pathlib import Path
from datetime import datetime
import json
import pandas as pd
import random
import glob
from typing import List, Optional, Dict, Any
from typing import List, Dict, Optional

# Default configuration based on DSI Studio source code analysis
DEFAULT_CONFIG = {
    # Common atlases - Note: Actual availability depends on your DSI Studio installation
    'atlases': [
        'AAL', 'AAL2', 'AAL3', 'Brodmann', 'HCP-MMP', 'AICHA', 
        'Talairach', 'FreeSurferDKT', 'FreeSurferDKT_Cortical', 'Schaefer100', 
        'Schaefer200', 'Schaefer400', 'Gordon333', 'Power264'
    ],
    # All connectivity values from DSI Studio source code
    'connectivity_values': ['count', 'ncount', 'ncount2', 'mean_length', 'qa', 'fa', 'dti_fa', 
                           'md', 'ad', 'rd', 'iso', 'rdi', 'ndi', 'dti_ad', 'dti_rd', 
                           'dti_md', 'trk'],
    'track_count': 100000,
    'thread_count': 8,
    'dsi_studio_cmd': 'dsi_studio',
    # Tracking parameters from source code analysis
    'tracking_parameters': {
        'method': 0,  # 0=streamline(Euler), 1=RK4, 2=voxel tracking
        'otsu_threshold': 0.6,  # Default Otsu threshold
        'fa_threshold': 0.0,  # FA threshold (0=automatic)
        'turning_angle': 0.0,  # Maximum turning angle (0=random 15-90°)
        'step_size': 0.0,  # Step size in mm (0=random 1-3 voxels)
        'smoothing': 0.0,  # Fraction of previous direction (0-1)
        'min_length': 0,  # Minimum fiber length (0=dataset specific)
        'max_length': 0,  # Maximum fiber length (0=dataset specific)
        'track_voxel_ratio': 2.0,  # Seeds-per-voxel ratio
        'check_ending': 0,  # Drop tracks not terminating in ROI (0=off, 1=on)
        'random_seed': 0,  # Random seed for tracking
        'dt_threshold': 0.2  # Differential tracking threshold
    },
    'connectivity_options': {
        'connectivity_type': 'pass',  # 'pass' or 'end'
        'connectivity_threshold': 0.001,  # Threshold for connectivity matrix
        'connectivity_output': 'matrix,connectogram,measure'  # Output types
    }
}

class ConnectivityExtractor:
    """Main class for extracting connectivity matrices from DSI Studio."""
    
    def __init__(self, config: Dict = None):
        """Initialize the extractor with configuration."""
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.setup_logging()
    
    def find_fib_files(self, input_folder: str, pattern: str = "*.fib.gz") -> List[str]:
        """
        Find all fiber files in a folder, supporting both .fib.gz and .fz extensions.
        
        Parameters:
        -----------
        input_folder : str
            Path to folder containing fiber files
        pattern : str
            File pattern to match (default: *.fib.gz)
            
        Returns:
        --------
        List[str]
            List of found fiber files
        """
        # Enhanced patterns to catch both .fz and .fib.gz files
        base_patterns = []
        
        if pattern == "*.fib.gz":
            # If default pattern, search for both extensions
            base_patterns = ["*.fib.gz", "*.fz"]
        else:
            # Use provided pattern, but also try .fz variant
            base_patterns = [pattern]
            if not pattern.endswith(".fz"):
                fz_pattern = pattern.replace(".fib.gz", ".fz")
                base_patterns.append(fz_pattern)
        
        # Create comprehensive search patterns
        search_patterns = []
        for base_pattern in base_patterns:
            # Direct search in folder
            search_patterns.append(os.path.join(input_folder, base_pattern))
            # Recursive search
            search_patterns.append(os.path.join(input_folder, "**", base_pattern))
        
        all_files = []
        for search_pattern in search_patterns:
            files = glob.glob(search_pattern, recursive=True)
            all_files.extend(files)
        
        # Remove duplicates and sort
        unique_files = sorted(list(set(all_files)))
        
        # Categorize files by type
        fz_files = [f for f in unique_files if f.endswith('.fz')]
        fib_gz_files = [f for f in unique_files if f.endswith('.fib.gz')]
        
        self.logger.info(f"Found {len(unique_files)} fiber files in {input_folder}")
        if fz_files:
            self.logger.info(f"  - {len(fz_files)} .fz files")
        if fib_gz_files:
            self.logger.info(f"  - {len(fib_gz_files)} .fib.gz files")
            
        # Show first few files as examples
        for i, file in enumerate(unique_files[:5]):
            self.logger.info(f"    {i+1}. {os.path.basename(file)}")
        if len(unique_files) > 5:
            self.logger.info(f"    ... and {len(unique_files) - 5} more")
            
        return unique_files
    
    def select_pilot_files(self, file_list: List[str], pilot_count: int = 1) -> List[str]:
        """
        Select random files for pilot testing.
        
        Parameters:
        -----------
        file_list : List[str]
            List of all available files
        pilot_count : int
            Number of files to select for pilot (default: 1)
            
        Returns:
        --------
        List[str]
            List of selected pilot files
        """
        if not file_list:
            self.logger.warning("No files available for pilot selection")
            return []
        
        if pilot_count >= len(file_list):
            self.logger.info(f"Pilot count ({pilot_count}) >= available files ({len(file_list)}), using all files")
            return file_list
        
        pilot_files = random.sample(file_list, pilot_count)
        
        self.logger.info(f"Selected {len(pilot_files)} pilot files:")
        for file in pilot_files:
            self.logger.info(f"  - {os.path.basename(file)}")
            
        return pilot_files
        
    def setup_logging(self):
        """Set up logging configuration with dedicated logs folder."""
        # Create logs directory if it doesn't exist
        logs_dir = 'logs'
        os.makedirs(logs_dir, exist_ok=True)
        
        # Generate timestamped log filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = os.path.join(logs_dir, f'connectivity_extraction_{timestamp}.log')
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_file)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Log session header with DSI Studio version
        self.logger.info("=" * 60)
        self.logger.info("🧠 DSI STUDIO CONNECTIVITY EXTRACTION SESSION START")
        self.logger.info("=" * 60)
        
        # Try to get and log DSI Studio version early
        dsi_check = self.check_dsi_studio()
        if dsi_check['available'] and dsi_check['version']:
            self.logger.info(f"🔧 DSI Studio Version: {dsi_check['version']}")
        self.logger.info(f"📁 DSI Studio Path: {dsi_check['path']}")
        self.logger.info(f"📅 Session Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(f"📄 Log File: {log_file}")
        self.logger.info("=" * 60)
    
    def check_dsi_studio(self) -> Dict[str, Any]:
        """Check if DSI Studio is available and working properly."""
        dsi_cmd = self.config['dsi_studio_cmd']
        result = {
            'available': False,
            'path': dsi_cmd,
            'version': None,
            'error': None
        }
        
        # Check if file exists (for absolute paths)
        if os.path.isabs(dsi_cmd):
            if not os.path.exists(dsi_cmd):
                result['error'] = f"DSI Studio executable not found at: {dsi_cmd}"
                return result
            if not os.access(dsi_cmd, os.X_OK):
                result['error'] = f"DSI Studio executable is not executable: {dsi_cmd}"
                return result
        
        # Test execution with --version (avoids GUI launch)
        try:
            version_result = subprocess.run(
                [dsi_cmd, '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if version_result.returncode == 0:
                result['available'] = True
                # Extract version info
                if version_result.stdout:
                    result['version'] = version_result.stdout.strip()
                elif version_result.stderr:
                    # Some versions output to stderr
                    result['version'] = version_result.stderr.strip()
                else:
                    result['version'] = "Version detected but no output"
            else:
                # If --version fails, try --help as fallback (but with shorter timeout)
                try:
                    help_result = subprocess.run(
                        [dsi_cmd, '--help'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if help_result.returncode == 0:
                        result['available'] = True
                        result['version'] = "Version unknown (--help works)"
                    else:
                        result['error'] = f"DSI Studio returned error code {version_result.returncode}"
                except subprocess.TimeoutExpired:
                    result['error'] = "DSI Studio --help command timed out (GUI launch issue?)"
                except Exception as e:
                    result['error'] = f"Error testing DSI Studio --help: {str(e)}"
                
        except subprocess.TimeoutExpired:
            result['error'] = "DSI Studio --version command timed out"
        except FileNotFoundError:
            result['error'] = f"DSI Studio command not found: {dsi_cmd}. Check PATH or use absolute path."
        except Exception as e:
            result['error'] = f"Error running DSI Studio --version: {str(e)}"
            
        return result
    
    def validate_input_file(self, filepath: str) -> bool:
        """Validate the input fiber file."""
        path = Path(filepath)
        if not path.exists():
            self.logger.error(f"Input file does not exist: {filepath}")
            return False
        
        if not (filepath.endswith('.fib.gz') or filepath.endswith('.fz')):
            self.logger.warning(f"Input file should be .fib.gz or .fz: {filepath}")
        
        return True
    
    def validate_configuration(self) -> Dict[str, Any]:
        """Comprehensive validation of configuration and environment."""
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': []
        }
        
        # 1. Check DSI Studio
        self.logger.info("🔍 Checking DSI Studio availability...")
        dsi_check = self.check_dsi_studio()
        if not dsi_check['available']:
            validation_result['errors'].append(f"DSI Studio check failed: {dsi_check['error']}")
            validation_result['valid'] = False
        else:
            msg = f"✅ DSI Studio found at: {dsi_check['path']}"
            if dsi_check['version']:
                msg += f" (Version: {dsi_check['version']})"
            validation_result['info'].append(msg)
            self.logger.info(msg)
        
        # 2. Validate atlases
        atlases = self.config.get('atlases', [])
        if not atlases:
            validation_result['warnings'].append("No atlases specified")
        else:
            self.logger.info(f"📊 Will process {len(atlases)} atlases: {', '.join(atlases)}")
            validation_result['info'].append(f"Configured atlases: {', '.join(atlases)}")
        
        # 3. Validate connectivity values
        conn_values = self.config.get('connectivity_values', [])
        if not conn_values:
            validation_result['warnings'].append("No connectivity values specified")
        else:
            self.logger.info(f"📊 Will extract {len(conn_values)} connectivity metrics: {', '.join(conn_values)}")
            validation_result['info'].append(f"Connectivity metrics: {', '.join(conn_values)}")
        
        # 4. Check tracking parameters for reasonable values
        tracking_params = self.config.get('tracking_parameters', {})
        track_count = self.config.get('track_count', 100000)
        
        if track_count <= 0:
            validation_result['errors'].append(f"Track count must be positive, got: {track_count}")
            validation_result['valid'] = False
        elif track_count < 1000:
            validation_result['warnings'].append(f"Low track count ({track_count}), results may be sparse")
        elif track_count > 1000000:
            validation_result['warnings'].append(f"Very high track count ({track_count}), processing may be slow")
        
        # Check FA threshold
        fa_threshold = tracking_params.get('fa_threshold', 0.0)
        if fa_threshold < 0 or fa_threshold > 1:
            validation_result['warnings'].append(f"FA threshold {fa_threshold} outside normal range [0-1]")
        
        # Check turning angle
        turning_angle = tracking_params.get('turning_angle', 0.0)
        if turning_angle > 180:
            validation_result['warnings'].append(f"Turning angle {turning_angle}° seems too large")
        
        # 5. Check thread count
        thread_count = self.config.get('thread_count', 8)
        if thread_count <= 0:
            validation_result['errors'].append(f"Thread count must be positive, got: {thread_count}")
            validation_result['valid'] = False
        elif thread_count > 32:
            validation_result['warnings'].append(f"Very high thread count ({thread_count}), may exceed system capacity")
        
        # Summary
        if validation_result['valid']:
            self.logger.info("✅ Configuration validation passed")
        else:
            self.logger.error("❌ Configuration validation failed")
        
        if validation_result['warnings']:
            self.logger.warning(f"⚠️  {len(validation_result['warnings'])} warning(s) found")
            for warning in validation_result['warnings']:
                self.logger.warning(f"   - {warning}")
        
        if validation_result['errors']:
            self.logger.error(f"❌ {len(validation_result['errors'])} error(s) found")
            for error in validation_result['errors']:
                self.logger.error(f"   - {error}")
        
        return validation_result
    
    def validate_input_path(self, input_path: str, file_pattern: str = "*.fib.gz") -> Dict[str, Any]:
        """Validate input path and find fiber files (runtime validation)."""
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': [],
            'files_found': []
        }
        
        if not os.path.exists(input_path):
            validation_result['errors'].append(f"Input path does not exist: {input_path}")
            validation_result['valid'] = False
            return validation_result
        
        if os.path.isfile(input_path):
            # Single file processing
            if not (input_path.endswith('.fib.gz') or input_path.endswith('.fz')):
                validation_result['warnings'].append(f"File extension should be .fib.gz or .fz: {input_path}")
            validation_result['files_found'] = [input_path]
            validation_result['info'].append(f"Single file mode: {os.path.basename(input_path)}")
            
        elif os.path.isdir(input_path):
            # Directory processing
            self.logger.info(f"🔍 Scanning directory: {input_path}")
            files_found = self.find_fib_files(input_path, file_pattern)
            
            if not files_found:
                validation_result['errors'].append(f"No fiber files found in directory: {input_path}")
                validation_result['valid'] = False
            else:
                validation_result['files_found'] = files_found
                # File info is logged by find_fib_files method
                
        else:
            validation_result['errors'].append(f"Input path is neither file nor directory: {input_path}")
            validation_result['valid'] = False
        
        return validation_result
    
    def create_output_structure(self, output_dir: str, base_name: str) -> Path:
        """Create organized output directory structure based on settings."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create parameter-based directory name for better organization
        tracking_params = self.config.get('tracking_parameters', {})
        method_name = {0: 'streamline', 1: 'rk4', 2: 'voxel'}.get(tracking_params.get('method', 0), 'streamline')
        track_count = self.config.get('track_count', 100000)
        
        # Create meaningful directory structure
        param_dir = f"tracks_{track_count//1000}k_{method_name}"
        if tracking_params.get('turning_angle', 0) != 0:
            param_dir += f"_angle{int(tracking_params['turning_angle'])}"
        if tracking_params.get('fa_threshold', 0) != 0:
            param_dir += f"_fa{tracking_params['fa_threshold']:.2f}"
            
        run_dir = Path(output_dir) / f"{base_name}_{timestamp}" / param_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # Create organized subdirectories
        for atlas in self.config['atlases']:
            # Create atlas-specific directory
            atlas_dir = run_dir / "by_atlas" / atlas
            atlas_dir.mkdir(parents=True, exist_ok=True)
            
        # Create value-specific directories
        for value in self.config['connectivity_values']:
            value_dir = run_dir / "by_metric" / value
            value_dir.mkdir(parents=True, exist_ok=True)
            
        # Create combined results directory
        (run_dir / "combined").mkdir(exist_ok=True)
        (run_dir / "logs").mkdir(exist_ok=True)
        
        return run_dir
    
    def extract_connectivity_matrix(self, input_file: str, output_dir: Path, 
                                  atlas: str, base_name: str) -> Dict:
        """Extract connectivity matrix for a specific atlas."""
        self.logger.info(f"Processing atlas: {atlas}")
        
        atlas_dir = output_dir / "by_atlas" / atlas
        output_prefix = atlas_dir / f"{base_name}_{atlas}"
        
        # Build DSI Studio command with comprehensive parameters
        cmd = [
            self.config['dsi_studio_cmd'],
            '--action=trk',
            f'--source={input_file}',
            f'--tract_count={self.config["track_count"]}',
            f'--connectivity={atlas}',
            f'--connectivity_value={",".join(self.config["connectivity_values"])}',
            f'--connectivity_type={self.config["connectivity_options"]["connectivity_type"]}',
            f'--connectivity_threshold={self.config["connectivity_options"]["connectivity_threshold"]}',
            f'--connectivity_output={self.config["connectivity_options"]["connectivity_output"]}',
            f'--thread_count={self.config["thread_count"]}',
            f'--output={output_prefix}.tt.gz',
            '--export=stat'
        ]
        
        # Add tracking parameters if they differ from defaults
        tracking_params = self.config.get('tracking_parameters', {})
        if tracking_params.get('method', 0) != 0:
            cmd.append(f'--method={tracking_params["method"]}')
        if tracking_params.get('otsu_threshold', 0.6) != 0.6:
            cmd.append(f'--otsu_threshold={tracking_params["otsu_threshold"]}')
        if tracking_params.get('fa_threshold', 0.0) != 0.0:
            cmd.append(f'--fa_threshold={tracking_params["fa_threshold"]}')
        if tracking_params.get('turning_angle', 0.0) != 0.0:
            cmd.append(f'--turning_angle={tracking_params["turning_angle"]}')
        if tracking_params.get('step_size', 0.0) != 0.0:
            cmd.append(f'--step_size={tracking_params["step_size"]}')
        if tracking_params.get('smoothing', 0.0) != 0.0:
            cmd.append(f'--smoothing={tracking_params["smoothing"]}')
        if tracking_params.get('min_length', 0) != 0:
            cmd.append(f'--min_length={tracking_params["min_length"]}')
        if tracking_params.get('max_length', 0) != 0:
            cmd.append(f'--max_length={tracking_params["max_length"]}')
        if tracking_params.get('track_voxel_ratio', 2.0) != 2.0:
            cmd.append(f'--track_voxel_ratio={tracking_params["track_voxel_ratio"]}')
        if tracking_params.get('check_ending', 0) != 0:
            cmd.append(f'--check_ending={tracking_params["check_ending"]}')
        if tracking_params.get('random_seed', 0) != 0:
            cmd.append(f'--random_seed={tracking_params["random_seed"]}')
            
        # Execute command
        start_time = datetime.now()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout
            )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            success = result.returncode == 0
            
            if success:
                self.logger.info(f"✓ Successfully processed {atlas} in {duration:.1f}s")
                # Organize output files by metric type
                self._organize_output_files(output_dir, atlas, base_name)
            else:
                self.logger.error(f"✗ Failed to process {atlas}")
                self.logger.error(f"Error output: {result.stderr}")
            
            return {
                'atlas': atlas,
                'success': success,
                'duration': duration,
                'command': ' '.join(cmd),
                'stdout': result.stdout,
                'stderr': result.stderr,
                'output_files': [str(f) for f in atlas_dir.glob(f"{base_name}_{atlas}*")]
            }
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"✗ Timeout while processing {atlas}")
            return {
                'atlas': atlas,
                'success': False,
                'duration': 3600,
                'error': 'Timeout'
            }
    
    def _organize_output_files(self, output_dir: Path, atlas: str, base_name: str):
        """Organize output files by metric type and create symlinks for easy access."""
        atlas_dir = output_dir / "by_atlas" / atlas
        
        # Move/copy connectivity files to metric-specific directories
        for value in self.config['connectivity_values']:
            metric_dir = output_dir / "by_metric" / value
            
            # Look for connectivity matrices with this metric
            pattern = f"{base_name}_{atlas}.*.{value}.*.connectivity.*"
            for file in atlas_dir.glob(pattern):
                # Create symlink in metric directory
                symlink_path = metric_dir / f"{atlas}_{file.name}"
                try:
                    if not symlink_path.exists():
                        symlink_path.symlink_to(file.resolve())
                except OSError:
                    # If symlinks not supported, copy the file
                    import shutil
                    shutil.copy2(file, symlink_path)
        
        # Create summary files in combined directory
        combined_dir = output_dir / "combined"
        
        # Copy all connectivity matrices to combined directory with descriptive names
        for conn_file in atlas_dir.glob("*.connectivity.*"):
            new_name = f"{atlas}_{conn_file.name}"
            combined_path = combined_dir / new_name
            try:
                if not combined_path.exists():
                    combined_path.symlink_to(conn_file.resolve())
            except OSError:
                import shutil
                shutil.copy2(conn_file, combined_path)
    
    def extract_all_matrices(self, input_file: str, output_dir: str, 
                           atlases: List[str] = None) -> Dict:
        """Extract connectivity matrices for all specified atlases."""
        # Run comprehensive validation first
        self.logger.info("🚀 Starting connectivity matrix extraction...")
        self.logger.info("=" * 60)
        
        validation_result = self.validate_configuration()
        if not validation_result['valid']:
            raise RuntimeError(f"Configuration validation failed: {validation_result['errors']}")
        
        # Additional DSI Studio check with detailed info
        dsi_check = self.check_dsi_studio()
        if not dsi_check['available']:
            raise RuntimeError(f"DSI Studio not available: {dsi_check['error']}")
        
        if not self.validate_input_file(input_file):
            raise ValueError(f"Invalid input file: {input_file}")
        
        atlases = atlases or self.config['atlases']
        base_name = Path(input_file).stem.replace('.fib', '').replace('.gz', '')
        
        # Create output directory structure
        run_dir = self.create_output_structure(output_dir, base_name)
        
        self.logger.info(f"🎯 Starting connectivity extraction for {len(atlases)} atlases")
        self.logger.info(f"📁 Input: {input_file}")
        self.logger.info(f"📁 Output: {run_dir}")
        self.logger.info(f"🧠 DSI Studio: {dsi_check['path']}")
        if dsi_check['version']:
            self.logger.info(f"📊 Version: {dsi_check['version']}")
        self.logger.info("=" * 60)
        
        # Process each atlas
        results = []
        for atlas in atlases:
            result = self.extract_connectivity_matrix(input_file, run_dir, atlas, base_name)
            results.append(result)
        
        # Save processing summary in logs directory
        dsi_check = self.check_dsi_studio()
        summary = {
            'input_file': input_file,
            'output_directory': str(run_dir),
            'timestamp': datetime.now().isoformat(),
            'dsi_studio': {
                'path': dsi_check['path'],
                'version': dsi_check.get('version', 'Unknown'),
                'available': dsi_check['available']
            },
            'config': self.config,
            'results': results,
            'summary': {
                'total_atlases': len(atlases),
                'successful': sum(1 for r in results if r.get('success', False)),
                'failed': sum(1 for r in results if not r.get('success', False)),
                'total_duration': sum(r.get('duration', 0) for r in results)
            }
        }
        
        # Save files in logs directory
        logs_dir = run_dir / "logs"
        summary_file = logs_dir / 'extraction_summary.json'
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        # Create results CSV in logs directory
        results_df = pd.DataFrame([
            {
                'atlas': r['atlas'],
                'success': r.get('success', False),
                'duration_seconds': r.get('duration', 0),
                'error': r.get('error', '')
            }
            for r in results
        ])
        results_df.to_csv(logs_dir / 'processing_results.csv', index=False)
        
        # Create analysis-ready summary files
        self._create_analysis_summary(run_dir, base_name, results)
        
        self.logger.info(f"Extraction completed: {summary['summary']['successful']}/{summary['summary']['total_atlases']} successful")
        return summary

    def _create_analysis_summary(self, run_dir: Path, base_name: str, results: List[Dict]):
        """Create analysis-ready summary files and directory structure overview."""
        combined_dir = run_dir / "combined"
        
        # Create directory structure README
        readme_content = f"""# Connectivity Analysis Results for {base_name}

## Directory Structure

📁 **by_atlas/** - Results organized by brain atlas
   └── Each atlas has its own subdirectory with all connectivity matrices
   
📁 **by_metric/** - Results organized by connectivity metric  
   └── Each metric has symlinks/copies from all atlases for easy comparison
   
📁 **combined/** - All connectivity matrices in one place
   └── Files renamed for easy identification: {base_name}_[atlas]_[metric].connectivity.*
   
📁 **logs/** - Processing logs and summaries
   └── extraction_summary.json, processing_results.csv, connectivity_extraction.log

## Quick Analysis Commands

### Load all connectivity matrices of same type:
```python
import glob
import scipy.io

# Load all 'count' matrices
count_matrices = []
for file in glob.glob('by_metric/count/*.connectivity.mat'):
    mat = scipy.io.loadmat(file)
    count_matrices.append(mat['connectivity'])
```

### Compare atlases for same metric:
```python
# Compare AAL2 vs HCP-MMP for FA metric
aal2_fa = scipy.io.loadmat('by_atlas/AAL2/*fa*.connectivity.mat')
hcp_fa = scipy.io.loadmat('by_atlas/HCP-MMP/*fa*.connectivity.mat')
```

### Batch load all results:
```python
combined_files = glob.glob('combined/*.connectivity.mat')
all_matrices = {{f.split('/')[-1]: scipy.io.loadmat(f) for f in combined_files}}
```

## Processing Summary

"""
        
        # Add results summary
        successful_atlases = [r['atlas'] for r in results if r.get('success', False)]
        failed_atlases = [r['atlas'] for r in results if not r.get('success', False)]
        
        readme_content += f"✅ **Successfully processed**: {', '.join(successful_atlases)}\n"
        if failed_atlases:
            readme_content += f"❌ **Failed**: {', '.join(failed_atlases)}\n"
        
        readme_content += f"\n📊 **Total matrices generated**: ~{len(successful_atlases) * len(self.config['connectivity_values'])}\n"
        
        # Write README
        with open(run_dir / "README.md", 'w') as f:
            f.write(readme_content)
            
        # Create a quick analysis starter script
        analysis_script = f'''#!/usr/bin/env python3
"""
Quick analysis starter script for connectivity matrices
Generated for: {base_name}
"""

import glob
import numpy as np
import pandas as pd
import scipy.io
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).parent
ATLASES = {self.config['atlases']}
METRICS = {self.config['connectivity_values']}

def load_connectivity_matrix(atlas, metric):
    """Load connectivity matrix for specific atlas and metric."""
    pattern = f"by_atlas/{{atlas}}/*{{metric}}*.connectivity.mat"
    files = list(BASE_DIR.glob(pattern))
    if files:
        return scipy.io.loadmat(files[0])
    return None

def load_all_matrices():
    """Load all connectivity matrices into a nested dictionary."""
    matrices = {{}}
    for atlas in ATLASES:
        matrices[atlas] = {{}}
        for metric in METRICS:
            mat = load_connectivity_matrix(atlas, metric)
            if mat:
                matrices[atlas][metric] = mat
    return matrices

def get_matrix_summary():
    """Get summary statistics for all matrices."""
    summary = []
    for atlas in ATLASES:
        for metric in METRICS:
            mat = load_connectivity_matrix(atlas, metric)
            if mat and 'connectivity' in mat:
                conn = mat['connectivity']
                summary.append({{
                    'atlas': atlas,
                    'metric': metric,
                    'shape': conn.shape,
                    'nonzero_connections': np.count_nonzero(conn),
                    'mean_strength': np.mean(conn[conn > 0]),
                    'density': np.count_nonzero(conn) / (conn.shape[0] * conn.shape[1])
                }})
    return pd.DataFrame(summary)

if __name__ == "__main__":
    print("Loading connectivity matrices...")
    matrices = load_all_matrices()
    
    print("\\nGenerating summary...")
    summary_df = get_matrix_summary()
    print(summary_df)
    
    print("\\nSaving summary to CSV...")
    summary_df.to_csv("analysis_summary.csv", index=False)
    
    print(f"\\nAnalysis complete! Found matrices for {{len(summary_df)}} atlas-metric combinations.")
'''
        
        with open(run_dir / "quick_analysis.py", 'w') as f:
            f.write(analysis_script)
            
        # Make analysis script executable
        import stat
        analysis_script_path = run_dir / "quick_analysis.py"
        analysis_script_path.chmod(analysis_script_path.stat().st_mode | stat.S_IEXEC)

def create_batch_processor(input_dir: str, output_dir: str, pattern: str = "*.fib.gz") -> List[Dict]:
    """Process multiple fiber files in batch."""
    extractor = ConnectivityExtractor()
    input_path = Path(input_dir)
    
    if not input_path.exists():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    
    # Find all matching files
    fiber_files = list(input_path.glob(pattern))
    if not fiber_files:
        raise ValueError(f"No files found matching pattern: {pattern}")
    
    extractor.logger.info(f"Found {len(fiber_files)} files to process")
    
    batch_results = []
    for fiber_file in fiber_files:
        try:
            result = extractor.extract_all_matrices(str(fiber_file), output_dir)
            batch_results.append(result)
        except Exception as e:
            extractor.logger.error(f"Failed to process {fiber_file}: {e}")
            batch_results.append({
                'input_file': str(fiber_file),
                'error': str(e),
                'success': False
            })
    
    return batch_results

def main():
    """Main function for command-line interface."""
    parser = argparse.ArgumentParser(
        description="🧠 DSI Studio Connectivity Matrix Extraction Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
🎯 QUICK START EXAMPLES:
  
  # 1. Validate setup first (recommended)
  python validate_setup.py --config my_config.json
  
  # 2. Single file processing
  python extract_connectivity_matrices.py --config my_config.json subject.fz output/
  
  # 3. Pilot test (test 1-2 files first)
  python extract_connectivity_matrices.py --config my_config.json --pilot --batch data_dir/ output/
  
  # 4. Full batch processing
  python extract_connectivity_matrices.py --config my_config.json --batch data_dir/ output/

📋 DETAILED EXAMPLES:

  # Basic single file with custom atlases
  python extract_connectivity_matrices.py --config my_config.json \\
      --atlases "AAL3,Brainnetome" subject.fz output/
  
  # Batch with specific settings
  python extract_connectivity_matrices.py --config my_config.json \\
      --batch --pattern "*.fz" --tracks 50000 --threads 16 data_dir/ output/
  
  # High-resolution tracking
  python extract_connectivity_matrices.py --config my_config.json \\
      --method 1 --fa_threshold 0.15 --turning_angle 35 subject.fz output/

📁 SUPPORTED FILE FORMATS: .fib.gz, .fz (auto-detected)

🔧 CONFIGURATION: Use --config to specify JSON configuration file
   (see example_config.json for template)

For more help: see README.md
        """)
    
    # Required arguments (made optional to show help when missing)
    parser.add_argument('input', nargs='?', 
                       help='📁 Input: .fib.gz/.fz file OR directory (for --batch mode)')
    parser.add_argument('output', nargs='?', 
                       help='📂 Output: Directory where results will be saved')
    
    # Configuration
    parser.add_argument('--config', type=str,
                       help='📄 JSON configuration file (recommended - see example_config.json)')
    
    # Processing mode
    parser.add_argument('--batch', action='store_true',
                       help='🔄 Batch mode: Process all files in input directory')
    
    parser.add_argument('--pilot', action='store_true',
                       help='🧪 Pilot mode: Test on subset of files first (use with --batch)')
    
    parser.add_argument('--pilot-count', type=int, default=1,
                       help='🔢 Number of files for pilot test (default: 1)')
    
    parser.add_argument('--pattern', default='*.fib.gz',
                       help='🔍 File pattern for batch mode (default: *.fib.gz, also searches .fz)')
    
    # Override configuration settings
    parser.add_argument('-a', '--atlases', 
                       help='🧠 Override config: Comma-separated atlases (e.g., "AAL3,Brainnetome")')
    
    parser.add_argument('-v', '--values',
                       help='📊 Override config: Comma-separated connectivity metrics')
    
    parser.add_argument('-t', '--tracks', type=int,
                       help='🛤️  Override config: Number of tracks to generate (e.g., 100000)')
    
    parser.add_argument('-j', '--threads', type=int,
                       help='⚡ Override config: Number of processing threads')
    
    # Advanced tracking parameters (override config)
    parser.add_argument('--method', type=int, choices=[0, 1, 2],
                       help='🎯 Tracking method: 0=Streamline(Euler), 1=RK4, 2=Voxel')
    
    parser.add_argument('--fa_threshold', type=float,
                       help='📉 FA threshold for termination (0=automatic, 0.1-0.3 typical)')
    
    parser.add_argument('--turning_angle', type=float,
                       help='🔄 Max turning angle in degrees (0=auto 15-90°, 35-60° typical)')
    
    parser.add_argument('--step_size', type=float,
                       help='📏 Step size in mm (0=auto 1-3 voxels)')
    
    parser.add_argument('--smoothing', type=float,
                       help='🌊 Smoothing fraction (0-1, higher=smoother tracks)')
    
    parser.add_argument('--track_voxel_ratio', type=float,
                       help='🎲 Seeds per voxel ratio (higher=more tracks per region)')
    
    parser.add_argument('--connectivity_type', choices=['pass', 'end'],
                       help='🔗 Connectivity type: pass=whole tract, end=endpoints only')
    
    parser.add_argument('--connectivity_threshold', type=float,
                       help='🎚️  Connectivity threshold for matrix filtering')
    
    args = parser.parse_args()
    
    # Show help if no arguments provided
    if len(sys.argv) == 1 or (not args.input and not args.output and not args.config):
        parser.print_help()
        print("\n💡 TIP: Start with validation:")
        print("   python validate_setup.py --config example_config.json")
        print("\n💡 Or see the README.md for detailed examples!")
        sys.exit(0)
    
    # Load configuration from file if provided
    config = DEFAULT_CONFIG.copy()
    if args.config:
        try:
            with open(args.config, 'r') as f:
                config.update(json.load(f))
        except FileNotFoundError:
            print(f"❌ Configuration file not found: {args.config}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in configuration file: {e}")
            sys.exit(1)
    
    # Override with command line arguments (only if provided)
    if args.atlases:
        config['atlases'] = args.atlases.split(',')
    if args.values:
        config['connectivity_values'] = args.values.split(',')
    if args.tracks:
        config['track_count'] = args.tracks
    if args.threads:
        config['thread_count'] = args.threads
    
    # Update tracking parameters if provided
    tracking_params = config.get('tracking_parameters', {})
    if args.method is not None:
        tracking_params['method'] = args.method
    if args.fa_threshold is not None:
        tracking_params['fa_threshold'] = args.fa_threshold
    if args.turning_angle is not None:
        tracking_params['turning_angle'] = args.turning_angle
    if args.step_size is not None:
        tracking_params['step_size'] = args.step_size
    if args.smoothing is not None:
        tracking_params['smoothing'] = args.smoothing
    if args.track_voxel_ratio is not None:
        tracking_params['track_voxel_ratio'] = args.track_voxel_ratio
    
    config['tracking_parameters'] = tracking_params
    
    # Update connectivity options if provided
    connectivity_options = config.get('connectivity_options', {})
    if args.connectivity_type is not None:
        connectivity_options['connectivity_type'] = args.connectivity_type
    if args.connectivity_threshold is not None:
        connectivity_options['connectivity_threshold'] = args.connectivity_threshold
    
    config['connectivity_options'] = connectivity_options
    
    # Check for required arguments
    if not args.input or not args.output:
        print("❌ Error: Both input and output arguments are required!\n")
        parser.print_help()
        print("\n💡 QUICK START:")
        print("   python validate_setup.py --config example_config.json")
        print("   python extract_connectivity_matrices.py --config example_config.json input.fz output/")
        sys.exit(1)
    
    try:
        extractor = ConnectivityExtractor(config)
        
        # Run validation first
        print("🔍 Validating configuration...")
        validation_result = extractor.validate_configuration()
        
        if not validation_result['valid']:
            print("❌ Configuration validation failed!")
            for error in validation_result['errors']:
                print(f"   ❌ {error}")
            sys.exit(1)
        
        if validation_result['warnings']:
            print(f"⚠️  {len(validation_result['warnings'])} warning(s):")
            for warning in validation_result['warnings']:
                print(f"   ⚠️  {warning}")
            print()
        
        if args.batch or os.path.isdir(args.input):
            # Batch processing mode
            print(f"🔍 Batch processing mode activated")
            print(f"📁 Input directory: {args.input}")
            print(f"🔍 File pattern: {args.pattern}")
            
            # Validate input path and find files
            input_validation = extractor.validate_input_path(args.input, args.pattern)
            if not input_validation['valid']:
                print("❌ Input validation failed!")
                for error in input_validation['errors']:
                    print(f"   ❌ {error}")
                sys.exit(1)
            
            fiber_files = input_validation['files_found']
            if not fiber_files:
                print("❌ No fiber files found!")
                print("💡 Supported formats: .fib.gz and .fz files")
                sys.exit(1)
            
            # Handle pilot mode
            if args.pilot:
                print(f"🧪 Pilot mode: selecting {args.pilot_count} random file(s)")
                fiber_files = extractor.select_pilot_files(fiber_files, args.pilot_count)
            
            # Process files
            print(f"📊 Processing {len(fiber_files)} file(s)...")
            batch_results = []
            
            for i, fiber_file in enumerate(fiber_files, 1):
                print(f"\n{'='*60}")
                print(f"Processing file {i}/{len(fiber_files)}: {os.path.basename(fiber_file)}")
                print(f"{'='*60}")
                
                try:
                    result = extractor.extract_all_matrices(str(fiber_file), args.output)
                    batch_results.append({
                        'file': fiber_file,
                        'success': True,
                        'output_dir': result.get('output_folder', 'unknown'),
                        'matrices_extracted': result.get('matrices_extracted', 0)
                    })
                    print(f"✅ Successfully processed {os.path.basename(fiber_file)}")
                    
                except Exception as e:
                    print(f"❌ Failed to process {os.path.basename(fiber_file)}: {e}")
                    batch_results.append({
                        'file': fiber_file,
                        'success': False,
                        'error': str(e)
                    })
                    continue
            
            # Summary
            successful = sum(1 for r in batch_results if r.get('success', False))
            failed = len(batch_results) - successful
            
            print(f"\n{'='*60}")
            print(f"BATCH PROCESSING SUMMARY")
            print(f"{'='*60}")
            print(f"📁 Total files processed: {len(batch_results)}")
            print(f"✅ Successful: {successful}")
            print(f"❌ Failed: {failed}")
            
            if args.pilot:
                print(f"🧪 Pilot mode: {args.pilot_count} file(s) tested")
                print(f"   Ready for full batch processing: {'YES' if successful > 0 else 'NO'}")
            
            # Save batch summary
            dsi_check = extractor.check_dsi_studio()
            summary_file = os.path.join(args.output, 'batch_processing_summary.json')
            with open(summary_file, 'w') as f:
                json.dump({
                    'processed_files': batch_results,
                    'dsi_studio': {
                        'path': dsi_check['path'],
                        'version': dsi_check.get('version', 'Unknown'),
                        'available': dsi_check['available']
                    },
                    'summary': {
                        'total': len(batch_results),
                        'successful': successful,
                        'failed': failed,
                        'pilot_mode': args.pilot,
                        'pilot_count': args.pilot_count if args.pilot else None
                    },
                    'timestamp': datetime.now().isoformat()
                }, f, indent=2)
            
            print(f"📄 Batch summary saved: {summary_file}")
            
        else:
            # Single file processing mode
            print(f"📊 Processing single file: {args.input}")
            
            # Log DSI Studio version at start of single file processing
            dsi_check = extractor.check_dsi_studio()
            logging.info(f"DSI Studio: {dsi_check['path']} - Version: {dsi_check.get('version', 'Unknown')}")
            
            # Validate single input file
            input_validation = extractor.validate_input_path(args.input)
            if not input_validation['valid']:
                print("❌ Input file validation failed!")
                for error in input_validation['errors']:
                    print(f"   ❌ {error}")
                sys.exit(1)

            result = extractor.extract_all_matrices(args.input, args.output)
            print("✅ Processing completed successfully!")
            
    except KeyboardInterrupt:
        print("\n⚠️  Processing interrupted by user")
        sys.exit(1)
    
    except Exception as e:
        logging.error(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
