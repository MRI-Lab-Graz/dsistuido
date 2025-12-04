# Connectometry Analysis Quick Reference

## Setup (Do Once)

1. **Edit `connectometry_config.json`**:
   ```json
   {
     "dsi_studio_cmd": "/path/to/dsi_studio",
     "core_parameters": {
       "source": {"value": "/path/to/study.db.fib.gz"},
       "demo": {"value": "/path/to/demographics.csv"},
       "variable_list": {"value": "0,1,2"},
       "voi": {"value": 1}
     }
   }
   ```

2. **Test DSI Studio**:
   ```bash
   dsi_studio --help
   ```

## Common Commands

### Run All Analyses
```bash
python run_connectometry_batch.py --config connectometry_config.json
```

### Run Specific Batch
```bash
python run_connectometry_batch.py --config connectometry_config.json --batch 0
```

### Custom Analysis
```bash
python run_connectometry_batch.py --config connectometry_config.json \
  --custom '{"index_name":"qa","effect_size":0.3,"permutation":2000}'
```

### Custom Output Directory
```bash
python run_connectometry_batch.py --config connectometry_config.json \
  --output /path/to/results
```

## Key Parameters

| Parameter | What It Does | Typical Values |
|-----------|--------------|----------------|
| `index_name` | Metric to analyze | `qa`, `dti_fa`, `md` |
| `effect_size` | Sensitivity threshold | 0.2-0.4 |
| `permutation` | Statistical robustness | 2000-5000 |
| `voi` | Which variable to study | 0, 1, 2, `longitudinal` |
| `fdr_threshold` | False discovery rate | 0 (off), 0.05 |
| `length_threshold` | Min tract length | 20-40 voxels |

## Parameter Sweeps

Test multiple values by using lists in batch configurations:

```json
{
  "name": "sweep_effect_size",
  "parameters": {
    "index_name": "qa",
    "effect_size": [0.2, 0.3, 0.4],
    "permutation": 2000
  }
}
```

This runs 3 analyses (one per effect size).

## Output Files

Each analysis creates:
- `*.fib.gz` - Statistical maps
- `*.txt` - Text report
- `*.html` - Visual report
- `*pos.trk.gz` - Positive correlations
- `*neg.trk.gz` - Negative correlations

Plus:
- `analysis_summary_*.json` - Summary of all runs
- `connectometry_batch_*.log` - Detailed log

## Demographics File

CSV format with header:
```csv
Subject,Gender,Age,BMI,Group
sub001,1,25,22.5,1
sub002,0,30,24.1,1
```

Then set:
```json
{
  "variable_list": "1,2,3",  // Gender,Age,BMI
  "voi": 2                   // Study Age (0-indexed from variable_list)
}
```

## Monitoring Progress

```bash
# Watch log file
tail -f connectometry_results/connectometry_batch_*.log

# Check summary
cat connectometry_results/analysis_summary_*.json
```

## Troubleshooting

**DSI Studio not found**:
```bash
which dsi_studio
# Update dsi_studio_cmd in config
```

**File not found**:
```bash
# Use absolute paths
ls -l /path/to/study.db.fib.gz
```

**Too slow**:
- Reduce `permutation` to 1000 for testing
- Increase `thread_count`
- Set `no_tractogram: 1`

## Full Documentation

See `CONNECTOMETRY_GUIDE.md` for complete details.
