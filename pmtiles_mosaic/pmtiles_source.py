
import mercantile

from pmtiles.reader import (
    MmapSource, 
    Reader as PMTilesReader, 
    all_tiles,
)

from pmtiles.tile import (
    deserialize_header,
    deserialize_directory,
    tileid_to_zxy,
)

from .tiles_common import MissingTileError
from .logger import LoggerMixin

# pmtiles sources
def traverse_sizes(get_bytes, header, dir_offset, dir_length):
    entries = deserialize_directory(get_bytes(dir_offset, dir_length))
    for entry in entries:
        if entry.run_length > 0:
            for i in range(entry.run_length):
                yield tileid_to_zxy(entry.tile_id + i), entry.length
        else:
            for t in traverse_sizes(
                get_bytes,
                header,
                header["leaf_directory_offset"] + entry.offset,
                entry.length,
            ):
                yield t

def all_tile_sizes(get_bytes):
    header = deserialize_header(get_bytes(0, 127))
    return traverse_sizes(get_bytes, header, header["root_offset"], header["root_length"])

class PMTilesSource(LoggerMixin):
    def __init__(self, fname, logger=None):
        self.logger = logger
        self.file = open(fname, 'rb')
        self.src = MmapSource(self.file)
        self.reader = PMTilesReader(self.src)

    def get_tile_data(self, tile):
        data = self.reader.get(tile.z, tile.x, tile.y)
        if data is None:
            raise MissingTileError()
        return data

    def get_tile_size(self, tile):
        data = self.get_tile_data(tile)
        return len(data)
 
    def all_z_sizes(self, z):
        for t, size in all_tile_sizes(self.reader.get_bytes):
            if t[0] == z:
                tile = mercantile.Tile(x=t[1], y=t[2], z=t[0])
                yield (tile, size)

    def all(self):
        for t, data in all_tiles(self.reader.get_bytes):
            tile = mercantile.Tile(x=t[1], y=t[2], z=t[0])
            yield (tile, data)

    def all_sizes(self):
        for t, size in all_tile_sizes(self.reader.get_bytes):
            tile = mercantile.Tile(x=t[1], y=t[2], z=t[0])
            yield (tile, size)

    def cleanup(self):
        self.file.close()

    @property
    def min_zoom(self):
        return int(self.reader.header()['min_zoom'])

    @property
    def max_zoom(self):
        return int(self.reader.header()['max_zoom'])

    def get_metadata(self):
        return self.reader.metadata()


