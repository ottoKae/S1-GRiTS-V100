# S1MGRIDTS Analysis Module

Analysis tools for Sentinel-1 monthly mosaic products.

## Overview

The `s1grits.analysis` subpackage provides tools for analyzing Zarr/COG outputs from the s1grits processing workflow:

- **io**: Load datasets and catalogs
- **timeseries**: Extract and analyze time series
- **plotting**: Create visualization figures
- **statistics**: Statistical analysis (to be implemented)
- **export**: Export to various formats (to be implemented)

## Installation

The analysis module is included in the main `s1grits` package. Install additional dependencies for visualization:

```bash
pip install matplotlib seaborn
```

## Quick Start

### 1. Load a Dataset

```python
from s1grits.analysis import load_zarr_dataset

# Load Zarr dataset for a specific tile and direction
ds = load_zarr_dataset("17MPV", "DESCENDING", output_dir="./output")

# Check dataset info
print(ds)
print(f"Time steps: {len(ds.time)}")
print(f"Variables: {list(ds.data_vars)}")
```

### 2. Extract Time Series

```python
from s1grits.analysis import extract_pixel_timeseries

# Extract time series for a specific pixel
ts = extract_pixel_timeseries(ds, row=1843, col=1831)

# Check results
print(f"Valid observations: {ts['valid_count']}/{ts['total_count']}")
print(f"VV range: {ts['vv_ts'].min():.2f} to {ts['vv_ts'].max():.2f} dB")
print(f"Time range: {ts['dates'][0]} to {ts['dates'][-1]}")
```

### 3. Plot Time Series

```python
from s1grits.analysis import plot_timeseries_figure

# Create a 4-panel figure (VV, VH, Ratio, RVI)
plot_timeseries_figure(
    ts,
    title="17MPV DESCENDING - Pixel (1843, 1831)",
    output_path="./figures/timeseries.png"
)
```

### 4. Compare Orbits

```python
from s1grits.analysis import (
    load_zarr_dataset,
    extract_pixel_timeseries,
    plot_orbit_comparison
)

# Load both orbits
ds_asc = load_zarr_dataset("17MPV", "ASCENDING")
ds_desc = load_zarr_dataset("17MPV", "DESCENDING")

# Extract time series for same pixel
ts_asc = extract_pixel_timeseries(ds_asc, 1843, 1831)
ts_desc = extract_pixel_timeseries(ds_desc, 1843, 1831)

# Plot comparison
plot_orbit_comparison(ts_asc, ts_desc, output_path="./figures/orbit_compare.png")
```

### 5. Geographic Coordinate Query

```python
from s1grits.analysis import lonlat_to_pixel

# Convert lon/lat to pixel coordinates
lon, lat = -122.5, 37.8
row, col = lonlat_to_pixel(lon, lat, ds)

print(f"Lon/Lat ({lon}, {lat}) → Pixel ({row}, {col})")

# Extract time series
ts = extract_pixel_timeseries(ds, row, col)
```

### 6. Region Statistics

```python
from s1grits.analysis import extract_region_timeseries

# Extract mean time series for a 100x100 region
ts_region = extract_region_timeseries(
    ds,
    row_slice=slice(1800, 1900),
    col_slice=slice(1800, 1900),
    aggregation='mean'
)

print(f"Mean VV: {ts_region['vv_ts'].mean():.2f} dB")
```

## Command-Line Tools

The original command-line scripts in `src/analysis/` can now use the analysis module as a library:

### Plot Time Series

```bash
# Using pixel coordinates
python src/analysis/plot_time_series.py \
    --tile 17MPV \
    --direction DESCENDING \
    --pixel 1843 1831 \
    --output timeseries.png

# Using lon/lat coordinates
python src/analysis/plot_time_series.py \
    --tile 17MPV \
    --direction DESCENDING \
    --lonlat -122.5 37.8 \
    --output timeseries.png

# Compare orbits
python src/analysis/plot_time_series.py \
    --tile 17MPV \
    --compare-orbits \
    --pixel 1843 1831 \
    --output orbit_compare.png
```

## API Reference

### IO Module

#### `load_zarr_dataset(tile_id, direction, output_dir="./output")`

Load Zarr dataset for a specific MGRS tile and flight direction.

**Returns:** `xarray.Dataset`

#### `load_catalog(output_dir="./output")`

Load the global catalog.parquet file.

**Returns:** `pandas.DataFrame`

#### `list_available_tiles(output_dir="./output")`

List all available tiles in the output directory.

**Returns:** List of dicts with `tile_id`, `direction`, and `path`

