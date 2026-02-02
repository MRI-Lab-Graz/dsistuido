# Pipeline Enhancements

## New Features Added

### 1. **Progress Tracking & Statistics Display**
The pipeline now shows real-time progress with a formatted counter:

```
╔══════════════════════════════════════════════════════════════╗
║ 📊 PIPELINE PROGRESS                                         ║
╠══════════════════════════════════════════════════════════════╣
║ Progress: [████████████░░░░░░░░░░░░░░░░] 35.0% (105/300)
║ ✓ Processed: 105  | ✗ Skipped:  45
║ ✓ SRC OK: 105  | ✓ FIB OK: 103
╚══════════════════════════════════════════════════════════════╝
```

**Where you'll see it:**
- Before each file is processed (shows progress from previous iteration)
- At the end of the pipeline run (final summary)

### 2. **Automatic FIB File Validation**
After each FIB reconstruction, the pipeline automatically:

✓ **Checks file existence** - Verifies the file was created
✓ **Validates file size** - Ensures it's not suspiciously small (>1 MB)
✓ **Loads and inspects structure** - Uses scipy to read the FIB file
✓ **Counts metrics** - Verifies expected diffusion metrics are present

**Example output:**
```
✓ FIB validated: sub-1293171_ses-3.odf.gqi.fz (62.45 MB, metrics: 8)
```

Or if there's a problem:
```
✗ FIB validation failed: sub-1293175_ses-1.odf.gqi.fz
  → File is suspiciously small (0.45 MB), may be corrupt
  → No standard metrics found in file
```

### 3. **SRC File Validation**
SRC files are also validated after generation:

```
✓ SRC validated: sub-1293171_ses-3.src.gz.sz (245.3 MB)
```

### 4. **Final Summary Report**
At the end of each run, you get a comprehensive summary:

```
╔══════════════════════════════════════════════════════════════╗
║ ✓ PIPELINE COMPLETE                                          ║
╠══════════════════════════════════════════════════════════════╣
║ Total DWI files found:    411
║ Successfully processed:   285
║ SRC files validated:      285
║ FIB files validated:      283
║ Skipped (existing):        45
║ Skipped (other reasons):   81
╚══════════════════════════════════════════════════════════════╝
```

## How It Works

### FIB Validation Checks

The `validate_fib_file()` function checks:

1. **File Existence**: Does the file exist on disk?
2. **File Size**: Is it >1 MB? (Catches empty/corrupted files)
3. **Structure Integrity**: Can scipy.io.loadmat() load it successfully?
4. **Metrics Presence**: Contains expected diffusion metrics
   - Checks for: `fa0`, `fa1`, `fa2`, `gfa`, `dti_fa`, `md`, `ad`, `rd`
   - Reports how many were found

**Return value (dictionary):**
```python
{
    "valid": True,                    # Overall validity
    "path": "/path/to/file.fib.gz",  # File path
    "size_mb": 62.45,                 # File size in MB
    "exists": True,                   # File exists on disk
    "metrics_found": ["fa0", "fa1", "fa2", "gfa", "dti_fa", "md", "ad", "rd"],
    "errors": []                      # List of error messages if any
}
```

## Statistics Tracked

The pipeline maintains a `self.stats` dictionary with:

- `found` - Total DWI files discovered
- `processed` - Successfully processed and validated
- `src_ok` - SRC files created successfully
- `fib_ok` - FIB files created and validated
- `skipped_existing` - Skipped due to `--skip_existing` flag
- `skipped_missing` - Skipped due to missing resources or age threshold

## Integration with Existing Flags

### `--skip_existing`
- Skips SRC/FIB generation if files already exist
- Validation still runs on skipped files

### `--force`
- Forces regeneration even if `--skip_existing` is set
- Useful when previous runs created incomplete files

### `--pilot`
- Processes only one random subject
- All validation and statistics still apply
- Useful for testing before full batch runs

## Performance Notes

- Validation adds minimal overhead (~100-200ms per FIB file)
- Uses scipy to load files (already a dependency)
- Only validates successfully generated files
- Failed command execution skips validation automatically

## Example Usage

**Run with progress tracking and validation:**
```bash
python dsi_studio_pipeline.py \
  --qsiprep_dir /data/qsiprep \
  --output_dir /data/output \
  --skip_existing \
  --dsi_studio_path /data/dsi-studio/
```

**Monitor progress in real-time:**
```
2026-02-02 08:50:29,755 - INFO - Processing sub-1293171_ses-3... [1/411]
2026-02-02 08:50:30,500 - INFO - ✓ SRC validated: sub-1293171_ses-3.src.gz.sz (245.3 MB)
2026-02-02 08:50:41,000 - INFO - ✓ FIB validated: sub-1293171_ses-3.odf.gqi.fz (62.45 MB, metrics: 8)
2026-02-02 08:50:41,100 - INFO - [████░░░░░░░░░░░░░░░░░░░░░░░░░░] 0.2% (1/411)
2026-02-02 08:50:52,000 - INFO - Processing sub-1293171_ses-2... [2/411]
...
```
