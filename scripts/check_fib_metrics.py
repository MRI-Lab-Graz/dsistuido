#!/usr/bin/env python3
"""
Check FIB files for number of metrics/keys to identify files with fewer metrics.
This helps identify which files need to be regenerated due to incompatible structures.
"""

import scipy.io
import gzip
import sys
from pathlib import Path
from collections import defaultdict
import argparse

def check_fib_file(fib_path):
    """Check a FIB file and return its keys and ODF count."""
    try:
        with gzip.open(fib_path, 'rb') as f:
            mat = scipy.io.loadmat(f)
        
        keys = list(mat.keys())
        # Filter out internal MATLAB keys
        keys = [k for k in keys if not k.startswith('__')]
        
        # Count ODF indices
        odf_keys = [k for k in keys if k.startswith('odf') and not ('slope' in k or 'inter' in k)]
        
        return {
            'path': fib_path,
            'total_keys': len(keys),
            'odf_count': len(odf_keys),
            'odf_keys': sorted(odf_keys),
            'all_keys': sorted(keys)
        }
    except Exception as e:
        return {
            'path': fib_path,
            'error': str(e)
        }

def main():
    parser = argparse.ArgumentParser(description='Check FIB files for metric count')
    parser.add_argument('fib_dir', help='Directory containing FIB files')
    parser.add_argument('--pattern', default='*.fz', help='File pattern (default: *.fz)')
    parser.add_argument('--show-keys', action='store_true', help='Show all keys for each file')
    parser.add_argument('--method', choices=['gqi', 'qsdr', 'all'], default='all', 
                       help='Filter by reconstruction method')
    args = parser.parse_args()
    
    fib_dir = Path(args.fib_dir)
    if not fib_dir.exists():
        print(f"Error: Directory not found: {fib_dir}")
        sys.exit(1)
    
    # Find all FIB files
    pattern = args.pattern
    fib_files = list(fib_dir.glob(pattern))
    
    # Filter by method if specified
    if args.method != 'all':
        fib_files = [f for f in fib_files if f'.odf.{args.method}.' in f.name]
    
    if not fib_files:
        print(f"No FIB files found matching pattern '{pattern}' in {fib_dir}")
        sys.exit(1)
    
    print(f"Checking {len(fib_files)} FIB files...\n")
    
    # Group by key count and ODF count
    by_structure = defaultdict(list)
    errors = []
    
    for fib_file in sorted(fib_files):
        result = check_fib_file(fib_file)
        if 'error' in result:
            errors.append(result)
        else:
            structure_key = (result['total_keys'], result['odf_count'])
            by_structure[structure_key].append(result)
    
    # Print summary
    print("=" * 80)
    print("SUMMARY BY FILE STRUCTURE")
    print("=" * 80)
    
    for (total_keys, odf_count), files in sorted(by_structure.items()):
        print(f"\n📊 Structure: {total_keys} total keys, {odf_count} ODF indices")
        print(f"   Found in {len(files)} files")
        
        # Show example ODF keys
        if files:
            print(f"   ODF keys: {', '.join(files[0]['odf_keys'])}")
        
        # Identify GQI vs QSDR
        gqi_files = [f for f in files if '.odf.gqi.' in str(f['path'])]
        qsdr_files = [f for f in files if '.odf.qsdr.' in str(f['path'])]
        
        if gqi_files:
            print(f"   - GQI files: {len(gqi_files)}")
        if qsdr_files:
            print(f"   - QSDR files: {len(qsdr_files)}")
        
        if args.show_keys and files:
            print(f"\n   All keys: {', '.join(files[0]['all_keys'][:20])}...")
        
        # Show a few example files
        print(f"\n   Example files:")
        for f in files[:3]:
            print(f"      {f['path'].name}")
        if len(files) > 3:
            print(f"      ... and {len(files) - 3} more")
    
    # Identify problematic files
    if len(by_structure) > 1:
        print("\n" + "=" * 80)
        print("⚠️  INCOMPATIBLE STRUCTURES DETECTED")
        print("=" * 80)
        
        structures = sorted(by_structure.keys(), key=lambda x: (x[0], x[1]), reverse=True)
        max_structure = structures[0]
        
        print(f"\n✅ Reference structure (most complete): {max_structure[0]} keys, {max_structure[1]} ODF indices")
        print(f"   ({len(by_structure[max_structure])} files)")
        
        for structure in structures[1:]:
            files = by_structure[structure]
            print(f"\n❌ Incomplete structure: {structure[0]} keys, {structure[1]} ODF indices")
            print(f"   ({len(files)} files need regeneration)")
            
            # Categorize by method
            gqi_files = [f for f in files if '.odf.gqi.' in str(f['path'])]
            qsdr_files = [f for f in files if '.odf.qsdr.' in str(f['path'])]
            
            if gqi_files:
                print(f"   - GQI files to regenerate: {len(gqi_files)}")
            if qsdr_files:
                print(f"   - QSDR files to regenerate: {len(qsdr_files)}")
            
            # Create list files for easy regeneration
            if gqi_files:
                list_file = Path('regenerate_gqi_files.txt')
                with open(list_file, 'w') as f:
                    for file_info in gqi_files:
                        f.write(f"{file_info['path']}\n")
                print(f"   📝 GQI files listed in: {list_file}")
            
            if qsdr_files:
                list_file = Path('regenerate_qsdr_files.txt')
                with open(list_file, 'w') as f:
                    for file_info in qsdr_files:
                        f.write(f"{file_info['path']}\n")
                print(f"   📝 QSDR files listed in: {list_file}")
    else:
        print("\n✅ All files have the same structure - no incompatibilities detected!")
    
    # Print errors if any
    if errors:
        print("\n" + "=" * 80)
        print("ERRORS")
        print("=" * 80)
        for err in errors:
            print(f"❌ {err['path'].name}: {err['error']}")
    
    print()

if __name__ == '__main__':
    main()
