# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "mercantile",
#     "pmtiles",
# ]
# ///

import re
import sys
import json
import gzip
import copy
import tempfile
import argparse

from pathlib import Path

import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer as PMTilesWriter

from .tile_sources import create_source_from_paths

SLICE_HEADER_EXPORT_KEYS = [
    'min_lon_e7',
    'min_lat_e7',
    'max_lon_e7',
    'max_lat_e7',
    'min_zoom',
    'max_zoom',
]

HEADER_EXPORT_KEYS = SLICE_HEADER_EXPORT_KEYS + [
    'center_zoom',
    'center_lon_e7',
    'center_lat_e7',
    'tile_type',
    'tile_compression',
]



def parse_size(size_str):
    size_str = str(size_str).strip().upper()

    intended_size = None

    # Handle preset values
    if size_str == 'GITHUB_RELEASE':
        intended_size = 2 * 1024 * 1024 * 1024
    elif size_str == 'GITHUB_FILE':
        intended_size = 100 * 1024 * 1024
    elif size_str == 'CLOUDFLARE_OBJECT':
        intended_size = 512 * 1024 * 1024
    else:
        # Handle numeric values with optional units
        match = re.match(r'^(\d+)([GKM]?)$', size_str)
        if not match:
            raise argparse.ArgumentTypeError(f"Invalid size format: {size_str}")

        value, unit = match.groups()
        value = int(value)

        if unit == 'G':
            intended_size = value * 1024 * 1024 * 1024
        elif unit == 'M':
            intended_size = value * 1024 * 1024
        elif unit == 'K':
            intended_size = value * 1024
        else:
            intended_size = value

    # Calculate scaled DELTA
    if intended_size < 1024 * 1024:  # less than 1MB
        raise argparse.ArgumentTypeError(f"Size must be at least 1MB, got {size_str}")

    return intended_size

def get_pmtiles_file_name(to_pmtiles_prefix, suffix):
    if suffix == '':
        out_pmtiles_file = f'{to_pmtiles_prefix}.pmtiles'
    else:
        out_pmtiles_file = f'{to_pmtiles_prefix}-{suffix}.pmtiles'

    return out_pmtiles_file

def get_bounds(tiles):

    bounds = [ mercantile.bounds(t) for t in tiles ]

    max_x = bounds[0].east
    min_x = bounds[0].west
    max_y = bounds[0].north
    min_y = bounds[0].south
    for b in bounds:
        if b.east > max_x:
            max_x = b.east

        if b.west < min_x:
            min_x = b.west

        if b.north > max_y:
            max_y = b.north

        if b.south < min_y:
            min_y = b.south

    return (min_y, min_x, max_y, max_x)


def get_info(tiles):
    tiles_by_z = {}

    for t in tiles:
        if t.z not in tiles_by_z:
            tiles_by_z[t.z] = []

        tiles_by_z[t.z].append(t)

    min_z = min(tiles_by_z.keys())
    max_z = max(tiles_by_z.keys())

    higher_bounds = get_bounds(tiles_by_z[max_z])
    lower_bounds = get_bounds(tiles_by_z[min_z])

    return min_z, max_z, lower_bounds, higher_bounds
 

def get_header_base(metadata):

    format_string = metadata['format']
    if format_string == "mvt":
        format_string = "pbf"

    if format_string == "jpg":
        format_string = "jpeg"

    is_vector_tiles = (format_string == "pbf")

    if is_vector_tiles:
        tile_compression = Compression.GZIP
    else:
        tile_compression = Compression.NONE

    if format_string in "pbf":
        tile_type = TileType.MVT
    elif format_string == "png":
        tile_type = TileType.PNG
    elif format_string == "jpeg":
        tile_type = TileType.JPEG
    elif format_string == "webp":
        tile_type = TileType.WEBP
    elif format_string == "avif":
        tile_type = TileType.AVIF
    else:
        tile_type = TileType.UNKNOWN

    header = {
        "tile_compression": tile_compression,
        "tile_type": tile_type,
    }
    return header

