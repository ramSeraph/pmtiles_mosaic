# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "mercantile",
#     "pmtiles",
# ]
# ///

import json
import gzip
import copy
import argparse
import re
import sys
from pathlib import Path

import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer as PMTilesWriter

from .tile_sources import create_source_from_paths

BASE_SIZE_FOR_DELTA = 2 * 1024 * 1024 * 1024 # 2GB
BASE_DELTA = 5 * 1024 * 1024 # 5MB

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

def adjust_size_limit(intended_size, delta_estimate=None):
    if delta_estimate is None:
        delta_estimate = int(BASE_DELTA * (intended_size / BASE_SIZE_FOR_DELTA))

    print(f'Using delta estimate: {delta_estimate} bytes for intended size: {intended_size} bytes')

    final_size = intended_size - delta_estimate
    if final_size < 0:
        raise argparse.ArgumentTypeError(f"Calculated size is negative: {final_size} for {intended_size}")

    return final_size

def convert_header(header, filter_keys):
    new_header = copy.copy(header)
    new_header['tile_compression'] = header['tile_compression'].value
    new_header['tile_type'] = header['tile_type'].value
    header_keys = list(new_header.keys())
    for k in header_keys:
        if k not in filter_keys:
            del new_header[k]
    return new_header


def get_pmtiles_file_name(to_pmtiles_prefix, suffix):
    if suffix == '':
        out_pmtiles_file = f'{to_pmtiles_prefix}.pmtiles'
    else:
        out_pmtiles_file = f'{to_pmtiles_prefix}-{suffix}.pmtiles'

    return out_pmtiles_file

