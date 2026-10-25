"""Regenerate the small interoperability fixture with the repository's Python PMTiles writer.

Run from the repository root: uv run python js/test/fixtures/generate.py
Payloads are opaque test bytes; the reader does not parse MVT features.
"""
import gzip
from pathlib import Path

from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

with Path(__file__).with_name("sample.pmtiles").open("wb") as out:
    writer = Writer(out)
    for zxy, content in [((0, 0, 0), b"world tile"), ((1, 1, 1), b"southeast tile")]:
        writer.write_tile(zxy_to_tileid(*zxy), gzip.compress(content, mtime=0))
    writer.finalize({
        "tile_type": TileType.MVT,
        "tile_compression": Compression.GZIP,
        "min_lon_e7": -1800000000,
        "min_lat_e7": -850511287,
        "max_lon_e7": 1800000000,
        "max_lat_e7": 850511287,
        "center_lon_e7": 0,
        "center_lat_e7": 0,
        "center_zoom": 0,
    }, {"format": "mvt"})
