# DSI Studio Connectometry Batch Analysis Guide

## Overview

This guide explains how to perform multiple connectometry analyses using DSI Studio with parameter sweeps and batch processing.

## Files

- **`connectometry_config.json`**: Main configuration file with parameter definitions and ranges
- **`run_connectometry_batch.py`**: Python script for executing batch analyses
- **`CONNECTOMETRY_GUIDE.md`**: This documentation file

## Quick Start

### 1. Prepare Your Data

Before running connectometry analysis, you need:

1. **Connectometry Database** (`*.db.fib.gz`): Created from multiple FIB files
2. **Demographics File** (CSV or TXT): Contains subject variables for regression

### 2. Edit Configuration

Edit `connectometry_config.json` and update these required fields:

```json
{
  "dsi_studio_cmd": "/path/to/dsi_studio",
  "core_parameters": {
    "source": {
      "value": "/path/to/your_study.db.fib.gz"
    },
    "demo": {
      "value": "/path/to/demographics.csv"
    },
    "variable_list": {
      "value": "0,1,2"
    },
    "voi": {
      "value": 1
    }
  }
}
```

### 3. Run Analysis

```bash
# Run all batch configurations
python run_connectometry_batch.py --config connectometry_config.json

# Run with custom output directory
python run_connectometry_batch.py --config connectometry_config.json --output ./my_results

# Run only a specific batch (e.g., batch 0)
python run_connectometry_batch.py --config connectometry_config.json --batch 0
```

## Configuration File Structure

### Core Parameters (Required)

| Parameter | Description | Example |
|-----------|-------------|---------|
| `source` | Path to db.fib.gz connectometry database | `study.db.fib.gz` |
| `index_name` | Diffusion metric to analyze | `qa`, `dti_fa`, `md` |
| `demo` | Demographics file with subject variables | `demographics.csv` |
| `variable_list` | Study variables for regression | `0,1,2` |
| `voi` | Variable of interest | `1` or `longitudinal` |

### Threshold Parameters

| Parameter | Description | Default | Range |
|-----------|-------------|---------|-------|
| `t_threshold` | T-statistic threshold | 2.5 | 1.5-4.0 |
| `effect_size` | Effect size threshold | 0.3 | 0.1-0.5 |
| `length_threshold` | Minimum tracking length (voxels) | 20 | 10-50 |
| `fdr_threshold` | FDR control (0=disabled) | 0 | 0-0.1 |

**Note**: Use either `t_threshold` OR `effect_size`, not both.

### Analysis Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `permutation` | Number of permutations | 2000 |
| `thread_count` | Number of CPU threads | 8 |
| `exclude_cb` | Exclude cerebellum (1/0) | 1 |
| `normalize_iso` | Normalize by ISO (1/0) | 1 |
| `tip_iteration` | Pruning iterations | 16 |
| `region_pruning` | Remove fragments (1/0) | 1 |
| `no_tractogram` | Skip 3D tractograms (1/0) | 1 |

### Optional Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `output` | Output file prefix | `study_voi1` |
| `select` | Subject selection criteria | `Gender=1,Age>20` |
| `seed` | Seed region file | `seed.nii.gz` |
| `roi` | Region of interest | `roi.nii.gz` |
| `roa` | Region of avoidance | `roa.nii.gz` |

## Batch Configurations

Batch configurations allow you to define parameter sweeps. Parameters can be:
- **Single values**: Run once with that value
- **Lists**: Run once for each value (creates multiple analyses)

### Example 1: Effect Size Sweep

```json
{
  "name": "effect_size_sweep",
  "description": "Test multiple effect sizes",
  "parameters": {
    "index_name": "qa",
    "effect_size": [0.2, 0.3, 0.4, 0.5],
    "length_threshold": 20,
    "permutation": 2000
  }
}
```

This creates 4 analyses, one for each effect size.

### Example 2: Multi-parameter Grid

```json
{
  "name": "grid_search",
  "description": "Test combinations of parameters",
  "parameters": {
    "index_name": ["qa", "dti_fa"],
    "effect_size": [0.2, 0.3],
    "length_threshold": [20, 30]
  }
}
```

This creates 2 × 2 × 2 = 8 analyses (all combinations).

### Example 3: Multimodal Comparison

```json
{
  "name": "multimodal",
  "description": "Compare different metrics",
  "parameters": {
    "index_name": ["qa", "dti_fa", "md", "rd", "rdi"],
    "effect_size": 0.3,
    "permutation": 2000
  }
}
```

This creates 5 analyses, one per metric.

## Demographics File Format

The demographics file should be CSV or tab-delimited text:

