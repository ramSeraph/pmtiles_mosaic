import glob

from pathlib import Path

from .mbtiles_source import MBTilesSource
from .pmtiles_source import PMTilesSource
from .disk_source import DiskTilesSource
from .stacked_source import StackedTileSource

        
def create_source_from_paths(source_paths, logger=None):
    sources = []
    for source_path in source_paths:
        if source_path.endswith('.mbtiles'):
            sources.append(MBTilesSource(source_path, logger=logger))
        elif source_path.endswith('.pmtiles'):
            pmtiles_files = glob.glob(source_path)
            if not pmtiles_files:
                raise ValueError(f"No PMTiles files found for pattern: {source_path}")
            for pmtiles_file in pmtiles_files:
                sources.append(PMTilesSource(pmtiles_file, logger=logger))
        else:
            if Path(source_path).is_dir():
                sources.append(DiskTilesSource(source_path, logger=logger))
            else:
                raise ValueError(f"Invalid source: {source_path}")

    if not sources:
        raise ValueError("No valid sources provided.")

    if len(sources) == 1:
        return sources[0]

    return StackedTileSource(sources, logger=logger)


