from datetime import datetime

import geopandas as gpd
import pandas as pd

from s1grits.asf_tiles import get_rtc_s1_ts_metadata_from_mgrs_tiles
from s1grits.dist_enum import enumerate_dist_s1_products
from s1grits.tabular_models import reorder_columns, rtc_s1_schema


def update_dist_s1_workflow_dict(data_dict: dict) -> dict:
    out = {}
    out.update(
        {
            key: val
            for (key, val) in data_dict.items()
            if key in ['mgrs_tile_id', 'acq_date_for_mgrs_pass', 'track_number', 'product_id', 'acq_dt']
        }
    )
    out_formatted = {
        'mgrs_tile_id': out['mgrs_tile_id'],
        'post_acq_date': out['acq_date_for_mgrs_pass'],
        'track_number': out['track_number'],
        'post_acq_timestamp': str(out['acq_dt']),
    }
    return out_formatted


def enumerate_dist_s1_workflow_inputs(
    mgrs_tile_ids: list[str] | str,
    track_numbers: list[int] | int | None = None,
    start_acq_dt: datetime | pd.Timestamp | str | None = None,
    stop_acq_dt: datetime | pd.Timestamp | str | None = None,
    lookback_strategy: str = 'multi_window',
    max_pre_imgs_per_burst: int | list[int] | tuple[int, ...] = (5, 5, 5),
    min_pre_imgs_per_burst: int = 1,
    delta_lookback_days: int | list[int] | tuple[int, ...] = 365,
    delta_window_days: int = 365,
    df_ts: gpd.GeoDataFrame | None = None,
) -> list[dict]:
    """Enumerate the inputs for a DIST-S1 workflow.

    This function enumerates DIST-S1 workflow inputs from MGRS tiles and track numbers.
    It uses the ASF DAAC API to get the necessary RTC-S1 metadata and then enumerates
    DIST-S1 products to create formatted DIST-S1 workflow inputs.

    Parameters
    ----------
    mgrs_tile_ids : list[str] | str
        MGRS tile(s) for DIST-S1 products. Can be a single string or list of strings.
    track_numbers : list[int] | int | None
        Track number(s) for RTC-S1 passes. Can be a single integer or list of integers.
    start_acq_dt : pd.Timestamp | str | None, optional
        Start acquisition datetime for filtering RTC-S1 data. If string, should be in
        ISO format. If None, no start filtering is applied.
    stop_acq_dt : pd.Timestamp | str | None, optional
        Stop acquisition datetime for filtering RTC-S1 data. If string, should be in
        ISO format. If None, no stop filtering is applied.
    lookback_strategy : str, optional
        Lookback strategy to use, by default 'multi_window'. Options are
        'immediate_lookback' or 'multi_window'.
    max_pre_imgs_per_burst : int | list[int] | tuple[int, ...], optional
        Maximum number of pre-images per burst to include, by default 10.
        If lookback strategy is 'multi_window':
            - this is interpreted as the maximum number of pre-images on each anniversary date.
            - tuple/list of integers are provided, each int represents the maximum number of pre-images on each
            anniversary date, most recent last.
            - if a single integer is provided, this is interpreted as the maximum number of pre-images on 3
            anniversary dates.
        If the lookback strategy is 'immediate_lookback':
            - Expects a single integer, tuples/lists will throw an error.
            - This means the maximum pre-images prior to the post-date.
    min_pre_imgs_per_burst : int, optional
        Minimum number of pre-images per burst to include, by default 1. This is for *all* the pre-images.
    delta_lookback_days : int | list[int] | tuple[int, ...], optional
        When to set the most recent pre-image date. Default is 0.
        If lookback strategy is 'multi_window', this means the maximum number of days to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....
        If lookback strategy is 'immediate_lookback', this must be set to 0.
    delta_window_days : int, optional
        The acceptable window of time to search for pre-image RTC-S1 data. Default is 365 days.
        This amounts to roughly `post_date - lookback_days - delta_window_days` to `post_date - lookback_days`.
        If lookback strategy is 'multi_window', this means the maximum window of time to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....
    df_ts : gpd.GeoDataFrame | None, optional
        RTC-S1 time series data. If None, will be enumerated from MGRS tiles and track numbers.

    Returns
    -------
    list[dict]
        List of dictionaries containing formatted DIST-S1 workflow inputs. Each dictionary contains:
        - mgrs_tile_id: MGRS tile identifier
        - post_acq_date: Post-image acquisition date
        - track_number: Track number for the RTC-S1 pass
    """
    if isinstance(mgrs_tile_ids, str):
        mgrs_tile_ids = [mgrs_tile_ids]
    if track_numbers is not None and isinstance(track_numbers, int):
        track_numbers = [track_numbers]
    if isinstance(start_acq_dt, str):
        start_acq_dt = pd.Timestamp(start_acq_dt, tz='UTC')
    if isinstance(stop_acq_dt, str):
        stop_acq_dt = pd.Timestamp(stop_acq_dt, tz='UTC')

    if df_ts is None:
        # Note we have to get full time-series to enumerate products! not just start/stop times.
        df_ts = get_rtc_s1_ts_metadata_from_mgrs_tiles(
            mgrs_tile_ids,
            track_numbers,
        )
    else:
        rtc_s1_schema.validate(df_ts)
        df_ts = reorder_columns(df_ts, rtc_s1_schema)

    df_products = enumerate_dist_s1_products(
        df_ts,
        mgrs_tile_ids,
        lookback_strategy=lookback_strategy,
        max_pre_imgs_per_burst=max_pre_imgs_per_burst,
        min_pre_imgs_per_burst=min_pre_imgs_per_burst,
        delta_lookback_days=delta_lookback_days,
        delta_window_days=delta_window_days,
    )

    df_post = df_products[df_products['input_category'] == 'post'].reset_index(drop=True)
    df_s1_workflow_inputs = df_post.groupby(['product_id']).first().reset_index(drop=True)
    df_s1_workflow_inputs = df_s1_workflow_inputs.sort_values(by='acq_dt', ascending=True).reset_index(drop=True)

    if start_acq_dt is not None:
        start_ind = df_s1_workflow_inputs.acq_dt >= start_acq_dt
        df_s1_workflow_inputs = df_s1_workflow_inputs[start_ind].reset_index(drop=True)
    if stop_acq_dt is not None:
        stop_ind = df_s1_workflow_inputs.acq_dt <= stop_acq_dt
        df_s1_workflow_inputs = df_s1_workflow_inputs[stop_ind].reset_index(drop=True)

    df_s1_workflow_input_data = df_s1_workflow_inputs.to_dict('records')

    df_s1_workflow_input_data_formatted = list(map(update_dist_s1_workflow_dict, df_s1_workflow_input_data))
    return df_s1_workflow_input_data_formatted