def get_header(tiles, header_base, use_lower_zoom_for_bounds=False, tiles_info=None):

    header = {}
    header.update(header_base)

    if tiles_info is None:
        tiles_info = get_info(tiles)

    min_zoom, max_zoom, lower_bounds, higher_bounds = tiles_info

    lower_min_lat, lower_min_lon, lower_max_lat, lower_max_lon = lower_bounds
    higher_min_lat, higher_min_lon, higher_max_lat, higher_max_lon = higher_bounds

    header.update({
        "min_lon_e7": int(lower_min_lon * 10000000) if use_lower_zoom_for_bounds else int(higher_min_lon * 10000000),
        "min_lat_e7": int(lower_min_lat * 10000000) if use_lower_zoom_for_bounds else int(higher_min_lat * 10000000),
        "max_lon_e7": int(lower_max_lon * 10000000) if use_lower_zoom_for_bounds else int(higher_max_lon * 10000000),
        "max_lat_e7": int(lower_max_lat * 10000000) if use_lower_zoom_for_bounds else int(higher_max_lat * 10000000),
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "center_zoom": (max_zoom + min_zoom) // 2,
        "center_lon_e7": int(10000000 * (higher_min_lon + higher_max_lon)/2),
        "center_lat_e7": int(10000000 * (higher_min_lat + higher_max_lat)/2),
    })
    return header

def convert_header(header, filter_keys):
    new_header = copy.copy(header)
    new_header['tile_compression'] = header['tile_compression'].value
    new_header['tile_type'] = header['tile_type'].value
    header_keys = list(new_header.keys())
    for k in header_keys:
        if k not in filter_keys:
            del new_header[k]
    return new_header

         
def get_header_and_metadata(header_base, metadata, tiles):
    tiles_info = get_info(tiles)
    header = get_header(tiles, header_base, use_lower_zoom_for_bounds=False, tiles_info=tiles_info)
    header_for_mosaic = get_header(tiles, header_base, use_lower_zoom_for_bounds=True, tiles_info=tiles_info)

    metadata = copy.deepcopy(metadata)

    min_zoom = header['min_zoom']
    max_zoom = header['max_zoom']
    if 'vector_layers' in metadata:
        for layer in metadata['vector_layers']:
            if layer['maxzoom'] > max_zoom:
                layer['maxzoom'] = max_zoom
            if layer['minzoom'] < min_zoom:
                layer['minzoom'] = min_zoom

    return header, header_for_mosaic, metadata


class WriterCheckpoint:
    def __init__(self, writer, tiles):
        self.tile_entries = copy.copy(writer.tile_entries)
        self.hash_to_offset = copy.copy(writer.hash_to_offset)
        self.offset = writer.offset
        self.addressed_tiles = writer.addressed_tiles
        self.clustered = writer.clustered
        self.tiles = copy.copy(tiles)


