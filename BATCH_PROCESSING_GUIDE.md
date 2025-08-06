# Batch Processing and Pilot Mode Guide

This guide shows how to use the enhanced batch processing and pilot testing features for connectivity matrix extraction.

## New Features

### 1. Batch Processing
Process all fiber files in a directory automatically.

### 2. Pilot Mode  
Test your settings on a random subset of files before running the full batch.

### 3. Smart File Discovery
Automatically finds `.fib.gz` and `.fz` files, including in subdirectories.

## Usage Examples

### Single File Processing (Original)
```bash
python extract_connectivity_matrices.py subject001.fib.gz ./output_dir
```

### Batch Processing - All Files
```bash
# Process all .fib.gz files in a directory
python extract_connectivity_matrices.py --batch ./input_folder ./output_folder

# Process with custom pattern (include .fz files)
python extract_connectivity_matrices.py --batch --pattern "*.fz" ./input_folder ./output_folder

# Process with specific atlases and metrics
python extract_connectivity_matrices.py \
  --batch \
  --atlases "AAL2,HCP-MMP,Schaefer200" \
  --values "count,ncount2,fa" \
  ./input_folder ./output_folder
```

### Pilot Mode - Test Before Full Processing
```bash
# Test on 1 random file (default)
python extract_connectivity_matrices.py \
  --batch --pilot \
  ./input_folder ./output_folder

# Test on 3 random files
python extract_connectivity_matrices.py \
  --batch --pilot --pilot-count 3 \
  ./input_folder ./output_folder

# Pilot test with specific settings
python extract_connectivity_matrices.py \
  --batch --pilot \
  --atlases "AAL2,HCP-MMP" \
  --values "count,fa" \
  --tracks 50000 \
  ./input_folder ./output_folder
```

### Configuration File with Batch Settings
Create `batch_config.json`:
```json
{
  "comment": "Batch processing configuration",
  
  "input_settings": {
    "input_folder": "/path/to/your/fib/files",
    "file_pattern": "*.fib.gz",
    "recursive_search": true,
    "pilot_mode": true,
    "pilot_count": 2
  },
  
  "atlases": ["AAL2", "HCP-MMP", "Schaefer200"],
  "connectivity_values": ["count", "ncount2", "fa"],
  "track_count": 100000,
  "thread_count": 8,
  
  "tracking_parameters": {
    "method": 0,
    "turning_angle": 45,
    "step_size": 0.5,
    "smoothing": 0.8
  }
}
```

Then run:
```bash
python extract_connectivity_matrices.py --config batch_config.json ./input_folder ./output_folder
```

## Workflow Recommendations

### 1. Start with Pilot Testing
```bash
# Test your settings on 1-2 files first
python extract_connectivity_matrices.py \
  --batch --pilot --pilot-count 2 \
  --atlases "AAL2,HCP-MMP" \
  --values "count,fa,ncount2" \
  ./data/subjects ./test_output
```

### 2. Review Pilot Results
Check the pilot output to ensure:
- ✅ All atlases processed successfully  
- ✅ Output structure looks correct
- ✅ File sizes are reasonable
- ✅ No error messages

### 3. Run Full Batch
```bash
# If pilot looks good, run on all files
python extract_connectivity_matrices.py \
  --batch \
  --atlases "AAL2,HCP-MMP" \
  --values "count,fa,ncount2" \
  ./data/subjects ./full_output
```

## File Structure Examples

### Input Directory Structure
```
./data/subjects/
├── subject001/
│   ├── subject001.fib.gz
│   └── other_files...
├── subject002/
│   ├── subject002.fib.gz
│   └── other_files...
├── subject003.fib.gz
└── batch_processing/
    ├── group1_subject004.fz
    └── group2_subject005.fz
```

### Output Directory Structure (Batch Mode)
```
./output_folder/
├── batch_processing_summary.json
├── tracks_100k_streamline_angle45_fa0.20_subject001/
│   ├── by_atlas/
│   ├── by_metric/
│   ├── combined/
│   └── logs/
├── tracks_100k_streamline_angle45_fa0.20_subject002/
│   ├── by_atlas/
│   ├── by_metric/
│   ├── combined/
│   └── logs/
└── tracks_100k_streamline_angle45_fa0.20_subject003/
    ├── by_atlas/
    ├── by_metric/
    ├── combined/
    └── logs/
```

## Pilot Mode Benefits

### 1. **Save Time**
- Test settings on 1-2 files instead of processing hundreds
- Identify parameter issues early
- Validate atlas availability

### 2. **Save Storage**
- Check output size before full processing
- Ensure you have enough disk space
- Test network locations

### 3. **Debugging**
- Catch file format issues
- Test DSI Studio version compatibility
- Validate tracking parameters

## Batch Processing Summary

After batch processing, you'll get a summary file (`batch_processing_summary.json`):

```json
{
  "processed_files": [
    {
      "file": "/data/subjects/subject001.fib.gz",
      "success": true,
      "output_dir": "tracks_100k_streamline_angle45_fa0.20_subject001",
      "matrices_extracted": 6
    },
    {
      "file": "/data/subjects/subject002.fib.gz", 
      "success": false,
      "error": "Atlas AAL2 not found"
    }
  ],
  "summary": {
    "total": 2,
    "successful": 1,
    "failed": 1,
    "pilot_mode": false,
    "pilot_count": null
  }
}
```

## Integration with Metric Evaluation

Perfect workflow combination:
1. **Extract with pilot**: Test parameters
2. **Extract full batch**: Get all connectivity matrices  
3. **Evaluate metrics**: Use the new metric evaluation tools
4. **Select best metrics**: Choose optimal connectivity measures
5. **Analyze**: Run your analysis pipeline

```bash
# 1. Pilot test
python extract_connectivity_matrices.py --batch --pilot ./subjects ./connectivity_test

# 2. Full extraction  
python extract_connectivity_matrices.py --batch ./subjects ./connectivity_full

# 3. Evaluate which metrics work best
python analysis/connectivity_metric_evaluator.py \
  --connectivity-folder ./connectivity_full/tracks_100k_streamline_angle45_fa0.20_subject001

# 4. Select metrics for your analysis goal
python analysis/metric_selection_workflow.py \
  --connectivity-folder ./connectivity_full/tracks_100k_streamline_angle45_fa0.20_subject001 \
  --analysis-goal longitudinal
```

## Tips and Best Practices

### File Organization
- Keep all subject files in a consistent directory structure
- Use consistent naming conventions
- Consider using symbolic links if files are in different locations

### Resource Management  
- Start with pilot mode to estimate processing time
- Monitor disk space during batch processing
- Use appropriate thread counts based on your system

### Error Handling
- The script continues processing even if some files fail
- Check the batch summary for any failed files
- Failed files can be reprocessed individually

### Parameter Selection
- Use pilot mode to test different parameter combinations
- Start with conservative settings and adjust based on pilot results
- Document your parameter choices for reproducibility
