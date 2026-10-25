import copy
import gzip
import tempfile
import unittest
from pathlib import Path

from mercantile import Tile
from pmtiles.tile import Compression

from pmtiles_mosaic.partition import Partitioner, get_header_and_metadata, get_header_base
from pmtiles_mosaic.partition_basic import Partitioner as BasicPartitioner
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from tests.helpers import LOGGER, MemorySource


class SharedPartitionTests(unittest.TestCase):
    def test_optional_layer_bounds_are_clipped_without_mutating_input(self):
        metadata = {'format': 'mvt', 'vector_layers': [
            {'id': 'roads', 'fields': {'name': 'String'}},
            {'id': 'water', 'minzoom': 3, 'maxzoom': 5},
            {'id': 'buildings', 'minzoom': 10, 'maxzoom': 15},
        ]}
        original = copy.deepcopy(metadata)
        tiles = {Tile(0, 0, 5): b'first', Tile(0, 0, 6): b'second'}
        source = MemorySource(tiles, metadata)
        basic = BasicPartitioner(source, 'unused', 1000, logger=LOGGER)
        recursive_result = get_header_and_metadata(get_header_base(metadata), metadata, tiles)
        self.assertEqual(basic.get_header_and_metadata(tiles), recursive_result)
        result = recursive_result[2]
        self.assertEqual(result['vector_layers'], [
            {'id': 'roads', 'fields': {'name': 'String'}, 'minzoom': 5, 'maxzoom': 6},
            {'id': 'water', 'minzoom': 5, 'maxzoom': 5},
        ])
        result['vector_layers'][0]['fields']['name'] = 'Boolean'
        self.assertEqual(metadata, original)

    def test_both_partitioners_write_payloads_matching_their_compression_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tiles = [Tile(0, 0, 0), Tile(0, 0, 1)]
            data = b'tile payload'
            for kind in ('recursive', 'basic'):
                for tile_format in ('mvt', 'png'):
                    with self.subTest(kind=kind, format=tile_format):
                        payload = data if tile_format == 'mvt' else gzip.compress(data)
                        source = MemorySource(dict.fromkeys(tiles, payload), {'format': tile_format})
                        prefix = root / f'{kind}-{tile_format}'
                        if kind == 'recursive':
                            partitioner = Partitioner(source, str(prefix), 1_000_000, True, logger=LOGGER)
                            partitioner.partition()
                            partitioner.finalize()
                        else:
                            partitioner = BasicPartitioner(source, str(prefix), 1_000_000, logger=LOGGER)
                            partitioner.partition()
                            partitioner.write_partitions()
                        output = PMTilesSource(prefix.with_suffix('.pmtiles'))
                        try:
                            header = output.reader.header()
                            self.assertEqual(header['tile_contents_count'], 1)
                            for tile in tiles:
                                actual = output.get_tile_data(tile)
                                if tile_format == 'mvt':
                                    self.assertEqual(header['tile_compression'], Compression.GZIP)
                                    self.assertEqual(actual[4:8], b'\x00' * 4)
                                    self.assertEqual(gzip.decompress(actual), data)
                                else:
                                    self.assertEqual(header['tile_compression'], Compression.NONE)
                                    self.assertEqual(actual, data)
                        finally:
                            output.cleanup()