class CheckpointablePMTilesWriter:
    def __init__(self, header_base, metadata):
        self.header_base = header_base
        self.should_be_compressed = (header_base.get('tile_compression', Compression.NONE) == Compression.GZIP)
        self.metadata = metadata

        self.header_f = tempfile.TemporaryFile()
        self.writer = PMTilesWriter(self.header_f)
        self.tiles = []
        self.last_checkpoint = None

    def write_tile(self, tile, tdata):
        self.tiles.append(tile)
        tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)

        if self.should_be_compressed and tdata[0:2] != b"\x1f\x8b":
            tdata = gzip.compress(tdata)

        self.writer.write_tile(tile_id, tdata)

    def checkpoint(self):
        self.last_checkpoint = WriterCheckpoint(self.writer, self.tiles)

    def rollback(self):
        if self.last_checkpoint is None:
            raise Exception("No checkpoint to rollback to")

        self.tiles = copy.copy(self.last_checkpoint.tiles)

        self.writer.tile_entries = copy.copy(self.last_checkpoint.tile_entries)
        self.writer.hash_to_offset = copy.copy(self.last_checkpoint.hash_to_offset)
        self.writer.offset = self.last_checkpoint.offset
        self.writer.addressed_tiles = self.last_checkpoint.addressed_tiles
        self.writer.clustered = self.last_checkpoint.clustered

        self.writer.tile_f.seek(self.writer.offset)
        self.writer.tile_f.truncate(self.writer.offset)


    def is_empty(self):
        return len(self.tiles) == 0

    def finalize(self, pmtiles_fname):
        self.writer.f.close()
        Path(pmtiles_fname).parent.mkdir(parents=True, exist_ok=True)
        self.writer.f = open(pmtiles_fname, 'wb')
        header, header_for_mosaic, metadata = get_header_and_metadata(self.header_base, self.metadata, self.tiles)
        self.writer.finalize(header, metadata)
        return header_for_mosaic

    def get_size(self):
        header = get_header(self.tiles, self.header_base, use_lower_zoom_for_bounds=True)

        prev_tile_f = self.writer.tile_f
        prev_f = self.writer.f

        self.writer.tile_f = tempfile.TemporaryFile()
        new_f = tempfile.TemporaryFile()
        self.writer.f = new_f

        self.writer.finalize(header, self.metadata)

        self.writer.tile_f = prev_tile_f
        self.writer.f = prev_f

        sz = self.writer.offset + new_f.tell()
        new_f.close()

        return sz

    def close(self):
        if self.last_checkpoint is not None:
            del self.last_checkpoint


