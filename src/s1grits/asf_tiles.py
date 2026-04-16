from datetime import datetime
from warnings import warn

import asf_search as asf
import geopandas as gpd
import pandas as pd
from pandera.pandas import check_input
from rasterio.crs import CRS
from shapely.geometry import shape

from s1grits.mgrs_burst_data import get_burst_ids_in_mgrs_tiles, get_lut_by_mgrs_tile_ids
from s1grits.tabular_models import reorder_columns, rtc_s1_resp_schema, rtc_s1_schema

SECONDS_PER_DAY: int = 86400          # Seconds in one day
S1_REPEAT_CYCLE_DAYS: int = 6        # Sentinel-1 exact repeat cycle in days


def convert_asf_url_to_cumulus(url: str) -> str:
    """
    Convert an ASF datapool URL to the equivalent Cumulus earthdatacloud URL.

    Args:
        url: Source URL (ASF datapool or Cumulus format)

    Returns:
        Cumulus URL if input is an ASF datapool URL, otherwise returns input unchanged.

    Raises:
        ValueError: If the granule name cannot be extracted from the filename.
    """
    asf_base = 'https://datapool.asf.alaska.edu/RTC/OPERA-S1/'
    cumulus_base = 'https://cumulus.asf.earthdatacloud.nasa.gov/OPERA/OPERA_L2_RTC-S1/'

    if not (url.startswith(cumulus_base) or url.startswith(asf_base)):
        warn(f'URL {url} is not a valid ASF datapool or cumulus earthdatacloud URL.')
        return url

    if not url.startswith(asf_base):
        return url

    filename = url.split('/')[-1]
    granule_pol_parts = filename.rsplit('_', 1)
    if len(granule_pol_parts) != 2:
        raise ValueError(f'Could not extract granule name from filename: {filename}')

    granule_name = granule_pol_parts[0]
    new_url = f'{cumulus_base}{granule_name}/{filename}'
    return new_url


def format_polarization(pol: list | str) -> str:
    """
    Normalize a polarization value to a canonical string like 'VV+VH' or 'HH+HV'.

    Args:
        pol: Polarization as a list (e.g. ['VV', 'VH']) or string (e.g. 'VV+VH').

    Returns:
        Canonical polarization string.

    Raises:
        TypeError: If pol is neither a list nor a string.
    """
    if isinstance(pol, list):
        if ('VV' in pol) and len(pol) == 2:
            return 'VV+VH'
        elif ('HH' in pol) and len(pol) == 2:
            return 'HH+HV'
        else:
            return '+'.join(pol)
    elif isinstance(pol, str):
        return pol
    else:
        raise TypeError(f'Invalid polarization: {pol}.')


def extract_pass_id(acq_dt: pd.Timestamp) -> int:
    """
    Compute a pass ID as the number of 6-day repeat cycles since 2014-01-01 UTC.

    Args:
        acq_dt: Acquisition datetime (timezone-aware).

    Returns:
        Integer pass ID (0-indexed from the Sentinel-1 reference epoch).
    """
    reference_date = pd.Timestamp('2014-01-01', tz='UTC')
    return int((acq_dt - reference_date).total_seconds() / SECONDS_PER_DAY / S1_REPEAT_CYCLE_DAYS)


