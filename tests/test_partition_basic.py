import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mercantile import Tile

from pmtiles_mosaic.partition_basic import Partitioner, adjust_size_limit
from pmtiles_mosaic.errors import EmptySourceError
from tests.helpers import LOGGER, MemorySource, read_pmtiles, write_mbtiles


class BasicPartitionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_cli_reserves_requested_overhead_before_partitioning(self):
        tiles = {Tile(0, 0, 1): b'a' * 400_000, Tile(1, 0, 1): b'b' * 400_000}
        source = self.root / 'source.mbtiles'
        write_mbtiles(source, tiles)
        subprocess.run([
            sys.executable, '-m', 'pmtiles_mosaic.partition_basic',
            '--from-source', str(source), '--to-pmtiles', str(self.root / 'map.pmtiles'),
            '--size-limit', '1M', '--delta-estimate', '262144', '--log-level', 'ERROR',
        ], check=True, capture_output=True, text=True, timeout=30)
        mosaic = json.loads((self.root / 'map.mosaic.json').read_text())
        self.assertEqual(len(mosaic['slices']), 2)
        actual = {}
        for name in mosaic['slices']:
            self.assertLess((self.root / name).stat().st_size, 1024 * 1024)
            actual.update(read_pmtiles(self.root / name))
        self.assertEqual(actual, tiles)

    def test_invalid_overhead_is_rejected(self):
        for delta in (-1, 1024, 2048):
            with self.subTest(delta=delta), self.assertRaises(argparse.ArgumentTypeError):
                adjust_size_limit(1024, LOGGER, delta)
        self.assertEqual(adjust_size_limit(1024, LOGGER, 0), 1024)

    def test_empty_source_does_not_create_an_empty_partition(self):
        partitioner = Partitioner(MemorySource({}), str(self.root / 'empty'), 1000, logger=LOGGER)
        self.assertEqual(partitioner.get_buckets({}, {}), ([], [], []))
        with self.assertRaisesRegex(EmptySourceError, 'No tiles'):
            partitioner.partition()
        self.assertEqual(partitioner.slices, [])

    def test_missing_low_zooms_do_not_create_an_empty_top_slice(self):
        source = MemorySource({Tile(0, 0, 2): b'a' * 400, Tile(3, 0, 2): b'b' * 400})
        source.min_zoom = 0
        partitioner = Partitioner(source, str(self.root / 'sparse'), 600, logger=LOGGER)
        partitioner.partition()
        self.assertEqual(len(partitioner.slices), 2)
        self.assertEqual(set(partitioner.tiles_to_slice_idx.values()), {0, 1})
