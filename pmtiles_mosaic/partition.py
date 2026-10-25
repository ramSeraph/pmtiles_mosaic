# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "mercantile",
#     "pmtiles",
#     "pillow",
# ]
# ///

import sys
import json
import gzip
import copy
import tempfile
import argparse
import logging

from pathlib import Path

import io

import mercantile
from PIL import Image
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer as PMTilesWriter

from .tile_sources import create_source_from_paths
from .partition_common import (
    HEADER_EXPORT_KEYS, SLICE_HEADER_EXPORT_KEYS, convert_header, get_bounds,
    get_header, get_header_and_metadata, get_header_base, get_info,
    get_pmtiles_file_name, parse_size, prepare_tile_data,
)
from .logger import LoggerMixin, get_logger


def copy_tile_entries(entries):
    entries = entries.copy()
    # PMTiles write_tile only mutates the last entry when extending a run.
    if entries:
        entries[-1] = copy.copy(entries[-1])
    return entries


def is_transparent_empty(tdata, tile_type, logger=None):
    if tile_type not in (TileType.PNG, TileType.WEBP):
        return False
    try:
        if tdata.startswith(b'\x1f\x8b'):
            tdata = gzip.decompress(tdata)
        image_format = 'PNG' if tile_type == TileType.PNG else 'WEBP'
        with Image.open(io.BytesIO(tdata), formats=[image_format]) as image:
            if 'A' in image.getbands() or 'transparency' in image.info:
                return image.convert('RGBA').getchannel('A').getextrema()[1] == 0
    except (OSError, ValueError):
        (logger or logging.getLogger(__name__)).warning('Error checking transparency', exc_info=True)
    return False


class WriterCheckpoint:
    def __init__(self, writer, tiles):
        self.tile_entries = copy_tile_entries(writer.tile_entries)
        self.hash_to_offset = copy.copy(writer.hash_to_offset)
        self.offset = writer.offset
        self.addressed_tiles = writer.addressed_tiles
        self.clustered = writer.clustered
        self.tiles = copy.copy(tiles)
        self.size = 0


class CheckpointablePMTilesWriter(LoggerMixin):
    def __init__(self, header_base, metadata, logger=None, exclude_transparent=False):
        self.header_base = header_base
        self.tile_compression = header_base.get('tile_compression', Compression.NONE)
        self.metadata = metadata
        self.logger = logger
        self.exclude_transparent = exclude_transparent
        self.tile_type = header_base.get('tile_type')

        self.header_f = tempfile.TemporaryFile()
        self.writer = PMTilesWriter(self.header_f)
        self.tiles = []
        self.last_checkpoint = None
        self._cached_size = 0
        self._is_size_cached = True


    def is_transparent_empty(self, tdata, tile_type):
        return is_transparent_empty(tdata, tile_type, self.logger)



    def write_tile(self, tile, tdata):
        self._is_size_cached = False

        if self.exclude_transparent and self.is_transparent_empty(tdata, self.tile_type):
            return

        self.tiles.append(tile)
        tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)

        tdata = prepare_tile_data(tdata, self.tile_compression)

        self.writer.write_tile(tile_id, tdata)

    def checkpoint(self):
        if not self._is_size_cached:
            self._cached_size = self._calculate_size()
            self._is_size_cached = True

        self.last_checkpoint = WriterCheckpoint(self.writer, self.tiles)
        self.last_checkpoint.size = self._cached_size


    def rollback(self):
        if self.last_checkpoint is None:
            raise Exception("No checkpoint to rollback to")

        self.tiles = copy.copy(self.last_checkpoint.tiles)

        self.writer.tile_entries = copy_tile_entries(self.last_checkpoint.tile_entries)
        self.writer.hash_to_offset = copy.copy(self.last_checkpoint.hash_to_offset)
        self.writer.offset = self.last_checkpoint.offset
        self.writer.addressed_tiles = self.last_checkpoint.addressed_tiles
        self.writer.clustered = self.last_checkpoint.clustered

        self.writer.tile_f.seek(self.writer.offset)
        self.writer.tile_f.truncate(self.writer.offset)

        self._cached_size = self.last_checkpoint.size
        self._is_size_cached = True


    def is_empty(self):
        return len(self.tiles) == 0

    def finalize(self, pmtiles_fname):
        self.writer.f.close()
        Path(pmtiles_fname).parent.mkdir(parents=True, exist_ok=True)
        header, header_for_mosaic, metadata = get_header_and_metadata(self.header_base, self.metadata, self.tiles)
        with open(pmtiles_fname, 'wb') as output:
            self.writer.f = output
            self.writer.finalize(header, metadata)
        return header_for_mosaic

    def get_size(self):
        if self._is_size_cached:
            return self._cached_size

        self._cached_size = self._calculate_size()
        self._is_size_cached = True
        return self._cached_size

    def _calculate_size(self):
        if not self.tiles:
            return 0

        header, _, metadata = get_header_and_metadata(self.header_base, self.metadata, self.tiles)
        # Finalization sorts entries and closes tile_f. Run it on a copy with
        # empty payload storage, then add the real payload size to the overhead.
        writer = copy.copy(self.writer)
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as tiles:
            writer.f = output
            writer.tile_f = tiles
            writer.finalize(header, metadata)
            return self.writer.offset + output.tell()

    def close(self):
        self.writer.f.close()
        self.writer.tile_f.close()
        self.last_checkpoint = None


