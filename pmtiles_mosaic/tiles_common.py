import json

from pmtiles.tile import TileType

# Keep the existing import locations available to source consumers.
from .errors import MissingTileError, SourceMetadataError, UnsupportedTileFormatError


TILE_TYPES = {
    'mvt': TileType.MVT,
    'png': TileType.PNG,
    'jpg': TileType.JPEG,
    'webp': TileType.WEBP,
    'avif': TileType.AVIF,
}


def get_tile_type(tile_format, source=None):
    try:
        return TILE_TYPES[tile_format]
    except (KeyError, TypeError):
        location = f' in {source}' if source is not None else ''
        raise UnsupportedTileFormatError(
            f"Unsupported tile format{location}: {tile_format!r}; expected one of {', '.join(TILE_TYPES)}"
        ) from None


def parse_metadata_json(data, source):
    try:
        metadata = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise SourceMetadataError(f'Invalid JSON metadata in {source}: {error}') from error
    if not isinstance(metadata, dict):
        raise SourceMetadataError(f'Metadata in {source} must be a JSON object')
    return metadata


def parse_metadata_zoom(value, key, source):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise SourceMetadataError(f'Invalid {key} in {source}: {value!r}; expected an integer') from error


INTERESTED_METADATA_KEYS = [
    'type', 'format', 'attribution', 'description', 'name', 
    'version', 'vector_layers'
]

