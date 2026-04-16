# S1-GRiTS Wheel Build Issue Analysis

## The Problem
The wheel was built with the **incorrect name** `s1grits-1.0.0-py3-none-any.whl` when it should be platform-specific like `s1grits-1.0.0-cp312-cp312-win_amd64.whl` because it contains compiled Cython extensions (.pyd files).

### Wheel Metadata Evidence
```
Wheel-Version: 1.0
Generator: setuptools (82.0.1)
Root-Is-Purelib: true                    ← THIS IS THE PROBLEM
Tag: py3-none-any                        ← Platform-agnostic tag
```

The `Root-Is-Purelib: true` flag indicates setuptools treated this as a **pure Python wheel**, which is incorrect.

### What's Actually in the Wheel
The wheel contains **three Cython-compiled extensions** (.pyd files):
- `s1grits/asf_array_processing.cp312-win_amd64.pyd` (132 KB, PE32+ DLL for x86-64)
- `s1grits/asf_output_writing.cp312-win_amd64.pyd` (574 KB, PE32+ DLL for x86-64)
- `s1grits/mgrs_burst_data.cp312-win_amd64.pyd` (78 KB, PE32+ DLL for x86-64)

## Root Cause Analysis

### 1. **Configuration Issue in pyproject.toml**
The `pyproject.toml` file uses **`tool.setuptools.package-data`** instead of properly declaring extension modules:

```toml
[tool.setuptools.package-data]
"s1grits" = [
    "data/*.parquet",
    "py.typed",
    "*.py",
    "asf_array_processing.cp*-win_amd64.pyd",    ← TREATING .pyd AS DATA FILES
    "asf_output_writing.cp*-win_amd64.pyd",       ← NOT AS COMPILED EXTENSIONS
    "mgrs_burst_data.cp*-win_amd64.pyd",
]
```

**Why this is wrong:**
- `package-data` is for non-Python resources (docs, data files, images)
- Compiled extensions are treated as **pure data**, not as platform-specific binaries
- setuptools doesn't know these are platform-dependent, so it marks the wheel as "py3-none-any"

### 2. **No Extension Modules Declaration**
The `pyproject.toml` has **NO**:
- `ext_modules` configuration
- `Extension()` declarations (Cython or distutils)
- `setup.py` with custom build logic
- Build backend specification for Cython

### 3. **Build Directory Structure**
Pre-built .pyd files exist in two places:
- `src/s1grits/` - source .pyd files (included from build cache)
- `build/lib/s1grits/` - additional .pyd files from previous builds
- `build/bdist.win-amd64/` - binary distribution staging area

**Note:** The .pyd files were already pre-compiled and committed to the repo (or generated earlier), not built during wheel creation.

## Why This Matters

### Current Behavior (WRONG)
```
s1grits-1.0.0-py3-none-any.whl
├── Tag: py3-none-any
├── Root-Is-Purelib: true
└── Contains: .pyd files (Windows x86-64 platform-specific binaries)
```
- pip will install this on ANY platform
- On non-Windows or non-x86-64 systems, the Windows .pyd files will be installed but cannot be loaded
- Python will fail at runtime when trying to `import asf_array_processing` on wrong platform

### Correct Behavior
```
s1grits-1.0.0-cp312-cp312-win_amd64.whl
├── Tag: cp312-cp312-win_amd64
├── Root-Is-Purelib: false (implicitly platform-specific)
└── Contains: .pyd files (will only be installed on compatible platforms)
```
- pip will only install on Python 3.12, Windows, x86-64
- Automatic platform matching prevents installation on incompatible systems

## The Solution

### Option 1: Proper setuptools Extension Declaration (Recommended)
Create or modify `setup.py`:
```python
from setuptools import setup, Extension

setup(
    ext_modules=[
        Extension(
            "s1grits.asf_array_processing",
            sources=["src/s1grits/asf_array_processing.pyx"],  # .pyx source
            include_dirs=[...],
        ),
        Extension(
            "s1grits.asf_output_writing",
            sources=["src/s1grits/asf_output_writing.pyx"],
        ),
        Extension(
            "s1grits.mgrs_burst_data",
            sources=["src/s1grits/mgrs_burst_data.pyx"],
        ),
    ]
)
```

### Option 2: Remove package-data .pyd Reference and Use Data Files Directory
Move .pyd files to a subdirectory not included in wheel, rebuild without package-data reference.

### Option 3: Use scikit-build-core or CMake
Modern build backend that properly handles compiled extensions.

## Files Examined

### pyproject.toml (FULL CONTENT)
✓ Confirmed: NO ext_modules or Extension declarations
✓ Confirmed: Uses package-data for .pyd files
✓ Confirmed: setuptools build-backend specified

### dist/s1grits-1.0.0-py3-none-any.whl
✓ Contains 3 .pyd files
✓ Wheel tag: py3-none-any (WRONG for platform-specific binaries)
✓ Root-Is-Purelib: true (WRONG)

### src/s1grits/ (Package Directory)
✓ Contains pre-compiled .pyd files:
  - asf_array_processing.cp312-win_amd64.pyd
  - asf_output_writing.cp312-win_amd64.pyd
  - mgrs_burst_data.cp312-win_amd64.pyd
✓ No .pyx source files found (only pre-compiled .pyd)
✓ asf_io.py contains: `from s1grits.asf_array_processing import despeckle_2d`

### build/ Directory
✓ Contains bdist.win-amd64/ and lib/ subdirectories
✓ lib/s1grits/ has different set of .pyd files:
  - asf_io.cp312-win_amd64.pyd (788 KB)
  - asf_tiles.cp312-win_amd64.pyd (114 KB)
  - rtc_s1_io.cp312-win_amd64.pyd (90 KB)
  - mgrs_burst_data.cp312-win_amd64.pyd (78 KB)

## Summary
The wheel's incorrect tagging is caused by setuptools treating pre-compiled .pyd files as **data files** (via `package-data`) rather than **compiled extensions**. This causes setuptools to mark the wheel as platform-agnostic, which will lead to runtime failures on non-Windows or non-x86-64 platforms.

**Required fix:** Declare extension modules properly in `pyproject.toml` or `setup.py` so setuptools marks the wheel with platform-specific tags like `cp312-cp312-win_amd64`.
