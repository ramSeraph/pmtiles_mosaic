import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mercantile import Tile

from pmtiles_mosaic.mbtiles_source import MBTilesSource
from pmtiles_mosaic.stacked_source import StackedTileSource
from pmtiles_mosaic.tiles_common import MissingTileError
from tests.helpers import LOGGER, read_pmtiles, write_mbtiles


class MBTilesSourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tiles = {Tile(0, 0, 0): b'root', Tile(1, 0, 1): b'north', Tile(1, 3, 2): b'south'}
        self.path = self.root / 'input.mbtiles'
        write_mbtiles(self.path, self.tiles)
        self.source = MBTilesSource(self.path, logger=LOGGER)
        self.addCleanup(self.source.cleanup)

    def test_iteration_matches_lookup_and_xyz_coordinates(self):
        self.assertEqual(dict(self.source.all()), self.tiles)
        self.assertEqual(dict(self.source.all_sizes()), {t: len(d) for t, d in self.tiles.items()})
        self.assertEqual(dict(self.source.all_z_sizes(2)), {Tile(1, 3, 2): 5})
        self.assertEqual(list(self.source.all_z_sizes(3)), [])
        for tile, data in self.tiles.items():
            self.assertEqual(self.source.get_tile_data(tile), data)
        with self.assertRaises(MissingTileError):
            self.source.get_tile_data(Tile(0, 0, 2))

    def test_stacked_iteration_retains_first_source_precedence(self):
        stacked = StackedTileSource([self.source, self.source], logger=LOGGER)
        self.assertEqual(dict(stacked.all_sizes()), {t: len(d) for t, d in self.tiles.items()})
        self.assertEqual(list(stacked.all_z_sizes(2)), [(Tile(1, 3, 2), 5)])

    def test_both_partition_clis_read_mbtiles(self):
        for module, options in [('partition', ['--no-cache']), ('partition_basic', [])]:
            with self.subTest(module=module):
                output = self.root / f'{module}.pmtiles'
                subprocess.run([
                    sys.executable, '-m', f'pmtiles_mosaic.{module}',
                    '--from-source', str(self.path), '--to-pmtiles', str(output),
                    '--log-level', 'ERROR', *options,
                ], check=True, capture_output=True, text=True, timeout=30)
                self.assertEqual(read_pmtiles(output), self.tiles)
