"""Shared size, header, metadata, and payload handling for both partitioners."""

import argparse
import copy
import gzip
import re

import mercantile
from pmtiles.tile import Compression, TileType

from .tiles_common import get_tile_type


SLICE_HEADER_EXPORT_KEYS = [
    'min_lon_e7', 'min_lat_e7', 'max_lon_e7', 'max_lat_e7', 'min_zoom', 'max_zoom',
]
HEADER_EXPORT_KEYS = SLICE_HEADER_EXPORT_KEYS + [
    'center_zoom', 'center_lon_e7', 'center_lat_e7', 'tile_type', 'tile_compression',
]


def parse_size(size_str):
    size_str = str(size_str).strip().upper()
    presets = {
        'GITHUB_RELEASE': 2 * 1024 ** 3,
        'GITHUB_FILE': 100 * 1024 ** 2,
        'CLOUDFLARE_OBJECT': 512 * 1024 ** 2,
    }
    if size_str in presets:
        size = presets[size_str]
    else:
        match = re.fullmatch(r'(\d+)([KMG]?)', size_str)
        if not match:
            raise argparse.ArgumentTypeError(f'Invalid size format: {size_str}')
        value, unit = match.groups()
        size = int(value) * {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}[unit]
    if size < 1024 ** 2:
        raise argparse.ArgumentTypeError(f'Size must be at least 1MB, got {size_str}')
    return size


def get_pmtiles_file_name(to_pmtiles_prefix, suffix):
    suffix = f'-{suffix}' if suffix else ''
    return f'{to_pmtiles_prefix}{suffix}.pmtiles'


def get_bounds(tiles):
    tiles = iter(tiles)
    try:
        bounds = mercantile.bounds(next(tiles))
    except StopIteration:
        raise ValueError('No tiles available for bounds') from None
    west, south, east, north = bounds
    for tile in tiles:
        bounds = mercantile.bounds(tile)
        west, south = min(west, bounds.west), min(south, bounds.south)
        east, north = max(east, bounds.east), max(north, bounds.north)
    return south, west, north, east


def get_info(tiles):
    tiles_by_z = {}
    for tile in tiles:
        tiles_by_z.setdefault(tile.z, []).append(tile)
    if not tiles_by_z:
        raise ValueError('No tiles available for a header')
    min_zoom, max_zoom = min(tiles_by_z), max(tiles_by_z)
    return min_zoom, max_zoom, get_bounds(tiles_by_z[min_zoom]), get_bounds(tiles_by_z[max_zoom])


def get_header_base(metadata):
    tile_type = get_tile_type(metadata['format'])
    return {
        'tile_type': tile_type,
        'tile_compression': Compression.GZIP if tile_type == TileType.MVT else Compression.NONE,
    }


def get_header(tiles, header_base, use_lower_zoom_for_bounds=False, tiles_info=None):
    if tiles_info is None:
        tiles_info = get_info(tiles)
    min_zoom, max_zoom, lower_bounds, higher_bounds = tiles_info
    south, west, north, east = lower_bounds if use_lower_zoom_for_bounds else higher_bounds
    return {
        **header_base,
        'min_lon_e7': int(west * 10000000),
        'min_lat_e7': int(south * 10000000),
        'max_lon_e7': int(east * 10000000),
        'max_lat_e7': int(north * 10000000),
        'min_zoom': min_zoom,
        'max_zoom': max_zoom,
        'center_zoom': (min_zoom + max_zoom) // 2,
        'center_lon_e7': int(10000000 * (higher_bounds[1] + higher_bounds[3]) / 2),
        'center_lat_e7': int(10000000 * (higher_bounds[0] + higher_bounds[2]) / 2),
    }


def convert_header(header, filter_keys):
    converted = {}
    for key in filter_keys:
        value = header[key]
        converted[key] = value.value if isinstance(value, (TileType, Compression)) else value
    return converted


def get_header_and_metadata(header_base, metadata, tiles):
    tiles_info = get_info(tiles)
    header = get_header(tiles, header_base, tiles_info=tiles_info)
    header_for_mosaic = get_header(tiles, header_base, True, tiles_info)
    metadata = copy.deepcopy(metadata)
    if 'vector_layers' in metadata:
        layers = []
        for layer in metadata['vector_layers']:
            min_zoom = max(layer.get('minzoom', header['min_zoom']), header['min_zoom'])
            max_zoom = min(layer.get('maxzoom', header['max_zoom']), header['max_zoom'])
            if min_zoom <= max_zoom:
                layer.update(minzoom=min_zoom, maxzoom=max_zoom)
                layers.append(layer)
        metadata['vector_layers'] = layers
    return header, header_for_mosaic, metadata


def prepare_tile_data(data, compression):
    compressed = data.startswith(b'\x1f\x8b')
    if compression == Compression.GZIP and not compressed:
        return gzip.compress(data, mtime=0)
    if compression == Compression.NONE and compressed:
        return gzip.decompress(data)
    return data