class Partitioner:
    def __init__(self, reader, to_pmtiles_prefix, size_limit_bytes):
        self.reader = reader

        self.max_zoom_level = reader.max_zoom
        self.min_zoom_level = reader.min_zoom

        self.to_pmtiles_prefix = to_pmtiles_prefix
        self.size_limit_bytes = size_limit_bytes

        self.tiles_to_slice_idx = {}

        self.slices = []
        self.expected_slice_sizes = []

    def get_layer_tiles_and_sizes(self, zoom_level):
        tiles = []
        size = 0

        for tile, tsize in self.reader.for_all_z(zoom_level):
            tiles.append(tile)
            size += tsize

        return tiles, size

    def add_to_current_slice(self, tiles, expected_bucket_size, partition_name=None):
        curr_idx = len(self.slices)
        for t in tiles:
            self.tiles_to_slice_idx[t] = curr_idx

        if partition_name is None:
            partition_name = f'part{curr_idx:04d}'
        self.slices.append(partition_name)
        self.expected_slice_sizes.append(expected_bucket_size)


    def create_top_slice(self):
    
        print('getting top slice')

        size_till_now = 0

        tiles = []
        expected_bucket_size = 0
        curr_level = self.min_zoom_level
        while curr_level <= self.max_zoom_level:

            curr_level_tiles, curr_level_size = self.get_layer_tiles_and_sizes(curr_level)

            size_till_now += curr_level_size

            if size_till_now > self.size_limit_bytes:
                break

            tiles.extend(curr_level_tiles)
            expected_bucket_size += curr_level_size
            curr_level += 1

        if curr_level != self.min_zoom_level:
            partition_name = None
            if curr_level == self.max_zoom_level + 1:
                partition_name = ''

            self.add_to_current_slice(tiles, expected_bucket_size,
                                      partition_name=partition_name)

        return curr_level - 1

    def get_x_stripes(self, min_stripe_level):
        tiles_by_x = {}
        sizes_by_x = {}
    
        for t, tsize in self.reader.all_sizes():
            if t.z < min_stripe_level:
                continue
    
            if t.z == min_stripe_level:
                pt = t
            else:
                pt = mercantile.parent(t, zoom=min_stripe_level)
    
            if pt.x not in tiles_by_x:
                tiles_by_x[pt.x] = []
            tiles_by_x[pt.x].append(t)
    
            if pt.x not in sizes_by_x:
                sizes_by_x[pt.x] = 0
            sizes_by_x[pt.x] += tsize
    
        return sizes_by_x, tiles_by_x


    def get_buckets(self, sizes_by_x, tiles_by_x):
        buckets = []
        all_bucket_tiles = []
        expected_bucket_sizes = []
    
        x_coords = sorted(sizes_by_x.keys())
        if not x_coords:
            return [], []
    
        current_bucket_tiles = []
        current_bucket_size = 0
        current_bucket_range = None
    
        for x in x_coords:
            x_size = sizes_by_x[x]
            x_tiles = tiles_by_x[x]
    
            # if the current bucket has tiles and adding the next stripe would exceed the size limit,
            # then finalize the current bucket and start a new one.
            if current_bucket_range is not None and (current_bucket_size + x_size > self.size_limit_bytes):
                if current_bucket_size > self.size_limit_bytes:
                    raise Exception(f'Current bucket size {current_bucket_size} exceeds size limit {self.size_limit_bytes}, the striping algorithm failed')
                buckets.append(current_bucket_range)
                all_bucket_tiles.append(current_bucket_tiles)
                expected_bucket_sizes.append(current_bucket_size)
    
                current_bucket_tiles = []
                current_bucket_size = 0
                current_bucket_range = None
    
            if current_bucket_range is None:  # Starting a new bucket
                current_bucket_range = (x, x)
    
            current_bucket_tiles.extend(x_tiles)
            current_bucket_size += x_size
            current_bucket_range = (current_bucket_range[0], x)
    
        # Add the last bucket if it has any tiles.
        if current_bucket_tiles:
            if current_bucket_size > self.size_limit_bytes:
                raise Exception(f'Current bucket size {current_bucket_size} exceeds size limit {self.size_limit_bytes}, the striping algorithm failed')
            buckets.append(current_bucket_range)
            all_bucket_tiles.append(current_bucket_tiles)
            expected_bucket_sizes.append(current_bucket_size)
    
    
        return buckets, all_bucket_tiles, expected_bucket_sizes


    def partition(self):

        top_slice_max_level = self.create_top_slice()

        print(f'top slice max zoom level: {top_slice_max_level}')

        if top_slice_max_level == self.max_zoom_level:
            print('no more slicing required')
            return

        from_level = top_slice_max_level + 1

        print('getting x stripes from zoom level', from_level)
        x_stripe_sizes, x_stripe_tiles = self.get_x_stripes(from_level)
        print('getting buckets')
        buckets, bucket_tiles, expected_bucket_sizes = self.get_buckets(x_stripe_sizes, x_stripe_tiles)
    
        for i,bucket in enumerate(buckets):
            self.add_to_current_slice(bucket_tiles[i], expected_bucket_sizes[i])

    def get_bounds(self, tiles):
    
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


    def get_info(self, tiles):
        tiles_by_z = {}

        for t in tiles:
            if t.z not in tiles_by_z:
                tiles_by_z[t.z] = []

            tiles_by_z[t.z].append(t)

        min_z = min(tiles_by_z.keys())
        max_z = max(tiles_by_z.keys())

        higher_bounds = self.get_bounds(tiles_by_z[max_z])
        lower_bounds = self.get_bounds(tiles_by_z[min_z])

        return min_z, max_z, lower_bounds, higher_bounds
 

    def get_header(self, tiles, use_lower_zoom_for_bounds=False, tiles_info=None):

        if tiles_info is None:
            tiles_info = self.get_info(tiles)

        min_zoom, max_zoom, lower_bounds, higher_bounds = tiles_info

        lower_min_lat, lower_min_lon, lower_max_lat, lower_max_lon = lower_bounds
        higher_min_lat, higher_min_lon, higher_max_lat, higher_max_lon = higher_bounds

        src_metadata = self.reader.get_metadata()

        format_string = src_metadata['format']
        if format_string == "mvt":
            format_string = "pbf"

        if format_string == "jpg":
            format_string = "jpeg"

        is_vector_tiles = (format_string == "pbf")

        if is_vector_tiles:
            tile_compression = Compression.GZIP
        else:
            tile_compression = Compression.NONE

        header = {
            "tile_compression": tile_compression,
            "min_lon_e7": int(lower_min_lon * 10000000) if use_lower_zoom_for_bounds else int(higher_min_lon * 10000000),
            "min_lat_e7": int(lower_min_lat * 10000000) if use_lower_zoom_for_bounds else int(higher_min_lat * 10000000),
            "max_lon_e7": int(lower_max_lon * 10000000) if use_lower_zoom_for_bounds else int(higher_max_lon * 10000000),
            "max_lat_e7": int(lower_max_lat * 10000000) if use_lower_zoom_for_bounds else int(higher_max_lat * 10000000),
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "center_zoom": (max_zoom + min_zoom) // 2,
            "center_lon_e7": int(10000000 * (higher_min_lon + higher_max_lon)/2),
            "center_lat_e7": int(10000000 * (higher_min_lat + higher_max_lat)/2),
        }
        if format_string in "pbf":
            header["tile_type"] = TileType.MVT
        elif format_string == "png":
            header["tile_type"] = TileType.PNG
        elif format_string == "jpeg":
            header["tile_type"] = TileType.JPEG
        elif format_string == "webp":
            header["tile_type"] = TileType.WEBP
        elif format_string == "avif":
            header["tile_type"] = TileType.AVIF
        else:
            header["tile_type"] = TileType.UNKNOWN

        return header
         
    def get_header_and_metadata(self, tiles):
        tiles_info = self.get_info(tiles)

        header = self.get_header(tiles, use_lower_zoom_for_bounds=False, tiles_info=tiles_info)
        header_for_mosaic = self.get_header(tiles, use_lower_zoom_for_bounds=True, tiles_info=tiles_info)

        src_metadata = self.reader.get_metadata()

        metadata = copy.deepcopy(src_metadata)

        min_zoom = header['min_zoom']
        max_zoom = header['max_zoom']
        if 'vector_layers' in metadata:
            for layer in metadata['vector_layers']:
                if layer['maxzoom'] > max_zoom:
                    layer['maxzoom'] = max_zoom
                if layer['minzoom'] < min_zoom:
                    layer['minzoom'] = min_zoom

        return header, header_for_mosaic, metadata
    
    def write_mosaic_file(self, mosaic_data):
        if len(self.slices) <= 1:
            print('No slices to write mosaic file')
            return

        out_mosaic_file = f'{self.to_pmtiles_prefix}.mosaic.json'
        with open(out_mosaic_file, 'w') as f:
            json.dump(mosaic_data, f)
        print(f'Wrote mosaic file to {out_mosaic_file}')

    def write_partitions(self):

        # go through all tiles and create a list of all tiles and a mapping from tile to slice index
        # could have calculate bounds here, but easier/more readable to do it later
        all_tiles = []
        tiles_by_idx = {}

        for t, idx in self.tiles_to_slice_idx.items():
            if idx not in tiles_by_idx:
                tiles_by_idx[idx] = []

            tiles_by_idx[idx].append(t)

            all_tiles.append(t)

        headers = []
        headers_for_mosaic = []
        metadatas = []
        writers = []
        out_pmtiles_files = []
        for i,slice in enumerate(self.slices):
            tiles = tiles_by_idx[i]

            header, header_for_mosaic, metadata = self.get_header_and_metadata(tiles)
            headers.append(header)
            headers_for_mosaic.append(header_for_mosaic)
            metadatas.append(metadata)

            out_pmtiles_file = get_pmtiles_file_name(self.to_pmtiles_prefix, slice)
            out_pmtiles_files.append(out_pmtiles_file)
            Path(out_pmtiles_file).parent.mkdir(exist_ok=True, parents=True)

            writer = PMTilesWriter(open(out_pmtiles_file, 'wb'))
            writers.append(writer)

        full_header = self.get_header(all_tiles, use_lower_zoom_for_bounds=False)
        full_metadata = self.reader.get_metadata()

        done = set()
        for tile, tdata in self.reader.all():
            if tile in done:
                continue

            idx = self.tiles_to_slice_idx[tile]
            writer = writers[idx]
            header = headers[idx]
            should_be_compressed = (header['tile_compression'] == Compression.GZIP)
            if should_be_compressed and tdata[0:2] != b"\x1f\x8b":
                tdata = gzip.compress(tdata)
            
            tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)
            writer.write_tile(tile_id, tdata)
            done.add(tile)

        for i, slice in enumerate(self.slices):
            out_pmtiles_file = out_pmtiles_files[i]
            print(f'writing partition {slice} to {out_pmtiles_file}')
            writer = writers[i]
            header = headers[i]
            metadata = metadatas[i]
            writer.finalize(header, metadata)
            file_size = Path(out_pmtiles_file).stat().st_size
            delta = file_size - self.expected_slice_sizes[i]
            print(f'partition {slice} written to {out_pmtiles_file} with size {file_size} bytes, expected size was {self.expected_slice_sizes[i]} bytes, delta: {delta} bytes')

        mosaic_data = {
            'version': 1,
            'metadata': full_metadata,
            'header': convert_header(full_header, HEADER_EXPORT_KEYS),
            'slices': {}
        }

        for i,slice in enumerate(self.slices):
            out_pmtiles_file = out_pmtiles_files[i]
            key = Path(out_pmtiles_file).name
            header = headers_for_mosaic[i]
            mosaic_data['slices'][key] = {
                'header': convert_header(header, SLICE_HEADER_EXPORT_KEYS)
            }

        self.write_mosaic_file(mosaic_data)

