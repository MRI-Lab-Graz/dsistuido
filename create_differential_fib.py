#!/usr/bin/env python3
import scipy.io
import gzip
import numpy as np
import argparse
import os

def create_diff_fib(baseline_path, followup_path, output_path):
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
    for metric in metrics_to_diff:
        if metric in baseline_mat and metric in followup_mat:
            print(f"Computing difference for {metric}...")
            # Ensure they have the same shape
            if baseline_mat[metric].shape == followup_mat[metric].shape:
                diff_mat[metric] = followup_mat[metric].astype(np.float32) - baseline_mat[metric].astype(np.float32)
                found_any = True
            else:
                print(f"Warning: Shape mismatch for {metric}, skipping.")
    
    if not found_any:
        print("Error: No common metrics found to subtract!")
        return False

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Create a differential FIB file for Connectometry.')
    parser.add_argument('--baseline', required=True, help='Baseline FIB file')
    parser.add_argument('--followup', required=True, help='Followup FIB file')
    parser.add_argument('--output', required=True, help='Output differential FIB file')
    
    args = parser.parse_args()
    create_diff_fib(args.baseline, args.followup, args.output)
