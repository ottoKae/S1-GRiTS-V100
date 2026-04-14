"""
STAC Item and Collection builder for S1-GRiTS outputs.

Generates STAC 1.1.0 Item JSON files alongside data products and maintains
a STAC Collection JSON at the output root directory. Implements the
STAC datacube extension v2.3.0.

No external STAC library is required — outputs are plain JSON.
"""

import json
import os

import pyproj
import pandas as pd

from s1grits.logger_config import get_logger

logger = get_logger(__name__)

STAC_VERSION = "1.1.0"
DATACUBE_EXTENSION_URI = "https://stac-extensions.github.io/datacube/v2.3.0/schema.json"
COLLECTION_ID = "s1-grits-monthly"

# Core 4 bands always present
_CORE_BANDS_VV = ["VV_dB", "VH_dB", "Ratio", "RVI"]
_CORE_BANDS_HH = ["HH_dB", "HV_dB", "Ratio", "RVI"]


def _epsg_int(crs_str: str) -> int:
    """Extract EPSG integer from an 'EPSG:XXXXX' string."""
    return int(crs_str.split(":")[-1])


def _utm_extent_to_wgs84(transform: list, width: int, height: int, crs_str: str):
    """
    Project raster UTM bounding box corners to WGS84.

    Returns:
        bbox: [west, south, east, north] in WGS84 degrees
        geometry: GeoJSON Polygon dict in WGS84
    """
    x_min = transform[2]
    x_max = transform[2] + transform[0] * width
    y_max = transform[5]
    y_min = transform[5] + transform[4] * height  # transform[4] is negative

    src_crs = pyproj.CRS.from_user_input(crs_str)
    dst_crs = pyproj.CRS.from_epsg(4326)
    tr = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True)

    # Five points: four corners + closing point
    xs = [x_min, x_max, x_max, x_min, x_min]
    ys = [y_min, y_min, y_max, y_max, y_min]
    lons, lats = tr.transform(xs, ys)

    bbox = [
        round(min(lons[:4]), 6),
        round(min(lats[:4]), 6),
        round(max(lons[:4]), 6),
        round(max(lats[:4]), 6),
    ]
    geometry = {
        "type": "Polygon",
        "coordinates": [[[round(lon, 6), round(lat, 6)] for lon, lat in zip(lons, lats)]],
    }
    return bbox, geometry


def _read_cog_bands(cog_path: str) -> list[str] | None:
    """
    Read band names from a COG file using rasterio band descriptions.

    Returns a list of band name strings, or None if the file cannot be read.
    Falls back to description index ('Band_N') for bands without a description.
    """
    try:
        import rasterio
        with rasterio.open(cog_path) as src:
            bands = []
            for i, desc in enumerate(src.descriptions, start=1):
                bands.append(desc if desc else f"Band_{i}")
            return bands
    except Exception as _e:
        logger.debug("Could not read band descriptions from COG: %s", _e)
        return None


def _band_values(polarization: str) -> list[str]:
    """
    Return the default 4-band core list for the given polarization mode.

    Args:
        polarization: SAR polarization mode ('VV+VH' or 'HH+HV').

    Returns:
        List of band name strings (e.g. ['VV_dB', 'VH_dB', 'Ratio', 'RVI']).
    """
    if polarization == "HH+HV":
        return _CORE_BANDS_HH
    return _CORE_BANDS_VV


def _resolve_bands(record: dict, output_root: str, polarization: str) -> list[str]:
    """
    Resolve the actual band list for a catalog record.

    Priority:
      1. Read band descriptions from the COG file on disk (authoritative).
      2. Fall back to the default core-band list for the given polarization.

    This ensures GLCM texture bands (e.g. VV_glcm_CONTR, VH_glcm_ENT, …) are
    included whenever the COG was produced with texture features enabled.
    """
    cog_rel = record.get("cog_path")
    if cog_rel:
        # cog_path in catalog may be relative to output_root
        cog_abs = cog_rel if os.path.isabs(cog_rel) else os.path.join(output_root, cog_rel)
        bands = _read_cog_bands(cog_abs)
        if bands:
            return bands
    return _band_values(polarization)