def append_pass_data(df_rtc: gpd.GeoDataFrame, mgrs_tile_ids: list[str]) -> gpd.GeoDataFrame:
    """Format the RTC S1 metadata for easier lookups."""
    # Extract the LUT acquisition info
    # Burst IDs will have multiple rows if they lie in multiple MGRS tiles and those tiles are specified
    rtc_columns = df_rtc.columns.tolist()
    if not all([col in rtc_columns for col in ['jpl_burst_id', 'pass_id', 'acq_dt', 'track_number']]):
        raise ValueError('Cannot append pass data without jpl_burst_id, pass_id, acq_dt, and track_number columns.')
   
    df_lut = get_lut_by_mgrs_tile_ids(mgrs_tile_ids)
    
    df_rtc = pd.merge(
        df_rtc,
        df_lut[['jpl_burst_id', 'mgrs_tile_id', 'acq_group_id_within_mgrs_tile', 'orbit_pass']],
        on='jpl_burst_id',
        how='inner',
    )
    # Creates a date string 'YYYY-MM-DD' for the earliest acquisition date for a pass of the mgrs tile
    df_rtc['acq_date_for_mgrs_pass'] = (
        df_rtc.groupby(['mgrs_tile_id', 'acq_group_id_within_mgrs_tile', 'pass_id'])['acq_dt']
        .transform('min')
        .dt.floor('D')
        .dt.strftime('%Y-%m-%d')
    )

    # Creates track_token that associates joins the track number with '_' within a pass of the mgrs tile
    def get_track_token(track_numbers: list[int]) -> str:
        unique_track_numbers = track_numbers.unique().tolist()
        return '_'.join(map(str, sorted(unique_track_numbers)))

    df_rtc['track_token'] = df_rtc.groupby(['mgrs_tile_id', 'acq_group_id_within_mgrs_tile'])['track_number'].transform(
        get_track_token
    )

    df_rtc = df_rtc.sort_values(by=['jpl_burst_id', 'acq_dt']).reset_index(drop=True)

    return df_rtc


def get_rtc_s1_ts_metadata_by_burst_ids(
    burst_ids: str | list[str],
    start_acq_dt: str | datetime | None | pd.Timestamp = None,
    stop_acq_dt: str | datetime | None | pd.Timestamp = None,
    polarizations: str | None = None,
    include_single_polarization: bool = False,
) -> gpd.GeoDataFrame:
    """Wrap/format the ASF search API for RTC-S1 metadata search. All searches go through this function.

    Requires search data to be dual polarized data of the same type (if not specified, will get all search results
    of the available type).

    If dual polarized data is mixed (that is there are HH+HV and VV+VH), will raise an error.
    """
    if isinstance(burst_ids, str):
        burst_ids = [burst_ids]

    if (polarizations is not None) and (polarizations not in ['HH+HV', 'VV+VH']):
        raise ValueError(f'Invalid polarization: {polarizations}. Must be one of: HH+HV, VV+VH, None.')

    # Convert all date inputs to datetime objects using pandas for flexibility
    start_acq_dt_obj = None
    stop_acq_dt_obj = None

    if start_acq_dt is not None:
        start_acq_dt_obj = pd.to_datetime(start_acq_dt, utc=True).to_pydatetime()

    if stop_acq_dt is not None:
        stop_acq_dt_obj = pd.to_datetime(stop_acq_dt, utc=True).to_pydatetime()

    # Make sure JPL syntax is transformed to asf syntax
    burst_ids = [burst_id.upper().replace('-', '_') for burst_id in burst_ids]
    resp = asf.geo_search(
        operaBurstID=burst_ids,
        processingLevel='RTC',
        start=start_acq_dt_obj,
        end=stop_acq_dt_obj,
    )
    if not resp:
        warn('No results - please check burst id and availability.', category=UserWarning)
        return gpd.GeoDataFrame(columns=rtc_s1_resp_schema.columns.keys())

    properties = [r.properties for r in resp]
    geometry = [shape(r.geojson()['geometry']) for r in resp]
    properties_f = [
        {
            'opera_id': p['sceneName'],
            'acq_dt': pd.to_datetime(p['startTime']),
            'track_number': p['pathNumber'],
            'polarizations': format_polarization(p['polarization']),
            'all_urls': [p['url']] + p['additionalUrls'],
        }
        for p in properties
    ]

    df_rtc = gpd.GeoDataFrame(properties_f, geometry=geometry, crs=CRS.from_epsg(4326))
    # Extract the burst_id from the opera_id
    df_rtc['jpl_burst_id'] = df_rtc['opera_id'].map(lambda bid: bid.split('_')[3])

    # pass_id is the integer number of 6 day periods since 2014-01-01
    df_rtc['pass_id'] = df_rtc.acq_dt.map(extract_pass_id)

    # Remove duplicates from time series
    df_rtc['dedup_id'] = df_rtc.opera_id.map(lambda id_: '_'.join(id_.split('_')[:5]))
    df_rtc = df_rtc.drop_duplicates(subset=['dedup_id']).reset_index(drop=True)
    df_rtc = df_rtc.drop(columns=['dedup_id'])

    # polarizations - ensure dual polarization
    # asf metadata can be ['HH', 'HV'] or 'HH+HV' - reformat to the latter
    df_rtc['polarizations'] = df_rtc['polarizations'].map(format_polarization)
    if polarizations is not None:
        ind_pol = df_rtc['polarizations'] == polarizations
    elif not include_single_polarization:
        ind_pol = df_rtc['polarizations'].isin(['HH+HV', 'VV+VH'])
    else:
        ind_pol = df_rtc['polarizations'].isin(['HH+HV', 'VV+VH', 'HH', 'HV', 'VV', 'VH'])
    if not ind_pol.any():
        warn(f'No valid dual polarization images found for {burst_ids}.')
    # First get all the dual-polarizations images
    df_rtc = df_rtc[ind_pol].reset_index(drop=True)

    def get_url_by_polarization(prod_urls: list[str], polarization_token: str) -> list[str]:
        if polarization_token == 'copol':
            polarizations_allowed = ['VV', 'HH']
        elif polarization_token == 'crosspol':
            polarizations_allowed = ['HV', 'VH']
        else:
            raise ValueError(f'Invalid polarization token: {polarization_token}. Must be one of: copol, crosspol.')
        possible_urls = [url for pol in polarizations_allowed for url in prod_urls if f'_{pol}.tif' == url[-7:]]
        if len(possible_urls) == 0:
            raise ValueError(f'No {polarizations_allowed} urls found')
        if len(possible_urls) > 1:
            raise ValueError(f'Multiple {polarization_token} urls found: {", ".join(possible_urls)}')
        return possible_urls[0]

    url_copol = df_rtc.all_urls.map(lambda urls_for_prod: get_url_by_polarization(urls_for_prod, 'copol'))
    url_crosspol = df_rtc.all_urls.map(lambda urls_for_prod: get_url_by_polarization(urls_for_prod, 'crosspol'))

    df_rtc['url_copol'] = url_copol
    df_rtc['url_crosspol'] = url_crosspol
    df_rtc['url_copol'] = df_rtc['url_copol'].map(convert_asf_url_to_cumulus)
    df_rtc['url_crosspol'] = df_rtc['url_crosspol'].map(convert_asf_url_to_cumulus)
    df_rtc = df_rtc.drop(columns=['all_urls'])

    # Ensure the data is sorted by jpl_burst_id and acq_dt
    df_rtc = df_rtc.sort_values(by=['jpl_burst_id', 'acq_dt'], ascending=True).reset_index(drop=True)

    rtc_s1_resp_schema.validate(df_rtc)
    df_rtc = reorder_columns(df_rtc, rtc_s1_resp_schema)

    return df_rtc


