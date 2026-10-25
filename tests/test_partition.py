import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mercantile import Tile
from pmtiles.tile import tileid_to_zxy

from pmtiles_mosaic.partition import CheckpointablePMTilesWriter, Partitioner, get_header_base
from tests.helpers import LOGGER, MemorySource, read_pmtiles


def tile_from_id(tile_id):
    z, x, y = tileid_to_zxy(tile_id)
    return Tile(x, y, z)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.writer = CheckpointablePMTilesWriter(get_header_base({'format': 'png'}), {'format': 'png'}, logger=LOGGER)
        self.addCleanup(lambda: self.writer.writer.f.close())
        self.addCleanup(lambda: self.writer.writer.tile_f.close())

    def test_rollback_discards_tiles_added_to_a_deduplicated_run(self):
        first, discarded, last = [tile_from_id(i) for i in (1, 2, 3)]
        self.writer.write_tile(first, b'same')
        self.writer.checkpoint()
        for _ in range(2):
            self.writer.write_tile(discarded, b'same')
            self.writer.rollback()
        self.writer.write_tile(last, b'other')
        output = self.root / 'rollback.pmtiles'
        self.writer.finalize(output)
        self.writer.writer.f.flush()
        self.assertEqual(read_pmtiles(output), {first: b'same', last: b'other'})

    def test_size_measurement_preserves_entry_order_and_matches_final_size(self):
        tiles = [tile_from_id(i) for i in (3, 1, 2)]
        for tile in tiles:
            self.writer.write_tile(tile, b'same')
        entries = [(e.tile_id, e.run_length) for e in self.writer.writer.tile_entries]
        size = self.writer.get_size()
        self.assertEqual([(e.tile_id, e.run_length) for e in self.writer.writer.tile_entries], entries)
        output = self.root / 'measured.pmtiles'
        self.writer.finalize(output)
        self.writer.writer.f.flush()
        self.assertEqual(output.stat().st_size, size)
        self.assertEqual(read_pmtiles(output), dict.fromkeys(tiles, b'same'))

    def test_failed_size_measurement_leaves_writer_usable(self):
        first, second = tile_from_id(1), tile_from_id(2)
        self.writer.write_tile(first, b'first')
        original_file = self.writer.writer.f
        original_tiles = self.writer.writer.tile_f
        with patch('pmtiles.writer.Writer.finalize', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.writer.get_size()
        self.assertIs(self.writer.writer.f, original_file)
        self.assertIs(self.writer.writer.tile_f, original_tiles)
        self.writer.write_tile(second, b'second')
        output = self.root / 'recovered.pmtiles'
        self.writer.finalize(output)
        self.writer.writer.f.flush()
        self.assertEqual(read_pmtiles(output), {first: b'first', second: b'second'})


class RecursivePartitionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def make_partitioner(self, tiles, size_limit=1_000_000, should_cache=True, **options):
        source = MemorySource(tiles)
        partitioner = Partitioner(source, str(self.root / 'map'), size_limit, should_cache, logger=LOGGER, **options)
        self.addCleanup(partitioner.cache_file.close)
        return partitioner

    def assert_output_tiles(self, partitioner, expected):
        actual = {}
        for name in partitioner.partition_names:
            tiles = read_pmtiles(self.root / name)
            self.assertFalse(actual.keys() & tiles.keys())
            actual.update(tiles)
            self.assertLessEqual((self.root / name).stat().st_size, partitioner.size_limit_bytes)
        self.assertEqual(actual, expected)

    def test_sparse_zooms_with_and_without_cache(self):
        tiles = {Tile(0, 0, 0): b'root', Tile(3, 2, 2): b'middle', Tile(15, 8, 4): b'leaf'}
        for cache in (True, False):
            with self.subTest(cache=cache):
                partitioner = self.make_partitioner(tiles, should_cache=cache)
                partitioner.partition()
                self.assert_output_tiles(partitioner, tiles)

    def test_sparse_zooms_split_without_losing_or_duplicating_tiles(self):
        tiles = {
            Tile(0, 0, 0): b'a' * 300,
            Tile(0, 0, 2): b'b' * 300,
            Tile(0, 3, 2): b'c' * 300,
            Tile(3, 3, 2): b'd' * 300,
            Tile(15, 15, 4): b'e' * 300,
        }
        partitioner = self.make_partitioner(tiles, size_limit=600)
        partitioner.partition()
        partitioner.finalize()
        self.assertGreater(partitioner.part_count, 1)
        self.assert_output_tiles(partitioner, tiles)

    def test_empty_source_fails_before_creating_partitions(self):
        partitioner = self.make_partitioner({})
        with self.assertRaisesRegex(ValueError, 'No tiles'):
            partitioner.partition()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_parent_tile_fails_without_recursing_into_its_children(self):
        partitioner = self.make_partitioner({Tile(0, 0, 0): b'a' * 1000, Tile(0, 0, 1): b'b'}, size_limit=600)
        with self.assertRaisesRegex(ValueError, 'cannot fit'):
            partitioner.partition()
