# DSI Studio Pipeline - Configuration Guide

## Pipeline Command-Line Flags

### `--skip_existing` and `--force`

These flags control how the pipeline handles existing output files:

- **`--skip_existing`**: Skip processing if output files (SRC, FIB, differential FIB) already exist
  - Use this to resume interrupted pipeline runs
  - Speeds up processing by skipping completed subjects
  
- **`--force`**: Force overwrite existing files even when `--skip_existing` is set
  - Use this when you need to regenerate files (e.g., after fixing corrupted data)
  - Overrides the skip behavior for all output types
  
**Example usage:**
```bash
# Normal run - always process all files
python dsi_studio_pipeline.py --qsiprep_dir /data/qsiprep --output_dir /data/output

# Resume run - skip existing files
python dsi_studio_pipeline.py --qsiprep_dir /data/qsiprep --output_dir /data/output --skip_existing

# Regenerate specific files - force overwrite even with skip_existing
python dsi_studio_pipeline.py --qsiprep_dir /data/qsiprep --output_dir /data/output --skip_existing --force
```

---

## How Configuration Works

The connectivity extraction uses a **deep merge** strategy:

1. **DEFAULT_CONFIG** (in `extract_connectivity_matrices.py` lines 40-76) provides all base parameters
2. **Your config file** (`graph_analysis_config.json`) overrides only what you specify
3. Unspecified parameters automatically use defaults

### Config Merge Example

```python
# DEFAULT_CONFIG has:
{
  "track_count": 100000,
  "atlases": ["AAL", "AAL2", ...],
  "connectivity_values": ["count", "qa", "fa", ...],
  "tracking_parameters": {
    "min_length": 0,
    "max_length": 0,
    ...
  }
}

# Your graph_analysis_config.json has:
{
  "track_count": 1000000,
  "tracking_parameters": {
    "min_length": 30,
    "max_length": 300
  }
}

# RESULT after merge:
{
  "track_count": 1000000,           # ← FROM YOUR CONFIG
  "atlases": ["AAL", "AAL2", ...],  # ← FROM DEFAULT (not specified in your config)
  "connectivity_values": [...],     # ← FROM DEFAULT (not specified in your config)
  "tracking_parameters": {
    "min_length": 30,               # ← FROM YOUR CONFIG
    "max_length": 300,              # ← FROM YOUR CONFIG
    "method": 0,                    # ← FROM DEFAULT (not overridden)
    "otsu_threshold": 0.6,          # ← FROM DEFAULT (not overridden)
    ...
  }
}
```

---

## Complete DEFAULT_CONFIG Reference

Location: `extract_connectivity_matrices.py` lines 40-76

### Atlases (14 parcellations)
```python
'atlases': [
    'AAL',                      # Automated Anatomical Labeling
    'AAL2',                     # AAL version 2
    'AAL3',                     # AAL version 3
    'Brodmann',                 # Brodmann areas
    'HCP-MMP',                  # Human Connectome Project Multi-Modal Parcellation
    'AICHA',                    # Atlas of Intrinsic Connectivity of Homotopic Areas
    'Talairach',                # Talairach atlas
    'FreeSurferDKT',            # FreeSurfer Desikan-Killiany-Tourville
    'FreeSurferDKT_Cortical',   # FreeSurfer DKT cortical only
    'Schaefer100',              # Schaefer 100 parcels
    'Schaefer200',              # Schaefer 200 parcels
    'Schaefer400',              # Schaefer 400 parcels
    'Gordon333',                # Gordon 333 parcels
    'Power264'                  # Power 264 ROIs
]
```

**How to override:**
```json
{
  "atlases": ["AAL3", "HCP-MMP", "Schaefer200"]
}
```

---

