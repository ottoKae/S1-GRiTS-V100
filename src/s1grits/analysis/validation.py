"""
Validation API for data integrity checks

This module provides functions for validating COG files, Zarr datasets,
and data integrity.

Functions:
    validate_cog_file: Validate a single COG file
    validate_zarr_structure: Validate Zarr dataset structure
    check_data_integrity: Check data integrity for a path
"""

from pathlib import Path
from typing import Any, Dict, Union, Optional
import numpy as np


def validate_cog_file(cog_path: Union[str, Path], verbose: bool = False) -> Dict[str, Any]:
    """
    Validate a single COG file

    Checks that the COG file is readable, has expected bands, valid data ranges,
    and proper COG optimization (overviews, tiling).

    Args:
        cog_path: Path to COG file
        verbose: If True, include detailed band statistics

    Returns:
        dict: Validation results with keys:
            - path (str): Path to the file
            - valid (bool): Whether file is valid
            - issues (list): List of critical issues
            - warnings (list): List of warnings
            - band_stats (dict): Band statistics (if verbose=True)

    Example:
        >>> result = validate_cog_file("output/50RKV/cog/50RKV_2024-01_ASCENDING.tif")
        >>> if not result['valid']:
        ...     for issue in result['issues']:
        ...         print(f"Issue: {issue}")
    """
    try:
        import rasterio
        from rasterio.errors import RasterioIOError
    except ImportError:
        return {
            'path': str(cog_path),
            'valid': False,
            'issues': ["rasterio library not available"],
            'warnings': []
        }

    cog_path = Path(cog_path)
    issues = []
    warnings = []
    band_stats = {}

    try:
        with rasterio.open(cog_path) as src:
            # Check 1: Band count
            if src.count != 4:
                issues.append(f"Expected 4 bands, got {src.count}")

            # Check 2: CRS
            if src.crs is None:
                issues.append("Missing CRS")

            # Check 3: Overviews (for COG optimization)
            if not src.overviews(1):
                warnings.append("No overviews found (not optimized as COG)")

            # Check 4: Tiling
            if not src.is_tiled:
                warnings.append("Not tiled (not optimized as COG)")

            # Check 5: Data validation
            band_names = ['VV_dB', 'VH_dB', 'Ratio', 'RVI']
            band_ranges = {
                'VV_dB': (-50, 10),
                'VH_dB': (-50, 10),
                'Ratio': (0, 10),
                'RVI': (0, 2)
            }

            for band_idx in range(1, min(src.count + 1, 5)):
                band_name = band_names[band_idx - 1] if band_idx <= len(band_names) else f"Band{band_idx}"
                data = src.read(band_idx)

                # Check for all NaN
                if np.all(np.isnan(data)):
                    issues.append(f"{band_name}: All values are NaN")
                    continue

                # Check NaN percentage
                nan_pct = np.sum(np.isnan(data)) / data.size * 100
                if nan_pct > 50:
                    warnings.append(f"{band_name}: {nan_pct:.1f}% NaN values")

                # Check value ranges
                valid_data = data[~np.isnan(data)]
                if len(valid_data) > 0:
                    min_val = np.min(valid_data)
                    max_val = np.max(valid_data)

                    if band_name in band_ranges:
                        expected_min, expected_max = band_ranges[band_name]
                        if min_val < expected_min or max_val > expected_max:
                            warnings.append(
                                f"{band_name}: Values [{min_val:.2f}, {max_val:.2f}] "
                                f"outside expected range [{expected_min}, {expected_max}]"
                            )

                    if verbose:
                        band_stats[band_name] = {
                            'min': float(min_val),
                            'max': float(max_val),
                            'nan_percent': float(nan_pct)
                        }

            # Check 6: Geotransform
            if src.transform.is_identity:
                issues.append("Identity transform (no georeferencing)")

            # Collect metadata
            result = {
                'path': str(cog_path),
                'valid': len(issues) == 0,
                'issues': issues,
                'warnings': warnings,
                'metadata': {
                    'band_count': src.count,
                    'crs': str(src.crs) if src.crs else None,
                    'width': src.width,
                    'height': src.height,
                    'is_tiled': src.is_tiled,
                    'has_overviews': bool(src.overviews(1))
                }
            }

            if verbose and band_stats:
                result['band_stats'] = band_stats

            return result

    except RasterioIOError as e:
        return {
            'path': str(cog_path),
            'valid': False,
            'issues': [f"Cannot read file: {e}"],
            'warnings': []
        }
    except Exception as e:
        return {
            'path': str(cog_path),
            'valid': False,
            'issues': [f"Validation error: {e}"],
            'warnings': []
        }


