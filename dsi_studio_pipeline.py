#!/usr/bin/env python3
"""
DSI Studio Preprocessing Pipeline

This script automates the preprocessing steps for DSI Studio starting from qsiprep outputs:
1. SRC file generation
2. Reconstruction (FIB file generation)
3. Connectometry Database creation

Usage:
    python dsi_studio_pipeline.py --qsiprep_dir /path/to/qsiprep --output_dir ./dsi_studio_output
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
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict

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
        
        # Set DSI Studio command path
        if args.dsi_studio_path:
            self.dsi_studio_cmd = str(Path(args.dsi_studio_path) / "dsi_studio")
        else:
            self.dsi_studio_cmd = args.dsi_studio_cmd
            
        self.method = args.method
        self.param0 = args.param0
        self.threads = args.threads
        self.db_name = args.db_name
        self.pilot = args.pilot
        self.verify_rawdata = args.verify_rawdata
        self.require_mask = args.require_mask
        self.require_t1w = args.require_t1w
        self.skip_existing = args.skip_existing
        self.min_file_age = args.min_file_age  # Minimum age in seconds
        self.dry_run = args.dry_run
        self.run_connectivity = args.run_connectivity
        self.connectivity_config = Path(args.connectivity_config).resolve() if args.connectivity_config else None
        self.connectivity_output_dir = Path(args.connectivity_output_dir).resolve() if args.connectivity_output_dir else self.output_dir / "connectivity"
        self.connectivity_threads = args.connectivity_threads

        # Optional rawdata path for cross-checking
        if args.rawdata_dir:
            self.rawdata_dir = Path(args.rawdata_dir).resolve()
        else:
            # Default: sibling rawdata directory
            self.rawdata_dir = self.qsiprep_dir.parent / "rawdata"
        
        self.src_dir = self.output_dir / "src"
        self.fib_dir = self.output_dir / "fib"
        self.diff_dir = self.output_dir / "diff"
        self.reports_dir = self.output_dir / "reports"
        
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.fib_dir.mkdir(parents=True, exist_ok=True)
        self.diff_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.connectivity_output_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = setup_logging(self.output_dir)
        self.logger.info(f"Starting DSI Studio Pipeline")
        self.logger.info(f"QSIPREP Dir: {self.qsiprep_dir}")
        self.logger.info(f"Output Dir: {self.output_dir}")

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

    def _collect_reconstruction_outputs(self, src_file: Path) -> List[Path]:
        """Gather reconstructed delta outputs (.fib or .odf) and decompress .sz archives."""
        outputs = []
        prefix = src_file.name.split(".src")[0]
        parent = src_file.parent
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

    def run_command(self, cmd: List[str]):
        """Run a shell command and log output"""
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

    def _parse_sub_ses(self, path: Path) -> Tuple[str, str]:
        parts = path.name.split("_")
        sub = next((p for p in parts if p.startswith("sub-")), "")
        ses = next((p for p in parts if p.startswith("ses-")), "")
        if ses:
            ses = ses.split(".")[0] # Clean 'ses-1.odf.qsdr.fz' to 'ses-1'
        return sub, ses

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
            
        valid_dwi = []
        current_time = datetime.now().timestamp()
        
        for dwi in all_dwi:
            bval = dwi.with_suffix('').with_suffix('').with_suffix('.bval')
            bvec = dwi.with_suffix('').with_suffix('').with_suffix('.bvec')
            if not (bval.exists() and bvec.exists()):
                self.logger.warning(f"Skipping {dwi.name}: Missing .bval or .bvec")
                self.stats["skipped_missing"] += 1
                continue
            # Sanity check file sizes
            if dwi.stat().st_size == 0 or bval.stat().st_size == 0 or bvec.stat().st_size == 0:
                self.logger.warning(f"Skipping {dwi.name}: One of the files is empty (nii/bval/bvec)")
                self.stats["skipped_missing"] += 1
                continue
            
            # Check if files are recent (possibly still being written by qsiprep)
            if self.min_file_age > 0:
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
        if self.pilot:
            subjects = set()
            for dwi in valid_dwi:
                sub_id = dwi.name.split('_')[0]
                subjects.add(sub_id)
            
            if subjects:
                selected_sub = random.choice(list(subjects))
                selected_files = [f for f in valid_dwi if f.name.startswith(selected_sub + "_")]
                self.logger.info(f"PILOT MODE: Randomly selected subject {selected_sub} ({len(selected_files)} files)")
                return selected_files
            
        self.logger.info(f"Found {len(valid_dwi)} valid DWI files")
        return valid_dwi

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

        output_src = self.src_dir / f"{base_id}.src.gz"
        
        cmd = [
            self.dsi_studio_cmd,
            "--action=src",
            f"--source={dwi_file}",
            f"--bval={bval_file}",
            f"--bvec={bvec_file}",
            f"--output={output_src}"
        ]

        # Try to find T1w image in anat directory
        anat_dir = dwi_file.parents[1] / "anat"
        t1w_files = list(anat_dir.glob(f"{subject_id}*_desc-preproc_T1w.nii.gz"))
        if t1w_files:
            cmd.append(f"--t1w={t1w_files[0]}")
            self.logger.info(f"Found T1w for {base_id}: {t1w_files[0].name}")

        # Try to find mask
        mask_files = list(dwi_file.parent.glob(f"{base_id}*_desc-brain_mask.nii.gz"))
        if mask_files:
            cmd.append(f"--mask={mask_files[0]}")
            self.logger.info(f"Found mask for {base_id}: {mask_files[0].name}")
        elif self.require_mask:
            self.logger.warning(f"Missing mask for {base_id}; skipping due to --require-mask")
            return None

        if self.require_t1w and not t1w_files:
            self.logger.warning(f"Missing T1w for {base_id}; skipping due to --require-t1w")
            return None
        
        if output_src.exists() and self.skip_existing:
            self.logger.info(f"SRC exists, skipping generation: {output_src.name}")
            return output_src

        if self.run_command(cmd):
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

        # If a fib already exists and skipping is enabled, reuse it
        existing_fib = [p for p in self.fib_dir.glob(f"{subject_prefix}*")]
        if existing_fib and self.skip_existing:
            self.logger.info(f"FIB exists, skipping reconstruction: {existing_fib[0].name}")
            self.stats["skipped_existing"] += 1
            return existing_fib[0]

        cmd = [
            self.dsi_studio_cmd,
            "--action=rec",
            f"--source={src_file}",
            f"--method={self.method}",
            f"--param0={self.param0}",
            f"--thread_count={self.threads}",
            "--other_output=all"
        ]
        
        if self.run_command(cmd):
            generated_fib = self._collect_reconstruction_outputs(src_file)
            if generated_fib:
                moved = []
                for fib in generated_fib:
                    dest = self.fib_dir / fib.name
                    if not self.dry_run:
                        fib.rename(dest)
                    else:
                        self.logger.info(f"Dry run: would move {fib} to {dest}")
                    moved.append(dest)
                self.stats["fib_ok"] += len(moved)
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
        output_path = self.diff_dir / output_name
        
        if output_path.exists() and self.skip_existing:
            self.logger.info(f"Diff already exists, skipping: {output_name}")
            return output_path

        # Use the specialized create_differential_fib.py script
        script_path = Path(__file__).parent / "create_differential_fib.py"
        cmd = [
            "python3",
            str(script_path),
            "--baseline", str(baseline_fib),
            "--followup", str(followup_fib),
            "--output", str(output_path)
        ]
        
        self.logger.info(f"Computing longitudinal change (v2): {ses_f} - {ses_b} for {sub_f}")
        if self.run_command(cmd):
            return output_path
        return None

    def run_connectivity_extraction(self, fib_files: List[Path]):
        """Run connectivity matrix extraction on generated FIB files."""
        if not fib_files:
            self.logger.warning("No FIB files available for connectivity extraction")
            return
        extractor = Path(__file__).parent / "extract_connectivity_matrices.py"
        if not extractor.exists():
            self.logger.error(f"Connectivity extractor not found at {extractor}")
            return
        if self.connectivity_config and not self.connectivity_config.exists():
            self.logger.error(f"Connectivity config not found: {self.connectivity_config}")
            return

        for fib in fib_files:
            cmd = ["python3", str(extractor)]
            if self.connectivity_config:
                cmd += ["--config", str(self.connectivity_config)]
            if self.connectivity_threads:
                cmd += ["--threads", str(self.connectivity_threads)]
            # Pass DSI Studio path to extractor
            cmd += ["--dsi_studio_cmd", self.dsi_studio_cmd]
            cmd += [str(fib), str(self.connectivity_output_dir)]
            self.logger.info(f"Launching connectivity extraction for {fib.name}")
            self.run_command(cmd)

    def create_database(self, fib_files: List[Path], output_db: Optional[Path] = None, index_name: Optional[str] = None):
        """Create connectometry database from FIB files"""
        if not fib_files:
            self.logger.error("No FIB files found to create database")
            return
        
        if output_db is None:
            output_db = self.output_dir / self.db_name
        
        fib_list = ",".join([str(f) for f in fib_files])
        
        cmd = [
            self.dsi_studio_cmd,
            "--action=db",
            f"--source={fib_list}",
            f"--output={output_db}"
        ]

        if index_name:
            cmd.append(f"--index_name={index_name}")
        
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
        
        report_file = self.reports_dir / f"{subject_id}_report.html"
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

        dwi_files = self.find_qsiprep_files()
        fib_files = []
        
        for dwi in dwi_files:
            self.logger.info(f"Processing {dwi.name}")
            
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
                fib = self.reconstruct_fib(src)
                if fib:
                    fib_files.append(fib)
                    session_info['fib_file'] = fib.name
                    session_info['status'] = 'success'
            else:
                self.stats["skipped_missing"] += 1
            
            # Track for HTML report
            if sub_id not in self.subject_details:
                self.subject_details[sub_id] = []
            self.subject_details[sub_id].append(session_info)
        
        # Generate HTML reports for each subject
        for subject_id, sessions in self.subject_details.items():
            self._generate_html_report(subject_id, sessions)
        
        self.stats["processed"] = len(fib_files)

        if self.run_connectivity:
            self.logger.info("Starting connectivity extraction step")
            self.run_connectivity_extraction(fib_files)
        
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
                    group_key = f"{followup_ses}_minus_{baseline_ses}"
                    if group_key not in diff_groups:
                        diff_groups[group_key] = []
                    diff_groups[group_key].append(diff_fib)

        # Create longitudinal databases
        for group_key, group_fibs in diff_groups.items():
            db_path = self.output_dir / f"longitudinal_{group_key}.db.fib.gz"
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
        
        self.logger.info(
            f"Pipeline completed. Processed={self.stats['processed']}, Found={self.stats.get('found', len(dwi_files))}, "
            f"SRC ok={self.stats['src_ok']}, FIB ok={self.stats['fib_ok']}, "
            f"Skipped missing={self.stats['skipped_missing']}, Skipped existing={self.stats['skipped_existing']}"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DSI Studio Preprocessing Pipeline")
    parser.add_argument("--qsiprep_dir", required=True, help="Path to qsiprep output directory")
    parser.add_argument("--output_dir", required=True, help="Path to output directory")
    parser.add_argument("--dsi_studio_cmd", default="/data/local/software/dsi-studio/dsi_studio", help="Path to dsi_studio executable")
    parser.add_argument("--dsi_studio_path", help="Path to DSI Studio installation folder (containing dsi_studio executable)")
    parser.add_argument("--method", default="4", help="Reconstruction method (4=GQI, 7=QSDR)")
    parser.add_argument("--param0", default="1.25", help="Diffusion sampling length ratio")
    parser.add_argument("--threads", default="8", help="Number of threads")
    parser.add_argument("--db_name", default="connectometry.db.fib.gz", help="Name of the output database file")
    parser.add_argument("--rawdata_dir", help="Path to raw BIDS data (for verification)")
    parser.add_argument("--verify_rawdata", action="store_true", help="Cross-check rawdata vs qsiprep outputs")
    parser.add_argument("--require_mask", action="store_true", help="Skip subjects without brain mask")
    parser.add_argument("--require_t1w", action="store_true", help="Skip subjects without T1w")
    parser.add_argument("--skip_existing", action="store_true", help="Skip subjects if SRC/FIB already exist")
    parser.add_argument("--min_file_age", type=int, default=300, help="Minimum file age in seconds (default: 300s/5min) to avoid processing files still being written")
    parser.add_argument("--pilot", action="store_true", help="Process only one randomly chosen subject")
    parser.add_argument("--dry_run", action="store_true", help="Show commands without running them")
    parser.add_argument("--run_connectivity", action="store_true", help="Run connectivity extraction after FIB generation")
    parser.add_argument("--connectivity_config", help="Path to connectivity extractor JSON config (e.g., graph_analysis_config.json)")
    parser.add_argument("--connectivity_output_dir", help="Directory for connectivity outputs (default: output_dir/connectivity)")
    parser.add_argument("--connectivity_threads", type=int, help="Thread override for connectivity extraction")
    
    args = parser.parse_args()
    
    pipeline = DSIStudioPipeline(args)
    pipeline.run()
