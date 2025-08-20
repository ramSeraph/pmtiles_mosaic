# Pmtiles Mosaic
[![PyPI - Latest version](https://img.shields.io/pypi/v/pmtiles_mosaic)](https://pypi.org/project/pmtiles_mosaic/) [![GitHub Tag](https://img.shields.io/github/v/tag/ramSeraph/pmtiles_mosaic?filter=v*)](https://github.com/ramSeraph/pmtiles_mosaic/releases/latest)


Tools to partition and rejoin PMTiles files as mosaics.

This project provides tools to partition large tile sources (like MBTiles, directories of tiles, or other PMTiles files) into smaller, more manageable PMTiles files. It can then generate a mosaic JSON file that allows these partitions to be treated as a single logical PMTiles archive, for example by using a client like `maplibre-gl-js` with the pmtiles plugin.

This is useful for overcoming file size limitations on platforms like GitHub Releases (2GB) or Cloudflare Objects (512MB).

## Installation

Install the tool from PyPI:

```bash
pip install pmtiles-mosaic
```

Or directly from this repository:

```bash
pip install git+https://github.com/ramSeraph/pmtiles_mosaic.git
```

## Usage

The scripts provided by this package are `partition`, `partition-basic`, and `download-mosaic`.

### Running with `uvx`

If you have `uv` installed, you can run the scripts without installing the package using `uvx`:

```bash
uvx --from pmtiles_mosaic partition --from-source <source> --to-pmtiles <output.pmtiles>
uvx --from pmtiles_mosaic partition-basic --from-source <source> --to-pmtiles <output.pmtiles>
uvx --from pmtiles_mosaic download-mosaic --mosaic-url <url> --output-file <output>
```

### `download-mosaic`

This script downloads all the PMTiles partitions listed in a remote mosaic JSON file and merges them into a single, local archive file (either `.mbtiles` or `.pmtiles`). This is useful for reassembling a partitioned tileset for local use or for creating a single-file distribution.

```bash
download-mosaic \
    --mosaic-url <url_to_mosaic.json> \
    --output-file <output_file.mbtiles>
```

**Arguments:**

*   `--mosaic-url`, `-u`: **(Required)** The URL of the remote `.mosaic.json` file.
*   `--output-file`, `-o`: The path for the final output file. The desired archive format is inferred from the file extension (`.mbtiles` or `.pmtiles`). If not provided, the output filename is derived from the mosaic URL.
*   `--archive-type`, `-a`: The type of archive to create (`mbtiles` or `pmtiles`). This is only required if the output file cannot be determined from the `--output-file` argument.
*   `--request-timeout-secs`, `-t`: Timeout for HTTP requests in seconds (default: 60).
*   `--num-http-retries`, `-r`: Number of retries for failed HTTP requests (default: 3).

### `partition`

> [!NOTE]
> This is a new, experimental script and may have bugs. For a more stable and well-tested option, please see `partition-basic`.

This script partitions a tile source into multiple smaller PMTiles files based on a specified size limit. It uses a recursive partitioning strategy, first by zoom level, then by X, and then by Y coordinates, to create reasonably uniform partitions.

```bash
partition \
    --from-source <path_to_source_1> \
    --from-source <path_to_source_2> \
    --to-pmtiles <output_prefix.pmtiles> \
    --size-limit <limit>
```

**Arguments:**

*   `--from-source`: Path to a source file or directory. Can be a `.mbtiles` file, a directory containing tiles in `Z/X/Y.ext` format, or a `.pmtiles` file. When specifying `.pmtiles` files, you can use a glob pattern (e.g., `"partitions/*.pmtiles"`) to include multiple files. This argument can be repeated to merge multiple sources.
*   `--to-pmtiles`: The base name for the output PMTiles files. For example, if you provide `my-map.pmtiles`, the script will generate `my-map-part0000.pmtiles`, `my-map-part0001.pmtiles`, etc., and a `my-map.mosaic.json` file.
*   `--size-limit`: Maximum size for each partition.
    *   Can be a preset: `github_release` (2GB), `github_file` (100MB), `cloudflare_object` (512MB).
    *   Can be a number in bytes, or with a `K`, `M`, or `G` suffix (e.g., `500M`).
    *   Defaults to `github_release`.
*   `--no-cache`: By default, the tool caches tile data in a temporary file to speed up processing. Use this flag to disable caching.

### `partition-basic`

> [!NOTE]
> This script has been in use for longer and is considered more stable and well-tested than the newer `partition` script.

This is a simpler version of the partitioning script that uses a less sophisticated strategy. It first creates a "top slice" of lower zoom levels that fit within the size limit, and then partitions the remaining zoom levels into vertical (X-based) stripes. This can sometimes result in less uniform partition sizes than the `partition` script.

```bash
partition-basic \
    --from-source <path_to_source> \
    --to-pmtiles <output_prefix.pmtiles> \
    --size-limit <limit>
```

**Arguments:**

Most arguments are the same as for the `partition` script, with one addition:

*   `--delta-estimate`: An integer representing the estimated overhead (in bytes) for the PMTiles header, directory, and other metadata. This amount is subtracted from the `--size-limit` to get the target size for the raw tile data. If not provided, it is calculated automatically based on the size limit (e.g., for a 2GB size limit, the delta is ~5MB).

## How it works

The partitioning scripts read tile data from one or more sources, determine the total size, and then divide the tiles into chunks that are each smaller than the specified `size-limit`.

1.  **`partition` (Recursive Strategy):**
    *   It first tries to group tiles by zoom level, starting from the lowest zoom.
    *   If a range of zoom levels exceeds the size limit, it stops and finalizes the previous chunk.
    *   For the remaining zoom levels that are too large to fit in a single partition, it starts dividing them by X coordinates (vertical stripes).
    *   If a single X stripe is still too large, it further subdivides it by Y coordinates.
    *   If a single tile area (at a specific X and Y) is still too large, it will be split by zoom level again.
    *   This recursive process continues until all tiles are assigned to a partition that respects the size limit.

2.  **`partition-basic` (Top-Slice + Striping Strategy):**
    *   It creates a "top slice" containing as many of the lowest zoom levels as possible without exceeding the size limit. If all zoom levels fit, a single PMTiles file is created.
    *   The remaining, higher zoom levels are then partitioned into vertical "stripes" based on their X coordinate. It groups as many adjacent X stripes as possible into a single partition.
    *   **Note:** This basic strategy has limitations. The size of the final PMTiles file is an estimate based on the raw tile data size and does not account for compression or the PMTiles header/directory overhead, so partitions may exceed the target size. Furthermore, if a single vertical (X) stripe is larger than the size limit, the script will fail for that stripe.

After partitioning, both scripts generate a `.mosaic.json` file. This file contains the metadata for the entire tileset and a list of the generated PMTiles partitions along with their bounding boxes and zoom ranges. This allows clients that support the mosaic format to request tiles seamlessly from the correct partition. For more details, see the [**Mosaic JSON Specification (spec.md)**](./spec.md).


## Tile Sources

The `pmtiles_mosaic.tile_sources` module provides classes for reading tiles from different sources.

-   `DiskTilesSource`: Reads tiles from a directory on the local disk.
-   `MBTilesSource`: Reads tiles from an MBTiles file.
-   `PMTilesSource`: Reads tiles from a PMTiles file.
-   `StackedTileSource`: Combines multiple tile sources (Disk, MBTiles, PMTiles) into a single logical source, allowing for seamless access across different storage types.

## Client Code

The only known implementation of a javascript client for the mosaics is in [indianopenmaps](https://github.com/ramSeraph/indianopenmaps/blob/main/server/mosaic_handler.js)


## Contributing

Contributions are welcome! If you find a bug or have a feature request, please open an issue or submit a pull request.

## License

This project is under UNLICENSE. See the [LICENSE](./LICENSE) file for details.
