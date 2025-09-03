from .tiles_common import MissingTileError, INTERESTED_METADATA_KEYS
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
        for src in self.srcs:
            src.cleanup()

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
        metadatas = []
        for src in self.srcs:
            try:
                metadata = src.get_metadata()
                metadatas.append(metadata)
            except ValueError:
                metadatas.append({})
                continue
        all_empty = all([not metadata for metadata in metadatas])
        if all_empty:
            raise ValueError("No metadata found in any of the sources.")

        for key in INTERESTED_METADATA_KEYS:
            if key == 'vector_layers':
                continue

            for metadata in metadatas:
                if key in metadata:
                    combined_metadata[key] = metadata[key]
                    break

        # handle vector layers separately
        all_vector_layers = {}
        for metadata in metadatas:
            vector_layers = metadata.get('vector_layers', [])
            for v in vector_layers:
                v_id = v['id']
                if v_id not in all_vector_layers:
                    all_vector_layers[v_id] = v
                else:
                    existing = all_vector_layers[v_id]
                    if existing['minzoom'] > v['minzoom']:
                        existing['minzoom'] = v['minzoom']
                    if existing['maxzoom'] < v['maxzoom']:
                        existing['maxzoom'] = v['maxzoom']

        if all_vector_layers:
            combined_metadata['vector_layers'] = list(all_vector_layers.values())

        return combined_metadata


