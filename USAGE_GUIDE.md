# DSI Studio Connectivity Matrix Extraction - Usage Guide

## 🏗️ **Configuration vs Command-Line Arguments**

The system now properly separates **processing settings** (in JSON config) from **execution parameters** (command-line arguments).

### 📄 **Configuration File (`connectivity_config.json`)**
**What goes in the config:** DSI Studio processing parameters that you want to reuse across different runs.

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

### ⚡ **Command-Line Arguments**
**What goes on command line:** Execution parameters that change between runs.

- **Input/Output paths:** `input.fz output_dir/`
- **Processing mode:** `--batch`, `--pilot`
- **File discovery:** `--pattern "*.fz"`
- **Runtime options:** `--pilot-count 3`

## 🚀 **Quick Start**

### 1. **Validate Your Setup First**
```bash
# Basic validation
python validate_setup.py --config connectivity_config.json

# Test with specific input
python validate_setup.py --config connectivity_config.json --test-input /path/to/data/
```

### 2. **Single File Processing**
```bash
python extract_connectivity_matrices.py \
    --config connectivity_config.json \
    subject_001.fz \
    results/
```

### 3. **Batch Processing (Recommended Workflow)**
```bash
# Step 1: Pilot test (test 1-2 files first)
python extract_connectivity_matrices.py \
    --config connectivity_config.json \
    --pilot --pilot-count 1 \
    --batch /data/fiber_files/ \
    results/

# Step 2: Full batch (after pilot succeeds)
python extract_connectivity_matrices.py \
    --config connectivity_config.json \
    --batch /data/fiber_files/ \
    results/
```

## 📋 **Complete Command Reference**

### **Configuration File Options**
```bash
--config connectivity_config.json    # Use custom config file
```

### **Input/Output**
```bash
input.fz output_dir/                 # Single file mode
--batch input_dir/ output_dir/       # Batch mode
--pattern "*.fz"                     # File pattern (default: *.fib.gz)
```

### **Processing Control**
```bash
--pilot                              # Pilot mode (test subset)
--pilot-count 2                      # Number of files in pilot (default: 1)
```

### **Override Config Settings** (Optional)
```bash
--atlases "AAL3,Brainnetome"         # Override config atlases
--values "count,fa,qa"               # Override connectivity values
--tracks 50000                       # Override track count
--threads 16                         # Override thread count
--method 1                           # Override tracking method
--fa_threshold 0.15                  # Override FA threshold
```

## 🔍 **File Format Support**

The system automatically detects and processes both:
- **`.fib.gz`** files (traditional DSI Studio format)
- **`.fz`** files (newer compressed format)

No need to specify file type - the script finds both automatically!

## 📊 **Validation Checklist**

The validation system checks:

✅ **DSI Studio Installation**
- Executable exists and is accessible
- Can run DSI Studio commands
- Version detection (if available)

✅ **Configuration Parameters**
- Atlases are specified
- Connectivity values are valid
- Track count is reasonable
- Thread count is appropriate
- Tracking parameters are in valid ranges

✅ **Input Discovery** (when testing with `--test-input`)
- Path exists and is accessible
- Fiber files are found (.fz and .fib.gz)
- File count and types reported

## 🧪 **Recommended Workflow**

### **For New Datasets:**
1. **Setup validation:**
   ```bash
   python validate_setup.py --config connectivity_config.json --test-input /your/data/
   ```

2. **Pilot test:**
   ```bash
   python extract_connectivity_matrices.py --config connectivity_config.json --pilot --batch /your/data/ results/
   ```

3. **Full processing:**
   ```bash
   python extract_connectivity_matrices.py --config connectivity_config.json --batch /your/data/ results/
   ```

### **For Single Files:**
1. **Validate:**
   ```bash
   python validate_setup.py --config connectivity_config.json --test-input subject.fz
   ```

2. **Process:**
   ```bash
   python extract_connectivity_matrices.py --config connectivity_config.json subject.fz results/
   ```

## 🎯 **Configuration Customization**

### **Example: High-Resolution Processing**
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

### **Example: Quick Testing**
```json
{
  "atlases": ["AAL3"],
  "connectivity_values": ["count", "fa"],
  "track_count": 10000,
  "thread_count": 4
}
```

## 🔧 **Troubleshooting**

### **Common Issues:**

**DSI Studio Not Found:**
```bash
# Check your path in config
"dsi_studio_cmd": "/full/path/to/dsi_studio"
```

**No Files Found:**
```bash
# Check file extensions and pattern
--pattern "*.fz"  # or "*.fib.gz"
```

**Configuration Errors:**
```bash
# Validate first
python validate_setup.py --config your_config.json
```

### **Getting Help:**
```bash
python extract_connectivity_matrices.py --help
python validate_setup.py --help
```

## 📈 **Performance Tips**

- Use **pilot mode** first to test settings
- Adjust `thread_count` based on your system (4-16 typical)
- Lower `track_count` for faster testing (10k-50k)
- Higher `track_count` for final results (100k-1M)

This separation makes your configuration reusable across different datasets while keeping execution flexible! 🚀
