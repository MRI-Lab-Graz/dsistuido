# DSI Studio Connectivity Matrix Extraction Tool

Advanced Python tool for extracting connectivity matrices using DSI Studio with comprehensive validation, batch processing, and organized output structure.

## 🚀 Quick Start

### 1. Installation

```bash
cd installation/
bash setup_env.sh
source ../venv/bin/activate
```

The installer uses **UV** for lightning-fast package installation (10-100x faster than pip).

See [Installation Guide](installation/SETUP.md) for detailed setup instructions.

### 2. Validate Setup

```bash
python scripts/validate_setup.py --config configs/example_config.json --test-input /path/to/data/
```

### 3. Pilot Test (1-2 files)

```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --pilot
```

### 4. Full Processing

```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --run_connectivity \
    --connectivity_config configs/graph_analysis_config.json
```

## 📂 Project Structure

```
dsistudio/
├── README.md                          # This file
├── installation/                      # Setup & installation files
│   ├── setup_env.sh                   # Environment setup script
│   ├── install.sh                     # Installation helper
│   └── requirements.txt               # Python dependencies
├── scripts/                           # All executable scripts
│   ├── dsi_studio_pipeline.py         # Main pipeline
│   ├── extract_connectivity_matrices.py
│   ├── run_connectometry_batch.py
│   ├── validate_setup.py
│   ├── webui.py
│   └── *.sh                           # Shell utility scripts
├── configs/                           # Configuration templates
│   ├── example_config.json
│   ├── graph_analysis_config.json
│   ├── connectivity_config.json
│   └── *.json
├── docs/                              # Documentation
│   ├── CONFIGURATION_GUIDE.md         # Detailed config reference
│   ├── PIPELINE_ENHANCEMENTS.md       # Feature overview
│   └── ENHANCEMENTS_SUMMARY.txt
└── code/                              # Shared utilities & libraries
```

## 🔧 Main Commands

### Pipeline Processing

#### Standard Run
```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --dsi_studio_path /path/to/dsi-studio/
```

#### With Connectivity Analysis
```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --run_connectivity \
    --connectivity_config configs/graph_analysis_config.json
```

#### Resume Processing
```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --skip_existing
```

#### Force Regeneration
```bash
python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    --skip_existing --force
```

### Connectivity Matrix Extraction

#### Single File
```bash
python scripts/extract_connectivity_matrices.py \
    --config configs/graph_analysis_config.json \
    subject.fz output_dir/
```

#### Batch Processing
```bash
python scripts/extract_connectivity_matrices.py \
    --config configs/graph_analysis_config.json \
    --batch /data/directory/ output_dir/
```

#### Pilot Mode (1-2 files)
```bash
python scripts/extract_connectivity_matrices.py \
    --config configs/graph_analysis_config.json \
    --batch /data/directory/ output_dir/ \
    --pilot --pilot-count 2
```

### Setup Validation

```bash
# Basic validation
python scripts/validate_setup.py

# With specific config
python scripts/validate_setup.py --config configs/example_config.json
```

## 📋 Command-Line Options Reference

### Pipeline Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--qsiprep_dir` | QSIPrep output directory | `--qsiprep_dir /data/qsiprep` |
| `--output_dir` | Output directory | `--output_dir /data/output` |
| `--dsi_studio_path` | DSI Studio installation | `--dsi_studio_path /path/to/dsi-studio/` |
| `--run_connectivity` | Enable connectivity analysis | `--run_connectivity` |
| `--connectivity_config` | Configuration file | `--connectivity_config configs/graph_analysis_config.json` |
| `--skip_existing` | Skip existing outputs | `--skip_existing` |
| `--force` | Force regeneration | `--force` |
| `--pilot` | Test mode | `--pilot` |

### Extraction Options

| Option | Purpose | Example |
|--------|---------|---------|
| `--config` | Configuration file | `--config configs/graph_analysis_config.json` |
| `--batch` | Batch processing mode | `--batch` |
| `--pilot` | Test 1-2 files | `--pilot` |
| `--pilot-count` | Number of pilot files | `--pilot-count 2` |
| `--pattern` | File pattern | `--pattern "*.fz"` |
| `--tracks` | Override track count | `--tracks 50000` |
| `--atlases` | Override atlases | `--atlases "AAL3,Brainnetome"` |

## 🔧 Configuration Files

### Configuration File Structure

```json
{
  "dsi_studio_cmd": "/path/to/dsi_studio",
  "atlases": ["AAL3", "Brainnetome"],
  "connectivity_values": ["count", "fa", "qa"],
  "track_count": 100000,
  "thread_count": 8,
  "tracking_parameters": {
    "method": 0,
    "fa_threshold": 0.0,
    "turning_angle": 45.0,
    "min_length": 0,
    "max_length": 0
  }
}
```

### Available Atlases

- **AAL Family**: AAL, AAL2, AAL3
- **Functional**: Brodmann, HCP-MMP, AICHA
- **Structural**: Talairach, FreeSurferDKT, FreeSurferDKT_Cortical
- **Parcellations**: Schaefer100/200/400, Gordon333, Power264

