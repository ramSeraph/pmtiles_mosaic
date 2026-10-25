# PMTiles Mosaic JSON Specification

This document defines version `1` of the PMTiles Mosaic JSON format. A
`.mosaic.json` file describes one logical tileset spanning several PMTiles files,
called **slices**.

## 1. Top-Level Object

The document is a JSON object with four required properties:

| Property | JSON type | Description |
| --- | --- | --- |
| `version` | integer | Always `1`. This is the mosaic format version, separate from the PMTiles archive version and `metadata.version`. |
| `metadata` | object | Metadata describing the logical tileset. |
| `header` | object | Bounds, zoom range, center, tile type, and tile compression for the combined tileset. |
| `slices` | object | A mapping from each slice filename to a [slice object](#4-slice-object). |

## 2. Metadata Object

The `metadata` object describes the logical tileset. `format` is required; the
remaining fields below are optional. Additional metadata properties may be present.

| Property | JSON type | Description |
| --- | --- | --- |
| `name` | string | Human-readable tileset name. |
| `description` | string | Description of the tileset's contents. |
| `attribution` | string | Attribution text for display. |
| `format` | string | Tile format: `mvt`, `png`, `jpg`, `webp`, `avif`, or `mlt`. The `pbf` and `jpeg` aliases are not accepted. |
| `type` | string | Tileset type, commonly `overlay` or `baselayer`. |
| `version` | string | Version of the tileset, separate from the mosaic format version. |
| `vector_layers` | array of objects | Vector layer definitions, including fields such as `id`, `minzoom`, `maxzoom`, and `fields`. |

## 3. Header Object

The header represents a subset of the fields defined by
[PMTiles v3](https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md), with
the same numeric enum values for tile type and compression.

The `header` object contains eleven required integer fields:

| Property | Description |
| --- | --- |
| `min_lon_e7` | Western bound, in E7 degrees. |
| `min_lat_e7` | Southern bound, in E7 degrees. |
| `max_lon_e7` | Eastern bound, in E7 degrees. |
| `max_lat_e7` | Northern bound, in E7 degrees. |
| `min_zoom` | Minimum zoom level of the tileset, inclusive. |
| `max_zoom` | Maximum zoom level of the tileset, inclusive. |
| `center_lon_e7` | Suggested initial view longitude, in E7 degrees. |
| `center_lat_e7` | Suggested initial view latitude, in E7 degrees. |
| `center_zoom` | Suggested initial view zoom level. |
| `tile_type` | Numeric tile format identifier; see below. |
| `tile_compression` | Numeric tile payload compression identifier; see below. |

### 3.1 Bounds and Center

The top-level bounds describe the tileset's geographic extent at `max_zoom`.
The center fields specify a suggested initial map view.

Coordinates with the `_e7` suffix represent longitude or latitude in degrees
scaled by `10,000,000`. Divide an E7 value by `10,000,000` to recover degrees.
For example, `125000000` means `12.5` degrees.

### 3.2 Tile Type and Compression

`tile_type` identifies the format of the tiles:

| Value | Tile format |
| --- | --- |
| `0` | Unknown |
| `1` | MVT (`mvt`) |
| `2` | PNG |
| `3` | JPEG (`jpg`) |
| `4` | WebP |
| `5` | AVIF |
| `6` | MLT |

`tile_compression` identifies compression of the tile payloads, using the
[PMTiles compression enum](https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md#33-compression):

| Value | Compression |
| --- | --- |
| `0` | Unknown |
| `1` | None |
| `2` | gzip |
| `3` | Brotli |
| `4` | Zstandard |

Tile payload compression is separate from a raster image's own encoding and from
PMTiles directory or metadata compression.

## 4. Slice Object

Each key in `slices` is a PMTiles filename relative to the mosaic file's directory
or URL. For example, `https://example.com/maps/my-map.mosaic.json` and the key
`my-map-part0000.pmtiles` identify
`https://example.com/maps/my-map-part0000.pmtiles`.

Each value contains a single `header` object with six required integer fields:

| Property | Description |
| --- | --- |
| `min_lon_e7` | Western bound, in E7 degrees. |
| `min_lat_e7` | Southern bound, in E7 degrees. |
| `max_lon_e7` | Eastern bound, in E7 degrees. |
| `max_lat_e7` | Northern bound, in E7 degrees. |
| `min_zoom` | Minimum zoom level in the slice, inclusive. |
| `max_zoom` | Maximum zoom level in the slice, inclusive. |

Slice bounds describe the slice's geographic extent at its `min_zoom`, using the
same E7 units as the top-level header. A bounding box and zoom range do not imply
that every tile within them exists.

The top-level header describes the tile type and compression for all slices.

## 5. Example

This example describes gzip-compressed MVT tiles in three slices: worldwide
coverage at zooms `0` through `8`, and western and eastern hemisphere coverage at
zooms `9` through `14`.

```json
{
  "version": 1,
  "metadata": {
    "name": "My Awesome Map",
    "description": "A description of the content of the tileset.",
    "attribution": "Attribution required to be displayed.",
    "format": "mvt",
    "type": "baselayer",
    "version": "1",
    "vector_layers": [
      {
        "id": "landuse",
        "minzoom": 0,
        "maxzoom": 14,
        "fields": {
          "class": "String"
        }
      }
    ]
  },
  "header": {
    "min_lon_e7": -1800000000,
    "min_lat_e7": -850511287,
    "max_lon_e7": 1800000000,
    "max_lat_e7": 850511287,
    "min_zoom": 0,
    "max_zoom": 14,
    "center_zoom": 7,
    "center_lon_e7": 0,
    "center_lat_e7": 0,
    "tile_type": 1,
    "tile_compression": 2
  },
  "slices": {
    "my-map-part0000.pmtiles": {
      "header": {
        "min_lon_e7": -1800000000,
        "min_lat_e7": -850511287,
        "max_lon_e7": 1800000000,
        "max_lat_e7": 850511287,
        "min_zoom": 0,
        "max_zoom": 8
      }
    },
    "my-map-part0001.pmtiles": {
      "header": {
        "min_lon_e7": -1800000000,
        "min_lat_e7": -850511287,
        "max_lon_e7": 0,
        "max_lat_e7": 850511287,
        "min_zoom": 9,
        "max_zoom": 14
      }
    },
    "my-map-part0002.pmtiles": {
      "header": {
        "min_lon_e7": 0,
        "min_lat_e7": -850511287,
        "max_lon_e7": 1800000000,
        "max_lat_e7": 850511287,
        "min_zoom": 9,
        "max_zoom": 14
      }
    }
  }
}
```
