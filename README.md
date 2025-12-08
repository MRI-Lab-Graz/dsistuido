# 🧠 DSI Studio Connectivity Matrix Extraction Tool

Advanced Python tool for extracting connectivity matrices using DSI Studio with comprehensive validation, batch processing, and organized output structure.

## 🚀 Quick Start

### 1. **Environment Setup (Recommended)**
It is highly recommended to use a virtual environment to manage dependencies (`pandas`, `numpy`, `scipy`).

```bash
# Run the setup script
./setup_env.sh

# Activate the environment
source venv/bin/activate
```

### 2. **Validate Setup**
```bash
# Basic validation
python validate_setup.py

# With configuration
python validate_setup.py --config example_config.json --test-input /path/to/data/
```

### 3. **Pilot Test**
```bash
# Test 1-2 files before full batch
python extract_connectivity_matrices.py \
    --config example_config.json \
    --pilot --pilot-count 2 \
    --batch /data/directory/ results/
```

### 3. **Full Processing**
```bash
# Single file
python extract_connectivity_matrices.py --config example_config.json subject.fz results/

# Batch processing
python extract_connectivity_matrices.py --config example_config.json --batch /data/directory/ results/
```

### 4. **Get Help**
```bash
# Detailed help (works without arguments too)
python extract_connectivity_matrices.py --help
python validate_setup.py --help
```

## 📁 Files Overview

### Connectivity Matrix Extraction
- **`extract_connectivity_matrices.py`** - Main processing script with validation
- **`validate_setup.py`** - Setup validation tool
- **`example_config.json`** - Example configuration file
- **`connectivity_config.json`** - Template configuration
- **`BATCH_PROCESSING_GUIDE.md`** - Detailed batch processing guide
- **`DSI_Studio_Parameters_Analysis.md`** - Parameter analysis guide

### Connectometry Analysis (NEW)
- **`run_connectometry_batch.py`** - Batch connectometry analysis script
- **`connectometry_config.json`** - Full connectometry configuration with parameter ranges
- **`connectometry_simple.json`** - Simple example configuration
- **`CONNECTOMETRY_GUIDE.md`** - Complete connectometry analysis guide

## 🔧 Configuration vs Command-Line

### **Configuration File (JSON)** - Reusable Processing Settings
```json
{
  "dsi_studio_cmd": "/path/to/dsi_studio",
  "atlases": ["AAL3", "Brainnetome", "FreeSurferDKT"],
  "connectivity_values": ["count", "fa", "qa"],
  "track_count": 100000,
  "thread_count": 8,
  "tracking_parameters": {
    "method": 0,
    "fa_threshold": 0.0,
    "turning_angle": 45.0
  }
}
```

### **Command-Line Arguments** - Per-Run Execution Parameters
- **Input/Output**: `input.fz output_dir/`
- **Processing Mode**: `--batch`, `--pilot`
- **File Discovery**: `--pattern "*.fz"`
- **Overrides**: `--tracks 50000`, `--atlases "AAL3,Brainnetome"`

## 📊 File Format Support

✅ **Supported Formats** (Auto-detected):
- **`.fz`** files (modern compressed format)
- **`.fib.gz`** files (traditional format)
- Both recursive and non-recursive directory scanning

## 🎯 Validation Features

The tool automatically validates:
- ✅ DSI Studio installation and accessibility
- ✅ Configuration file validity
- ✅ Input path accessibility and file discovery
- ✅ Atlas and metric specifications
- ✅ Parameter ranges and reasonableness
- ✅ File format compatibility

## 📋 Command Reference

### **Essential Commands**

| Command | Purpose | Example |
|---------|---------|---------|
| `--help` | Show detailed help | `python extract_connectivity_matrices.py --help` |
| `--config FILE` | Use JSON configuration | `--config example_config.json` |
| `--batch` | Process directory | `--batch /data/dir/ output/` |
| `--pilot` | Test mode first | `--pilot --pilot-count 2` |
| `--pattern "*.fz"` | File pattern | `--pattern "*.fz"` |
| `--atlases "A,B"` | Override atlases | `--atlases "AAL3,Brainnetome"` |
| `--tracks 50000` | Override track count | `--tracks 50000` |

### **Detailed Examples**

```bash
# Basic single file with custom atlases
python extract_connectivity_matrices.py --config my_config.json \
    --atlases "AAL3,Brainnetome" subject.fz output/

# Batch with specific settings
python extract_connectivity_matrices.py --config my_config.json \
    --batch --pattern "*.fz" --tracks 50000 --threads 16 data_dir/ output/

# High-resolution tracking
python extract_connectivity_matrices.py --config my_config.json \
    --method 1 --fa_threshold 0.15 --turning_angle 35 subject.fz output/
```

## 🧠 Supported Atlases

- **AAL Family**: AAL, AAL2, AAL3
- **Functional**: Brodmann, HCP-MMP, AICHA
- **Structural**: Talairach, FreeSurferDKT, FreeSurferDKT_Cortical
- **Parcellations**: Schaefer100/200/400, Gordon333, Power264
## 📊 Connectivity Metrics