def write_stac_item(
    record: dict,
    output_root: str,
    polarization: str = "VV+VH",
) -> str:
    """
    Write a STAC Item JSON for one tile × month composite.

    The file is written to {output_root}/{tile_dir_name}/{item_id}.json,
    alongside the COG, Zarr, and preview files.

    Band list is resolved dynamically from the COG file on disk so that
    GLCM texture bands are included whenever texture features were enabled
    during processing (12-band output). Falls back to the 4-band core list
    when the COG is unavailable.

    Args:
        record: Catalog record dict (keys: mgrs_tile_id, flight_direction, month,
                cog_path, zarr_path, preview_path, crs, width, height, transform).
        output_root: Root output directory.
        polarization: SAR polarization mode ('VV+VH' or 'HH+HV').

    Returns:
        Path to the written Item JSON file.
    """
    mgrs_tile_id = record["mgrs_tile_id"]
    flight_direction = record.get("flight_direction")
    month = str(record["month"])  # "YYYY-MM"
    crs = record["crs"]
    width = int(record["width"])
    height = int(record["height"])
    transform = list(record["transform"])

    flight_suffix = f"_{flight_direction}" if flight_direction else ""
    tile_dir_name = f"{mgrs_tile_id}{flight_suffix}"
    item_id = f"{mgrs_tile_id}{flight_suffix}_{month}"
    datetime_str = f"{month}-01T00:00:00Z"

    # Compute WGS84 bbox and GeoJSON geometry
    bbox, geometry = _utm_extent_to_wgs84(transform, width, height, crs)

    # Compute spatial extents in tile CRS
    x_min = transform[2]
    x_max = transform[2] + transform[0] * width
    y_max = transform[5]
    y_min = transform[5] + transform[4] * height
    pixel_size = abs(transform[0])
    epsg = _epsg_int(crs)

    # Resolve actual band list from the COG file (includes GLCM bands if present)
    bands = _resolve_bands(record, output_root, polarization)

    def _rel_to_tile(path_from_root):
        """Convert an absolute or output_root-relative path to tile-dir-relative href."""
        if not path_from_root:
            return None
        p = str(path_from_root).replace("\\", "/")
        # Strip output_root prefix if the path is absolute
        out_root_norm = str(output_root).replace("\\", "/").rstrip("/") + "/"
        if p.startswith(out_root_norm):
            p = p[len(out_root_norm):]
        prefix = tile_dir_name + "/"
        if p.startswith(prefix):
            return "./" + p[len(prefix):]
        return "../" + p

    item = {
        "stac_version": STAC_VERSION,
        "stac_extensions": [DATACUBE_EXTENSION_URI],
        "type": "Feature",
        "id": item_id,
        "geometry": geometry,
        "bbox": bbox,
        "properties": {
            "datetime": datetime_str,
            "platform": "sentinel-1",
            "instruments": ["c-sar"],
            "mgrs:tile_id": mgrs_tile_id,
            "s1:orbit_direction": flight_direction.lower() if flight_direction else None,
            "s1:processing_level": record.get("processing_level", "hARDCp"),
            "s1:monthly_composite": "median",
            "s1:despeckle": record.get("processing_level") != "ARDC",
            "cube:dimensions": {
                "x": {
                    "type": "spatial",
                    "axis": "x",
                    "extent": [round(x_min, 3), round(x_max, 3)],
                    "step": pixel_size,
                    "reference_system": epsg,
                },
                "y": {
                    "type": "spatial",
                    "axis": "y",
                    "extent": [round(y_min, 3), round(y_max, 3)],
                    "step": pixel_size,
                    "reference_system": epsg,
                },
                "time": {
                    "type": "temporal",
                    "extent": [datetime_str, datetime_str],
                    "step": "P1M",
                },
                "spectral": {
                    "type": "bands",
                    "values": bands,
                },
            },
        },
        "assets": {},
        "links": [
            {"rel": "self", "href": f"./{item_id}.json"},
            {"rel": "collection", "href": "../collection.json"},
            {"rel": "root", "href": "../collection.json"},
        ],
    }

    # Assets
    cog_href = _rel_to_tile(record.get("cog_path"))
    if cog_href:
        item["assets"]["cog"] = {
            "href": cog_href,
            "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "roles": ["data"],
            "title": f"Monthly Composite COG ({', '.join(bands)})",
        }

    zarr_href = _rel_to_tile(record.get("zarr_path"))
    if zarr_href:
        item["assets"]["zarr"] = {
            "href": zarr_href,
            "type": "application/vnd+zarr",
            "roles": ["data"],
            "title": "Full Time Series Zarr Store",
        }

    preview_href = _rel_to_tile(record.get("preview_path"))
    if preview_href:
        item["assets"]["preview"] = {
            "href": preview_href,
            "type": "image/png",
            "roles": ["overview"],
            "title": "RGB Preview (VV / VH / Ratio)",
        }

    tile_dir = os.path.join(output_root, tile_dir_name)
    os.makedirs(tile_dir, exist_ok=True)
    item_path = os.path.join(tile_dir, f"{item_id}.json")
    with open(item_path, "w", encoding="utf-8") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)

    return item_path


