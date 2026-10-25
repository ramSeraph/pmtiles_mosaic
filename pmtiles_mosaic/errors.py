"""Errors callers can distinguish when opening and reading tile sources."""


class TileSourceError(ValueError):
    """Base for source configuration and metadata errors.

    ValueError inheritance preserves existing callers' exception handling.
    Missing files use FileNotFoundError; other filesystem, SQLite, and archive
    decoding errors retain their original types rather than being wrapped here.
    """


class UnsupportedSourceError(TileSourceError):
    """An existing input is not an MBTiles file, PMTiles file, or tile directory.

    Missing paths and unmatched PMTiles patterns raise FileNotFoundError instead.
    """


class SourceMetadataError(TileSourceError):
    """Required metadata is absent, malformed, or incompatible across sources.

    Includes invalid JSON, non-object metadata, unparseable zoom values, and
    conflicting tile formats in a stack. Parsing errors retain their original
    exception as __cause__. An omitted zoom is inferred from directory tiles or
    the MBTiles tile table; a malformed value must not trigger that fallback.
    """


class UnsupportedTileFormatError(SourceMetadataError):
    """The source declares a tile format outside TILE_TYPES.

    Also exported from tiles_common for existing callers.
    """


class EmptySourceError(TileSourceError):
    """No sources were supplied, or no usable tiles are available.

    Raised when a source has no tiles from which to infer its zoom range, or
    partitioning finds no tiles (including after transparent tiles are excluded).
    Stacked sources skip only this error when combining source zoom ranges.
    """


class MissingTileError(Exception):
    """A requested tile is absent; callers may try the next source.

    This is separate from TileSourceError: a lookup miss is expected during
    fallback, whereas a malformed source or an I/O failure must propagate.
    Also exported from tiles_common for existing callers.
    """