class Partitioner(LoggerMixin):
    def __init__(self, reader, to_pmtiles_prefix, size_limit_bytes, should_cache, logger=None, exclude_transparent=False):
        self.reader = reader
        self.logger = logger
        self.exclude_transparent = exclude_transparent

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
        self._writers = set()


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
        caching_msg = ''
        if self.should_cache:
            caching_msg = ' and caching them as well'

        self.log_info(f'Collecting tiles from source{caching_msg}...')

        count = 0
        check_transparency = self.exclude_transparent and self.header_base['tile_type'] in (TileType.PNG, TileType.WEBP)
        if self.should_cache or check_transparency:
            for tile, tdata in self.reader.all():
                if check_transparency and is_transparent_empty(tdata, self.header_base['tile_type'], self.logger):
                    continue
                if count % 100000 == 0:
                    self.log_info(f'Handled {count} tiles...')
                self.all_tiles.append(tile)
                if self.should_cache:
                    self.cache_tile(tile, tdata)
                count += 1
        else:
            for tile, tsize in self.reader.all_sizes():
                if count % 100000 == 0:
                    self.log_info(f'Handled {count} tiles...')
                self.all_tiles.append(tile)
                count += 1

    def complete_current_slice(self, curr_slice_writer, context, pmtiles_file_name=None):
        if pmtiles_file_name is None:
            pmtiles_file_name = self.get_current_partition_filename()

        self.log_info(f'Finalizing partition {pmtiles_file_name} with {len(curr_slice_writer.tiles)} tiles, context: {context}')
        try:
            header = curr_slice_writer.finalize(pmtiles_file_name)
        finally:
            curr_slice_writer.close()
            self._writers.discard(curr_slice_writer)
        self.headers.append(header)
        self.partition_names.append(Path(pmtiles_file_name).name)
        self.part_count += 1
 
    def create_new_writer(self):
        writer = CheckpointablePMTilesWriter(self.header_base, self.src_metadata, logger=self.logger)
        self._writers.add(writer)
        return writer

    def partition_by_y(self, from_zoom_level, to_zoom_level, x_tiles, context):
        self.log_info(f'Partitioning by x for context: {context}, {from_zoom_level=}-->{to_zoom_level=}')

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
        self.log_info(f'Partitioning by x for context: {context}, {from_zoom_level=}-->{to_zoom_level=}')

        tiles_by_x = {}
        for zoom_level in range(from_zoom_level, to_zoom_level + 1):
            for tile in tiles_by_z.get(zoom_level, []):

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

        self.log_info(f'Partitioning by x for context: {context}, {from_zoom_level=}-->{to_zoom_level=}')

        tiles_by_z = {}
        for tile in tiles:
            if tile.z not in tiles_by_z:
                tiles_by_z[tile.z] = []
            tiles_by_z[tile.z].append(tile)

        top_slice_writer = self.create_new_writer()

        top_slice_max_level = None
        for zoom_level in range(from_zoom_level, to_zoom_level + 1):
            zoom_tiles = tiles_by_z.get(zoom_level, [])
            if not zoom_tiles:
                continue
            self.log_debug(f'Trying to add zoom level {zoom_level} to top slice for context: {context}')
            top_slice_writer.checkpoint()


            self.log_debug(f'Adding zoom level {zoom_level} with {len(zoom_tiles)} tiles to top slice for context: {context}')
            for tile in zoom_tiles:
                top_slice_writer.write_tile(tile, self.get_tile_data(tile))

            


            size_till_now = top_slice_writer.get_size()
            self.log_debug(f'Size after adding zoom level {zoom_level} is {size_till_now} bytes for context: {context}')
            if size_till_now > self.size_limit_bytes:
                top_slice_writer.rollback()
                if top_slice_writer.is_empty() and len(zoom_tiles) == 1:
                    raise ValueError(f'Tile {zoom_tiles[0]} cannot fit within the {self.size_limit_bytes}-byte size limit')
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

        if len(tiles) <= 1:
            raise ValueError(f'Cannot partition further, context: {context}')

        new_context = context + [('z', new_from_zoom_level, to_zoom_level)]
        self.partition_by_x(new_from_zoom_level, to_zoom_level, tiles_by_z, new_context)

    def partition(self):
        try:
            self.collect_tiles()
            if not self.all_tiles:
                raise ValueError('No tiles found in the source')
            self.partition_by_z(self.min_zoom_level, self.max_zoom_level, self.all_tiles, [])
        finally:
            self.close()

    def close(self):
        for writer in self._writers:
            writer.close()
        self._writers.clear()
        self.cache_file.close()

    def write_mosaic_file(self, mosaic_data):

        out_mosaic_file = f'{self.to_pmtiles_prefix}.mosaic.json'
        with open(out_mosaic_file, 'w') as f:
            json.dump(mosaic_data, f)
        self.log_info(f'Wrote mosaic file to {out_mosaic_file}')

    def finalize(self):
        if self.part_count <= 1:
            return

        self.log_info(f'Finalizing {self.part_count} partitions')
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
    parser.add_argument('--exclude-transparent', action='store_true', default=False, help='Exclude transparent empty tiles from raster sources (PNG, WEBP).')
    parser.add_argument('--log-level', '-l', type=str, default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], help='Set the logging level.')
    args = parser.parse_args(args)

    logger = get_logger("partition", args.log_level)

    if not args.to_pmtiles.endswith('.pmtiles'):
        parser.error("Output PMTiles file must end with '.pmtiles'")
    to_pmtiles_prefix = args.to_pmtiles.removesuffix('.pmtiles')

    reader = create_source_from_paths(args.from_source, logger=logger)

    logger.info(f'Size limit: {args.size_limit} bytes')

    try:
        partitioner = Partitioner(reader, to_pmtiles_prefix, args.size_limit, not args.no_cache, logger=logger, exclude_transparent=args.exclude_transparent)
        partitioner.partition()
        partitioner.finalize()
    finally:
        reader.cleanup()

def cli():
    partition_main(sys.argv[1:])

if __name__ == '__main__':
    cli()