def validate_zarr_structure(zarr_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Validate Zarr dataset structure

    Checks that the Zarr dataset is readable, has expected variables,
    proper dimensions, and valid time ordering.

    Args:
        zarr_path: Path to Zarr dataset

    Returns:
        dict: Validation results with keys:
            - path (str): Path to the dataset
            - valid (bool): Whether dataset is valid
            - issues (list): List of critical issues
            - warnings (list): List of warnings
            - metadata (dict): Dataset metadata

    Example:
        >>> result = validate_zarr_structure("output/50RKV/zarr/50RKV_S1_ASCENDING.zarr")
        >>> if result['valid']:
        ...     print(f"Dataset has {result['metadata']['time_steps']} time steps")
    """
    try:
        import xarray as xr
    except ImportError:
        return {
            'path': str(zarr_path),
            'valid': False,
            'issues': ["xarray library not available"],
            'warnings': []
        }

    zarr_path = Path(zarr_path)
    issues = []
    warnings = []

    if not zarr_path.exists():
        return {
            'path': str(zarr_path),
            'valid': False,
            'issues': [f"Zarr dataset does not exist: {zarr_path}"],
            'warnings': []
        }

    try:
        with xr.open_zarr(zarr_path) as ds:

            # Check 1: Expected variables
            expected_vars = ['VV_dB', 'VH_dB', 'Ratio', 'RVI']
            missing_vars = [v for v in expected_vars if v not in ds.data_vars]
            if missing_vars:
                issues.append(f"Missing variables: {missing_vars}")

            # Check 2: Expected dimensions
            expected_dims = ['time', 'y', 'x']
            missing_dims = [d for d in expected_dims if d not in ds.dims]
            if missing_dims:
                issues.append(f"Missing dimensions: {missing_dims}")

            # Check 3: Time ordering
            if 'time' in ds.dims and len(ds['time']) > 1:
                time_values = ds['time'].values
                is_sorted = np.all(time_values[:-1] <= time_values[1:])
                if not is_sorted:
                    warnings.append("Time dimension is not sorted")

            # Check 4: Empty dataset
            if 'time' in ds.dims and len(ds['time']) == 0:
                warnings.append("Dataset has no time steps")

            # Check 5: Data validity
            for var_name in expected_vars:
                if var_name in ds.data_vars:
                    var = ds[var_name]
                    if 'time' in var.dims and len(ds['time']) > 0:
                        # Sample first time step
                        sample = var.isel(time=0).compute()
                        if np.all(np.isnan(sample)):
                            warnings.append(f"{var_name}: First time step is all NaN")

            # Collect metadata
            metadata = {
                'variables': list(ds.data_vars.keys()),
                'dimensions': dict(ds.dims),
                'time_steps': len(ds['time']) if 'time' in ds.dims else 0,
            }

            if 'time' in ds.dims and len(ds['time']) > 0:
                metadata['time_range'] = (
                    str(ds['time'].values[0]),
                    str(ds['time'].values[-1])
                )

            return {
                'path': str(zarr_path),
                'valid': len(issues) == 0,
                'issues': issues,
                'warnings': warnings,
                'metadata': metadata
            }

    except Exception as e:
        return {
            'path': str(zarr_path),
            'valid': False,
            'issues': [f"Failed to validate Zarr: {e}"],
            'warnings': []
        }


def check_data_integrity(path: Union[str, Path], data_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Check data integrity for a file or directory

    Automatically detects data type (COG or Zarr) and performs appropriate validation.

    Args:
        path: Path to file or directory
        data_type: Force data type ('cog' or 'zarr'), or None for auto-detect

    Returns:
        dict: Validation results

    Example:
        >>> result = check_data_integrity("output/50RKV/cog/50RKV_2024-01_ASCENDING.tif")
        >>> print(f"Valid: {result['valid']}")
    """
    path = Path(path)

    if not path.exists():
        return {
            'path': str(path),
            'valid': False,
            'issues': [f"Path does not exist: {path}"],
            'warnings': []
        }

    # Auto-detect data type
    if data_type is None:
        if path.suffix in ['.tif', '.tiff']:
            data_type = 'cog'
        elif path.suffix == '.zarr' or (path.is_dir() and '.zarr' in path.name):
            data_type = 'zarr'
        else:
            return {
                'path': str(path),
                'valid': False,
                'issues': [f"Cannot determine data type for: {path}"],
                'warnings': []
            }

    # Validate based on type
    if data_type == 'cog':
        return validate_cog_file(path)
    elif data_type == 'zarr':
        return validate_zarr_structure(path)
    else:
        return {
            'path': str(path),
            'valid': False,
            'issues': [f"Unknown data type: {data_type}"],
            'warnings': []
        }
