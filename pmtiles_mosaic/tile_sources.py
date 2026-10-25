import glob
import os
from contextlib import ExitStack
from pathlib import Path

from .mbtiles_source import MBTilesSource
from .pmtiles_source import PMTilesSource
from .disk_source import DiskTilesSource
from .stacked_source import StackedTileSource

        
def create_source_from_paths(source_paths, logger=None):
    sources = []
    with ExitStack() as resources:
        for source_path in source_paths:
            source_path = os.fspath(source_path)
            paths = [source_path]
            if source_path.endswith('.mbtiles'):
                source_type = MBTilesSource
            elif source_path.endswith('.pmtiles'):
                paths = sorted(glob.glob(source_path))
                if not paths:
                    raise ValueError(f"No PMTiles files found for pattern: {source_path}")
                source_type = PMTilesSource
            elif Path(source_path).is_dir():
                source_type = DiskTilesSource
            else:
                raise ValueError(f"Invalid source: {source_path}")
            for path in paths:
                source = source_type(path, logger=logger)
                resources.callback(source.cleanup)
                sources.append(source)

        if not sources:
            raise ValueError("No valid sources provided.")
        result = sources[0] if len(sources) == 1 else StackedTileSource(sources, logger=logger)
        resources.pop_all()
        return result


