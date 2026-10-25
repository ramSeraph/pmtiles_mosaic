from pathlib import Path

import mercantile

from .errors import EmptySourceError, SourceMetadataError
from .tiles_common import (
    MissingTileError, INTERESTED_METADATA_KEYS, get_tile_type,
    parse_metadata_json, parse_metadata_zoom,
)
from .logger import LoggerMixin

class DiskTilesSource(LoggerMixin):
    def __init__(self, directory, logger=None):
        self.logger = logger
        self.dir = Path(directory)
        metadata = self.get_metadata()
        if 'format' not in metadata:
            raise SourceMetadataError(f'Missing format in {self.get_tilejson_file()}')
        self.ext = metadata['format']

    def _get_tile_from_file(self, file):
        parts = file.parts
        tile = mercantile.Tile(z=int(parts[-3]),
                               x=int(parts[-2]),
                               y=int(parts[-1].replace(f'.{self.ext}', '')))
        return tile

    def _file_from_tile(self, tile):
        return self.dir / f'{tile.z}' / f'{tile.x}' / f'{tile.y}.{self.ext}'

    def get_tile_data(self, tile):
        file = self._file_from_tile(tile)
        if not file.exists():
            raise MissingTileError()

        return file.read_bytes()

    def get_tile_size(self, tile):
        file = self._file_from_tile(tile)
        if not file.exists():
            raise MissingTileError()

        return file.stat().st_size
        
    def all_z_sizes(self, z):
        for file in self.dir.glob(f'{z}/*/*.{self.ext}'):
            tile = self._get_tile_from_file(file)
            fstats = file.stat()
            yield (tile, fstats.st_size)

    def all(self):
        for file in self.dir.glob(f'*/*/*.{self.ext}'):
            tile = self._get_tile_from_file(file)
            t_data = file.read_bytes()
            yield (tile, t_data)

    def all_sizes(self):
        for file in self.dir.glob(f'*/*/*.{self.ext}'):
            tile = self._get_tile_from_file(file)
            fstats = file.stat()
            yield (tile, fstats.st_size)


    def cleanup(self):
        pass

    def _get_zoom_levels(self):
        zoom_levels = set()
        for file in self.dir.glob(f'*/*/*.{self.ext}'):
            try:
                zoom = int(file.parts[-3])
                zoom_levels.add(zoom)
            except (ValueError, IndexError):
                continue
        return list(zoom_levels)

    def get_min_zoom(self):
        zoom_levels = self._get_zoom_levels()
        if len(zoom_levels) == 0:
            raise EmptySourceError(f'No tiles found in {self.dir}')

        return min(zoom_levels)

    def get_max_zoom(self):
        zoom_levels = self._get_zoom_levels()
        if len(zoom_levels) == 0:
            raise EmptySourceError(f'No tiles found in {self.dir}')

        return max(zoom_levels)

    def get_tilejson_file(self):
        return self.dir / 'metadata.json'

    def get_full_metadata(self):
        tilejson_file = self.get_tilejson_file()

        return parse_metadata_json(tilejson_file.read_bytes(), tilejson_file)

    # this is supposed to match what is expected in a pmtiles metadata dict
    def get_metadata(self):
        full_metadata = self.get_full_metadata()

        metadata = {}
        # ensure we have the keys we are interested in
        for key in INTERESTED_METADATA_KEYS:
            if key in full_metadata:
                metadata[key] = full_metadata[key]

        if 'format' in metadata:
            get_tile_type(metadata['format'], self.get_tilejson_file())
        return metadata

    @property
    def min_zoom(self):
        full_metadata = self.get_full_metadata()
        if 'minzoom' not in full_metadata:
            return self.get_min_zoom()
        return parse_metadata_zoom(full_metadata['minzoom'], 'minzoom', self.get_tilejson_file())
 
    @property
    def max_zoom(self):
        full_metadata = self.get_full_metadata()
        if 'maxzoom' not in full_metadata:
            return self.get_max_zoom()
        return parse_metadata_zoom(full_metadata['maxzoom'], 'maxzoom', self.get_tilejson_file())


