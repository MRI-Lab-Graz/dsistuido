# Track Count Recommendations

## 🎯 **TRACK COUNT GUIDELINES**:

### **Quick Test/Pilot** (100k - 1M):
```json
"track_count": 100000   // 2-3 min/file - for testing parameters
"track_count": 1000000  // 3-5 min/file - basic quality
```

### **Standard Research** (1M - 5M):
```json  
"track_count": 1000000  // Good for most studies
"track_count": 5000000  // High-quality research standard ⭐
```

### **High-End Research** (5M+):
```json
"track_count": 10000000 // Publication-quality, 15-30 min/file
"track_count": 20000000 // Ultra-high quality, 30-60 min/file
```

## ⚖️ **TRADE-OFFS**:

| Track Count | Time/File | Quality | Use Case |
|-------------|-----------|---------|----------|
| 100K | 2-3 min | Basic | Parameter testing |
| 1M | 3-5 min | Good | Pilot studies |
| 5M ⭐ | 8-15 min | High | Most research |
| 10M | 15-30 min | Excellent | Publication |
| 20M+ | 30-60 min | Ultra | Special studies |

## 💡 **RECOMMENDATIONS**:

### **For Your 52 Files**:
- **100K tracks**: ~2 hours total (current)
- **5M tracks**: ~8-12 hours total (recommended)
- **10M tracks**: ~15-25 hours total (high-end)

### **Strategy**:
1. **Test with 1M tracks first** (pilot with 1-2 files)
2. **Scale up to 5M** for actual analysis
3. **Use 10M+** only if you need publication-quality results

## 🚀 **UPDATED COMMAND**:
```bash
# Test with 1M tracks first
python extract_connectivity_matrices.py \
  --config research_config.json \
  -t 1000000 \
  --pilot --pilot-count 1 \
  /data/local/122_AF17/derivatives/fz/ \
  /data/local/122_AF17/derivatives/
```