def get_rtc_s1_metadata_from_acq_group(
    mgrs_tile_ids: list[str],
    track_numbers: list[int],
    n_images_per_burst: int = 1,
    start_acq_dt: datetime | str | None = None,
    stop_acq_dt: datetime | str | None = None,
    max_variation_seconds: float | None = None,
    polarizations: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Meant for acquiring a pre-image or post-image set from MGRS tiles for a given S1 pass.

    Obtains the most recent burst image set within a date range.

    For acquiring a post-image set, we provide the keyword argument max_variation_seconds to ensure the latest
    acquisition of are within the latest acquisition time from the most recent burst image. If this is not provided,
    you will get the latest burst image product for each burst within the allowable date range. This could yield imagery
    collected on different dates for the burst_ids provided.

    For acquiring a pre-image set, we use n_images_per_burst > 1. We get the latest n_images_per_burst images for each
    burst and there can be different number of images per burst for all the burst supplied and/or the image
    time series can be composed of images from different dates.

    Note we take care of the equator edge cases in LUT of the MGRS/burst_ids, so only need to provide 1 valid track
    number in pass.

    Parameters
    ----------
    mgrs_tile_ids: list[str]
    track_numbers: list[int]
    start_acq_dt: datetime | str
    stop_acq_dt : datetime
    max_variation_seconds : float, optional
    n_images_per_burst : int, optional

    Returns
    -------
    gpd.GeoDataFrame
    """
    if len(track_numbers) > 2:
        raise ValueError('Cannot handle more than 2 track numbers.')
    if (len(track_numbers) == 2) and (abs(track_numbers[0] - track_numbers[1]) > 1):
        raise ValueError('Two track numbers that are not consecutive were provided.')
    burst_ids = get_burst_ids_in_mgrs_tiles(mgrs_tile_ids, track_numbers=track_numbers)
    if not burst_ids:
        mgrs_tiles_str = ','.join(mgrs_tile_ids)
        track_numbers_str = ','.join(map(str, track_numbers))
        raise ValueError(
            f'No burst ids found for the provided MGRS tile {mgrs_tiles_str} and track numbers {track_numbers_str}.'
        )

    if (n_images_per_burst == 1) and (max_variation_seconds is None):
        warn(
            'No maximum variation in acq dts provided although n_images_per_burst is 1. '
            'This could yield imagery collected on '
            'different dates for the burst_ids provided.',
            category=UserWarning,
        )
    df_rtc = get_rtc_s1_ts_metadata_by_burst_ids(
        burst_ids,
        start_acq_dt=start_acq_dt,
        stop_acq_dt=stop_acq_dt,
        polarizations=polarizations,
    )
    # Assumes that each group is ordered by date (earliest first and most recent last)
    columns = df_rtc.columns
    df_rtc = df_rtc.groupby('jpl_burst_id').tail(n_images_per_burst).reset_index(drop=False)
    df_rtc = df_rtc[columns]
    if max_variation_seconds is not None:
        if (n_images_per_burst is None) or (n_images_per_burst > 1):
            raise ValueError('Cannot apply maximum variation in acq dts when n_images_per_burst > 1 or None.')
        max_dt = df_rtc['acq_dt'].max()
        ind = df_rtc['acq_dt'] > max_dt - pd.Timedelta(seconds=max_variation_seconds)
        df_rtc = df_rtc[ind].reset_index(drop=True)

    if not df_rtc.empty:
        df_rtc = append_pass_data(df_rtc, mgrs_tile_ids)
        rtc_s1_schema.validate(df_rtc)
    df_rtc = reorder_columns(df_rtc, rtc_s1_schema)

    return df_rtc


def get_rtc_s1_ts_metadata_from_mgrs_tiles(
    mgrs_tile_ids: list[str],
    track_numbers: list[int] | None = None,
    start_acq_dt: str | datetime | None = None,
    stop_acq_dt: str | datetime | None = None,
    polarizations: str | None = None,
) -> gpd.GeoDataFrame:
    """Get the RTC S1 time series for a given MGRS tile and track number."""
    if isinstance(start_acq_dt, str):
        start_acq_dt = datetime.strptime(start_acq_dt, '%Y-%m-%d')
    if isinstance(stop_acq_dt, str):
        stop_acq_dt = datetime.strptime(stop_acq_dt, '%Y-%m-%d')

    burst_ids = get_burst_ids_in_mgrs_tiles(mgrs_tile_ids, track_numbers=track_numbers)
    df_rtc_ts = get_rtc_s1_ts_metadata_by_burst_ids(
        burst_ids, start_acq_dt=start_acq_dt, stop_acq_dt=stop_acq_dt, polarizations=polarizations
    )
    if df_rtc_ts.empty:
        mgrs_tiles_str = ','.join(mgrs_tile_ids)
        msg = f'No RTC S1 metadata found for  MGRS tile {mgrs_tiles_str}.'
        if track_numbers is not None:
            track_number_token = '_'.join(map(str, track_numbers))
            msg += f' Track numbers provided: {track_number_token}.'
        warn(msg)
        return gpd.GeoDataFrame(columns=rtc_s1_schema.columns.keys())

    df_rtc_ts = append_pass_data(df_rtc_ts, mgrs_tile_ids)
    rtc_s1_schema.validate(df_rtc_ts)
    df_rtc_ts = reorder_columns(df_rtc_ts, rtc_s1_schema)

    return df_rtc_ts


@check_input(rtc_s1_schema, 0)
def agg_rtc_metadata_by_burst_id(df_rtc_ts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    df_agg = (
        df_rtc_ts.groupby('jpl_burst_id')
        .agg(count=('jpl_burst_id', 'size'), earliest_acq_date=('acq_dt', 'min'), latest_acq_date=('acq_dt', 'max'))
        .reset_index()
    )

    return df_agg