class Partitioner:
    def __init__(self, reader, to_pmtiles_prefix, size_limit_bytes, should_cache):
        self.reader = reader

        self.max_zoom_level = reader.max_zoom
        self.min_zoom_level = reader.min_zoom

        self.src_metadata = reader.get_metadata()

        self.header_base = get_header_base(self.src_metadata)

        self.to_pmtiles_prefix = to_pmtiles_prefix
        self.size_limit_bytes = size_limit_bytes
        self.should_cache = should_cache

        self.part_count = 0
        self.headers = []
        self.partition_names = []

        self.all_tiles = []

        self.cache_dict = {}
        self.cache_file = tempfile.TemporaryFile()
        self.cache_offset = 0


    def get_current_partition_filename(self):
        return get_pmtiles_file_name(self.to_pmtiles_prefix, f'part{self.part_count:04d}')

    def cache_tile(self, tile, tdata):
        tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)
        if tile_id in self.cache_dict:
            return

        self.cache_dict[tile_id] = (self.cache_offset, len(tdata))
        self.cache_file.write(tdata)
        self.cache_offset += len(tdata)

    def get_tile_data(self, tile):
        if not self.should_cache:
            return self.reader.get_tile_data(tile)

        tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)
        if tile_id not in self.cache_dict:
            raise Exception(f'Tile {tile} not found in cache')

        offset, size = self.cache_dict[tile_id]
        self.cache_file.seek(offset)
        return self.cache_file.read(size)

    def collect_tiles(self):
        print('Collecting tiles from source...')
        if self.should_cache:
            for tile, tdata in self.reader.all():
                self.all_tiles.append(tile)
                self.cache_tile(tile, tdata)
        else:
            for tile, tsize in self.reader.all_sizes():
                self.all_tiles.append(tile)

    def complete_current_slice(self, curr_slice_writer, context, pmtiles_file_name=None):
        if pmtiles_file_name is None:
            pmtiles_file_name = self.get_current_partition_filename()

        print(f'Finalizing partition {pmtiles_file_name} with {len(curr_slice_writer.tiles)} tiles, context: {context}')
        header = curr_slice_writer.finalize(pmtiles_file_name)
        curr_slice_writer.close()
        self.headers.append(header)
        self.partition_names.append(Path(pmtiles_file_name).name)
        self.part_count += 1
 
    def create_new_writer(self):
        return CheckpointablePMTilesWriter(self.header_base, self.src_metadata)

    def partition_by_y(self, from_zoom_level, to_zoom_level, x_tiles, context):
        print(f'Partitioning by y for context: {context}')

        tiles_by_y = {}
        for t in x_tiles:
            if t.z == from_zoom_level:
                pt = t
            else:
                pt = mercantile.parent(t, zoom=from_zoom_level)
            if pt.y not in tiles_by_y:
                tiles_by_y[pt.y] = []
            tiles_by_y[pt.y].append(t)

        y_levels = sorted(tiles_by_y.keys())

        curr_slice_writer = self.create_new_writer()

        i = 0
        start_y = None
        while i < len(y_levels):
            y = y_levels[i]
            y_tiles = tiles_by_y[y]

            if start_y is None:
                start_y = y

            curr_slice_writer.checkpoint()

            # write all tiles in this y stripe to the current slice writer
            for t in y_tiles:
                curr_slice_writer.write_tile(t, self.get_tile_data(t))


            size_till_now = curr_slice_writer.get_size()
            if size_till_now > self.size_limit_bytes:

                curr_slice_writer.rollback()

                if not curr_slice_writer.is_empty():
                    new_context = context + [('y', start_y, y_levels[i - 1])]
                    self.complete_current_slice(curr_slice_writer, new_context)

                    curr_slice_writer = self.create_new_writer()
                    start_y = None
                    continue

                new_context = context + [('y', start_y, y_levels[i - 1])]
                self.partition_by_z(from_zoom_level, to_zoom_level, y_tiles, new_context)

            i += 1

        if not curr_slice_writer.is_empty():
            new_context = context + [('y', start_y, y_levels[i - 1])]
            self.complete_current_slice(curr_slice_writer, new_context)

    def partition_by_x(self, from_zoom_level, to_zoom_level, tiles_by_z, context):
        print(f'Partitioning by x for context: {context}')

        tiles_by_x = {}
        for zoom_level in range(from_zoom_level, to_zoom_level + 1):
            for tile in tiles_by_z[zoom_level]:

                if tile.z == from_zoom_level:
                    pt = tile
                else:
                    pt = mercantile.parent(tile, zoom=from_zoom_level)
                if pt.x not in tiles_by_x:
                    tiles_by_x[pt.x] = []
                tiles_by_x[pt.x].append(tile)

        x_levels = sorted(tiles_by_x.keys())

        curr_slice_writer = self.create_new_writer()

        i = 0
        start_x = None
        while i < len(x_levels):
            x = x_levels[i]
            x_tiles = tiles_by_x[x]

            if start_x is None:
                start_x = x

            curr_slice_writer.checkpoint()

            # write all tiles in this x stripe to the current slice writer
            for t in x_tiles:
                curr_slice_writer.write_tile(t, self.get_tile_data(t))

            size_till_now = curr_slice_writer.get_size()
            if size_till_now > self.size_limit_bytes:

                curr_slice_writer.rollback()

                if not curr_slice_writer.is_empty():
                    new_context = context + [('x', start_x, x_levels[i - 1])]
                    self.complete_current_slice(curr_slice_writer, new_context)

                    curr_slice_writer = self.create_new_writer()
                    start_x = None
                    continue

                new_context = context + [('x', start_x, x_levels[i - 1])]
                self.partition_by_y(from_zoom_level, to_zoom_level, x_tiles, new_context)

            i += 1

        if not curr_slice_writer.is_empty():
            new_context = context + [('x', start_x, x_levels[i - 1])]
            self.complete_current_slice(curr_slice_writer, new_context)

    def partition_by_z(self, from_zoom_level, to_zoom_level, tiles, context):

        print(f'Partitioning by z for context: {context}')

        tiles_by_z = {}
        for tile in tiles:
            if tile.z not in tiles_by_z:
                tiles_by_z[tile.z] = []
            tiles_by_z[tile.z].append(tile)

        top_slice_writer = self.create_new_writer()

        top_slice_max_level = None
        for zoom_level in range(from_zoom_level, to_zoom_level + 1):
            top_slice_writer.checkpoint()

            for tile in tiles_by_z[zoom_level]:
                top_slice_writer.write_tile(tile, self.get_tile_data(tile))


            size_till_now = top_slice_writer.get_size()
            if size_till_now > self.size_limit_bytes:
                top_slice_writer.rollback()
                top_slice_max_level = zoom_level - 1
                break


        if top_slice_max_level is None:
            top_slice_max_level = to_zoom_level

        if not top_slice_writer.is_empty():

            new_context = context + [('z', from_zoom_level, top_slice_max_level)]
            if top_slice_max_level == to_zoom_level and len(context) == 0:
                pmtiles_file_name = get_pmtiles_file_name(self.to_pmtiles_prefix, '')
                self.complete_current_slice(top_slice_writer, new_context, pmtiles_file_name=pmtiles_file_name)
            else:
                self.complete_current_slice(top_slice_writer, new_context)

            if top_slice_max_level == to_zoom_level:
                return

        new_from_zoom_level = top_slice_max_level + 1

        # TODO: think this case through.. you need to stop somewhere and give up
        if len(tiles) <= 1:
            raise Exception(f'Cannot drill down further, context: {context}')

        new_context = context + [('z', new_from_zoom_level, to_zoom_level)]
        self.partition_by_x(new_from_zoom_level, to_zoom_level, tiles_by_z, new_context)

    def partition(self):
        self.collect_tiles()

        self.partition_by_z(self.min_zoom_level, self.max_zoom_level, self.all_tiles, [])

    def write_mosaic_file(self, mosaic_data):

        out_mosaic_file = f'{self.to_pmtiles_prefix}.mosaic.json'
        with open(out_mosaic_file, 'w') as f:
            json.dump(mosaic_data, f)
        print(f'Wrote mosaic file to {out_mosaic_file}')

    def finalize(self):
        if self.part_count <= 1:
            return

        print(f'finalizing {self.part_count} partitions')
        header = get_header(self.all_tiles, self.header_base, use_lower_zoom_for_bounds=False)
        header = convert_header(header, HEADER_EXPORT_KEYS)
        mosaic_data = {
            'version': 1,
            'metadata': self.src_metadata,
            'header': header,
            'slices': {}
        }

        for i, partition_name in enumerate(self.partition_names):
            header = self.headers[i]
            header = convert_header(header, SLICE_HEADER_EXPORT_KEYS)
            mosaic_data['slices'][partition_name] = {
                'header': header,
            }

        self.write_mosaic_file(mosaic_data)

def partition_main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-source', action='append', required=True, help='Path to a source file or directory. Can be repeated.')
    parser.add_argument('--to-pmtiles', required=True, help='Output PMTiles file.')
    parser.add_argument('--size-limit', default='github_release', type=parse_size, help='Maximum size of each partition. Can be a number in bytes or a preset: github_release (2G), github_file (100M), cloudflare_object (512M). Can also be a number with optional units (K, M, G). Default is github_release (2G).')
    parser.add_argument('--no-cache', action='store_true', default=False, help='Cache tiles locally for speed')
    args = parser.parse_args(args)

    if not args.to_pmtiles.endswith('.pmtiles'):
        parser.error("Output PMTiles file must end with '.pmtiles'")
    to_pmtiles_prefix = args.to_pmtiles.removesuffix('.pmtiles')

    reader = create_source_from_paths(args.from_source)

    print(f'size limit: {args.size_limit} bytes')

    partitioner = Partitioner(reader, to_pmtiles_prefix, args.size_limit, not args.no_cache)

    partitioner.partition()

    partitioner.finalize()

def cli():
    partition_main(sys.argv[1:])

if __name__ == '__main__':
    cli()


