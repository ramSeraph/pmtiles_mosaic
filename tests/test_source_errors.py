import gzip
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mercantile import Tile
from pmtiles.tile import deserialize_header, serialize_header

from pmtiles_mosaic.disk_source import DiskTilesSource
from pmtiles_mosaic.errors import EmptySourceError, SourceMetadataError, UnsupportedSourceError
from pmtiles_mosaic.mbtiles_source import MBTilesSource
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from pmtiles_mosaic.stacked_source import StackedTileSource
from pmtiles_mosaic.tile_sources import create_source_from_paths
from tests.helpers import LOGGER, write_mbtiles, write_pmtiles


class SourceErrorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tiles = {Tile(1, 1, 2): b'first', Tile(4, 4, 4): b'second'}

    def disk_directory(self, name, metadata, tiles=()):
        directory = self.root / name
        directory.mkdir()
        (directory / 'metadata.json').write_text(json.dumps(metadata))
        for tile in tiles:
            path = directory / f'{tile.z}/{tile.x}/{tile.y}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'tile')
        return directory

    def test_factory_distinguishes_missing_unsupported_and_empty_inputs(self):
        for name in ('missing', 'missing.mbtiles', 'missing.pmtiles', '*.pmtiles'):
            with self.subTest(name=name), self.assertRaises(FileNotFoundError) as caught:
                create_source_from_paths([self.root / name])
            self.assertEqual(caught.exception.filename, str(self.root / name))
        unsupported = self.root / 'map.tif'
        unsupported.write_bytes(b'not a tile archive')
        with self.assertRaisesRegex(UnsupportedSourceError, 'map.tif'):
            create_source_from_paths([unsupported])
        with self.assertRaises(EmptySourceError):
            create_source_from_paths([])

    def test_missing_disk_metadata_retains_the_filename(self):
        directory = self.root / 'tiles'
        directory.mkdir()
        with self.assertRaises(FileNotFoundError) as caught:
            DiskTilesSource(directory)
        self.assertEqual(caught.exception.filename, str(directory / 'metadata.json'))

    def test_invalid_disk_metadata_reports_source_and_preserves_parse_error(self):
        directory = self.root / 'tiles'
        directory.mkdir()
        (directory / 'metadata.json').write_text('{')
        with self.assertRaisesRegex(SourceMetadataError, 'metadata.json') as caught:
            DiskTilesSource(directory)
        self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)
        for invalid in ([], None, {}):
            with self.subTest(metadata=invalid):
                (directory / 'metadata.json').write_text(json.dumps(invalid))
                with self.assertRaises(SourceMetadataError):
                    DiskTilesSource(directory)

    def test_disk_zoom_fallback_only_applies_to_omitted_values(self):
        directory = self.disk_directory('tiles', {'format': 'png'}, self.tiles)
        source = DiskTilesSource(directory)
        self.assertEqual((source.min_zoom, source.max_zoom), (2, 4))
        for field, prop in (('minzoom', 'min_zoom'), ('maxzoom', 'max_zoom')):
            for invalid in ('bad', None, {}):
                with self.subTest(field=field, value=invalid):
                    (directory / 'metadata.json').write_text(json.dumps({'format': 'png', field: invalid}))
                    with self.assertRaisesRegex(SourceMetadataError, field):
                        getattr(source, prop)

    def test_disk_zoom_lookup_does_not_hide_metadata_io_failures(self):
        source = DiskTilesSource(self.disk_directory('tiles', {'format': 'png'}, self.tiles))
        error = PermissionError('metadata is unreadable')
        with patch.object(Path, 'read_bytes', side_effect=error):
            for prop in ('min_zoom', 'max_zoom'):
                with self.subTest(prop=prop), self.assertRaises(PermissionError) as caught:
                    getattr(source, prop)
                self.assertIs(caught.exception, error)

    def test_stack_skips_empty_sources_and_propagates_bad_zoom_metadata(self):
        empty = DiskTilesSource(self.disk_directory('empty', {'format': 'png'}))
        populated = DiskTilesSource(self.disk_directory('populated', {'format': 'png'}, self.tiles))
        stack = StackedTileSource([empty, populated], logger=LOGGER)
        self.assertEqual((stack.min_zoom, stack.max_zoom), (2, 4))
        for prop in ('min_zoom', 'max_zoom'):
            with self.subTest(prop=prop), self.assertRaises(EmptySourceError):
                getattr(StackedTileSource([empty], logger=LOGGER), prop)
        for field, prop in (('minzoom', 'min_zoom'), ('maxzoom', 'max_zoom')):
            directory = self.disk_directory(field, {'format': 'png', field: 'bad'}, self.tiles)
            invalid = DiskTilesSource(directory)
            with self.subTest(field=field), self.assertRaisesRegex(SourceMetadataError, field):
                getattr(StackedTileSource([populated, invalid], logger=LOGGER), prop)

    def test_mbtiles_infers_omitted_zooms_and_distinguishes_empty_data(self):
        path = self.root / 'source.mbtiles'
        write_mbtiles(path, self.tiles)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DELETE FROM metadata WHERE name IN ('minzoom', 'maxzoom')")
            connection.commit()
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        self.assertEqual((source.min_zoom, source.max_zoom), (2, 4))
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('DELETE FROM tiles')
            connection.commit()
        for prop in ('min_zoom', 'max_zoom'):
            with self.subTest(prop=prop), self.assertRaises(EmptySourceError):
                getattr(source, prop)

    def test_mbtiles_invalid_zooms_are_not_treated_as_missing(self):
        path = self.root / 'source.mbtiles'
        write_mbtiles(path, self.tiles)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE metadata SET value = 'bad' WHERE name IN ('minzoom', 'maxzoom')")
            connection.commit()
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        for prop in ('min_zoom', 'max_zoom'):
            with self.subTest(prop=prop), self.assertRaisesRegex(SourceMetadataError, 'source.mbtiles'):
                getattr(source, prop)

    def test_mbtiles_json_errors_identify_the_archive(self):
        for index, invalid in enumerate(('{', '[]', None)):
            path = self.root / f'bad-{index}.mbtiles'
            write_mbtiles(path, self.tiles)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO metadata VALUES ('json', ?)", (invalid,))
                connection.commit()
            source = MBTilesSource(path)
            self.addCleanup(source.cleanup)
            with self.subTest(metadata=invalid), self.assertRaises(SourceMetadataError) as caught:
                source.get_metadata()
            self.assertIn(str(path), str(caught.exception))
            if invalid == '{':
                self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)

    def test_pmtiles_json_errors_identify_the_archive(self):
        for index, invalid in enumerate((b'{', b'[]')):
            path = self.root / f'bad-{index}.pmtiles'
            write_pmtiles(path, self.tiles)
            data = path.read_bytes()
            header = deserialize_header(data[:127])
            metadata = gzip.compress(invalid)
            header.update(metadata_offset=len(data), metadata_length=len(metadata))
            path.write_bytes(serialize_header(header) + data[127:] + metadata)
            source = PMTilesSource(path)
            self.addCleanup(source.cleanup)
            with self.subTest(metadata=invalid), self.assertRaises(SourceMetadataError) as caught:
                source.get_metadata()
            self.assertIn(str(path), str(caught.exception))
            if invalid == b'{':
                self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)

    def test_native_database_errors_are_not_reclassified_as_metadata_errors(self):
        path = self.root / 'broken.mbtiles'
        path.write_bytes(b'not a sqlite database')
        source = MBTilesSource(path)
        self.addCleanup(source.cleanup)
        with self.assertRaises(sqlite3.DatabaseError):
            source.get_metadata()
