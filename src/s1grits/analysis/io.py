"""
IO utilities for loading and reading S1 monthly mosaic datasets

This module provides convenient functions to load Zarr/COG outputs
from the s1grits processing workflow.
"""

import xarray as xr
import pandas as pd
from pathlib import Path
from typing import Any, Optional, List, Dict, Tuple

from s1grits.logger_config import get_logger

logger = get_logger(__name__)


def load_zarr_dataset(
    tile_id: str,
    direction: str,
    output_dir: str = "./output"
) -> xr.Dataset:
    """
    Load Zarr dataset for a specific MGRS tile and flight direction

    Args:
        tile_id: MGRS tile ID (e.g., "17MPV")
        direction: Flight direction ("ASCENDING" or "DESCENDING")
        output_dir: Output root directory (default: "./output")

    Returns:
        xarray.Dataset containing VV_dB, VH_dB, time coordinates, and attributes

    Raises:
        FileNotFoundError: If Zarr dataset does not exist

    Example:
        >>> ds = load_zarr_dataset("17MPV", "DESCENDING")
        >>> print(ds)
        >>> print(f"Time range: {ds.time.values[0]} to {ds.time.values[-1]}")
    """
    zarr_path = Path(output_dir) / f"{tile_id}_{direction}" / "zarr" / "S1_monthly.zarr"

    if not zarr_path.exists():
        raise FileNotFoundError(
            f"Zarr dataset not found: {zarr_path}\n"
            f"Available tiles: {list_available_tiles(output_dir)}"
        )

    logger.info("Loading Zarr: %s", zarr_path)
    ds = xr.open_zarr(zarr_path)

    logger.info("Dataset shape: %s (time, y, x)", ds['VV_dB'].shape)
    logger.info("Time range: %s to %s", ds.time.values[0], ds.time.values[-1])
    logger.info("Variables: %s", list(ds.data_vars))

    return ds


def load_catalog(output_dir: str = "./output") -> pd.DataFrame:
    """
    Load the global catalog.parquet file

    Args:
        output_dir: Output root directory (default: "./output")

    Returns:
        pandas.DataFrame with catalog metadata

    Raises:
        FileNotFoundError: If catalog does not exist

    Example:
        >>> cat = load_catalog()
        >>> print(cat[['mgrs_tile_id', 'flight_direction', 'month']].head())
    """
    catalog_path = Path(output_dir) / "catalog.parquet"

    if not catalog_path.exists():
        raise FileNotFoundError(
            f"Catalog not found: {catalog_path}\n"
            f"Run rebuild_catalog.py to generate it"
        )

    cat = pd.read_parquet(catalog_path)

    logger.info("Loaded catalog: %d records", len(cat))
    logger.info("Tiles: %d", cat['mgrs_tile_id'].nunique())
    if 'flight_direction' in cat.columns:
        logger.info("Directions: %s", cat['flight_direction'].unique().tolist())
    logger.info("Months: %d", cat['month'].nunique())

    return cat


def list_available_tiles(output_dir: str = "./output") -> List[Dict[str, str]]:
    """
    List all available tiles in the output directory

    Args:
        output_dir: Output root directory (default: "./output")

    Returns:
        List of dicts with 'tile_id' and 'direction' keys

    Example:
        >>> tiles = list_available_tiles()
        >>> for tile in tiles:
        >>>     print(f"{tile['tile_id']} - {tile['direction']}")
    """
    output_root = Path(output_dir)

    if not output_root.exists():
        return []

    tiles = []
    for item in output_root.iterdir():
        if item.is_dir() and "_" in item.name:
            # Check if Zarr exists
            zarr_path = item / "zarr" / "S1_monthly.zarr"
            if zarr_path.exists():
                # Parse tile_id and direction from folder name
                # Format: {tile_id}_{direction}
                parts = item.name.split("_")
                if len(parts) >= 2:
                    tile_id = parts[0]
                    direction = "_".join(parts[1:])  # Handle multi-part directions
                    tiles.append({
                        'tile_id': tile_id,
                        'direction': direction,
                        'path': str(item)
                    })

    return sorted(tiles, key=lambda x: (x['tile_id'], x['direction']))


def get_zarr_info(zarr_path: str) -> Dict[str, Any]:
    """
    Get basic information about a Zarr dataset without loading all data

    Args:
        zarr_path: Path to Zarr dataset

    Returns:
        dict with dataset metadata

    Example:
        >>> info = get_zarr_info("./output/17MPV_DESCENDING/zarr/S1_monthly.zarr")
        >>> print(info['time_steps'])
    """
    with xr.open_zarr(zarr_path) as ds:
        info = {
            'variables': list(ds.data_vars),
            'coordinates': list(ds.coords),
            'dims': dict(ds.dims),
            'time_steps': len(ds.time) if 'time' in ds else 0,
            'time_range': (
                str(ds.time.values[0]),
                str(ds.time.values[-1])
            ) if 'time' in ds and len(ds.time) > 0 else None,
            'spatial_shape': (ds.dims.get('y', 0), ds.dims.get('x', 0)),
            'attrs': dict(ds.attrs),
        }

    return info


def find_tile_by_lonlat(
    lon: float,
    lat: float,
    output_dir: str = "./output"
) -> Optional[Tuple[str, str]]:
    """
    Find which MGRS tile contains a given lon/lat coordinate.

    Searches the global catalog for tiles whose bounding box contains
    the given coordinate. Returns the first match sorted by tile_id.

    Args:
        lon: Longitude (WGS84)
        lat: Latitude (WGS84)
        output_dir: Output root directory

    Returns:
        Tuple of (tile_id, direction) or None if not found

    Raises:
        FileNotFoundError: If catalog does not exist in output_dir

    Example:
        >>> result = find_tile_by_lonlat(-78.5, -2.1)
        >>> if result:
        ...     tile_id, direction = result
    """
    import pyproj
    from shapely.geometry import Point

    catalog_path = Path(output_dir) / "catalog.parquet"
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}")

    cat = pd.read_parquet(catalog_path)
    if cat.empty:
        return None

    point_wgs84 = Point(lon, lat)

    for _, row in cat.drop_duplicates(subset=["mgrs_tile_id", "flight_direction"]).iterrows():
        try:
            t = list(row["transform"])
            w = int(row["width"])
            h = int(row["height"])
            crs_str = row["crs"]

            src_crs = pyproj.CRS.from_user_input(crs_str)
            dst_crs = pyproj.CRS.from_epsg(4326)
            tr = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True)

            x_min, x_max = t[2], t[2] + t[0] * w
            y_max, y_min = t[5], t[5] + t[4] * h

            corners_x = [x_min, x_max, x_max, x_min]
            corners_y = [y_min, y_min, y_max, y_max]
            lons, lats = tr.transform(corners_x, corners_y)

            bbox_west = min(lons)
            bbox_east = max(lons)
            bbox_south = min(lats)
            bbox_north = max(lats)

            if bbox_west <= lon <= bbox_east and bbox_south <= lat <= bbox_north:
                return (row["mgrs_tile_id"], row.get("flight_direction", ""))
        except Exception as _e:
            logger.debug("Coordinate lookup skipped for row: %s", _e)
            continue

    return None