### Connectivity Metrics

- **count** - Number of streamlines
- **ncount** - Normalized count
- **mean_length** - Average streamline length
- **qa** - Quantitative anisotropy (GQI)
- **fa** - Fractional anisotropy (DTI)
- **dti_fa**, **dti_md**, **dti_ad**, **dti_rd** - DTI-specific metrics
- **md** - Mean diffusivity
- **ad** - Axial diffusivity
- **rd** - Radial diffusivity

See [Configuration Guide](docs/CONFIGURATION_GUIDE.md) for detailed reference.

## 📂 Output Structure

```
output_folder/
├── subject_20240806_143022/
│   └── tracks_100k_streamline/
│       ├── by_atlas/          # Results organized by atlas
│       │   ├── AAL3/
│       │   ├── Brainnetome/
│       │   └── FreeSurferDKT/
│       ├── by_metric/         # Results organized by metric
│       │   ├── count/
│       │   ├── fa/
│       │   └── qa/
│       ├── combined/          # All results in one place
│       └── logs/              # Processing logs & summaries
│           ├── extraction_summary.json
│           └── processing_results.csv
```

## 🎯 Recommended Workflows

### New Project Workflow

1. **Setup environment**
   ```bash
   cd installation && bash setup_env.sh && cd ..
   source venv/bin/activate
   ```

2. **Validate installation**
   ```bash
   python scripts/validate_setup.py --config configs/example_config.json
   ```

3. **Test with pilot**
   ```bash
   python scripts/dsi_studio_pipeline.py \
       --qsiprep_dir /path/to/qsiprep \
       --output_dir /path/to/output \
       --pilot
   ```

4. **Run full pipeline**
   ```bash
   python scripts/dsi_studio_pipeline.py \
       --qsiprep_dir /path/to/qsiprep \
       --output_dir /path/to/output \
       --run_connectivity \
       --connectivity_config configs/graph_analysis_config.json
   ```

### Batch Processing Workflow

```bash
# Batch extraction with custom config
python scripts/extract_connectivity_matrices.py \
    --config configs/graph_analysis_config.json \
    --batch /data/subjects/ output/ \
    --tracks 100000 --threads 16
```

### Background Execution

```bash
# Run pipeline in background
nohup python scripts/dsi_studio_pipeline.py \
    --qsiprep_dir /path/to/qsiprep \
    --output_dir /path/to/output \
    > pipeline.log 2>&1 &

# Monitor progress
tail -f pipeline.log
```

## 🐛 Troubleshooting

### DSI Studio Not Found
- Ensure DSI Studio is installed
- Verify `--dsi_studio_path` points to the correct installation
- On macOS, often at: `/Applications/dsi_studio.app/Contents/MacOS/dsi_studio`

### FSLDIR Error
```bash
# Add to ~/.zshrc or ~/.bash_profile
export FSLDIR=/usr/local/fsl
. ${FSLDIR}/etc/fslconf/fsl.sh
export PATH=${FSLDIR}/bin:${PATH}
```

### Configuration Errors
```bash
# Validate configuration before running
python scripts/validate_setup.py --config configs/your_config.json
```

### Processing Failures
1. Start with pilot mode to test
2. Check generated logs in output directory
3. Review detailed error messages

## 📚 Detailed Documentation

- **[Configuration Guide](docs/CONFIGURATION_GUIDE.md)** - Complete reference for configuration options
- **[Pipeline Enhancements](docs/PIPELINE_ENHANCEMENTS.md)** - Feature overview and capabilities
- **[Enhanced Summary](docs/ENHANCEMENTS_SUMMARY.txt)** - Additional details

## 📋 File Guide

### Python Scripts (in `scripts/`)

| Script | Purpose |
|--------|---------|
| `dsi_studio_pipeline.py` | Main processing pipeline |
| `extract_connectivity_matrices.py` | Connectivity matrix extraction |
| `run_connectometry_batch.py` | Batch connectometry analysis |
| `validate_setup.py` | Setup validation tool |
| `webui.py` | Web UI for visualization |
| `inspect_fib.py` | FIB file inspection |
| `utils.py` | Shared utilities |

### Configuration Templates (in `configs/`)

| File | Purpose |
|------|---------|
| `example_config.json` | Basic example configuration |
| `graph_analysis_config.json` | Connectivity analysis config |
| `connectivity_config.json` | Extended template |
| `research_config.json` | Research-specific settings |

## 🤝 Getting Help

```bash
# Show help for any script
python scripts/dsi_studio_pipeline.py --help
python scripts/extract_connectivity_matrices.py --help
python scripts/validate_setup.py --help
```

---

**For detailed configuration information, see [docs/CONFIGURATION_GUIDE.md](docs/CONFIGURATION_GUIDE.md)**

**For feature details, see [docs/PIPELINE_ENHANCEMENTS.md](docs/PIPELINE_ENHANCEMENTS.md)**
