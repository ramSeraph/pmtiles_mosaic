import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import mercantile
from PIL import Image
from pmtiles.tile import Compression, TileType
from pmtiles.writer import Writer

from pmtiles_mosaic.disk_source import DiskTilesSource
from pmtiles_mosaic.download_mosaic import Merger
from pmtiles_mosaic.mbtiles_source import MBTilesSource
from pmtiles_mosaic.partition import get_header, get_header_base
from pmtiles_mosaic.partition_basic import Partitioner as BasicPartitioner
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from pmtiles_mosaic.stacked_source import StackedTileSource
from pmtiles_mosaic.tiles_common import UnsupportedTileFormatError


class TileFormatTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tile = mercantile.Tile(0, 0, 0)
        image = io.BytesIO()
        Image.new('RGB', (1, 1), 'red').save(image, format='JPEG')
        self.jpeg_bytes = image.getvalue()

    def disk_source(self, tile_format):
        directory = self.root / f'disk-{tile_format}'
        (directory / '0/0').mkdir(parents=True)
        (directory / f'0/0/0.{tile_format}').write_bytes(self.jpeg_bytes)
        (directory / 'metadata.json').write_text(json.dumps({
            'format': tile_format, 'minzoom': 0, 'maxzoom': 0,
        }))
        return directory

    def mbtiles_source(self, tile_format):
        path = self.root / f'{tile_format}.mbtiles'
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
            connection.execute(
                'CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, '
                'tile_row INTEGER, tile_data BLOB)'
            )
            connection.executemany('INSERT INTO metadata VALUES (?, ?)', [
                ('format', tile_format), ('minzoom', '0'), ('maxzoom', '0'),
            ])
            connection.execute('INSERT INTO tiles VALUES (0, 0, 0, ?)', (self.jpeg_bytes,))
            connection.commit()
        finally:
            connection.close()
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        return source

    def pmtiles_source(self, tile_format):
        path = self.root / f'{tile_format}.pmtiles'
        header = get_header([self.tile], get_header_base({'format': 'jpg'}))
        with path.open('wb') as output:
            writer = Writer(output)
            writer.write_tile(0, self.jpeg_bytes)
            writer.finalize(header, {'format': tile_format})
        source = PMTilesSource(path)
        self.addCleanup(source.cleanup)
        return source

    def test_disk_source_accepts_jpg_and_rejects_jpeg(self):
        source = DiskTilesSource(self.disk_source('jpg'))
        self.assertEqual(source.get_metadata()['format'], 'jpg')
        self.assertEqual(source.get_tile_data(self.tile), self.jpeg_bytes)
        with self.assertRaisesRegex(UnsupportedTileFormatError, "'jpeg'"):
            DiskTilesSource(self.disk_source('jpeg'))

    def test_archive_sources_accept_jpg_and_reject_jpeg(self):
        for make_source in (self.mbtiles_source, self.pmtiles_source):
            with self.subTest(source=make_source.__name__):
                source = make_source('jpg')
                self.assertEqual(source.get_metadata()['format'], 'jpg')
                self.assertEqual(source.get_tile_data(self.tile), self.jpeg_bytes)
                with self.assertRaisesRegex(UnsupportedTileFormatError, "'jpeg'"):
                    make_source('jpeg').get_metadata()

    def test_stacked_sources_do_not_hide_an_invalid_format(self):
        source = StackedTileSource([self.mbtiles_source('jpg'), self.mbtiles_source('jpeg')])
        with self.assertRaises(UnsupportedTileFormatError):
            source.get_metadata()

    def test_partitioners_agree_on_formats_and_compression(self):
        for tile_format, tile_type in (
            ('jpg', TileType.JPEG), ('png', TileType.PNG), ('webp', TileType.WEBP),
            ('avif', TileType.AVIF), ('pbf', TileType.MVT), ('mvt', TileType.MVT),
        ):
            with self.subTest(format=tile_format):
                source = SimpleNamespace(
                    min_zoom=0, max_zoom=0, get_metadata=lambda: {'format': tile_format},
                )
                basic = BasicPartitioner(source, str(self.root / 'output'), 1_000_000)
                expected_compression = Compression.GZIP if tile_type == TileType.MVT else Compression.NONE
                for header in (get_header_base(source.get_metadata()), basic.get_header([self.tile])):
                    self.assertEqual(header['tile_type'], tile_type)
                    self.assertEqual(header['tile_compression'], expected_compression)

        source = SimpleNamespace(
            min_zoom=0, max_zoom=0, get_metadata=lambda: {'format': 'jpeg'},
        )
        basic = BasicPartitioner(source, str(self.root / 'output'), 1_000_000)
        with self.assertRaises(UnsupportedTileFormatError):
            get_header_base(source.get_metadata())
        with self.assertRaises(UnsupportedTileFormatError):
            basic.get_header([self.tile])

    def test_partition_clis_preserve_jpg_metadata_and_bytes(self):
        directory = self.disk_source('jpg')
        for module in ('partition', 'partition_basic'):
            with self.subTest(module=module):
                output = self.root / f'{module}.pmtiles'
                subprocess.run([
                    sys.executable, '-m', f'pmtiles_mosaic.{module}',
                    '--from-source', str(directory), '--to-pmtiles', str(output),
                    '--log-level', 'ERROR',
                ], check=True, capture_output=True, text=True, timeout=30)
                source = PMTilesSource(output)
                try:
                    self.assertEqual(source.get_metadata()['format'], 'jpg')
                    self.assertEqual(source.reader.header()['tile_type'], TileType.JPEG)
                    self.assertEqual(source.reader.header()['tile_compression'], Compression.NONE)
                    self.assertEqual(source.get_tile_data(self.tile), self.jpeg_bytes)
                finally:
                    source.cleanup()

    def test_merger_rejects_jpeg_before_creating_output(self):
        manifest = self.root / 'map.mosaic.json'
        manifest.write_text(json.dumps({
            'version': 1, 'metadata': {'format': 'jpeg'}, 'slices': {},
        }))
        output = self.root / 'merged.pmtiles'
        merger = Merger(str(manifest), 'pmtiles', output, 60, 3)
        with self.assertRaisesRegex(UnsupportedTileFormatError, "'jpeg'"):
            merger.process()
        self.assertFalse(output.exists())
        self.assertFalse(merger.tracker_file.exists())


if __name__ == '__main__':
    unittest.main()
