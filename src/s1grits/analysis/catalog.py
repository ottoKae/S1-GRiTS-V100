"""
Catalog management API

This module provides functions for managing the global catalog.parquet file
and per-tile catalog files.

Functions:
    rebuild_global_catalog: Rebuild global catalog from tile catalogs
    validate_catalog: Validate catalog structure and integrity
    get_catalog_statistics: Get statistics from catalog
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import pandas as pd
import rasterio

from s1grits.logger_config import get_logger

logger = get_logger(__name__)


def rebuild_tile_catalog_from_cogs(tile_dir: Path) -> Optional[pd.DataFrame]:
    """
    Rebuild a single tile's catalog from its COG files.

    Args:
        tile_dir: Path to tile directory (e.g., output/50RKV_ASCENDING)

    Returns:
        DataFrame with catalog records, or None if no COG files found
    """
    cog_dir = tile_dir / 'cog'
    zarr_dir = tile_dir / 'zarr'
    preview_dir = tile_dir / 'preview'

    if not cog_dir.exists():
        return None

    cog_files = sorted(cog_dir.glob('*.tif'))
    if not cog_files:
        return None

    # Extract tile info from directory name
    tile_name = tile_dir.name
    parts = tile_name.split('_')
    if len(parts) < 2:
        return None

    mgrs_tile_id = parts[0]
    flight_direction = parts[1]

    records = []

    for cog_file in cog_files:
        try:
            # Extract month from filename
            # Format: {tile}_S1_Monthly_{direction}_{YYYY-MM}.tif
            filename = cog_file.stem
            file_parts = filename.split('_')
            month = file_parts[-1]  # YYYY-MM

            # Get metadata from COG
            with rasterio.open(cog_file) as src:
                crs = src.crs.to_string()
                width = src.width
                height = src.height
                transform = src.transform

            # Build paths
            zarr_path = str(zarr_dir / 'S1_monthly.zarr')
            preview_path = str(preview_dir / f'{month}.png')

            record = {
                'mgrs_tile_id': mgrs_tile_id,
                'flight_direction': flight_direction,
                'month': month,
                'datetime': pd.Timestamp(f'{month}-01'),
                'cog_path': str(cog_file),
                'zarr_path': zarr_path,
                'preview_path': preview_path if Path(preview_path).exists() else '',
                'crs': crs,
                'width': width,
                'height': height,
                'transform': list(transform)[:6],
                'preview_crs': 'EPSG:4326',
                'preview_width': 0,
                'preview_height': 0,
                'preview_bounds': None
            }
            records.append(record)
        except Exception as _e:
            logger.debug("Skipping COG file %s: failed to read metadata", cog_file, exc_info=True)
            continue

    if not records:
        return None

    return pd.DataFrame(records)


def rebuild_global_catalog(output_dir: Union[str, Path]) -> Dict[str, Any]:
    """
    Rebuild global catalog from COG files in all tile directories.

    This function:
    1. Scans all tile directories for COG files
    2. Rebuilds each tile's catalog.parquet from its COG files
    3. Merges all tile catalogs into a global catalog.parquet

    Args:
        output_dir: Output directory path containing tile subdirectories

    Returns:
        dict: Result dictionary with keys:
            - success (bool): Whether operation succeeded
            - message (str): Status message
            - catalog_path (Path): Path to global catalog (if successful)
            - total_records (int): Total number of records (if successful)
            - tile_count (int): Number of tiles (if successful)
            - month_count (int): Number of unique months (if successful)
            - tiles (list): List of tile IDs (if successful)
            - directions (list): List of flight directions (if successful)

    Example:
        >>> result = rebuild_global_catalog("./output")
        >>> if result['success']:
        ...     print(f"Rebuilt catalog with {result['total_records']} records")
    """
    output_root = Path(output_dir)

    if not output_root.exists():
        return {
            'success': False,
            'message': f"Output directory does not exist: {output_root}"
        }

    # Find all tile directories (format: {TILE}_{DIRECTION})
    tile_dirs = [d for d in output_root.iterdir()
                 if d.is_dir() and '_' in d.name and not d.name.startswith('.')]

    if not tile_dirs:
        return {
            'success': False,
            'message': f'No tile directories found in {output_dir}'
        }

    all_catalogs = []
    rebuilt_count = 0

    for tile_dir in sorted(tile_dirs):
        # Rebuild tile catalog from COG files
        tile_catalog = rebuild_tile_catalog_from_cogs(tile_dir)

        if tile_catalog is not None and len(tile_catalog) > 0:
            # Save tile catalog
            tile_catalog_path = tile_dir / 'catalog.parquet'
            tile_catalog.to_parquet(tile_catalog_path, index=False)
            all_catalogs.append(tile_catalog)
            rebuilt_count += 1

    if not all_catalogs:
        return {
            'success': False,
            'message': 'No valid tile catalogs found to merge'
        }

    # Merge all catalogs
    try:
        merged = pd.concat(all_catalogs, ignore_index=True)

        # Sort by mgrs_tile_id and datetime
        merged = merged.sort_values(["mgrs_tile_id", "datetime"]).reset_index(drop=True)

        # Save global catalog
        global_catalog_path = output_root / "catalog.parquet"
        merged.to_parquet(global_catalog_path, index=False)

        # Collect statistics
        result = {
            'success': True,
            'message': f"Successfully rebuilt global catalog with {len(merged)} records",
            'catalog_path': global_catalog_path,
            'total_records': len(merged),
            'tile_count': merged['mgrs_tile_id'].nunique(),
            'month_count': merged['month'].nunique(),
            'tiles': sorted(merged['mgrs_tile_id'].unique().tolist()),
        }

        # Add flight direction info if available
        if 'flight_direction' in merged.columns:
            result['directions'] = merged['flight_direction'].unique().tolist()

        # Rebuild STAC Items and Collection JSON in sync with catalog
        try:
            from s1grits.stac_builder import rebuild_stac_from_catalog
            rebuild_stac_from_catalog(str(output_root))
        except Exception as _stac_e:
            logger.warning("STAC rebuild failed (catalog rebuild succeeded): %s", _stac_e)

        return result

    except Exception as e:
        return {
            'success': False,
            'message': f"Failed to merge catalogs: {str(e)}"
        }


def validate_catalog(catalog_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Validate catalog structure and integrity

    Checks that the catalog file exists, is readable, and contains
    expected columns and data types.

    Args:
        catalog_path: Path to catalog.parquet file

    Returns:
        dict: Validation result with keys:
            - valid (bool): Whether catalog is valid
            - issues (list): List of validation issues found
            - warnings (list): List of warnings
            - record_count (int): Number of records (if readable)
            - columns (list): List of column names (if readable)

    Example:
        >>> result = validate_catalog("./output/catalog.parquet")
        >>> if not result['valid']:
        ...     for issue in result['issues']:
        ...         print(f"Issue: {issue}")
    """
    catalog_path = Path(catalog_path)
    issues = []
    warnings = []

    # Check if file exists
    if not catalog_path.exists():
        return {
            'valid': False,
            'issues': [f"Catalog file does not exist: {catalog_path}"],
            'warnings': []
        }

    # Try to read catalog
    try:
        df = pd.read_parquet(catalog_path)
    except Exception as e:
        return {
            'valid': False,
            'issues': [f"Failed to read catalog: {str(e)}"],
            'warnings': []
        }

    # Check for required columns
    required_columns = ['mgrs_tile_id', 'datetime', 'month', 'cog_path']
    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        issues.append(f"Missing required columns: {missing_columns}")

    # Check for empty catalog
    if len(df) == 0:
        warnings.append("Catalog is empty (0 records)")

    # Check datetime column
    if 'datetime' in df.columns:
        try:
            pd.to_datetime(df['datetime'])
        except Exception as _e:
            logger.debug("datetime column validation failed: %s", _e)
            issues.append("datetime column contains invalid values")

    # Check for duplicate records
    if 'mgrs_tile_id' in df.columns and 'datetime' in df.columns:
        duplicates = df.duplicated(subset=['mgrs_tile_id', 'datetime', 'flight_direction']
                                   if 'flight_direction' in df.columns
                                   else ['mgrs_tile_id', 'datetime'])
        if duplicates.any():
            warnings.append(f"Found {duplicates.sum()} duplicate records")

    # Check for missing values in critical columns
    for col in required_columns:
        if col in df.columns:
            null_count = df[col].isnull().sum()
            if null_count > 0:
                warnings.append(f"Column '{col}' has {null_count} null values")

    return {
        'valid': len(issues) == 0,
        'issues': issues,
        'warnings': warnings,
        'record_count': len(df),
        'columns': df.columns.tolist()
    }


