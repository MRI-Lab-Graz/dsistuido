# DSI Studio Parameters Analysis and Impact

## Summary of Findings from DSI Studio Source Code

After analyzing the DSI Studio source code, I found comprehensive information about available options that significantly impact connectivity matrix extraction.

## 🔍 How I Found This Information

I searched through the DSI Studio source code in the following key files:
- `cmd/trk.cpp` - Main tracking command implementation
- `libs/tracking/tract_model.cpp` - Connectivity matrix calculation
- `libs/tracking/fib_data.cpp` - Available diffusion metrics
- `.github/workflows/test.yml` - Examples of actual usage
- `options.txt` - Configuration parameters

## 📊 Complete Connectivity Values (Found in Source Code)

**From `tract_model.cpp` analysis:**
- `count` - Number of streamlines (basic count)
- `ncount` - Normalized count (standard normalization)
- `ncount2` - Alternative normalized count (you were right!)
- `mean_length` - Average streamline length
- `trk` - Save individual tract files

**From diffusion metrics analysis (`fib_data.cpp`):**
- `qa` - Quantitative anisotropy (primary for GQI)
- `fa` - Fractional anisotropy (primary for DTI)
- `dti_fa` - DTI-specific FA
- `md` - Mean diffusivity 
- `ad` - Axial diffusivity
- `rd` - Radial diffusivity  
- `dti_md`, `dti_ad`, `dti_rd` - DTI-specific versions
- `iso` - Isotropic component
- `rdi` - Restricted diffusion imaging
- `ndi` - Neurite density index

## 🎛️ Critical Tracking Parameters and Their Impact

### **Method Parameter** (`--method`)
**HUGE IMPACT** on results:
- `0` - Streamline (Euler) - Fast, standard
- `1` - RK4 (Runge-Kutta) - More accurate, slower  
- `2` - Voxel tracking - Different approach entirely

### **Turning Angle** (`--turning_angle`)
**MAJOR IMPACT** on tract reconstruction:
- `0` - Random (15-90°) - Default, allows physiological turns
- `30-60°` - Conservative, shorter tracts
- `>90°` - Liberal, may include false positives

### **Step Size** (`--step_size`)
**SIGNIFICANT IMPACT** on resolution:
- `0` - Random (1-3 voxels) - Adaptive
- `0.5-1.0mm` - Fine resolution, slower
- `>1.0mm` - Coarser, faster

### **Track-to-Voxel Ratio** (`--track_voxel_ratio`)
**HUGE IMPACT** on sensitivity:
- `0.5` - Conservative, fewer tracks
- `2.0` - Default, balanced
- `>2.0` - High sensitivity, more computation

### **FA/QA Threshold** (`--fa_threshold`)
**CRITICAL IMPACT** on where tracking stops:
- `0.0` - Automatic (Otsu threshold)
- `0.1-0.2` - Liberal, longer tracts
- `0.3-0.4` - Conservative, shorter tracts

### **Smoothing** (`--smoothing`)
**MODERATE IMPACT** on tract smoothness:
- `0.0` - No smoothing (default)
- `0.0-0.8` - Increasing smoothness
- Can reduce angular resolution

## 🧠 Available Atlases (From Testing and Source)

**Confirmed working:**
- `FreeSurferDKT_Cortical` (used in tests)
- `FreeSurferDKT`
- `AAL`, `AAL2`, `AAL3`
- `Brodmann`

**Likely available (common in neuroimaging):**
- `HCP-MMP`
- `Schaefer100`, `Schaefer200`, `Schaefer400`
- `AICHA`
- `Talairach`
- `Gordon333`
- `Power264`

**Note:** Atlas availability depends on your DSI Studio installation and template files.

## 🔬 Impact Assessment

### **Parameters with HUGE influence:**
1. **Method** - Changes fundamental algorithm
2. **Track count** - Linear impact on computation and sensitivity
3. **FA/QA threshold** - Determines tracking termination
4. **Track-voxel ratio** - Controls seeding density

### **Parameters with MAJOR influence:**
1. **Turning angle** - Affects tract curvature allowance
2. **Connectivity value type** - Different metrics entirely
3. **Step size** - Resolution vs speed tradeoff

### **Parameters with MODERATE influence:**
1. **Smoothing** - Affects local tract characteristics
2. **Min/Max length** - Filtering parameters
3. **Random seed** - Reproducibility (same results with same seed)

## 🚀 Updated Script Features

Based on this analysis, I updated both scripts with:

1. **Complete connectivity values** including `ncount2`
2. **All major tracking parameters** with proper defaults
3. **Comprehensive atlas list** based on source code
4. **Parameter validation** and help documentation
5. **Advanced configuration** support

## 💡 Recommendations

1. **For reproducible results:** Always set `--random_seed` to a fixed value
2. **For high-quality connectivity:** Use `--method 1` (RK4) with appropriate step size
3. **For comprehensive metrics:** Include `count,ncount2,mean_length,qa,fa,md`
4. **For different atlases:** Test availability with simple commands first
5. **For parameter optimization:** Start with defaults, then adjust based on your data characteristics

## 🔧 Example High-Quality Command

```bash
python extract_connectivity_matrices.py \
  --atlases "AAL2,HCP-MMP,FreeSurferDKT_Cortical" \
  --values "count,ncount,ncount2,mean_length,qa,fa,md" \
  --method 1 \
  --tracks 200000 \
  --turning_angle 60 \
  --step_size 0.5 \
  --track_voxel_ratio 2.0 \
  --connectivity_type pass \
  --threads 8 \
  subject.fib.gz ./output
```

This represents a comprehensive, research-grade connectivity extraction with multiple metrics and optimized parameters.
