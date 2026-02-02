# Installation & Setup Guide

## Quick Setup with UV

UV is a lightning-fast Python package installer (10-100x faster than pip).

```bash
# From the dsistudio root directory
cd installation/
bash setup_env.sh

# Activate the environment
cd ..
source venv/bin/activate
```

## What UV Does

- **⚡ 10-100x faster** than pip for package installation
- **🔒 Reliable** dependency resolution
- **🎯 Deterministic** installs (reproducible across machines)
- **📦 Modern** package management for Python

## Installation Steps

### 1. Run the Setup Script (Automatic UV Installation)
```bash
cd installation/
bash setup_env.sh
```

The script will:
- Check for UV and install it if needed
- Create virtual environment with UV
- Install all dependencies using UV

### 2. Activate Virtual Environment
```bash
cd ..
source venv/bin/activate
```

### 3. Verify Installation
```bash
python scripts/validate_setup.py
```

## Manual UV Installation (Optional)

If you want to install UV separately before running setup:

```bash
# Using pip (fastest way to bootstrap)
pip install uv

# Or using curl (official installer)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

See [UV documentation](https://github.com/astral-sh/uv) for more details.

## Requirements

### System Requirements
- Python 3.6 or later
- 8+ GB RAM recommended
- DSI Studio installed and accessible

### Python Dependencies
- pandas
- numpy
- scipy
- scikit-image

See `requirements.txt` for the complete list.

## UV Advantages Over Pip

| Feature | UV | Pip |
|---------|-----|-----|
| Speed | ⚡⚡⚡ 10-100x faster | Standard |
| Caching | Smart caching | Basic caching |
| Lock files | Supported | Via pip-tools |
| Resolution | Deterministic | Can vary |
| Installation | Instant | Slower |

## Troubleshooting Installation

### UV Installation Fails
```bash
# Try manual installation via pip
pip install uv

# Then run setup again
bash setup_env.sh
```

### Python Not Found
- Ensure Python 3.6+ is installed: `python3 --version`
- Update shebang in scripts if needed

### Permission Denied
- Make scripts executable: `chmod +x setup_env.sh`
- Run with bash explicitly: `bash setup_env.sh`

### Dependency Installation Fails with UV
```bash
# Clear UV cache and retry
rm -rf ~/.cache/uv
bash setup_env.sh
```

## Using UV After Installation

Once installed, you can use UV for other tasks:

```bash
# Install additional packages
uv pip install package_name

# Upgrade packages
uv pip install --upgrade package_name

# Show installed packages
uv pip list
```

## After Installation

1. **Activate environment:**
   ```bash
   source venv/bin/activate
   ```

2. **Validate setup:**
   ```bash
   python scripts/validate_setup.py --config configs/example_config.json
   ```

3. **Review main README:**
   ```bash
   cat ../README.md
   ```

4. **Start using the tools:**
   ```bash
   python scripts/dsi_studio_pipeline.py --help
   ```

See the main [README.md](../README.md) for usage instructions.