def get_catalog_statistics(catalog_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Get statistics from catalog

    Computes various statistics about the data coverage, including
    tile counts, date ranges, and temporal coverage.

    Args:
        catalog_path: Path to catalog.parquet file

    Returns:
        dict: Statistics dictionary with keys:
            - success (bool): Whether operation succeeded
            - message (str): Status message
            - total_records (int): Total number of records
            - tile_count (int): Number of unique tiles
            - date_range (tuple): (min_date, max_date)
            - month_count (int): Number of unique months
            - tiles (dict): Per-tile statistics
            - directions (list): Flight directions (if available)

    Example:
        >>> stats = get_catalog_statistics("./output/catalog.parquet")
        >>> if stats['success']:
        ...     print(f"Coverage: {stats['date_range'][0]} to {stats['date_range'][1]}")
    """
    catalog_path = Path(catalog_path)

    if not catalog_path.exists():
        return {
            'success': False,
            'message': f"Catalog file does not exist: {catalog_path}"
        }

    try:
        df = pd.read_parquet(catalog_path)
        df['datetime'] = pd.to_datetime(df['datetime'])

        # Overall statistics
        stats = {
            'success': True,
            'message': "Statistics computed successfully",
            'total_records': len(df),
            'tile_count': df['mgrs_tile_id'].nunique(),
            'date_range': (
                df['datetime'].min().strftime('%Y-%m-%d'),
                df['datetime'].max().strftime('%Y-%m-%d')
            ),
            'month_count': df['datetime'].dt.to_period('M').nunique(),
        }

        # Add flight direction info if available
        if 'flight_direction' in df.columns:
            stats['directions'] = df['flight_direction'].unique().tolist()

        # Per-tile statistics
        tile_stats = {}
        for tile_id in sorted(df['mgrs_tile_id'].unique()):
            tile_df = df[df['mgrs_tile_id'] == tile_id]

            tile_info = {
                'record_count': len(tile_df),
                'date_range': (
                    tile_df['datetime'].min().strftime('%Y-%m-%d'),
                    tile_df['datetime'].max().strftime('%Y-%m-%d')
                ),
                'month_count': tile_df['datetime'].dt.to_period('M').nunique()
            }

            # Add per-direction stats if available
            if 'flight_direction' in df.columns:
                tile_info['by_direction'] = {}
                for direction in tile_df['flight_direction'].unique():
                    dir_df = tile_df[tile_df['flight_direction'] == direction]
                    tile_info['by_direction'][direction] = {
                        'record_count': len(dir_df),
                        'month_count': dir_df['datetime'].dt.to_period('M').nunique()
                    }

            tile_stats[tile_id] = tile_info

        stats['tiles'] = tile_stats

        return stats

    except Exception as e:
        return {
            'success': False,
            'message': f"Failed to compute statistics: {str(e)}"
        }