### Connectivity Values (17 metrics)
```python
'connectivity_values': [
    'count',        # Track count between regions
    'ncount',       # Normalized count
    'ncount2',      # Normalized count variant 2
    'mean_length',  # Mean fiber length
    'qa',           # Quantitative Anisotropy
    'fa',           # Fractional Anisotropy
    'dti_fa',       # DTI Fractional Anisotropy
    'md',           # Mean Diffusivity
    'ad',           # Axial Diffusivity
    'rd',           # Radial Diffusivity
    'iso',          # Isotropic component
    'rdi',          # Restricted Diffusion Index
    'ndi',          # Neurite Density Index
    'dti_ad',       # DTI Axial Diffusivity
    'dti_rd',       # DTI Radial Diffusivity
    'dti_md',       # DTI Mean Diffusivity
    'trk'           # Track file output
]
```

**How to override:**
```json
{
  "connectivity_values": ["count", "qa", "fa", "md"]
}
```

---

### Basic Parameters
```python
'track_count': 100000,           # Number of tracks to generate
'thread_count': 8,               # Number of CPU threads
'dsi_studio_cmd': 'dsi_studio'   # DSI Studio executable path
```

**How to override:**
```json
{
  "track_count": 1000000,
  "thread_count": 16,
  "dsi_studio_cmd": "/path/to/dsi_studio"
}
```

---

### Tracking Parameters
```python
'tracking_parameters': {
    'method': 0,               # 0=streamline(Euler), 1=RK4, 2=voxel
    'otsu_threshold': 0.6,     # Otsu threshold for tracking
    'fa_threshold': 0.0,       # FA threshold (0=automatic)
    'turning_angle': 0.0,      # Max turning angle (0=random 15-90°)
    'step_size': 0.0,          # Step size in mm (0=random 1-3 voxels)
    'smoothing': 0.0,          # Smoothing (0-1)
    'min_length': 0,           # Min fiber length (0=auto)
    'max_length': 0,           # Max fiber length (0=auto)
    'track_voxel_ratio': 2.0,  # Seeds per voxel
    'check_ending': 0,         # Drop tracks not in ROI (0=off)
    'random_seed': 0,          # Random seed (0=random)
    'dt_threshold': 0.2        # Differential tracking threshold
}
```

**How to override (partial):**
```json
{
  "tracking_parameters": {
    "min_length": 30,
    "max_length": 300,
    "fa_threshold": 0.15
  }
}
```
*Note: Unspecified parameters remain at defaults*

---

### Connectivity Options
```python
'connectivity_options': {
    'connectivity_type': 'pass',           # 'pass' or 'end'
    'connectivity_threshold': 0.001,       # Connectivity threshold
    'connectivity_output': 'matrix,connectogram,measure'  # Output types
}
```

**Types explained:**
- **`pass`**: Count tracks that pass through regions
- **`end`**: Count tracks that end in regions (more conservative)

**How to override:**
```json
{
  "connectivity_options": {
    "connectivity_type": "end",
    "connectivity_threshold": 0.001
  }
}
```

---

## Your Current Configuration

**File:** `graph_analysis_config.json`

```json
{
  "track_count": 1000000,
  "tracking_parameters": {
    "min_length": 30,
    "max_length": 300
  },
  "connectivity_options": {
    "connectivity_type": "end",
    "connectivity_threshold": 0.001
  }
}
```

### What This Means:

**Overridden from defaults:**
- ✅ `track_count`: 1,000,000 (instead of 100,000)
- ✅ `tracking_parameters.min_length`: 30 mm (instead of 0/auto)
- ✅ `tracking_parameters.max_length`: 300 mm (instead of 0/auto)
- ✅ `connectivity_options.connectivity_type`: "end" (instead of "pass")

**Using defaults (not specified in your config):**
- ⚙️ `atlases`: All 14 atlases (AAL, AAL2, AAL3, Brodmann, HCP-MMP, etc.)
- ⚙️ `connectivity_values`: All 17 metrics (count, qa, fa, md, ad, rd, etc.)
- ⚙️ `thread_count`: 8
- ⚙️ `tracking_parameters.method`: 0 (streamline/Euler)
- ⚙️ `tracking_parameters.fa_threshold`: 0.0 (automatic)
- ⚙️ `tracking_parameters.turning_angle`: 0.0 (random 15-90°)
- ⚙️ And all other parameters...

---

## Example Configurations

### Minimal Config (Fast Processing)
```json
{
  "atlases": ["AAL3"],
  "connectivity_values": ["count", "qa"],
  "track_count": 100000
}
```

