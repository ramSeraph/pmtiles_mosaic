import sqlite3
import json

import mercantile

from .tiles_common import MissingTileError, INTERESTED_METADATA_KEYS
from .logger import LoggerMixin

class MBTilesSource(LoggerMixin):
    def __init__(self, fname, logger=None):
        self.logger = logger
        self.con = sqlite3.connect(fname)
        self._full_metadata = None

    def _to_xyz(self, x, y, z):
        y = (1 << z) - 1 - y
        return x, y, z

    def get_tile_data(self, tile):
        x, y, z = self._to_xyz(tile.x, tile.y, tile.z)
        res = self.con.execute(f'select tile_data from tiles where zoom_level={z} and tile_column={x} and tile_row={y};')
        out = res.fetchone()
        if not out:
            raise MissingTileError()
        return out[0]

    def get_tile_size(self, tile):
        data = self.get_tile_data(tile)
        return len(data)
 
    def all_sizes_z(self, z):
        res = self.con.execute(f'select tile_column, tile_row, length(tile_data) from tiles where zoom_level={z};')
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

    def all_z_sizes(self):
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
                json_data = json.loads(v)
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

        return metadata

            
    def _get_meta_prop(self, prop_name):
        full_metadata = self.get_full_metadata()
        if prop_name in full_metadata:
            return full_metadata[prop_name]

        raise ValueError(f"Source does not have a {prop_name} property.")

    @property
    def min_zoom(self):
        return int(self._get_meta_prop('minzoom'))

    @property
    def max_zoom(self):
        return int(self._get_meta_prop('maxzoom'))


