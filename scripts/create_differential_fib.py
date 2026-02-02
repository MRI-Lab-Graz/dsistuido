#!/usr/bin/env python3
import scipy.io
import gzip
import numpy as np
import argparse
import os
import sys
import traceback

def create_diff_fib(baseline_path, followup_path, output_path, method=4):
    """Create a differential FIB file for connectometry analysis.
    
    Args:
        baseline_path: Path to baseline FIB file
        followup_path: Path to followup FIB file
        output_path: Path for output differential FIB file
        method: Reconstruction method (4=GQI native space, 7=QSDR standard space)
    
    For GQI (method 4, native space): Shape mismatches are expected due to different
    brain masks per session. These are logged as warnings and the operation is skipped
    (returns False, not an error).
    
    For QSDR (method 7, standard space): Shape mismatches should NOT occur. If they do,
    it indicates a preprocessing issue and is treated as an error.
    
    WARNING: This approach may not work correctly with DSI Studio's binary FIB format.
    DSI Studio FIB (.fz) files are in a custom binary format, not standard MATLAB .mat files.
    This script attempts to treat them as MATLAB files, which may fail silently.
    ""
    try:
        print(f"Loading baseline: {baseline_path}")
        with gzip.open(baseline_path, 'rb') as f:
            baseline_mat = scipy.io.loadmat(f)
        
        print(f"Loading followup: {followup_path}")
        with gzip.open(followup_path, 'rb') as f:
            followup_mat = scipy.io.loadmat(f)
        
        # Create a copy of baseline as the template for the output
        diff_mat = baseline_mat.copy()
        
        # We want to replace fa0, fa1, fa2 (the QA/Peak values)
        # with the difference (Followup - Baseline)
        # Note: Connectometry uses these fa* indices as the metric.
        
        metrics_to_diff = ['fa0', 'fa1', 'fa2', 'fa3', 'fa4', 'fa5', 'qa', 
                          'gfa', 'dti_fa', 'md', 'ad', 'rd', 'iso', 'rdi']
        
        found_any = False
        common_metrics = []
        shape_mismatch_detected = False
        
        for metric in metrics_to_diff:
            if metric in baseline_mat and metric in followup_mat:
                print(f"Computing difference for {metric}...")
                # Ensure they have the same shape
                if baseline_mat[metric].shape == followup_mat[metric].shape:
                    diff_mat[metric] = followup_mat[metric].astype(np.float32) - baseline_mat[metric].astype(np.float32)
                    common_metrics.append(metric)
                    found_any = True
                else:
                    shape_mismatch_detected = True
                    print(f"Warning: Shape mismatch for {metric}, skipping.")
                    print(f"  Baseline shape: {baseline_mat[metric].shape}, Followup shape: {followup_mat[metric].shape}")
        
        # Handle shape mismatches based on reconstruction method
        if not found_any and shape_mismatch_detected:
            if method == 4:  # GQI in native space
                print("\nINFO: Shape mismatch is expected for GQI (native space) due to different brain masks per session.")
                print("This is normal and not an error. Skipping differential FIB for this pair.")
                return False
            else:  # QSDR or other methods in standard space
                print("ERROR: No common metrics found to subtract!")
                print(f"Available in baseline: {list(baseline_mat.keys())}")
            print(f"Available in followup: {list(followup_mat.keys())}")
            return False
        
        print(f"Successfully subtracted {len(common_metrics)} metrics: {common_metrics}")

        # Keep orientations (index0, index1, ...) from baseline
        # These are already in diff_mat because we copied it.
        
        # Update report if possible
        if 'report' in diff_mat:
            diff_mat['report'] = np.array([f"Differential FIB (Followup - Baseline). Baseline: {os.path.basename(baseline_path)}, Followup: {os.path.basename(followup_path)}"])

        print(f"Saving differential FIB to {output_path}")
        with gzip.open(output_path, 'wb') as f:
            scipy.io.savemat(f, diff_mat, format='4', appendmat=False)
        
        print("Done!")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to create differential FIB file!")
        print(f"Baseline: {baseline_path}")
        print(f"Followup: {followup_path}")
        print(f"Output: {output_path}")
        print(f"\nException: {str(e)}")
        traceback.print_exc()
        print("\nNOTE: DSI Studio FIB files are in a custom binary format.")
        print("The scipy.io.loadmat() approach may not work with .fz files.")
        print("This requires proper DSI Studio SDK or file format documentation.")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Create a differential FIB file for Connectometry.')
    parser.add_argument('--baseline', required=True, help='Baseline FIB file')
    parser.add_argument('--followup', required=True, help='Followup FIB file')
    parser.add_argument('--output', required=True, help='Output differential FIB file')
    parser.add_argument('--method', type=int, default=4, help='Reconstruction method (4=GQI native space, 7=QSDR standard space)')
    
    args = parser.parse_args()
    success = create_diff_fib(args.baseline, args.followup, args.output, method=args.method)
    
    if not success:
        print("\n❌ Differential FIB creation failed!")
        print("This output file should NOT be used for downstream analysis.")
        sys.exit(1)
    else:
        print("\n✅ Differential FIB created successfully!")
        sys.exit(0)
