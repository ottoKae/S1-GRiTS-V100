from datetime import datetime, timedelta

import geopandas as gpd
import pandas as pd
from pandera.pandas import check_input
from tqdm.auto import tqdm

from s1grits.asf_tiles import get_rtc_s1_metadata_from_acq_group
from s1grits.param_models import LookbackStrategyParams
from s1grits.tabular_models import dist_s1_input_schema, reorder_columns, rtc_s1_schema


def enumerate_one_dist_s1_product(
    mgrs_tile_id: str,
    track_number: int | list[int],
    post_date: datetime | pd.Timestamp | str,
    lookback_strategy: str = 'multi_window',
    post_date_buffer_days: int = 1,
    max_pre_imgs_per_burst: int | list[int] | tuple[int, ...] = (5, 5, 5),
    delta_window_days: int = 60,
    delta_lookback_days: int | list[int] | tuple[int, ...] = 365,
    min_pre_imgs_per_burst: int = 1,
    tqdm_enabled: bool = True,
) -> gpd.GeoDataFrame:
    """Enumerate a single product using unique DIST-S1 identifiers.

    The product identifiers are:

    1. MGRS Tile
    2. Track Number
    3. Post-image date (with a buffer)

    Hits the ASF DAAC API to get the necessary pre-/post-image data. Not
    recommended for enumerating large numbers of products over multiple MGRS
    tiles and/or track numbers.

    Parameters
    ----------
    mgrs_tile_id : str
        MGRS tile for DIST-S1 product
    track_number : int
        Track number for RTC-S1 pass
    post_date : datetime | pd.Timestamp | str
        Approximate date of post-image Acquistion, if string should be in the form of 'YYYY-MM-DD'.
    post_date_buffer_days : int, optional
        Number of days around the specified post date to search for post-image
        RTC-S1 data
    lookback_strategy : str, optional
        Lookback strategy to use, by default 'multi_window'. Options are
        'immediate_lookback' or 'multi_window'.
    max_pre_imgs_per_burst : int, optional
        Number of pre-images per burst to include, by default (5, 5, 5).
        If lookback strategy is 'multi_window':
            - this is interpreted as the maximum number of pre-images on each anniversary date.
            - tuple/list of integers are provided, each int represents the maximum number of pre-images on each
            anniversary date,
            most recent last.
            - if a single integer is provided, this is interpreted as the maximum number of pre-images on 3
            anniversary dates.
        If the lookback strategy is 'immediate_lookback':
            - Expects a single integer, tuples/lists will throw an error.
            - This means the maximum pre-images on prior to the post-date.
    delta_window_days : int, optional
        The acceptable window of time to search for pre-image RTC-S1 data. Default is 60 days (or 2 months).
        This amounts to roughly `post_date - lookback_days - delta_window_days` to `post_date - lookback_days`.
        If lookback strategy is 'multi_window', this means the maximum window of time to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....
    delta_lookback_days : int | list[int] | tuple[int, ...], optional
        When to set the most recent pre-image date. Default is 365 days.
        If lookback strategy is 'multi_window', this means the maximum number of days to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....
        If lookback strategy is 'immediate_lookback', this must be set to 0.
    min_pre_imgs_per_burst : int, optional
        Minimum number of pre-images per burst to include, by default 1. This is for *all* the pre-images.

    Returns
    -------
    gpd.GeoDataFrame
        DataFrame containing enumerated DIST-S1 products and the requisite OPERA RTC-S1 inputs and metadata.
        This is used within some of the DIST-S1 workflows to enumerate the requisited pre- and post-image inputs.
        The metadata includes polarization, url, burst_id, etc.
    """
    params = LookbackStrategyParams(
        lookback_strategy=lookback_strategy,
        max_pre_imgs_per_burst=max_pre_imgs_per_burst,
        delta_lookback_days=delta_lookback_days,
        min_pre_imgs_per_burst=min_pre_imgs_per_burst,
        delta_window_days=delta_window_days,
    )

    if isinstance(post_date, str):
        post_date = pd.Timestamp(post_date)

    if post_date_buffer_days >= 6:
        raise ValueError('post_date_buffer_days must be less than 6 (S1 pass length) - please check available data')

    if isinstance(track_number, int):
        track_numbers = [track_number]
    elif isinstance(track_number, list):
        track_numbers = track_number
    else:
        raise TypeError('track_number must be a single integer or a list of integers.')

    if isinstance(mgrs_tile_id, list):
        raise TypeError('mgrs_tile_id must be a single string; we are enumerating inputs for a single DIST-S1 product.')

    if isinstance(post_date, pd.Timestamp):
        post_date = post_date.to_pydatetime()

    print(f'Searching for post-images for track {track_number} in MGRS tile {mgrs_tile_id}')
    df_rtc_post = get_rtc_s1_metadata_from_acq_group(
        [mgrs_tile_id],
        track_numbers=track_numbers,
        start_acq_dt=post_date + timedelta(days=post_date_buffer_days),
        stop_acq_dt=post_date - timedelta(days=post_date_buffer_days),
        # Should take less than 5 minutes for S1 to pass over MGRS tile
        max_variation_seconds=300,
        n_images_per_burst=1,
    )
    if df_rtc_post.empty:
        raise ValueError(f'No RTC-S1 post-images found for track {track_number} in MGRS tile {mgrs_tile_id}.')

    if lookback_strategy == 'immediate_lookback':
        # Add 5 minutes buffer to ensure we don't include post-images in pre-image set.
        print('Searching for pre-images for immediate_lookback products')
        print(
            f'Lookback days {params.delta_lookback_days} and window days {params.delta_window_days} '
            f'with max pre-images per burst {params.max_pre_imgs_per_burst}'
        )
        post_date_min = df_rtc_post.acq_dt.min() - pd.Timedelta(seconds=300)
        earliest_lookback = params.delta_window_days + params.delta_lookback_days
        latest_lookback = params.delta_lookback_days
        start_acq_dt = post_date_min - timedelta(days=earliest_lookback)
        stop_acq_dt = post_date_min - timedelta(days=latest_lookback)
        df_rtc_pre = get_rtc_s1_metadata_from_acq_group(
            [mgrs_tile_id],
            track_numbers=track_numbers,
            start_acq_dt=start_acq_dt,
            stop_acq_dt=stop_acq_dt,
            n_images_per_burst=max_pre_imgs_per_burst,
        )
        df_unique_keys = df_rtc_post[['jpl_burst_id', 'polarizations']].drop_duplicates()

        df_rtc_pre = pd.merge(df_rtc_pre, df_unique_keys, on=['jpl_burst_id', 'polarizations'], how='inner')

        df_rtc_pre['input_category'] = 'pre'

    elif lookback_strategy == 'multi_window':
        df_rtc_pre_list = []
        zipped_data = list(zip(params.delta_lookback_days, params.max_pre_imgs_per_burst))
        print('Searching for pre-images for multi_window baseline')
        print(
            f'Lookback days {params.delta_lookback_days} and window days {params.delta_window_days} '
            f'with max pre-images per burst {params.max_pre_imgs_per_burst}'
        )
        for delta_lookback_day, max_pre_img_per_burst in tqdm(
            zipped_data,
            desc='Windows',
            dynamic_ncols=True,
            disable=(not tqdm_enabled),
        ):
            # Add 5 minutes buffer to ensure we don't include post-images in pre-image set.
            post_date_min = df_rtc_post.acq_dt.min() - pd.Timedelta(seconds=300)
            earliest_lookback = params.delta_window_days + delta_lookback_day
            latest_lookback = delta_lookback_day
            start_acq_dt = post_date_min - timedelta(days=latest_lookback)
            stop_acq_dt = post_date_min - timedelta(days=earliest_lookback)
            df_rtc_pre_window = get_rtc_s1_metadata_from_acq_group(
                [mgrs_tile_id],
                track_numbers=track_numbers,
                start_acq_dt=start_acq_dt,
                stop_acq_dt=stop_acq_dt,
                n_images_per_burst=max_pre_img_per_burst,
                polarizations=None,
            )
            df_unique_keys = df_rtc_post[['jpl_burst_id', 'polarizations']].drop_duplicates()

            df_rtc_pre_window = pd.merge(
                df_rtc_pre_window, df_unique_keys, on=['jpl_burst_id', 'polarizations'], how='inner'
            )

            if not df_rtc_pre_window.empty:
                df_rtc_pre_list.append(df_rtc_pre_window)

        df_rtc_pre = pd.concat(df_rtc_pre_list, ignore_index=True) if df_rtc_pre_list else pd.DataFrame()

    else:
        raise ValueError(
            f'Unsupported lookback_strategy: {lookback_strategy}. Expected "multi_window" or "immediate_lookback".'
        )

    if not df_rtc_pre.empty:
        pre_counts = df_rtc_pre.groupby('jpl_burst_id').size()
        burst_ids_with_min_pre_images = pre_counts[pre_counts >= params.min_pre_imgs_per_burst].index.tolist()
        df_rtc_pre = df_rtc_pre[df_rtc_pre.jpl_burst_id.isin(burst_ids_with_min_pre_images)].reset_index(drop=True)

        post_burst_ids = df_rtc_post.jpl_burst_id.unique().tolist()
        pre_burst_ids = df_rtc_pre.jpl_burst_id.unique().tolist()

        final_burst_ids = list(set(post_burst_ids) & set(pre_burst_ids))
        df_rtc_pre = df_rtc_pre[df_rtc_pre.jpl_burst_id.isin(final_burst_ids)].reset_index(drop=True)
        df_rtc_post = df_rtc_post[df_rtc_post.jpl_burst_id.isin(final_burst_ids)].reset_index(drop=True)

        if df_rtc_pre.empty:
            raise ValueError(
                f'Not enough RTC-S1 pre-images found for track {track_number} in MGRS tile {mgrs_tile_id} '
                'with available pre-images.'
            )
        if df_rtc_post.empty:
            raise ValueError(
                f'Not enough RTC-S1 post-images found for track {track_number} in MGRS tile {mgrs_tile_id} '
                'with available pre-images.'
            )

        df_rtc_pre['input_category'] = 'pre'
        df_rtc_post['input_category'] = 'post'

        df_rtc_product = pd.concat([df_rtc_pre, df_rtc_post], axis=0).reset_index(drop=True)

        # Validation
        dist_s1_input_schema.validate(df_rtc_product)
    else:
        df_rtc_product = gpd.GeoDataFrame()
    df_rtc_product = reorder_columns(df_rtc_product, dist_s1_input_schema)

    return df_rtc_product


