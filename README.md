# DSI Studio Connectivity Matrix Extraction - Python Script

This Python script extracts connectivity matrices for multiple atlases using DSI Studio's command-line interface with intelligent output organization based on your analysis settings.

## Files

- `extract_connectivity_matrices.py` - Advanced Python script with organized output structure
- `connectivity_config.json` - Configuration file template  
- `README_connectivity.md` - This documentation
- `DSI_Studio_Parameters_Analysis.md` - Detailed parameter analysis and impact guide

## Prerequisites

1. **DSI Studio** must be installed and available in your system PATH
   - Download from: https://dsi-studio.labsolver.org/download.html
   - Ensure `dsi_studio` command works from terminal

2. **For Python script**: Python 3.6+ with required packages:
   ```bash
   pip install pandas numpy
   ```

## Supported Atlases

The scripts support the following atlases (modify as needed):
- AAL, AAL2, AAL3
- Brodmann
- HCP-MMP
- AICHA
- Talairach
- FreeSurferDKT
- Schaefer100, Schaefer200, Schaefer400
- Gordon333
- Power264

## Connectivity Metrics (Complete List from Source Code)

The following connectivity metrics can be extracted:
- `count` - Number of streamlines
- `ncount` - Normalized count  
- `ncount2` - Alternative normalized count
- `mean_length` - Average streamline length
- `qa` - Quantitative anisotropy (for GQI)
- `fa` - Fractional anisotropy (for DTI)
- `dti_fa` - DTI-specific FA
- `md` - Mean diffusivity
- `ad` - Axial diffusivity
- `rd` - Radial diffusivity  
- `iso` - Isotropic component
- `rdi` - Restricted diffusion imaging
- `ndi` - Neurite density index
- `dti_md`, `dti_ad`, `dti_rd` - DTI-specific versions

## Organized Output Structure

The Python script now organizes output intelligently based on your tracking settings:

```
output_folder/
├── tracks_100k_streamline_angle45_fa0.20/
│   ├── by_atlas/
│   │   ├── AAL/
│   │   ├── AAL2/
│   │   ├── HCP-MMP/
│   │   └── ...
│   ├── by_metric/
│   │   ├── count/
│   │   ├── fa/
│   │   ├── ncount2/
│   │   └── ...
│   ├── combined/
│   │   ├── all_atlases_count.csv
│   │   ├── all_atlases_fa.csv
│   │   └── ...
│   ├── logs/
│   │   └── extraction.log
│   └── analysis/
│       ├── README.md (analysis documentation)
│       └── analysis_example.py (example analysis script)
```

This organization makes it easy to:
- Compare the same metric across different atlases
- Analyze multiple metrics for the same atlas
- Have analysis-ready combined datasets
- Track extraction progress through detailed logs

## Usage

### Python Script (Recommended)

#### Python API usage:
```python
from extract_connectivity_matrices import ConnectivityExtractor

# Initialize extractor
extractor = ConnectivityExtractor()

# Extract with default settings
extractor.extract_all('subject1.fib.gz', './output_folder')

# Extract with custom settings  
extractor.extract_all(
    fib_file='subject1.fib.gz',
    output_folder='./output_folder',
    track_count=100000,
    threshold=('fa', 0.2),
    connectivity_value=['ncount2', 'fa', 'count']
)
```

### Configuration File with Batch Settings

Create a `batch_config.json`:
```json
{
  "input_settings": {
    "input_folder": "/path/to/your/fib/files",
    "file_pattern": "*.fib.gz",
    "pilot_mode": true,
    "pilot_count": 2
  },
  "atlases": ["AAL2", "HCP-MMP", "Schaefer200"],
  "connectivity_values": ["count", "ncount2", "fa"],
  "track_count": 100000
}
```

Then run:
```bash
python extract_connectivity_matrices.py --config batch_config.json ./input_folder ./output_folder
```

## Batch Processing Benefits

- **🚀 Efficient**: Process hundreds of subjects automatically
- **🧪 Pilot Mode**: Test settings on random files before full processing  
- **📁 Smart Discovery**: Finds all `.fib.gz` and `.fz` files recursively
- **📊 Progress Tracking**: Detailed logging and batch summaries
- **⚡ Robust**: Continues processing even if individual files fail
- **🔄 Reproducible**: Consistent parameters across all subjects

### Configuration File

You can also use the provided `connectivity_config.json` template:

```bash
python extract_connectivity_matrices.py --config connectivity_config.json --fib-file subject1.fib.gz
```

## Key Features

- **Comprehensive Atlas Support**: All 22 atlases discovered from DSI Studio source code
- **Complete Metrics**: All connectivity values including the elusive `ncount2`
- **Organized Output**: Intelligent directory structure based on tracking parameters
- **Progress Tracking**: Detailed logging and progress reports
- **Analysis Ready**: Generates combined datasets and example analysis scripts
- **Robust Error Handling**: Continues processing even if individual extractions fail
```bash
python extract_connectivity_matrices.py --fib-file subject1.fib.gz --output-folder ./connectivity_results
```

## Usage

### Python Script (Recommended)

#### Single File Processing
```bash
python extract_connectivity_matrices.py --fib-file subject1.fib.gz --output-folder ./connectivity_results
```

#### Batch Processing - All Files in Directory
```bash
# Process all .fib.gz files in a directory
python extract_connectivity_matrices.py --batch ./input_folder ./output_folder

# Process with custom file pattern
python extract_connectivity_matrices.py --batch --pattern "*.fz" ./input_folder ./output_folder
```

