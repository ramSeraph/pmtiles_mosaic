import logging
import json
import sqlite3
from pathlib import Path

from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid

from pmtiles_mosaic.partition import HEADER_EXPORT_KEYS, SLICE_HEADER_EXPORT_KEYS, convert_header, get_header, get_header_base
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from pmtiles_mosaic.tiles_common import MissingTileError


LOGGER = logging.getLogger('pmtiles_mosaic.tests')
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False


class MemorySource:
    def __init__(self, tiles, metadata=None):
        self.tiles = dict(tiles)
        self.metadata = metadata if metadata is not None else {'format': 'png'}
        self.min_zoom = min((t.z for t in self.tiles), default=0)
        self.max_zoom = max((t.z for t in self.tiles), default=0)
        self.closed = False

    def get_metadata(self):
        return self.metadata

    def get_tile_data(self, tile):
        try:
            return self.tiles[tile]
        except KeyError:
            raise MissingTileError() from None

    def get_tile_size(self, tile):
        return len(self.get_tile_data(tile))

    def all(self):
        return iter(self.tiles.items())

    def all_sizes(self):
        return ((tile, len(data)) for tile, data in self.tiles.items())

    def all_z_sizes(self, z):
        return ((tile, size) for tile, size in self.all_sizes() if tile.z == z)

    def cleanup(self):
        self.closed = True


def write_mbtiles(path, tiles, tile_format='png'):
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
        connection.execute(
            'CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, '
            'tile_row INTEGER, tile_data BLOB)'
        )
        connection.executemany('INSERT INTO metadata VALUES (?, ?)', [
            ('format', tile_format),
            ('minzoom', str(min(t.z for t in tiles))),
            ('maxzoom', str(max(t.z for t in tiles))),
        ])
        connection.executemany('INSERT INTO tiles VALUES (?, ?, ?, ?)', [
            (tile.z, tile.x, (1 << tile.z) - 1 - tile.y, data)
            for tile, data in tiles.items()
        ])
        connection.commit()
    finally:
        connection.close()


def write_pmtiles(path, tiles, metadata=None):
    metadata = metadata if metadata is not None else {'format': 'png'}
    with Path(path).open('wb') as output:
        writer = Writer(output)
        try:
            for tile, data in tiles.items():
                writer.write_tile(zxy_to_tileid(tile.z, tile.x, tile.y), data)
            writer.finalize(get_header(tiles, get_header_base(metadata)), metadata)
        finally:
            writer.tile_f.close()


def read_pmtiles(path):
    source = PMTilesSource(path)
    try:
        return dict(source.all())
    finally:
        source.cleanup()


def write_mosaic(root, slices, metadata=None):
    metadata = metadata if metadata is not None else {'format': 'png'}
    header_base = get_header_base(metadata)
    entries = {}
    all_tiles = {}
    for index, tiles in enumerate(slices):
        name = f'part{index}.pmtiles'
        write_pmtiles(root / name, tiles, metadata)
        entries[name] = {'header': convert_header(get_header(tiles, header_base, True), SLICE_HEADER_EXPORT_KEYS)}
        all_tiles.update(tiles)
    document = {
        'version': 1, 'metadata': metadata,
        'header': convert_header(get_header(all_tiles, header_base), HEADER_EXPORT_KEYS),
        'slices': entries,
    }
    path = root / 'map.mosaic.json'
    path.write_text(json.dumps(document))
    return path
