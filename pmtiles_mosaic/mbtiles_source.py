import sqlite3
from pathlib import Path

import mercantile

from .errors import EmptySourceError
from .tiles_common import (
    MissingTileError, INTERESTED_METADATA_KEYS, get_tile_type,
    parse_metadata_json, parse_metadata_zoom,
)
from .logger import LoggerMixin

class MBTilesSource(LoggerMixin):
    def __init__(self, fname, logger=None):
        self.logger = logger
        self.path = Path(fname)
        self.path.stat()
        self.con = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        self._full_metadata = None

    def _to_xyz(self, x, y, z):
        y = (1 << z) - 1 - y
        return x, y, z

    def get_tile_data(self, tile):
        x, y, z = self._to_xyz(tile.x, tile.y, tile.z)
        res = self.con.execute(
            'SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?',
            (z, x, y),
        )
        out = res.fetchone()
        if not out:
            raise MissingTileError()
        return out[0]

    def get_tile_size(self, tile):
        data = self.get_tile_data(tile)
        return len(data)
 
    def all_z_sizes(self, z):
        res = self.con.execute(
            'SELECT tile_column, tile_row, length(tile_data) FROM tiles WHERE zoom_level=?',
            (z,),
        )
        while True:
            t = res.fetchone()
            if not t:
                break
            x, y, z = self._to_xyz(t[0], t[1], z)
            tile_size = t[2]
            tile = mercantile.Tile(x=x, y=y, z=z)
            yield (tile, tile_size)

    def all(self):
        res = self.con.execute('select zoom_level, tile_column, tile_row, tile_data from tiles;')
        while True:
            t = res.fetchone()
            if not t:
                break
            x, y, z = self._to_xyz(t[1], t[2], t[0])
            data = t[3]
            tile = mercantile.Tile(x=x, y=y, z=z)
            yield (tile, data)

    def all_sizes(self):
        res = self.con.execute('select zoom_level, tile_column, tile_row, length(tile_data) from tiles;')
        while True:
            t = res.fetchone()
            if not t:
                break
            x, y, z = self._to_xyz(t[1], t[2], t[0])
            tile_size = t[3]
            tile = mercantile.Tile(x=x, y=y, z=z)
            yield (tile, tile_size)

    def cleanup(self):
        self.con.close()

    def get_full_metadata(self):
        if self._full_metadata is not None:
            return self._full_metadata

        all_metadata = {}
        for row in self.con.execute("SELECT name,value FROM metadata"):
            k = row[0]
            v = row[1]
            if k == 'json':
                json_data = parse_metadata_json(v, self.path)
                for k, v in json_data.items():
                    all_metadata[k] = v
                continue
            all_metadata[k] = v

        self._full_metadata = all_metadata
        return self._full_metadata

    def get_metadata(self):
        full_metadata = self.get_full_metadata()

        metadata = {}
        for k in INTERESTED_METADATA_KEYS:
            if k not in full_metadata:
                continue
            metadata[k] = full_metadata[k]

        if 'format' in metadata:
            get_tile_type(metadata['format'], self.path)
        return metadata

            
    def _get_zoom(self, prop_name, aggregate):
        full_metadata = self.get_full_metadata()
        if prop_name in full_metadata:
            return parse_metadata_zoom(full_metadata[prop_name], prop_name, self.path)
        # Zoom metadata is optional. Distinguish an empty tiles table from a
        # populated source that simply omits the corresponding metadata row.
        query = {'min': 'SELECT MIN(zoom_level) FROM tiles', 'max': 'SELECT MAX(zoom_level) FROM tiles'}[aggregate]
        value = self.con.execute(query).fetchone()[0]
        if value is None:
            raise EmptySourceError(f'No tiles found in {self.path}')
        return parse_metadata_zoom(value, prop_name, self.path)

    @property
    def min_zoom(self):
        return self._get_zoom('minzoom', 'min')

    @property
    def max_zoom(self):
        return self._get_zoom('maxzoom', 'max')