#### Pilot Mode - Test Before Full Processing  
```bash
# Test on 1 random file to validate settings
python extract_connectivity_matrices.py --batch --pilot ./input_folder ./test_output

# Test on 3 random files
python extract_connectivity_matrices.py --batch --pilot --pilot-count 3 ./input_folder ./test_output
```

#### Advanced usage with custom parameters:
```bash
python extract_connectivity_matrices.py 
  --batch --pilot 
  --fib-file subject1.fib.gz 
  --output-folder ./connectivity_results 
  --track-count 100000 
  --threshold "fa,0.2" 
  --step-size 0.5 
  --turning-angle 45 
  --smoothing 0.8 
  --connectivity-value ncount2,fa,count 
  --atlases AAL,HCP-MMP
```

#### Python API usage:
```python
python extract_connectivity_matrices.py subject001.fib.gz ./output_directory
```

#### Custom atlases and parameters:
```python
python extract_connectivity_matrices.py \
  --atlases "AAL2,HCP-MMP" \
  --values "count,fa" \
  --tracks 75000 \
  --threads 6 \
  subject001.fib.gz ./output_directory
```

#### Batch processing multiple files:
```python
python extract_connectivity_matrices.py \
  --batch \
  --pattern "*.fib.gz" \
  ./input_directory ./output_directory
```

#### Using configuration file:
```python
python extract_connectivity_matrices.py \
  --config connectivity_config.json \
  subject001.fib.gz ./output_directory
```

#### Help:
```python
python extract_connectivity_matrices.py --help
```

## Configuration File

You can customize the `connectivity_config.json` file to set default parameters:

```json
{
  "atlases": ["AAL2", "HCP-MMP", "Brodmann"],
  "connectivity_values": ["count", "fa", "length"],
  "track_count": 100000,
  "thread_count": 8,
  "tracking_parameters": {
    "step_size": 0,
    "turning_angle": 0,
    "smoothing": 0
  }
}
```

## Output Structure

The scripts create organized output directories:

```
output_directory/
└── subject001_20240806_143022/
    ├── extraction_summary.json      # Python script only
    ├── processing_results.csv       # Python script only
    ├── AAL2/
    │   ├── subject001_AAL2.tt.gz
    │   ├── subject001_AAL2.connectivity.csv
    │   └── subject001_AAL2.stat.txt
    ├── HCP-MMP/
    │   ├── subject001_HCP-MMP.tt.gz
    │   ├── subject001_HCP-MMP.connectivity.csv
    │   └── subject001_HCP-MMP.stat.txt
    └── Brodmann/
        ├── subject001_Brodmann.tt.gz
        ├── subject001_Brodmann.connectivity.csv
        └── subject001_Brodmann.stat.txt
```

## Key Features

### Bash Script
- ✅ Simple and fast
- ✅ Minimal dependencies
- ✅ Good for single file processing
- ✅ Command-line argument parsing
- ✅ Error checking

### Python Script
- ✅ Advanced error handling and logging
- ✅ Batch processing capabilities
- ✅ JSON configuration support
- ✅ Detailed processing reports
- ✅ Progress tracking and timing
- ✅ CSV output for results summary
- ✅ Timeout handling for long processes

## Examples

### Process single file with specific atlases:
```bash
# Bash
./extract_connectivity_matrices.sh \
  -a "AAL2,HCP-MMP" \
  -v "count,fa" \
  -t 50000 \
  subject001.fib.gz ./results

# Python
python extract_connectivity_matrices.py \
  --atlases "AAL2,HCP-MMP" \
  --values "count,fa" \
  --tracks 50000 \
  subject001.fib.gz ./results
```

### Batch process all files in directory:
```python
python extract_connectivity_matrices.py \
  --batch \
  --pattern "*.fib.gz" \
  --atlases "AAL2,Brodmann,HCP-MMP" \
  ./fiber_files ./connectivity_results
```

### High-performance processing:
```bash
./extract_connectivity_matrices.sh \
  --tracks 200000 \
  --threads 16 \
  --atlases "AAL2,HCP-MMP,Schaefer400" \
  high_res_subject.fib.gz ./high_res_output
```

## Troubleshooting

1. **DSI Studio not found**: Ensure DSI Studio is installed and `dsi_studio` command is in PATH
2. **Permission denied**: Make sure bash script is executable: `chmod +x extract_connectivity_matrices.sh`
3. **Out of memory**: Reduce `--tracks` parameter or increase system RAM
4. **Slow processing**: Increase `--threads` parameter (up to CPU core count)
5. **Atlas not found**: Check DSI Studio documentation for supported atlas names

## Performance Tips

1. **Multi-threading**: Use `--threads` parameter to match your CPU cores
2. **Track count**: Balance between accuracy and speed (50K-200K tracks typical)
3. **Batch processing**: Use Python script for processing multiple subjects
4. **Disk space**: Ensure sufficient space for output files (can be large)
5. **Memory**: Monitor system memory usage, especially with high track counts

## References

- DSI Studio: https://dsi-studio.labsolver.org/
- CLI Documentation: https://dsi-studio.labsolver.org/doc/cli_t3.html
- Yeh, F.C., et al. "Deterministic diffusion fiber tracking improved by quantitative anisotropy." PLoS ONE 8.11 (2013): e80713.

## Support

For DSI Studio specific issues:
- Forum: https://groups.google.com/g/dsi-studio
- Documentation: https://dsi-studio.labsolver.org/manual

For script issues:
- Check the log files generated by the Python script
- Verify your DSI Studio installation
- Ensure input files are valid .fib.gz or .fz files
