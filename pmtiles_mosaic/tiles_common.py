from pmtiles.tile import TileType


class MissingTileError(Exception):
    pass


class UnsupportedTileFormatError(ValueError):
    pass


TILE_TYPES = {
    'mvt': TileType.MVT,
    'png': TileType.PNG,
    'jpg': TileType.JPEG,
    'webp': TileType.WEBP,
    'avif': TileType.AVIF,
}


def get_tile_type(tile_format):
    try:
        return TILE_TYPES[tile_format]
    except (KeyError, TypeError):
        raise UnsupportedTileFormatError(
            f"Unsupported tile format: {tile_format!r}; expected one of {', '.join(TILE_TYPES)}"
        ) from None


INTERESTED_METADATA_KEYS = [
    'type', 'format', 'attribution', 'description', 'name', 
    'version', 'vector_layers'
]