### Time Series Module

#### `extract_pixel_timeseries(dataset, row, col)`

Extract time series for a specific pixel.

**Returns:** Dict with `vv_ts`, `vh_ts`, `dates`, `valid_count`, etc.

#### `extract_region_timeseries(dataset, row_slice, col_slice, aggregation='mean')`

Extract aggregated time series for a rectangular region.

**Parameters:**
- `aggregation`: 'mean', 'median', 'std', 'min', or 'max'

**Returns:** Dict with aggregated time series

#### `lonlat_to_pixel(lon, lat, dataset, src_crs="EPSG:4326")`

Convert lon/lat coordinates to pixel row/col indices.

**Returns:** Tuple of `(row, col)`

#### `compute_time_series_statistics(ts_dict)`

Compute basic statistics (mean, std, min, max, median) for a time series.

**Returns:** Dict with statistics for VV and VH

#### `detect_outliers(ts_dict, method='iqr', threshold=1.5)`

Detect outliers using IQR or Z-score method.

**Returns:** Dict with outlier boolean masks

### Plotting Module

#### `plot_timeseries_figure(ts_dict, title=None, output_path=None, figsize=(12,10))`

Plot VV, VH, Ratio, and RVI time series in a 4-panel figure.

**Returns:** `matplotlib.figure.Figure`

#### `plot_orbit_comparison(ts_asc, ts_desc, output_path=None, figsize=(14,8))`

Compare ASCENDING and DESCENDING orbit time series.

**Returns:** `matplotlib.figure.Figure`

#### `plot_monthly_preview(dataset, month, variable='VV_dB', output_path=None)`

Plot a preview of a specific month from the dataset.

**Parameters:**
- `month`: Month string (e.g., "2024-01")
- `variable`: 'VV_dB', 'VH_dB', 'Ratio', or 'RVI'

**Returns:** `matplotlib.figure.Figure`

## Examples

### Example 1: Extract and Analyze Multiple Pixels

```python
from s1grits.analysis import *

# Load dataset
ds = load_zarr_dataset("17MPV", "DESCENDING")

# Define points of interest
points = [
    (1843, 1831, "Forest"),
    (2000, 2100, "Urban"),
    (1500, 1600, "Water"),
]

# Extract and compare
for row, col, label in points:
    ts = extract_pixel_timeseries(ds, row, col)
    stats = compute_time_series_statistics(ts)

    print(f"\n{label} ({row}, {col}):")
    print(f"  VV mean: {stats['vv']['mean']:.2f} dB")
    print(f"  VH mean: {stats['vh']['mean']:.2f} dB")
    print(f"  Valid obs: {ts['valid_count']}/{ts['total_count']}")
```

### Example 2: Batch Export Time Series

```python
import pandas as pd
from s1grits.analysis import *

# Load dataset
ds = load_zarr_dataset("17MPV", "DESCENDING")

# Extract time series
ts = extract_pixel_timeseries(ds, 1843, 1831)

# Convert to DataFrame
df = pd.DataFrame({
    'date': ts['dates'],
    'VV_dB': ts['vv_ts'],
    'VH_dB': ts['vh_ts'],
})

# Export to CSV
df.to_csv("timeseries.csv", index=False)
print(f"Exported {len(df)} records to timeseries.csv")
```

### Example 3: Create Monthly Animation (Conceptual)

```python
from s1grits.analysis import load_zarr_dataset, plot_monthly_preview

ds = load_zarr_dataset("17MPV", "DESCENDING")

# Get all months
months = sorted(set(str(t)[:7] for t in ds.time.values))

# Plot each month
for month in months:
    plot_monthly_preview(
        ds,
        month,
        variable="VV_dB",
        output_path=f"./animation/VV_{month}.png"
    )
    print(f"Generated preview for {month}")

# Use external tool to create animation from frames
# ffmpeg -framerate 2 -pattern_type glob -i 'animation/VV_*.png' -c:v libx264 animation.mp4
```

## Notes

- All functions follow Claude.md standards (English comments, no emojis)
- Use severity tags (INFO, WARN, ERROR) for console output
- Spatial coordinates use (row, col) indexing for consistency with numpy/xarray
- Time series are automatically filtered for NoData values
- Figures are saved at 300 DPI for publication quality

## Future Enhancements

- [ ] Statistical analysis module (trend detection, change detection)
- [ ] Export module (GeoJSON, Shapefile, NetCDF)
- [ ] Interactive visualization (Bokeh, Plotly)
- [ ] Multi-tile analysis and mosaicking
- [ ] Machine learning feature extraction
