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


def convert_single_mat(mat_file_path, verbose=False):
    """Convert a single .mat file to CSV."""
    try:
        # Load .mat file
        mat_data = scipy.io.loadmat(str(mat_file_path))
        
        # Find the connectivity matrix
        connectivity_key = None
        for key in ['connectivity', 'matrix', 'data']:
            if key in mat_data:
                connectivity_key = key
                break
        
        if connectivity_key is None:
            available_keys = [k for k in mat_data.keys() if not k.startswith('__')]
            if available_keys:
                connectivity_key = available_keys[0]
                if verbose:
                    print(f"   Using key '{connectivity_key}' from {available_keys}")
            else:
                return {'success': False, 'error': 'No data found in .mat file'}
        
        connectivity_matrix = mat_data[connectivity_key]
        
        if connectivity_matrix.ndim != 2:
            return {'success': False, 'error': f'Expected 2D matrix, got {connectivity_matrix.ndim}D'}
        
        # Generate output paths
        csv_path = mat_file_path.with_suffix('.csv')
        simple_csv_path = mat_file_path.with_suffix('.simple.csv')
        
        # Save as labeled CSV (with row/column names)
        n_regions = connectivity_matrix.shape[0]
        region_names = [f'region_{i+1:03d}' for i in range(n_regions)]
        df = pd.DataFrame(connectivity_matrix, index=region_names, columns=region_names)
        df.to_csv(csv_path, index=True)
        
        # Save as simple CSV (numbers only, easier for some tools)
        np.savetxt(simple_csv_path, connectivity_matrix, delimiter=',', fmt='%.6f')
        
        return {
            'success': True,
            'csv_path': str(csv_path),
            'simple_csv_path': str(simple_csv_path),
            'shape': connectivity_matrix.shape
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
    
    for mat_file in mat_files:
        if args.verbose:
            print(f"\n📄 Converting: {mat_file.name}")
        
        result = convert_single_mat(mat_file, args.verbose)
        
        if result['success']:
            successful += 1
            if args.verbose:
                print(f"   ✓ Success: {result['shape']} matrix")
                print(f"   📊 Labeled CSV: {Path(result['csv_path']).name}")
                print(f"   📊 Simple CSV: {Path(result['simple_csv_path']).name}")
            else:
                print(f"✓ {mat_file.name} → CSV")
        else:
            failed += 1
            print(f"✗ {mat_file.name}: {result['error']}")
    
    print(f"\n📊 Conversion Summary:")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")
    print(f"   📄 Total CSV files created: {successful * 2}")
    
    if successful > 0:
        print(f"\n💡 Usage Tips:")
        print(f"   - Use .csv files with pandas: pd.read_csv('file.csv', index_col=0)")
        print(f"   - Use .simple.csv with numpy: np.loadtxt('file.simple.csv', delimiter=',')")


if __name__ == '__main__':
    main()
