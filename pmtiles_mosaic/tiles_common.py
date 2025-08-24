
class MissingTileError(Exception):
    pass

INTERESTED_METADATA_KEYS = [
    'type', 'format', 'attribution', 'description', 'name', 
    'version', 'vector_layers'
]

