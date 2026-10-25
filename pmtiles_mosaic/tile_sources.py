import glob
import os
import errno
from contextlib import ExitStack
from pathlib import Path

from .mbtiles_source import MBTilesSource
from .pmtiles_source import PMTilesSource
from .disk_source import DiskTilesSource
from .stacked_source import StackedTileSource
from .errors import EmptySourceError, UnsupportedSourceError

        
def create_source_from_paths(source_paths, logger=None):
    sources = []
    with ExitStack() as resources:
        for source_path in source_paths:
            source_path = os.fspath(source_path)
            paths = [source_path]
            if source_path.endswith('.pmtiles'):
                paths = sorted(glob.glob(source_path))
                if not paths:
                    raise FileNotFoundError(errno.ENOENT, 'No PMTiles files found', source_path)
                source_type = PMTilesSource
            else:
                path = Path(source_path)
                path.stat()  # Preserve missing-file and permission errors.
                if path.is_dir():
                    source_type = DiskTilesSource
                elif source_path.endswith('.mbtiles'):
                    source_type = MBTilesSource
                else:
                    raise UnsupportedSourceError(
                        f'Unsupported source: {source_path}; expected .mbtiles, .pmtiles, or a tile directory'
                    )
            for path in paths:
                source = source_type(path, logger=logger)
                resources.callback(source.cleanup)
                sources.append(source)

        if not sources:
            raise EmptySourceError('No source paths provided')
        result = sources[0] if len(sources) == 1 else StackedTileSource(sources, logger=logger)
        resources.pop_all()
        return result