@check_input(rtc_s1_schema, 0)
def enumerate_dist_s1_products(
    df_rtc_ts: gpd.GeoDataFrame,
    mgrs_tile_ids: list[str],
    lookback_strategy: str = 'multi_window',
    max_pre_imgs_per_burst: int = (5, 5, 5),
    min_pre_imgs_per_burst: int = 1,
    tqdm_enabled: bool = True,
    delta_lookback_days: int = 365,
    delta_window_days: int = 60,
) -> gpd.GeoDataFrame:
    """
    Enumerate DIST-S1 products from a stack of RTC-S1 metadata and a list of MGRS tiles.

    This function avoids repeated calls to the ASF DAAC API by working from a local stack of RTC-S1 metadata.

    This enumeration finds all the available post-image dates from a given stack of RTC-S1 inputs.


    Parameters
    ----------
    df_rtc_ts : gpd.GeoDataFrame
        RTC-S1 data.
    mgrs_tile_ids : list[str]
        List of MGRS tiles to enumerate.
    lookback_strategy : str, optional
        Lookback strategy to use, by default 'immediate_lookback'. Options are
        'immediate_lookback' or 'multi_window'.
    max_pre_imgs_per_burst : int, optional
        Number of pre-images per burst to include, by default 10.
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
    tqdm_enabled : bool, optional
        Whether to enable tqdm progress bars, by default True.
    delta_lookback_days : int, optional
        When to set the most recent pre-image date. Default is 365.
        If lookback strategy is 'multi_window', this means the maximum number of days to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....
        If lookback strategy is 'immediate_lookback', this must be set to 0.
    delta_window_days : int, optional
        The acceptable window of time to search for pre-image RTC-S1 data. Default is 60 days (or 2 months).
        This amounts to roughly `post_date - lookback_days - delta_window_days` to `post_date - lookback_days`.
        If lookback strategy is 'multi_window', this means the maximum window of time to search for pre-images on each
        anniversary date where `post_date - n * lookback_days` are the anniversary dates for n = 1,....

    Returns
    -------
    gpd.GeoDataFrame
        DataFrame containing enumerated OPERA RTC-S1 input metadata including polarization, url, burst_id, etc.
    """
    params = LookbackStrategyParams(
        lookback_strategy=lookback_strategy,
        max_pre_imgs_per_burst=max_pre_imgs_per_burst,
        delta_lookback_days=delta_lookback_days,
        min_pre_imgs_per_burst=min_pre_imgs_per_burst,
        delta_window_days=delta_window_days,
    )

    products = []
    product_id = 0
    for mgrs_tile_id in tqdm(mgrs_tile_ids, desc='Enumerate by MGRS tiles', disable=(not tqdm_enabled)):
        df_rtc_ts_tile = df_rtc_ts[df_rtc_ts.mgrs_tile_id == mgrs_tile_id].reset_index(drop=True)
        acq_group_ids_in_tile = df_rtc_ts_tile.acq_group_id_within_mgrs_tile.unique().tolist()
        # Groups are analogs to tracks (excepted grouped around the equator to ensure a single pass is grouped properly)
        for group_id in acq_group_ids_in_tile:
            df_rtc_ts_tile_track = df_rtc_ts_tile[df_rtc_ts_tile.acq_group_id_within_mgrs_tile == group_id].reset_index(
                drop=True
            )
            # Latest pass is now the first to appear in the list of pass_ids
            pass_ids_unique = sorted(df_rtc_ts_tile_track.pass_id.unique().tolist(), reverse=True)
            # Now traverse over all the passes
            for pass_id in pass_ids_unique:
                # post
                df_rtc_post = df_rtc_ts_tile_track[df_rtc_ts_tile_track.pass_id == pass_id].reset_index(drop=True)
                df_rtc_post['input_category'] = 'post'

                if lookback_strategy == 'immediate_lookback':
                    # pre-image accounting
                    post_date = df_rtc_post.acq_dt.min()
                    delta_lookback_timedelta = pd.Timedelta(params.delta_lookback_days, unit='D')
                    delta_window_timedelta = pd.Timedelta(params.delta_window_days, unit='D')
                    window_start = post_date - delta_lookback_timedelta - delta_window_timedelta
                    window_stop = post_date - delta_lookback_timedelta

                    # pre-image filtering
                    # Select pre-images temporally
                    ind_time = (df_rtc_ts_tile_track.acq_dt < window_stop) & (
                        df_rtc_ts_tile_track.acq_dt >= window_start
                    )
                    df_rtc_ts_tile_track_filtered = df_rtc_ts_tile_track[ind_time].reset_index(drop=True)
                    # Select images that are present in the post-image
                    df_unique_keys = df_rtc_post[['jpl_burst_id', 'polarizations']].drop_duplicates()
                    df_rtc_pre = pd.merge(
                        df_rtc_ts_tile_track_filtered,
                        df_unique_keys,
                        on=['jpl_burst_id', 'polarizations'],
                        how='inner',
                    )
                    df_rtc_pre['input_category'] = 'pre'

                    # It is unclear how merging when multiple MGRS tiles are provided will impact order so this
                    # is done to ensure the most recent pre-image set for each burst is selected
                    df_rtc_pre = df_rtc_pre.sort_values(by='acq_dt', ascending=True).reset_index(drop=True)
                    # Assume the data is sorted by acquisition date
                    df_rtc_pre = df_rtc_pre.groupby('jpl_burst_id').tail(max_pre_imgs_per_burst).reset_index(drop=True)
                    if df_rtc_pre.empty:
                        continue

                    # product and provenance
                    df_rtc_product = pd.concat([df_rtc_pre, df_rtc_post]).reset_index(drop=True)
                    df_rtc_product['product_id'] = product_id

                elif lookback_strategy == 'multi_window':
                    # pre-image accounting
                    post_date = df_rtc_post.acq_dt.min()
                    # Loop over the different lookback days
                    df_rtc_pre_list = []
                    zipped_data = list(zip(params.delta_lookback_days, params.max_pre_imgs_per_burst))
                    for delta_lookback_day, max_pre_img_per_burst_param in zipped_data:
                        delta_lookback_timedelta = pd.Timedelta(delta_lookback_day, unit='D')
                        delta_window_timedelta = pd.Timedelta(params.delta_window_days, unit='D')
                        window_start = post_date - delta_lookback_timedelta - delta_window_timedelta
                        window_stop = post_date - delta_lookback_timedelta

                        # pre-image filtering
                        # Select pre-images temporally
                        ind_time = (df_rtc_ts_tile_track.acq_dt < window_stop) & (
                            df_rtc_ts_tile_track.acq_dt >= window_start
                        )
                        df_rtc_ts_tile_track_filtered = df_rtc_ts_tile_track[ind_time].reset_index(drop=True)

                        df_unique_keys = df_rtc_post[['jpl_burst_id', 'polarizations']].drop_duplicates()
                        df_rtc_pre = pd.merge(
                            df_rtc_ts_tile_track_filtered,
                            df_unique_keys,
                            on=['jpl_burst_id', 'polarizations'],
                            how='inner',
                        )
                        df_rtc_pre['input_category'] = 'pre'

                        # It is unclear how merging when multiple MGRS tiles are provided will impact order so this
                        # is done to ensure the most recent pre-image set for each burst is selected
                        df_rtc_pre = df_rtc_pre.sort_values(by='acq_dt', ascending=True).reset_index(drop=True)
                        # Assume the data is sorted by acquisition date
                        df_rtc_pre = (
                            df_rtc_pre.groupby('jpl_burst_id').tail(max_pre_img_per_burst_param).reset_index(drop=True)
                        )

                        if df_rtc_pre.empty:
                            continue

                        if not df_rtc_pre.empty:
                            df_rtc_pre_list.append(df_rtc_pre)

                    # Concatenate all df_rtc_pre into a single DataFrame
                    df_rtc_pre_final = (
                        pd.concat(df_rtc_pre_list, ignore_index=True) if df_rtc_pre_list else pd.DataFrame()
                    )
                    df_rtc_product = pd.concat([df_rtc_pre_final, df_rtc_post]).reset_index(drop=True)
                    df_rtc_product['product_id'] = product_id

                else:
                    raise ValueError(
                        f'Unsupported lookback_strategy: {lookback_strategy}. '
                        'Expected "multi_window" or "immediate_lookback".'
                    )

                # Remove bursts that don't have minimum number of pre images
                pre_counts = df_rtc_product[df_rtc_product.input_category == 'pre'].groupby('jpl_burst_id').size()
                burst_ids_with_min_pre_images = pre_counts[pre_counts >= params.min_pre_imgs_per_burst].index.tolist()
                df_rtc_product = df_rtc_product[
                    df_rtc_product.jpl_burst_id.isin(burst_ids_with_min_pre_images)
                ].reset_index(drop=True)

                # finalize products
                if not df_rtc_product.empty:
                    products.append(df_rtc_product)
                    product_id += 1
    if products:
        df_prods = pd.concat(products, axis=0).reset_index(drop=True)
        dist_s1_input_schema.validate(df_prods)
    else:
        df_prods = gpd.GeoDataFrame()

    df_prods = reorder_columns(df_prods, dist_s1_input_schema)
    df_prods = df_prods.sort_values(by=['product_id', 'acq_dt'], ascending=True).reset_index(drop=True)

    return df_prods
