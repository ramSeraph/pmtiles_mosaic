import sqlite3
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from mercantile import Tile
from pmtiles.writer import Writer

from pmtiles_mosaic.mbtiles_source import MBTilesSource
from pmtiles_mosaic.partition import Partitioner
from pmtiles_mosaic.partition_basic import Partitioner as BasicPartitioner
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from pmtiles_mosaic.tile_sources import create_source_from_paths
from tests.helpers import LOGGER, MemorySource, write_mbtiles, write_pmtiles


class ResourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tile = Tile(0, 0, 0)

    def test_pmtiles_cleanup_closes_mapping_and_file(self):
        path = self.root / 'source.pmtiles'
        write_pmtiles(path, {self.tile: b'tile'})
        source = PMTilesSource(path)
        self.assertEqual(source.get_tile_data(self.tile), b'tile')
        source.cleanup()
        source.cleanup()
        self.assertTrue(source.file.closed)
        self.assertTrue(source.mapping.closed)

    def test_mbtiles_reader_never_creates_or_writes_its_input(self):
        path = self.root / 'missing.mbtiles'
        with self.assertRaises(FileNotFoundError):
            MBTilesSource(path)
        self.assertFalse(path.exists())
        write_mbtiles(path, {self.tile: b'tile'})
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        with self.assertRaises(sqlite3.OperationalError):
            source.con.execute('DELETE FROM tiles')

    def test_source_factory_accepts_paths_and_sorts_globs(self):
        for name in ('b', 'a'):
            write_pmtiles(self.root / f'{name}.pmtiles', {self.tile: name.encode()})
        source = create_source_from_paths([self.root / '*.pmtiles'], logger=LOGGER)
        self.addCleanup(source.cleanup)
        self.assertEqual(source.get_tile_data(self.tile), b'a')

    def test_source_factory_cleans_up_after_later_input_failure(self):
        path = self.root / 'source.pmtiles'
        write_pmtiles(path, {self.tile: b'tile'})
        source = PMTilesSource(path)
        with patch('pmtiles_mosaic.tile_sources.PMTilesSource', return_value=source):
            with self.assertRaises(FileNotFoundError):
                create_source_from_paths([path, self.root / 'missing'])
        self.assertTrue(source.file.closed)
        self.assertTrue(source.mapping.closed)

    def test_recursive_partition_failure_closes_writer_and_cache(self):
        partitioner = Partitioner(MemorySource({self.tile: b'tile'}), str(self.root / 'map'), 1000, True, logger=LOGGER)
        writers = []
        create = partitioner.create_new_writer

        def capture_writer():
            writer = create()
            writers.append(writer)
            return writer

        with patch.object(partitioner, 'create_new_writer', side_effect=capture_writer):
            with patch.object(Writer, 'write_tile', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    partitioner.partition()
        self.assertTrue(partitioner.cache_file.closed)
        self.assertTrue(writers)
        for writer in writers:
            self.assertTrue(writer.writer.f.closed)
            self.assertTrue(writer.writer.tile_f.closed)

    def test_basic_partition_failure_closes_output_and_tile_storage(self):
        partitioner = BasicPartitioner(MemorySource({self.tile: b'tile'}), str(self.root / 'map'), 1000, logger=LOGGER)
        partitioner.partition()
        writers = []

        def capture_writer(output):
            writer = Writer(output)
            writers.append(writer)
            return writer

        with patch('pmtiles_mosaic.partition_basic.PMTilesWriter', side_effect=capture_writer):
            with patch.object(Writer, 'write_tile', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    partitioner.write_partitions()
        self.assertTrue(writers)
        for writer in writers:
            self.assertTrue(writer.f.closed)
            self.assertTrue(writer.tile_f.closed)

    def test_both_clis_clean_up_readers_on_metadata_failure(self):
        for name in ('partition', 'partition_basic'):
            with self.subTest(module=name):
                module = import_module(f'pmtiles_mosaic.{name}')
                source = MemorySource({self.tile: b'tile'})
                with patch.object(module, 'create_source_from_paths', return_value=source), \
                        patch.object(module, 'get_logger', return_value=LOGGER), \
                        patch.object(source, 'get_metadata', side_effect=ValueError('bad metadata')):
                    with self.assertRaisesRegex(ValueError, 'bad metadata'):
                        module.partition_main(['--from-source', 'source', '--to-pmtiles', str(self.root / 'out.pmtiles')])
                self.assertTrue(source.closed)
