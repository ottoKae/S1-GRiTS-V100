"""
Zarr time series ordering fix module

This module provides functions to verify and fix time dimension ordering in Zarr datasets.
When incremental processing adds data out of chronological order, these functions ensure
proper temporal sequence by sorting the time coordinate and all data variables.

Functions:
    verify_time_order: Check if time dimension is chronologically sorted
    fix_zarr_order: Fix time ordering in Zarr dataset with backup support
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from s1grits.logger_config import get_logger

logger = get_logger(__name__)


def verify_time_order(zarr_path: Path) -> dict[str, Any]:
    """
    Verify if time dimension is chronologically sorted

    Args:
        zarr_path: Path to Zarr dataset

    Returns:
        Dict with verification results including:
            - path: str - Zarr path
            - exists: bool - Whether path exists
            - n_times: int - Number of time points
            - is_sorted: bool - Whether time is sorted
            - sort_indices: ndarray - Indices to sort time
            - times: DatetimeIndex - Time values
            - min_time: str - Minimum time
            - max_time: str - Maximum time
            - first_disorder_index: int - First disorder index (if unsorted)
            - error: str - Error message (if failed)
    """
    try:
        import zarr
    except ImportError:
        logger.error("Missing required library: zarr. Please run: pip install zarr")
        sys.exit(1)

    if not zarr_path.exists():
        return {
            "path": str(zarr_path),
            "exists": False,
            "error": "Zarr path does not exist"
        }

    try:
        g = zarr.open_group(str(zarr_path), mode="r")

        if "time" not in g:
            return {
                "path": str(zarr_path),
                "exists": True,
                "error": "No 'time' coordinate found in Zarr"
            }

        times = pd.to_datetime(g["time"][:])
        n_times = len(times)

        if n_times == 0:
            return {
                "path": str(zarr_path),
                "exists": True,
                "n_times": 0,
                "is_sorted": True,
                "warning": "Empty time dimension"
            }

        # Calculate sort indices
        sort_indices = np.argsort(times.values)
        expected_indices = np.arange(n_times)
        is_sorted = np.array_equal(sort_indices, expected_indices)

        result = {
            "path": str(zarr_path),
            "exists": True,
            "n_times": n_times,
            "is_sorted": is_sorted,
            "sort_indices": sort_indices,
            "times": times,
            "min_time": str(times.min()),
            "max_time": str(times.max()),
        }

        if not is_sorted:
            # Find first disorder
            for i in range(1, len(times)):
                if times[i] < times[i-1]:
                    result["first_disorder_index"] = i
                    result["first_disorder_time"] = str(times[i])
                    result["previous_time"] = str(times[i-1])
                    break

        return result

    except Exception as e:
        return {
            "path": str(zarr_path),
            "exists": True,
            "error": f"Failed to read Zarr: {e}"
        }


def _backup_zarr(zarr_path: Path, backup_dir: Path = None) -> Path:
    """
    Create a backup of the Zarr dataset

    Args:
        zarr_path: Path to Zarr dataset
        backup_dir: Optional custom backup directory

    Returns:
        Path to backup directory
    """
    if backup_dir is None:
        # Create backup next to original with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = zarr_path.parent / f"{zarr_path.name}.backup_{timestamp}"
    else:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / zarr_path.name

    logger.info("Creating backup: %s", backup_path)

    # Copy entire Zarr directory
    shutil.copytree(zarr_path, backup_path)

    backup_size = sum(f.stat().st_size for f in backup_path.rglob('*') if f.is_file())
    logger.info("Backup size: %.1f MB", backup_size / (1024**2))

    return backup_path


def fix_zarr_order(zarr_path: Path, dry_run: bool = False, skip_backup: bool = False,
                   backup_dir: Path = None) -> bool:
    """
    Fix time ordering in Zarr dataset

    Args:
        zarr_path: Path to Zarr dataset
        dry_run: If True, only verify without making changes
        skip_backup: If True, skip backup creation (not recommended)
        backup_dir: Optional custom backup directory

    Returns:
        bool: True if successful or already sorted, False otherwise
    """
    try:
        import zarr
    except ImportError:
        logger.error("Missing required library: zarr. Please run: pip install zarr")
        return False

    logger.info("Processing: %s", zarr_path)
    logger.info("=" * 60)

    # 1. Verify current state
    result = verify_time_order(zarr_path)

    if "error" in result:
        logger.error("%s", result['error'])
        return False

    if "warning" in result:
        logger.warning("%s", result['warning'])
        return True

    logger.info("Time points: %d", result['n_times'])
    logger.info("Time range: %s to %s", result['min_time'], result['max_time'])

    if result["is_sorted"]:
        logger.info("Time dimension is already chronologically sorted")
        logger.info("No fix needed")
        return True

    logger.warning("Time dimension is NOT chronologically sorted")
    if "first_disorder_index" in result:
        logger.warning("First disorder at index %d", result['first_disorder_index'])
        logger.warning("Expected: %s < %s", result['previous_time'], result['first_disorder_time'])

    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        logger.info("To fix this issue, run without --dry-run flag")
        return True

    # 2. Create backup
    if not skip_backup:
        try:
            backup_path = _backup_zarr(zarr_path, backup_dir)
            logger.info("Backup created: %s", backup_path)
        except Exception as e:
            logger.error("Backup failed: %s. Aborting operation for safety.", e)
            return False
    else:
        logger.warning("Skipping backup (skip_backup=True)")
        backup_path = None

    # 3. Read all data
    logger.info("Reading Zarr data...")

    try:
        g = zarr.open_group(str(zarr_path), mode="r")
        # Read time coordinate
        times = g["time"][:]
        sort_indices = result["sort_indices"]

        # Read all data variables
        data_vars = {}
        for var_name in ["VV_dB", "VH_dB", "Ratio", "RVI"]:
            if var_name in g:
                logger.info("Reading %s...", var_name)
                data_vars[var_name] = g[var_name][:]
            else:
                logger.warning("Variable %s not found, skipping", var_name)

    except Exception as e:
        logger.error("Failed to read Zarr data: %s", e)
        return False

    # 4. Reorder data
    logger.info("Reordering data...")

    try:
        # Sort time coordinate
        sorted_times = times[sort_indices]

        # Sort all data variables along time axis (axis=0)
        sorted_data = {}
        for var_name, data in data_vars.items():
            logger.info("Reordering %s (shape: %s)...", var_name, data.shape)
            sorted_data[var_name] = data[sort_indices, :, :]

    except Exception as e:
        logger.error("Failed to reorder data: %s", e)
        return False

    # 5. Write reordered data
    logger.info("Writing reordered data back to Zarr...")

    try:
        g = zarr.open_group(str(zarr_path), mode="r+")
        # Write time coordinate
        g["time"][:] = sorted_times

        # Write data variables
        for var_name, data in sorted_data.items():
            logger.info("Writing %s...", var_name)
            g[var_name][:] = data

        logger.info("Data successfully reordered")

    except Exception as e:
        logger.error("Failed to write data: %s", e)
        logger.error(
            "Your data may be corrupted. Restore from backup: %s",
            backup_path if not skip_backup else "N/A"
        )
        return False

    # 6. Verify fix
    logger.info("Verifying fix...")

    verify_result = verify_time_order(zarr_path)

    if verify_result.get("is_sorted", False):
        logger.info("SUCCESS - Time dimension is now chronologically sorted")
        logger.info("Time range: %s to %s", verify_result['min_time'], verify_result['max_time'])
        return True
    else:
        logger.error("Verification failed after fix")
        if not skip_backup:
            logger.error("Restore from backup: %s", backup_path)
        return False
