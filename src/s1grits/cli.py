"""
CLI with subcommands structure

Provides a professional CLI interface with subcommands:
- s1grits process  --config config.yaml
- s1grits catalog  rebuild  --output-dir ./output
- s1grits catalog  validate --output-dir ./output
- s1grits catalog  inspect  --output-dir ./output
- s1grits tile     inspect  --tile 50RKV --output-dir ./output
- s1grits tile     inspect  --tile 50RKV --direction ASCENDING --output-dir ./output
- s1grits mosaic   --month 2024-01 --direction ASCENDING
"""

import argparse
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
import rioxarray  # Register .rio accessor for xarray

from s1grits.logger_config import get_logger

logger = get_logger(__name__)
console = Console(legacy_windows=True, no_color=False)


def print_summary(results: dict):
    """
    Print processing results summary (using Rich for formatting)

    Args:
        results: Processing results dictionary
    """
    console.print("\n")
    console.rule(f"[bold blue]Processing Results Summary[/bold blue]", style="blue")

    # Statistics
    success_count = sum(1 for r in results.values() if r['status'] == 'success')
    failed_count = sum(1 for r in results.values() if r['status'] == 'failed')

    # Create summary table
    summary_table = Table(title="Overall Statistics", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", justify="right", style="green")

    summary_table.add_row("Total MGRS Tiles", str(len(results)))
    summary_table.add_row("Success", f"[green]{success_count}[/green]")
    summary_table.add_row("Failed", f"[red]{failed_count}[/red]")
    if len(results) > 0:
        summary_table.add_row("Success Rate", f"{success_count/len(results)*100:.1f}%")

    console.print(summary_table)

    # Detailed results table
    detail_table = Table(title=f"\nDetailed Results", show_header=True, header_style="bold cyan")
    detail_table.add_column("MGRS Tile", style="cyan", width=12)
    detail_table.add_column("Status", justify="center", width=6)
    detail_table.add_column("Months", justify="right", width=7)
    detail_table.add_column("Size", justify="right", width=10)
    detail_table.add_column("Path/Error", style="dim")

    def get_dir_size(path):
        """Calculate total directory size (GB)"""
        import os
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    if os.path.exists(filepath):
                        total += os.path.getsize(filepath)
        except Exception as _e:
            logger.debug("Could not compute directory size for %s: %s", path, _e)
            return 0.0
        return total / (1024**3)  # Convert to GB

    for mgrs_id, result in sorted(results.items()):
        if result['status'] == 'success':
            status_icon = "[green]OK[/green]"
            months_count = str(len(result['written_months']))
            tile_dir = result.get('tile_dir', result.get('zarr_path', 'N/A'))
            path_info = tile_dir

            import os
            if tile_dir and tile_dir != 'N/A' and os.path.exists(tile_dir):
                size_gb = get_dir_size(tile_dir)
                size_str = f"{size_gb:.2f} GB" if size_gb >= 0.01 else f"{size_gb*1024:.1f} MB"
            else:
                size_str = "-"
        else:
            status_icon = "[red]FAIL[/red]"
            months_count = "-"
            size_str = "-"
            err_msg = str(result.get('error', 'Unknown error'))
            path_info = f"[red]{err_msg[:50]}...[/red]" if len(err_msg) > 50 else f"[red]{err_msg}[/red]"

        detail_table.add_row(mgrs_id, status_icon, months_count, size_str, path_info)

    console.print(detail_table)
    console.rule(style="blue")


def cmd_process(args):
    """Run the main processing workflow"""
    from s1grits.workflow import run_multi_mgrs_monthly_workflow
    from s1grits.logger_config import setup_logging, get_logger
    import pandas as pd
    import yaml

    config_path = Path(args.config)
    if not config_path.exists():
        console.print(f"[red]ERROR: Config file does not exist: {config_path}[/red]")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    console.rule("[bold cyan]S1-GRiTS: Sentinel-1 Gridded Time Series[/bold cyan]", style="cyan")
    console.print(f"[dim]Config: {config_path}[/dim]\n")

    log_file, logger = setup_logging(config)
    console.print(f"[dim]Log: {log_file}[/dim]\n")

    logger.info("Starting workflow: %s", config_path)
    start_time = pd.Timestamp.now()

    console.print("[dim]Processing...[/dim]")
    results = run_multi_mgrs_monthly_workflow(config_path)

    end_time = pd.Timestamp.now()
    duration = end_time - start_time

    print_summary(results)

    logger.info("Workflow completed in %s", duration)
    console.print(f"\nTotal time: [bold]{duration}[/bold]")

    if all(r['status'] == 'success' for r in results.values()):
        console.print(f"\n[bold green]Success![/bold green]\n")
        sys.exit(0)
    else:
        console.print(f"\n[bold yellow]WARNING: Some tasks failed[/bold yellow]\n")
        sys.exit(1)


def cmd_catalog_rebuild(args):
    """Rebuild global catalog from COG files, then resync STAC Items and collection.json"""
    from s1grits.analysis import rebuild_global_catalog

    output_dir = args.output_dir

    console.rule("[bold cyan]Rebuild Catalog + STAC[/bold cyan]", style="cyan")
    console.print(f"[dim]Output directory: {output_dir}[/dim]\n")

    result = rebuild_global_catalog(output_dir)

    if result['success']:
        console.print(f"[green]INFO   Catalog rebuilt successfully[/green]")
        console.print(f"[dim]       Tiles:   {result['tile_count']}[/dim]")
        console.print(f"[dim]       Records: {result['total_records']}[/dim]")
        console.print(f"[dim]       Catalog: {result['catalog_path']}[/dim]")
        sys.exit(0)
    else:
        console.print(f"[red]ERROR  {result['message']}[/red]")
        sys.exit(1)


def cmd_catalog_validate(args):
    """Validate catalog schema integrity and STAC Item alignment"""
    from s1grits.analysis import validate_catalog
    import pandas as pd

    output_dir = Path(args.output_dir)
    catalog_path = output_dir / 'catalog.parquet'

    console.rule("[bold cyan]Validate Catalog[/bold cyan]", style="cyan")
    console.print(f"[dim]Catalog: {catalog_path}[/dim]\n")

    result = validate_catalog(catalog_path)

    # --- STAC alignment check (CLI layer) ---
    if catalog_path.exists():
        try:
            df = pd.read_parquet(catalog_path)
            stac_missing = []
            for _, row in df.iterrows():
                tile_id = row['mgrs_tile_id']
                direction = row.get('flight_direction', '') or ''
                month = str(row['month'])
                suffix = f"_{direction}" if direction else ""
                item_id = f"{tile_id}{suffix}_{month}"
                item_path = output_dir / f"{tile_id}{suffix}" / f"{item_id}.json"
                if not item_path.exists():
                    stac_missing.append(item_id)

            if stac_missing:
                result.setdefault('warnings', [])
                result['warnings'].append(
                    f"{len(stac_missing)} STAC Item JSON(s) missing on disk — "
                    "run 's1grits catalog rebuild' to resync."
                )
                for item_id in stac_missing[:5]:
                    result['warnings'].append(f"  Missing: {item_id}.json")
                if len(stac_missing) > 5:
                    result['warnings'].append(f"  ... and {len(stac_missing) - 5} more")
        except Exception as _e:
            logger.debug("STAC alignment check skipped: %s", _e)

    # --- Display results ---
    if result['valid']:
        console.print(f"[green]INFO   Catalog schema is valid[/green]")
        console.print(f"[dim]       Records: {result.get('record_count', '?')}[/dim]")
    else:
        issues = result.get('issues', [])
        first_issue = issues[0] if issues else "Unknown validation failure"
        console.print(f"[red]ERROR  {first_issue}[/red]")
        for issue in issues[1:]:
            console.print(f"[red]       {issue}[/red]")

    warnings = result.get('warnings', [])
    for warning in warnings:
        console.print(f"[yellow]WARN   {warning}[/yellow]")

    sys.exit(0 if result['valid'] else 1)


def cmd_catalog_inspect(args):
    """Show global coverage summary across all tiles and directions"""
    from s1grits.analysis.reporting import generate_coverage_report

    output_dir = args.output_dir

    console.rule("[bold cyan]Catalog Coverage[/bold cyan]", style="cyan")
    console.print(f"[dim]Output directory: {output_dir}[/dim]\n")

    result = generate_coverage_report(output_dir)

    if not result['success']:
        console.print(f"[red]ERROR  {result['message']}[/red]")
        sys.exit(1)

    # Overall summary
    overall = result['overall']
    console.print(f"Total records:  {overall['total_records']}")
    console.print(f"MGRS tiles:     {overall['tile_count']}")
    console.print(f"Date range:     {overall['date_range'][0]} to {overall['date_range'][1]}")
    console.print(f"Total months:   {overall['total_months']}")
    if 'directions' in overall:
        console.print(f"Directions:     {', '.join(str(d) for d in overall['directions'])}")

    # Coverage table
    table = Table(title="\nCoverage by Tile", show_header=True, header_style="bold cyan")
    table.add_column("Tile",      style="cyan", width=10)
    table.add_column("Direction",              width=12)
    table.add_column("Months",    justify="right", width=7)
    table.add_column("Expected",  justify="right", width=9)
    table.add_column("Missing",   justify="right", width=8)
    table.add_column("Complete",  justify="right", width=9)
    table.add_column("Range",                  width=18)

    for tile in result['tiles']:
        completeness = tile['completeness']
        color = "green" if completeness == 100.0 else ("yellow" if completeness >= 80.0 else "red")
        direction = str(tile.get('direction') or '-')
        table.add_row(
            tile['tile_id'],
            direction,
            str(tile['months']),
            str(tile['expected_months']),
            str(tile['missing_months']),
            f"[{color}]{completeness:.1f}%[/{color}]",
            f"{tile['start_date']} ~ {tile['end_date']}",
        )

    console.print(table)

    gaps = result['gaps']
    if gaps['tiles_with_gaps'] > 0:
        console.print(
            f"\n[yellow]WARN   {gaps['tiles_with_gaps']}/{gaps['total_tiles']} "
            f"tile-direction(s) have temporal gaps[/yellow]"
        )
    else:
        console.print(
            f"\n[green]INFO   All {gaps['total_tiles']} tile-direction(s) are complete[/green]"
        )

    sys.exit(0)


def cmd_tile_inspect(args):
    """Show detailed temporal completeness for a single MGRS tile"""
    import pandas as pd
    from s1grits.analysis.reporting import analyze_temporal_gaps

    output_dir = Path(args.output_dir)
    tile_id = args.tile
    filter_direction = args.direction.upper() if getattr(args, 'direction', None) else None

    catalog_path = output_dir / 'catalog.parquet'
    if not catalog_path.exists():
        console.print(f"[red]ERROR  Catalog not found: {catalog_path}[/red]")
        console.print(f"[dim]       Run: s1grits catalog rebuild --output-dir {output_dir}[/dim]")
        sys.exit(1)

    catalog = pd.read_parquet(catalog_path)
    tile_data = catalog[catalog['mgrs_tile_id'] == tile_id]

    if len(tile_data) == 0:
        console.print(f"[red]ERROR  No data found for tile {tile_id}[/red]")
        # Show available tiles as hint
        available = sorted(catalog['mgrs_tile_id'].unique().tolist())
        console.print(f"[dim]       Available tiles: {', '.join(available)}[/dim]")
        sys.exit(1)

    # Filter by direction if --direction was specified
    direction_col = 'flight_direction' if 'flight_direction' in tile_data.columns else None
    if filter_direction and direction_col:
        tile_data = tile_data[tile_data[direction_col] == filter_direction]
        if len(tile_data) == 0:
            available_dirs = sorted(catalog[catalog['mgrs_tile_id'] == tile_id][direction_col].dropna().unique().tolist())
            console.print(f"[red]ERROR  No data found for tile {tile_id} direction {filter_direction}[/red]")
            console.print(f"[dim]       Available directions for this tile: {', '.join(available_dirs)}[/dim]")
            sys.exit(1)

    title = f"Tile: {tile_id}" + (f"  |  {filter_direction}" if filter_direction else "")

    directions = sorted(tile_data[direction_col].dropna().unique()) if direction_col else [None]

    # Collect all output lines first, then print once to avoid Rich re-render artifacts
    lines = []
    sep = "─" * 60
    lines.append(f"\n{sep} {title} {sep}")

    for direction in directions:
        if direction is not None:
            lines.append(f"\n{direction}")

        gaps = analyze_temporal_gaps(tile_data, tile_id=tile_id, direction=direction)

        completeness = gaps['completeness']

        lines.append(f"  Present months:  {gaps['present_months']}")
        lines.append(f"  Expected months: {gaps['total_months']}")
        lines.append(f"  Date range:      {gaps['date_range'][0]} ~ {gaps['date_range'][1]}")
        lines.append(f"  Completeness:    {completeness:.1f}%")

        if gaps['missing_list']:
            lines.append(f"\n  Missing months ({len(gaps['missing_list'])}):")
            for month_str in gaps['missing_list']:
                if direction:
                    cog_path = (
                        output_dir / f"{tile_id}_{direction}" / "cog"
                        / f"{tile_id}_S1_Monthly_{direction}_{month_str}.tif"
                    )
                    if cog_path.exists():
                        lines.append(
                            f"    - {month_str}"
                            f"  (COG exists but missing from catalog -- run rebuild)"
                        )
                    else:
                        lines.append(f"    - {month_str}  (no source data)")
                else:
                    lines.append(f"    - {month_str}")
        else:
            lines.append("\n  No missing months -- complete time series")

    lines.append(sep)
    print("\n".join(lines))
    sys.exit(0)


def cmd_mosaic(args):
    """Create a multi-tile mosaic VRT or COG for a given month"""
    from s1grits.analysis import create_mosaic_vrt, find_cog_files_for_mosaic

    output_dir = Path(args.output_dir).resolve()

    console.rule("[bold cyan]Create Mosaic[/bold cyan]", style="cyan")
    console.print(f"[dim]Month: {args.month}, Direction: {args.direction}[/dim]\n")

    try:
        cog_files = find_cog_files_for_mosaic(
            month=args.month,
            direction=args.direction,
            output_root=str(output_dir),
            mgrs_prefix=args.mgrs_prefix,
        )

        if not cog_files:
            console.print("[red]ERROR  No COG files found[/red]")
            console.print(f"[yellow]WARN   Searched in: {output_dir}[/yellow]")
            sys.exit(1)

        console.print(f"[dim]Found {len(cog_files)} COG file(s)[/dim]")

        # Resolve output directory for mosaic files
        if args.output:
            mosaic_output_dir = Path(args.output)
        else:
            mosaic_output_dir = Path("analysis_results") / "mosaic"
        mosaic_output_dir.mkdir(parents=True, exist_ok=True)

        # Resolve target CRS: --keep-utm overrides --crs
        target_crs = None if args.keep_utm else args.crs

        allow_mixed = (args.direction == "ALL")

        result_path = create_mosaic_vrt(
            cog_files,
            output_dir=str(mosaic_output_dir),
            output_format=args.format,
            target_crs=target_crs,
            allow_mixed_directions=allow_mixed,
        )

        if not result_path:
            console.print("[red]ERROR  Failed to create mosaic[/red]")
            sys.exit(1)

        console.print(f"[green]INFO   Mosaic created: {result_path}[/green]")
        sys.exit(0)

    except Exception as e:
        console.print(f"[red]ERROR  {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        prog='s1grits',
        description='S1-GRiTS: Sentinel-1 Grid Time Series Processor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
Examples:
  # Run processing workflow
  s1grits process --config config.yaml

  # Catalog management (rebuild also resyncs STAC Items + collection.json)
  s1grits catalog rebuild  --output-dir ./output
  s1grits catalog validate --output-dir ./output
  s1grits catalog inspect  --output-dir ./output

  # Single tile temporal completeness
  s1grits tile inspect --tile 50RKV --output-dir ./output

  # Create multi-tile mosaic (default: EPSG:4326, VRT format)
  s1grits mosaic --month 2024-01 --direction ASCENDING
  s1grits mosaic --month 2024-01 --direction ASCENDING --crs EPSG:3857
  s1grits mosaic --month 2024-01 --direction ASCENDING --keep-utm
  s1grits mosaic --month 2024-01 --direction ASCENDING --format COG
  s1grits mosaic --month 2024-01 --direction ALL
  s1grits mosaic --month 2024-01 --direction ASCENDING --mgrs-prefix 50R
        '''
    )

    parser.add_argument('--version', action='version', version='s1grits 1.0.0')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # ── process ──────────────────────────────────────────────────────────────
    parser_process = subparsers.add_parser(
        'process',
        help='Run the full processing workflow from a YAML config'
    )
    parser_process.add_argument('--config', required=True, help='Path to YAML config file')
    parser_process.set_defaults(func=cmd_process)

    # ── catalog ───────────────────────────────────────────────────────────────
    parser_catalog = subparsers.add_parser('catalog', help='Catalog management')
    catalog_sub = parser_catalog.add_subparsers(dest='catalog_cmd', help='Catalog operations')

    # catalog rebuild
    p = catalog_sub.add_parser(
        'rebuild',
        help='Rebuild catalog.parquet from COG files and resync STAC Items + collection.json'
    )
    p.add_argument('--output-dir', required=True, help='Output root directory')
    p.set_defaults(func=cmd_catalog_rebuild)

    # catalog validate
    p = catalog_sub.add_parser(
        'validate',
        help='Validate catalog schema and check STAC Item alignment'
    )
    p.add_argument('--output-dir', required=True, help='Output root directory')
    p.set_defaults(func=cmd_catalog_validate)

    # catalog inspect
    p = catalog_sub.add_parser(
        'inspect',
        help='Show global coverage summary (completeness per tile and direction)'
    )
    p.add_argument('--output-dir', required=True, help='Output root directory')
    p.set_defaults(func=cmd_catalog_inspect)

    # ── tile ──────────────────────────────────────────────────────────────────
    parser_tile = subparsers.add_parser('tile', help='Tile-level operations')
    tile_sub = parser_tile.add_subparsers(dest='tile_cmd', help='Tile operations')

    # tile inspect
    p = tile_sub.add_parser(
        'inspect',
        help='Show detailed temporal completeness for a single MGRS tile'
    )
    p.add_argument('--tile', required=True, help='MGRS tile ID (e.g., 50RKV)')
    p.add_argument('--direction', required=False, default=None,
                   choices=['ASCENDING', 'DESCENDING', 'ascending', 'descending'],
                   help='Filter by orbit direction (optional, shows all directions if omitted)')
    p.add_argument('--output-dir', required=True, help='Output root directory')
    p.set_defaults(func=cmd_tile_inspect)

    # ── mosaic ────────────────────────────────────────────────────────────────
    parser_mosaic = subparsers.add_parser(
        'mosaic',
        help='Create a multi-tile mosaic VRT or COG for a given month'
    )
    parser_mosaic.add_argument('--month', required=True, help='Month to mosaic (YYYY-MM)')
    parser_mosaic.add_argument(
        '--direction', required=True,
        choices=['ASCENDING', 'DESCENDING', 'ALL'],
        help=(
            'Flight direction. '
            'ALL: ASCENDING has pixel-level priority; DESCENDING fills NoData gaps.'
        )
    )
    parser_mosaic.add_argument(
        '--output-dir', default='./output',
        help='Source output directory containing tile subdirectories (default: ./output)'
    )
    parser_mosaic.add_argument(
        '--output',
        help='Destination directory for mosaic output (default: analysis_results/mosaic/)'
    )
    parser_mosaic.add_argument(
        '--format', choices=['VRT', 'COG'], default='VRT',
        help='Output format (default: VRT)'
    )
    parser_mosaic.add_argument(
        '--crs', default='EPSG:4326',
        help='Target CRS for reprojection (default: EPSG:4326). Ignored when --keep-utm is set.'
    )
    parser_mosaic.add_argument(
        '--keep-utm', action='store_true',
        help='Keep original per-tile UTM projection; skip reprojection'
    )
    parser_mosaic.add_argument(
        '--mgrs-prefix',
        help='Filter tiles by MGRS prefix (e.g., 50R includes 50RKU, 50RKV, …)'
    )
    parser_mosaic.set_defaults(func=cmd_mosaic)

    # ── dispatch ──────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not hasattr(args, 'func'):
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print(f"\n[yellow]WARNING: Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]ERROR: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