### Conservative Config (Quality over Speed)
```json
{
  "atlases": ["AAL3", "HCP-MMP", "Schaefer200"],
  "connectivity_values": ["count", "qa", "fa", "md"],
  "track_count": 5000000,
  "tracking_parameters": {
    "min_length": 40,
    "max_length": 250,
    "fa_threshold": 0.2,
    "turning_angle": 60
  },
  "connectivity_options": {
    "connectivity_type": "end"
  }
}
```

### Research-Grade Config (Comprehensive)
```json
{
  "atlases": ["AAL3", "HCP-MMP", "Schaefer200", "Schaefer400"],
  "connectivity_values": ["count", "ncount", "qa", "fa", "md", "ad", "rd"],
  "track_count": 10000000,
  "thread_count": 16,
  "tracking_parameters": {
    "min_length": 30,
    "max_length": 300,
    "fa_threshold": 0.15,
    "turning_angle": 70,
    "smoothing": 0.5
  },
  "connectivity_options": {
    "connectivity_type": "end",
    "connectivity_threshold": 0.0001
  }
}
```

---

## Configuration Tips

### 1. **Atlas Selection**
- Start with 1-3 atlases for testing
- Add more after confirming results
- Each atlas multiplies processing time

### 2. **Track Count**
- **100,000**: Fast, good for testing
- **1,000,000**: Standard for research
- **5,000,000+**: High quality, slow

### 3. **Connectivity Values**
- `count`: Essential
- `qa`: Most commonly used metric
- `fa`, `md`: Standard diffusion metrics
- Add others as needed for specific analyses

### 4. **Min/Max Length**
- Too short: Spurious connections
- Too long: May miss valid short connections
- **Recommended:** min=30mm, max=300mm

### 5. **Connectivity Type**
- `pass`: More connections, less conservative
- `end`: Fewer connections, more conservative
- **Recommended for functional connectivity:** `pass`
- **Recommended for structural connectivity:** `end`

---

## How to Check Current Effective Config

Run this to see what configuration is actually being used:

```python
import json
from extract_connectivity_matrices import ConnectivityExtractor

# Load your config
with open('graph_analysis_config.json') as f:
    user_config = json.load(f)

# Create extractor (merges with defaults)
extractor = ConnectivityExtractor(user_config)

# Print effective config
print(json.dumps(extractor.config, indent=2))
```

---

## Common Questions

**Q: Why aren't atlases specified in my config?**  
A: They're taken from DEFAULT_CONFIG. All 14 atlases are used by default.

**Q: How do I use only specific atlases?**  
A: Add `"atlases": ["AAL3", "HCP-MMP"]` to your config.

**Q: Where is the fa parameter defined?**  
A: In DEFAULT_CONFIG's `connectivity_values` list. It's used for all atlases automatically.

**Q: What if I only want qa and count?**  
A: Add `"connectivity_values": ["qa", "count"]` to your config.

**Q: Can I disable CSV conversion?**  
A: Add `"connectivity_options": {"convert_to_csv": false}`

**Q: How do I change the number of threads?**  
A: Add `"thread_count": 16` (or desired number) to your config.

---

## Where Parameters Are Used

```
graph_analysis_config.json
         ↓
   (merged with)
         ↓
DEFAULT_CONFIG (extract_connectivity_matrices.py)
         ↓
   (passed to)
         ↓
DSI Studio command line
         ↓
   (generates)
         ↓
Connectivity matrices (.mat, .txt, .csv)
```

---

## Summary

**Default behavior (no config or minimal config):**
- Uses all 14 atlases
- Extracts all 17 connectivity metrics
- Generates 100,000 tracks per connectivity matrix
- Uses 8 threads
- Outputs matrices, connectograms, and measures

**Your current config:**
- Uses all 14 atlases (default)
- Extracts all 17 connectivity metrics (default)
- Generates **1,000,000** tracks (your override)
- Constrains fibers: 30-300mm length (your override)
- Uses "end" connectivity type (your override)

All parameters not in your config automatically come from DEFAULT_CONFIG!
