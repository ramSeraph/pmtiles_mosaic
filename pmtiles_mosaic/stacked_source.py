import copy
from contextlib import ExitStack

from .tiles_common import MissingTileError, INTERESTED_METADATA_KEYS, get_tile_type
from .logger import LoggerMixin
 
# hybrid source that combines multiple tile sources in order
class StackedTileSource(LoggerMixin):
    def __init__(self, srcs, logger=None):
        self.logger = logger
        self.srcs = srcs

    def get_tile_data(self, tile):
        for src in self.srcs:
            try:
                return src.get_tile_data(tile)
            except MissingTileError:
                continue
        raise MissingTileError()

    def get_tile_size(self, tile):
        for src in self.srcs:
            try:
                return src.get_tile_size(tile)
            except MissingTileError:
                continue
        raise MissingTileError()

    def all_z_sizes(self, z):
        seen = set()
        for i, src in enumerate(self.srcs):
            self.log_debug(f'Iterating over source {i} for {z}')
            for (tile, size) in src.all_z_sizes(z):
                if tile in seen:
                    continue
                seen.add(tile)
                yield (tile, size)

    def all(self):
        seen = set()
        for i, src in enumerate(self.srcs):
            self.log_debug(f'Iterating over source {i} for all levels')
            for (tile, data) in src.all():
                if tile in seen:
                    continue
                seen.add(tile)
                yield (tile, data)

    def all_sizes(self):
        seen = set()
        for i, src in enumerate(self.srcs):
            self.log_debug(f'Iterating over source {i} for all levels')
            for (tile, size) in src.all_sizes():
                if tile in seen:
                    continue
                seen.add(tile)
                yield (tile, size)

    def cleanup(self):
        with ExitStack() as resources:
            for src in self.srcs:
                resources.callback(src.cleanup)

    @property
    def min_zoom(self):
        min_zooms = []
        for src in self.srcs:
            try:
                min_zooms.append(src.min_zoom)
            except ValueError:
                continue

        if len(min_zooms) == 0:
            raise ValueError("No zoom levels found in any of the sources.")

        return min(min_zooms)

    @property
    def max_zoom(self):
        max_zooms = []
        for src in self.srcs:
            try:
                max_zooms.append(src.max_zoom)
            except ValueError:
                continue

        if len(max_zooms) == 0:
            raise ValueError("No zoom levels found in any of the sources.")

        return max(max_zooms)

    def get_metadata(self):
        combined_metadata = {}
        metadatas = [src.get_metadata() for src in self.srcs]
        if not any(metadatas):
            raise ValueError("No metadata found in any of the sources.")
        tile_types = {get_tile_type(metadata['format']) for metadata in metadatas if 'format' in metadata}
        if len(tile_types) > 1:
            raise ValueError('Cannot combine sources with different tile formats')

        for key in INTERESTED_METADATA_KEYS:
            if key == 'vector_layers':
                continue

            for metadata in metadatas:
                if key in metadata:
                    combined_metadata[key] = copy.deepcopy(metadata[key])
                    break

        # handle vector layers separately
        all_vector_layers = {}
        for metadata in metadatas:
            vector_layers = metadata.get('vector_layers', [])
            for v in vector_layers:
                v_id = v['id']
                if v_id not in all_vector_layers:
                    all_vector_layers[v_id] = copy.deepcopy(v)
                else:
                    existing = all_vector_layers[v_id]
                    for key, combine in (('minzoom', min), ('maxzoom', max)):
                        if key in existing and key in v:
                            existing[key] = combine(existing[key], v[key])
                        else:
                            # An omitted bound must not narrow another source.
                            existing.pop(key, None)
                    if 'fields' in v:
                        fields = existing.setdefault('fields', {})
                        for name, field_type in v['fields'].items():
                            fields.setdefault(name, copy.deepcopy(field_type))

        if all_vector_layers:
            combined_metadata['vector_layers'] = list(all_vector_layers.values())

        return combined_metadata