### CSV Example
```csv
Subject,Gender,Age,BMI,Group
sub001,1,25,22.5,1
sub002,0,30,24.1,1
sub003,1,28,21.8,2
```

### Usage
```json
{
  "variable_list": "1,2,3",  // Gender, Age, BMI
  "voi": 2  // Age is the variable of interest (0-indexed)
}
```

## Subject Selection

Use the `select` parameter to filter subjects:

```json
{
  "select": "Gender=1,Age>20,Age<50,Group/3"
}
```

- `=`: Equal to
- `/`: Not equal to
- `>`: Greater than
- `<`: Less than

## Output Files

Each analysis generates:

- **`*.fib.gz`**: Statistical parametric mapping results
- **`*.txt`**: Text report with findings
- **`*.html`**: HTML visualization report
- **`*pos.txt`**: Positive correlation results
- **`*neg.txt`**: Negative correlation results
- **`*pos.trk.gz`**: Positive correlation tractography
- **`*neg.trk.gz`**: Negative correlation tractography

Plus batch script generates:
- **`analysis_summary_*.json`**: Summary of all analyses
- **`connectometry_batch_*.log`**: Detailed execution log

## Examples

### Example 1: Basic Single Analysis

```bash
python run_connectometry_batch.py --config connectometry_config.json \
  --custom '{"index_name":"qa","effect_size":0.3,"permutation":2000}'
```

### Example 2: Run Specific Batch

```bash
# Run only the second batch configuration (index 1)
python run_connectometry_batch.py --config connectometry_config.json --batch 1
```

### Example 3: Custom Output Directory

```bash
python run_connectometry_batch.py --config connectometry_config.json \
  --output /data/results/study_2025
```

## Advanced Usage

### Parameter Value Types

In the config file, each parameter has:

```json
{
  "parameter_name": {
    "description": "What it does",
    "type": "float|int|string",
    "default": 0.3,
    "range": [0.1, 0.5],
    "step": 0.1,
    "values": [0.2, 0.3, 0.4],
    "options": ["choice1", "choice2"]
  }
}
```

- `range` + `step`: Define continuous range
- `values`: Explicit list of values to test
- `options`: Valid categorical choices

### Longitudinal Analysis

For longitudinal studies, use `voi: "longitudinal"`:

```json
{
  "core_parameters": {
    "voi": {
      "value": "longitudinal"
    }
  }
}
```

### Using Region Files

Limit analysis to specific brain regions:

```json
{
  "optional_parameters": {
    "seed": "/path/to/seed_region.nii.gz",
    "roi": "/path/to/roi.nii.gz",
    "roa": "/path/to/excluded_region.nii.gz"
  }
}
```

## Troubleshooting

### Common Issues

1. **"DSI Studio not found"**
   - Update `dsi_studio_cmd` in config to full path
   - Check DSI Studio is installed: `dsi_studio --help`

2. **"Source file not found"**
   - Use absolute paths in config
   - Verify `.db.fib.gz` file exists

3. **"Demographics file error"**
   - Check CSV format (comma or tab-delimited)
   - Ensure `variable_list` indices are valid
   - Verify `voi` index exists in variable_list

4. **Analysis timeout**
   - Reduce `permutation` count
   - Increase timeout in script (default: 2 hours)
   - Use more threads with `thread_count`

5. **Memory issues**
   - Enable `no_tractogram: 1` to save memory
   - Reduce `permutation` count
   - Process fewer subjects with `select`

### Checking Progress

Monitor the log file:

```bash
tail -f connectometry_results/connectometry_batch_*.log
```

### Validating Results

Check the summary file:

```bash
cat connectometry_results/analysis_summary_*.json | jq '.successful'
```

## Performance Tips

1. **Parallel Processing**: Set `thread_count` to CPU count
2. **Skip Tractograms**: Set `no_tractogram: 1` for faster processing
3. **Test First**: Run with low `permutation` (e.g., 500) to test setup
4. **Batch Scheduling**: Split large batches across multiple runs

## References

- [DSI Studio Connectometry Documentation](https://dsi-studio.labsolver.org/doc/cli_cnt.html)
- [DSI Studio CLI Guide](https://dsi-studio.labsolver.org/doc/cli_data.html)
- [Connectometry Analysis Tutorial](https://dsi-studio.labsolver.org/doc/gui_cx.html)

## Citation

If you use this script in your research, please cite:

> Yeh FC, et al. "Differential tractography as a track-based biomarker for neuronal injury." 
> NeuroImage, 2019.

## Support

For issues specific to:
- **This script**: Check GitHub issues or contact your lab
- **DSI Studio**: [DSI Studio Forum](https://groups.google.com/g/dsi-studio)