**Complete list of available metrics:**
- **`count`** - Number of streamlines
- **`ncount`** - Normalized count  
- **`ncount2`** - Alternative normalized count
- **`mean_length`** - Average streamline length
- **`qa`** - Quantitative anisotropy (for GQI)
- **`fa`** - Fractional anisotropy (for DTI)
- **`dti_fa`** - DTI-specific FA
- **`md`** - Mean diffusivity
- **`ad`** - Axial diffusivity
- **`rd`** - Radial diffusivity  
- **`iso`** - Isotropic component
- **`rdi`** - Restricted diffusion imaging
- **`ndi`** - Neurite density index
- **`dti_md`, `dti_ad`, `dti_rd`** - DTI-specific versions

## 🗂️ Prerequisites

1. **DSI Studio** installation
   - Download: https://dsi-studio.labsolver.org/download.html
   - Ensure command-line access works
   - Update path in configuration file

2. **Python 3.6+** with packages:
   ```bash
   pip install pandas numpy
   ```

## 📂 Organized Output Structure

The Python script now organizes output intelligently based on your tracking settings:

```
📂 output_folder/
└── subject_20240806_143022/
    └── tracks_100k_streamline/
        ├── 📁 by_atlas/          # Results by brain atlas
        │   ├── AAL3/
        │   ├── Brainnetome/
        │   └── FreeSurferDKT/
        ├── 📁 by_metric/         # Results by connectivity metric
        │   ├── count/
        │   ├── fa/
        │   └── qa/
        ├── 📁 combined/          # All results in one place
        ├── 📁 logs/              # Processing logs & summaries
        │   ├── extraction_summary.json
        │   └── processing_results.csv
        └── 📄 README.md          # Analysis guide & commands
```

**Benefits:**
- ✅ Compare same metric across atlases
- ✅ Analyze multiple metrics for same atlas  
- ✅ Ready for batch loading in analysis scripts
- ✅ Complete processing logs and summaries

## 💡 Recommended Workflow

### **For New Projects:**
1. **📋 Validate Setup**
   ```bash
   python validate_setup.py --config example_config.json --test-input /data/
   ```

2. **🧪 Pilot Test** (1-2 files)
   ```bash
   python extract_connectivity_matrices.py --config example_config.json --pilot --batch /data/ results/
   ```

3. **🚀 Full Processing**
   ```bash
   python extract_connectivity_matrices.py --config example_config.json --batch /data/ results/
   ```

### **For Single Files:**
```bash
python extract_connectivity_matrices.py --config example_config.json subject.fz results/
```

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| **DSI Studio not found** | Update `dsi_studio_cmd` path in config |
| **No files found** | Check file extensions (.fz/.fib.gz) and paths |
| **Configuration errors** | Run validation: `python validate_setup.py` |
| **Processing fails** | Start with pilot mode: `--pilot --pilot-count 1` |

## 🎯 Advanced Usage

### **Configuration Examples**

**High-Resolution Processing:**
```json
{
  "atlases": ["Schaefer400", "HCP-MMP"],
  "track_count": 1000000,
  "thread_count": 16,
  "tracking_parameters": {
    "method": 1,
    "fa_threshold": 0.15,
    "turning_angle": 35.0
  }
}
```

**Quick Testing:**
```json
{
  "atlases": ["AAL3"],
  "connectivity_values": ["count", "fa"],
  "track_count": 10000,
  "thread_count": 4
}
```

### **Performance Optimization**

| Parameter | Faster | Higher Quality |
|-----------|--------|----------------|
| `track_count` | 10,000 | 1,000,000 |
| `method` | 0 (Streamline) | 1 (RK4) |
| `thread_count` | Match CPU cores | Match CPU cores |
| Atlases | Fewer (1-3) | More (5-10) |

**Script Structure:**
- **`ConnectivityExtractor`** - Main processing class with validation
- **`validate_configuration()`** - Pre-flight checks
- **`validate_input_path()`** - Runtime path validation  
- **`find_fib_files()`** - Multi-format file discovery
- **Organized output** - Results sorted by atlas and metric

**Key Features:**
- ✅ **Comprehensive validation** before processing
- ✅ **Multi-format support** (.fz and .fib.gz)
- ✅ **Batch processing** with pilot mode
- ✅ **Progress tracking** and detailed logging
- ✅ **Organized output** structure for analysis
- ✅ **Error handling** - continues despite individual failures
- ✅ **Configuration-driven** - reusable settings

## 📚 Additional Documentation

- **`BATCH_PROCESSING_GUIDE.md`** - Detailed batch processing workflow
- **`DSI_Studio_Parameters_Analysis.md`** - Parameter impact analysis
- **Configuration files** - `example_config.json` and `connectivity_config.json`
- **Generated output** - Each run includes analysis guides and example scripts

## 🤝 Contributing

This tool is designed to be robust and user-friendly. For issues or improvements:
1. Check validation first: `python validate_setup.py`
2. Test with pilot mode: `--pilot --pilot-count 1`  
3. Review the generated logs in `results/logs/`

---

**Happy brain connectivity analysis!** 🧠✨

## Running in Background (Detached)

To run the batch script and detach from the terminal, use:

```bash
nohup python run_connectometry_batch.py --config connectometry_simple.json --workers 4 > batch.log 2>&1 &
```

This will:
- Run the process in the background
- Log all output to `batch.log`
- Allow you to log off safely

You can check progress with:
```bash
tail -f batch.log
```