def write_stac_collection(
    catalog_path_or_df,
    output_root: str,
    polarization: str = "VV+VH",
) -> str | None:
    """
    Write or overwrite the STAC Collection JSON at {output_root}/collection.json.

    Computes the global bbox and temporal extent by aggregating all catalog records.
    The spectral band list is resolved from actual COG files on disk so that GLCM
    texture bands are correctly reflected when texture features were enabled.

    Args:
        catalog_path_or_df: Path to catalog.parquet file, or a pandas DataFrame.
        output_root: Root output directory.
        polarization: SAR polarization mode used during processing ('VV+VH' or 'HH+HV').

    Returns:
        Path to the written collection.json, or None if no records.
    """
    if isinstance(catalog_path_or_df, (str, os.PathLike)):
        df = pd.read_parquet(catalog_path_or_df)
    else:
        df = catalog_path_or_df

    if df.empty:
        logger.warning("No records — collection.json not written.")
        return None

    # Global WGS84 bbox from preview_bounds or UTM reprojection
    bboxes = []
    for _, row in df.iterrows():
        pb = row.get("preview_bounds")
        if isinstance(pb, dict) and all(k in pb for k in ("left", "bottom", "right", "top")):
            bboxes.append([pb["left"], pb["bottom"], pb["right"], pb["top"]])
        else:
            try:
                bbox, _ = _utm_extent_to_wgs84(
                    list(row["transform"]), int(row["width"]), int(row["height"]), row["crs"]
                )
                bboxes.append(bbox)
            except Exception as _e:
                logger.debug("Skipping bbox computation for row: %s", _e)

    global_bbox = (
        [
            round(min(b[0] for b in bboxes), 6),
            round(min(b[1] for b in bboxes), 6),
            round(max(b[2] for b in bboxes), 6),
            round(max(b[3] for b in bboxes), 6),
        ]
        if bboxes
        else [-180, -90, 180, 90]
    )

    # Temporal extent
    datetimes = pd.to_datetime(df["datetime"])
    t_start = datetimes.min().strftime("%Y-%m-%dT00:00:00Z")
    t_end = datetimes.max().strftime("%Y-%m-%dT00:00:00Z")

    # Pixel size from first valid transform record
    pixel_size = 10.0
    for _, row in df.iterrows():
        t = row.get("transform")
        if t is not None:
            try:
                pixel_size = float(abs(list(t)[0]))
                break
            except Exception as _e:
                logger.debug("Could not extract pixel size from transform: %s", _e)

    # Resolve the authoritative band list from COG files.
    # Scan up to 5 records to find one whose COG is readable; use the longest
    # band list found (most likely to include GLCM bands).
    all_bands: list[str] = _band_values(polarization)  # safe fallback
    for _, row in df.head(5).iterrows():
        bands = _resolve_bands(row.to_dict(), output_root, polarization)
        if len(bands) > len(all_bands):
            all_bands = bands

    # Build band description strings for the collection description
    core_band_desc = (
        "VV_dB (dB), VH_dB (dB), Ratio = VH/VV (dimensionless, linear), "
        "RVI = 4*VH/(VV+VH) (dimensionless, range 0-4)"
    ) if polarization != "HH+HV" else (
        "HH_dB (dB), HV_dB (dB), Ratio = HV/HH (dimensionless, linear), "
        "RVI = 4*HV/(HH+HV) (dimensionless, range 0-4)"
    )
    glcm_bands = [b for b in all_bands if "glcm" in b.lower()]
    glcm_desc = (
        f" GLCM texture bands: {', '.join(glcm_bands)}."
        if glcm_bands else ""
    )

    # Summaries
    tiles = sorted(df["mgrs_tile_id"].unique().tolist())
    directions = (
        sorted(df["flight_direction"].dropna().unique().tolist())
        if "flight_direction" in df.columns
        else []
    )

    # Item links (one per row)
    item_links = []
    for _, row in df.iterrows():
        tile_id = row["mgrs_tile_id"]
        fd = row.get("flight_direction")
        month = str(row["month"])
        fs = f"_{fd}" if fd else ""
        tile_dir_name = f"{tile_id}{fs}"
        item_id = f"{tile_id}{fs}_{month}"
        item_links.append(
            {
                "rel": "item",
                "href": f"./{tile_dir_name}/{item_id}.json",
                "type": "application/geo+json",
                "title": item_id,
            }
        )

    collection = {
        "stac_version": STAC_VERSION,
        "stac_extensions": [DATACUBE_EXTENSION_URI],
        "type": "Collection",
        "id": COLLECTION_ID,
        "title": "S1-GRiTS: Sentinel-1 Monthly Composite Time Series",
        "description": (
            "Monthly Sentinel-1 SAR backscatter composites processed from OPERA RTC-S1 "
            "products and gridded to MGRS tiles. "
            f"Tiles: {', '.join(tiles)}. "
            f"Bands: {core_band_desc}.{glcm_desc}"
        ),
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [global_bbox]},
            "temporal": {"interval": [[t_start, None]]},
        },
        "cube:dimensions": {
            "x": {"type": "spatial", "axis": "x", "step": pixel_size},
            "y": {"type": "spatial", "axis": "y", "step": pixel_size},
            "time": {
                "type": "temporal",
                "step": "P1M",
                "extent": [t_start, t_end],
            },
            "spectral": {
                "type": "bands",
                "values": all_bands,
            },
        },
        "summaries": {
            "platform": ["sentinel-1a", "sentinel-1b"],
            "instruments": ["c-sar"],
            "mgrs:tile_id": tiles,
            "s1:orbit_direction": [d.lower() for d in directions],
            "s1:band_count": len(all_bands),
            "s1:glcm_enabled": len(glcm_bands) > 0,
        },
        "links": [
            {"rel": "self", "href": "./collection.json"},
            *item_links,
        ],
    }

    collection_path = os.path.join(output_root, "collection.json")
    with open(collection_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2, ensure_ascii=False)

    logger.info("Collection written: %s (%d items, %d bands)", collection_path, len(df), len(all_bands))
    return collection_path


def rebuild_stac_from_catalog(
    output_root: str,
    polarization: str = "VV+VH",
) -> None:
    """
    Rebuild all STAC Item JSONs and collection.json from the global catalog.parquet.

    Called by 's1grits catalog rebuild' to resync STAC after manual file changes.

    Args:
        output_root: Root output directory containing catalog.parquet.
        polarization: SAR polarization mode ('VV+VH' or 'HH+HV').
    """
    catalog_path = os.path.join(output_root, "catalog.parquet")
    if not os.path.exists(catalog_path):
        logger.warning("No catalog.parquet at: %s", catalog_path)
        return

    df = pd.read_parquet(catalog_path)
    logger.info("Rebuilding %d STAC Items...", len(df))

    ok = 0
    for _, row in df.iterrows():
        try:
            write_stac_item(row.to_dict(), output_root, polarization)
            ok += 1
        except Exception as e:
            logger.warning(
                "Item failed for %s %s: %s",
                row.get('mgrs_tile_id'), row.get('month'), e
            )

    logger.info("Items written: %d/%d", ok, len(df))
    write_stac_collection(df, output_root, polarization=polarization)
