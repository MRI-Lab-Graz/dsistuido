#!/usr/bin/env python3
"""
Convert DSI Studio outputs to CSV format

This utility script converts DSI Studio output files to CSV format:
- .mat files (connectivity matrices) → .csv + .simple.csv  
- .connectogram.txt files (edge lists) → .csv
- .network_measures.txt files (graph measures) → .csv

Usage: python convert_mat_to_csv.py [directory_with_dsi_outputs]
"""

import os
import sys
import glob
import argparse
from pathlib import Path

try:
    import scipy.io
    import numpy as np
    import pandas as pd
    MAT_SUPPORT = True
except ImportError:
    print("❌ Error: Required packages not found!")
    print("   Install with: pip install scipy pandas numpy")
    sys.exit(1)


def _write_matrix_csv(mat_file_path, matrix, suffix_tag):
    """Write one region-by-region matrix to a labeled CSV + a plain numeric CSV."""
    n_regions = matrix.shape[0]
    region_names = [f'region_{i+1:03d}' for i in range(n_regions)]
    df = pd.DataFrame(matrix, index=region_names, columns=region_names)

    base = mat_file_path.with_suffix('')
    csv_path = base.with_name(f'{base.name}{suffix_tag}.csv')
    simple_csv_path = base.with_name(f'{base.name}{suffix_tag}.simple.csv')
    df.to_csv(csv_path, index=True)
    np.savetxt(simple_csv_path, matrix, delimiter=',', fmt='%.6f')
    return str(csv_path), str(simple_csv_path)


def convert_single_mat(mat_file_path, verbose=False):
    """Convert a single .mat file to CSV.

    Handles two DSI Studio .mat layouts:
    - Legacy: a single matrix under 'connectivity'/'matrix'/'data'.
    - 2025.04.16+ builds: every metric bundled into one file as
      "<metric> r2r" (region-to-region NxN matrix) plus a "<metric> t2r"
      per-region column that isn't a connectivity matrix.
    """
    try:
        # Load .mat file
        mat_data = scipy.io.loadmat(str(mat_file_path))

        # Legacy format: one matrix under a simple key
        legacy_key = next((k for k in ('connectivity', 'matrix', 'data') if k in mat_data), None)
        if legacy_key is not None:
            connectivity_matrix = mat_data[legacy_key]
            if connectivity_matrix.ndim != 2:
                return {'success': False, 'error': f'Expected 2D matrix, got {connectivity_matrix.ndim}D'}
            csv_path, simple_csv_path = _write_matrix_csv(mat_file_path, connectivity_matrix, '')
            return {
                'success': True,
                'csv_path': csv_path,
                'simple_csv_path': simple_csv_path,
                'shape': connectivity_matrix.shape
            }

        # Newer format: auto-discover every "<metric> r2r" square matrix
        r2r_keys = [k for k in mat_data.keys()
                   if k.endswith(' r2r') and getattr(mat_data[k], 'ndim', 0) == 2
                   and mat_data[k].shape[0] == mat_data[k].shape[1]]

        if not r2r_keys:
            available_keys = [k for k in mat_data.keys() if not k.startswith('__')]
            if verbose:
                print(f"   No connectivity matrix found. Available keys: {available_keys}")
            return {'success': False, 'error': 'No region-to-region connectivity matrix found in .mat file'}

        metrics = {}
        for key in r2r_keys:
            metric_name = key[:-len(' r2r')]
            safe_tag = '.' + metric_name.replace(' ', '_').replace('/', '_')
            csv_path, simple_csv_path = _write_matrix_csv(mat_file_path, mat_data[key], safe_tag)
            metrics[metric_name] = {'csv_path': csv_path, 'simple_csv_path': simple_csv_path,
                                    'shape': mat_data[key].shape}
            if verbose:
                print(f"   Wrote '{metric_name}' ({mat_data[key].shape}) -> {Path(csv_path).name}")

        first = next(iter(metrics.values()))
        return {
            'success': True,
            'csv_path': first['csv_path'],
            'simple_csv_path': first['simple_csv_path'],
            'shape': first['shape'],
            'metrics': metrics
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="🔄 Convert DSI Studio .mat files to CSV format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
📋 EXAMPLES:

  # Convert all .mat files in current directory
  python convert_mat_to_csv.py .
  
  # Convert all .mat files in specific directory
  python convert_mat_to_csv.py /path/to/matrices/
  
  # Convert single file
  python convert_mat_to_csv.py matrix.mat
  
  # Verbose output
  python convert_mat_to_csv.py --verbose /path/to/matrices/

📄 OUTPUT:
  For each input.mat file, creates:
  - input.csv (with region labels)
  - input.simple.csv (numbers only)
        """)
    
    parser.add_argument('input', 
                       help='📁 Input: .mat file OR directory containing .mat files')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='🔍 Verbose output')
    
    args = parser.parse_args()
    
    # Determine input type and find .mat files
    input_path = Path(args.input)
    
    if not input_path.exists():
        print(f"❌ Error: Input path does not exist: {args.input}")
        sys.exit(1)
    
    mat_files = []
    if input_path.is_file():
        if input_path.suffix == '.mat':
            mat_files = [input_path]
        else:
            print(f"❌ Error: File is not a .mat file: {args.input}")
            sys.exit(1)
    elif input_path.is_dir():
        mat_files = list(input_path.rglob('*.mat'))
        if not mat_files:
            print(f"❌ Error: No .mat files found in directory: {args.input}")
            sys.exit(1)
    
    print(f"🔄 Converting {len(mat_files)} .mat file(s) to CSV...")
    
    successful = 0
    failed = 0
    csv_files_created = 0

    for mat_file in mat_files:
        if args.verbose:
            print(f"\n📄 Converting: {mat_file.name}")

        result = convert_single_mat(mat_file, args.verbose)

        if result['success']:
            successful += 1
            n_metrics = len(result['metrics']) if 'metrics' in result else 1
            csv_files_created += n_metrics * 2
            if args.verbose:
                if 'metrics' in result:
                    print(f"   ✓ Success: {n_metrics} metric(s) -> {n_metrics * 2} CSV files")
                else:
                    print(f"   ✓ Success: {result['shape']} matrix")
                    print(f"   📊 Labeled CSV: {Path(result['csv_path']).name}")
                    print(f"   📊 Simple CSV: {Path(result['simple_csv_path']).name}")
            else:
                print(f"✓ {mat_file.name} → CSV ({n_metrics} metric(s))")
        else:
            failed += 1
            print(f"✗ {mat_file.name}: {result['error']}")

    print(f"\n📊 Conversion Summary:")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")
    print(f"   📄 Total CSV files created: {csv_files_created}")
    
    if successful > 0:
        print(f"\n💡 Usage Tips:")
        print(f"   - Use .csv files with pandas: pd.read_csv('file.csv', index_col=0)")
        print(f"   - Use .simple.csv with numpy: np.loadtxt('file.simple.csv', delimiter=',')")


if __name__ == '__main__':
    main()