# TODO: this code doesn't account for tile redundency and compression that may happen when writing to pmtiles 
# TODO: maybe keep track of delta size based on the number of tiles being added into each partition instead fixing it ahead of time.
# TODO: handle cases where vertical striping fails because some single x stripe is too large
#       may be slice that x strip by y, if that also doesn't fix it and we have a single x,y area whcih is too big, drill down by z
def partition_main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-source', action='append', required=True, help='Path to a source file or directory. Can be repeated.')
    parser.add_argument('--to-pmtiles', required=True, help='Output PMTiles file.')
    parser.add_argument('--size-limit', default='github_release', type=parse_size, help='Maximum size of each partition. Can be a number in bytes or a preset: github_release (2G), github_file (100M), cloudflare_object (512M). Can also be a number with optional units (K, M, G). Default is github_release (2G).')
    parser.add_argument('--delta-estimate', required=False, type=int, help='Estimated delta above tile data. This is used to calculate the final size of each partition. if not provided it will be calculated based on the size limit.. approximately 5MB for 2GB size limit.')
    args = parser.parse_args(args)

    if not args.to_pmtiles.endswith('.pmtiles'):
        parser.error("Output PMTiles file must end with '.pmtiles'")
    to_pmtiles_prefix = args.to_pmtiles.removesuffix('.pmtiles')

    reader = create_source_from_paths(args.from_source)

    size_limit_bytes = args.size_limit
    adjusted_size_limit = adjust_size_limit(size_limit_bytes, args.delta_estimate)
    print(f'Partitioning with size limit: {adjusted_size_limit} bytes')

    partitioner = Partitioner(reader, to_pmtiles_prefix, args.size_limit)

    partitioner.partition()

    partitioner.write_partitions()

def cli():
    partition_main(sys.argv[1:])

if __name__ == '__main__':
    cli()


