# Installation & Setup Guide

## Quick Setup with UV

UV is a lightning-fast Python package installer (10-100x faster than pip).

```bash
# From the dsistudio root directory
bash installation/install.sh

# Activate the environment
source venv/bin/activate
```

`installation/install.sh` installs the latest compatible DSI Studio build for the current OS, CPU architecture, and CUDA/GPU availability, then updates all repo config files with the resolved executable path.

If you only need the Python environment, run `bash installation/setup_env.sh` instead.

## What UV Does

- **⚡ 10-100x faster** than pip for package installation
- **🔒 Reliable** dependency resolution
- **🎯 Deterministic** installs (reproducible across machines)
- **📦 Modern** package management for Python

## Installation Steps

### 1. Run the Full Installer (DSI Studio + Python Environment)
```bash
bash installation/install.sh
```

The script will:
- Detect the current OS, architecture, and CUDA/GPU availability
- Download the latest matching DSI Studio release asset from GitHub
- Install DSI Studio into a managed directory and update `configs/*.json`
- Check for UV and install it if needed
- Create virtual environment with UV
- Install all dependencies using UV

### 2. Python-Only Setup (Optional)
```bash
bash installation/setup_env.sh
```

Use this variant only when DSI Studio is already installed and `dsi_studio_cmd` is already configured.

### 3. Activate Virtual Environment
```bash
source venv/bin/activate
```

### 4. Verify Installation
```bash
python scripts/connectivity/validate_setup.py --config configs/example_config.json
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
- Internet access for automatic DSI Studio downloads when using `installation/install.sh`
- DSI Studio installed and accessible if using `installation/setup_env.sh` only

### Python Dependencies
- pandas
- numpy
- scipy
- Flask
- waitress
- Pillow
- reportlab

See `../requirements.txt` for the complete list.

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
- Make scripts executable: `chmod +x installation/install.sh installation/setup_env.sh`
- Run with bash explicitly: `bash installation/install.sh`

### Dependency Installation Fails with UV
```bash
# Clear UV cache and retry
rm -rf ~/.cache/uv
bash installation/setup_env.sh
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
   python scripts/connectivity/validate_setup.py --config configs/example_config.json
   ```

3. **Review main README:**
   ```bash
   cat ../README.md
   ```

4. **Start using the tools:**
   ```bash
   python scripts/pipeline/dsi_studio_pipeline.py --help
   ```

## Optional: DataLad qsiprep Source (`--qsiprep_datalad`)

If your qsiprep data lives in a remote DataLad dataset (e.g. this lab's
`MRI-Lab_Repository` on `it035016`), two extra prerequisites apply:

### 1. A current git-annex build

This machine's distro-packaged git-annex may be too old to transfer file
content from a remote running a newer git-annex (the remote gets silently
marked "annex-ignore" and every `datalad get` fails with
`not available ... annex-ignore set`). Fix once with:

```bash
bash installation/install_git_annex.sh
```

This installs a current standalone git-annex via `datalad-installer` (run
through `uv tool run`, so nothing is added permanently to this project's
Python environment) and wires it into `venv/bin/activate` so it's preferred
automatically once you `source venv/bin/activate`.

### 2. An SSH config alias if your login itself contains an `@`

DataLad's own SSH wrapper (`datalad sshrun`) expects a login target in the
form `[user@]hostname` - a *single* `@`. Accounts with an email-style login
(e.g. `karl.koschutnig@uni-graz.at`) produce a "double-@" URL
(`ssh://karl.koschutnig@uni-graz.at@it035016.uni-graz.at/...`) that DataLad's
SSH wrapper cannot parse correctly, even though plain `git`/`ssh` handle it
fine. Symptom: `git annex info origin` shows a mangled `repository location`
(e.g. `./ssh://...git`) and `get` fails with "Remote origin cannot currently
be accessed" despite the remote being fully reachable.

Fix: add an SSH config alias with no `@` in it, e.g. in `~/.ssh/config`:

```
Host mri-it035016
    HostName it035016.uni-graz.at
    User karl.koschutnig@uni-graz.at
```

```bash
chmod 600 ~/.ssh/config
ssh mri-it035016 'echo it works'   # verify
```

Then use `ssh://mri-it035016/...` (instead of the double-`@` form) as the
`--qsiprep_datalad_source` / "Clone qsiprep from" value.

See the main [README.md](../README.md) for usage instructions.
