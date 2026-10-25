import gzip
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
        self.mvt_bytes = gzip.compress(b'vector tile', mtime=0)

    def tile_bytes(self, tile_format):
        return self.mvt_bytes if tile_format in ('mvt', 'pbf') else self.jpeg_bytes

    def disk_source(self, tile_format):
        directory = self.root / f'disk-{tile_format}'
        (directory / '0/0').mkdir(parents=True)
        (directory / f'0/0/0.{tile_format}').write_bytes(self.tile_bytes(tile_format))
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
            connection.execute('INSERT INTO tiles VALUES (0, 0, 0, ?)', (self.tile_bytes(tile_format),))
            connection.commit()
        finally:
            connection.close()
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        return source

    def pmtiles_source(self, tile_format):
        path = self.root / f'{tile_format}.pmtiles'
        canonical_format = 'mvt' if tile_format in ('mvt', 'pbf') else 'jpg'
        header = get_header([self.tile], get_header_base({'format': canonical_format}))
        with path.open('wb') as output:
            writer = Writer(output)
            writer.write_tile(0, self.tile_bytes(tile_format))
            writer.finalize(header, {'format': tile_format})
        source = PMTilesSource(path)
        self.addCleanup(source.cleanup)
        return source

    def test_disk_source_accepts_canonical_formats_and_rejects_aliases(self):
        for tile_format in ('jpg', 'mvt'):
            with self.subTest(format=tile_format):
                source = DiskTilesSource(self.disk_source(tile_format))
                self.assertEqual(source.get_metadata()['format'], tile_format)
                self.assertEqual(source.get_tile_data(self.tile), self.tile_bytes(tile_format))
        for tile_format in ('jpeg', 'pbf'):
            with self.subTest(format=tile_format), self.assertRaisesRegex(UnsupportedTileFormatError, repr(tile_format)):
                DiskTilesSource(self.disk_source(tile_format))

    def test_archive_sources_accept_canonical_formats_and_reject_aliases(self):
        for make_source in (self.mbtiles_source, self.pmtiles_source):
            for tile_format in ('jpg', 'mvt'):
                with self.subTest(source=make_source.__name__, format=tile_format):
                    source = make_source(tile_format)
                    self.assertEqual(source.get_metadata()['format'], tile_format)
                    self.assertEqual(source.get_tile_data(self.tile), self.tile_bytes(tile_format))
            for tile_format in ('jpeg', 'pbf'):
                with self.subTest(source=make_source.__name__, format=tile_format):
                    with self.assertRaisesRegex(UnsupportedTileFormatError, repr(tile_format)):
                        make_source(tile_format).get_metadata()

    def test_stacked_sources_do_not_hide_an_invalid_format(self):
        first = self.mbtiles_source('jpg')
        for tile_format in ('jpeg', 'pbf'):
            with self.subTest(format=tile_format):
                source = StackedTileSource([first, self.mbtiles_source(tile_format)])
                with self.assertRaises(UnsupportedTileFormatError):
                    source.get_metadata()

    def test_partitioners_agree_on_formats_and_compression(self):
        for tile_format, tile_type in (
            ('jpg', TileType.JPEG), ('png', TileType.PNG), ('webp', TileType.WEBP),
            ('avif', TileType.AVIF), ('mvt', TileType.MVT),
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

        for tile_format in ('jpeg', 'pbf'):
            with self.subTest(format=tile_format):
                source = SimpleNamespace(
                    min_zoom=0, max_zoom=0, get_metadata=lambda: {'format': tile_format},
                )
                basic = BasicPartitioner(source, str(self.root / 'output'), 1_000_000)
                with self.assertRaises(UnsupportedTileFormatError):
                    get_header_base(source.get_metadata())
                with self.assertRaises(UnsupportedTileFormatError):
                    basic.get_header([self.tile])

    def test_partition_clis_preserve_canonical_formats_and_bytes(self):
        for tile_format, tile_type, compression in (
            ('jpg', TileType.JPEG, Compression.NONE), ('mvt', TileType.MVT, Compression.GZIP),
        ):
            directory = self.disk_source(tile_format)
            for module in ('partition', 'partition_basic'):
                with self.subTest(module=module, format=tile_format):
                    output = self.root / f'{module}-{tile_format}.pmtiles'
                    subprocess.run([
                        sys.executable, '-m', f'pmtiles_mosaic.{module}',
                        '--from-source', str(directory), '--to-pmtiles', str(output),
                        '--log-level', 'ERROR',
                    ], check=True, capture_output=True, text=True, timeout=30)
                    source = PMTilesSource(output)
                    try:
                        self.assertEqual(source.get_metadata()['format'], tile_format)
                        self.assertEqual(source.reader.header()['tile_type'], tile_type)
                        self.assertEqual(source.reader.header()['tile_compression'], compression)
                        self.assertEqual(source.get_tile_data(self.tile), self.tile_bytes(tile_format))
                    finally:
                        source.cleanup()

    def test_merger_rejects_aliases_before_creating_output(self):
        for tile_format in ('jpeg', 'pbf'):
            with self.subTest(format=tile_format):
                manifest = self.root / 'map.mosaic.json'
                manifest.write_text(json.dumps({
                    'version': 1, 'metadata': {'format': tile_format}, 'slices': {},
                }))
                output = self.root / 'merged.pmtiles'
                merger = Merger(str(manifest), 'pmtiles', output, 60, 3)
                with self.assertRaisesRegex(UnsupportedTileFormatError, repr(tile_format)):
                    merger.process()
                self.assertFalse(output.exists())
                self.assertFalse(merger.tracker_file.exists())


if __name__ == '__main__':
    unittest.main()
