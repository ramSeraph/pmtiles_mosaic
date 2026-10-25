import tempfile
import unittest
from pathlib import Path

from mercantile import Tile

from pmtiles_mosaic.download_mosaic import Merger, get_filename_from_url
from pmtiles_mosaic.mbtiles_source import MBTilesSource
from tests.helpers import LOGGER, read_pmtiles, write_mosaic


class MergeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'maps with # and %'
        self.root.mkdir()
        self.slices = [{Tile(0, 0, 0): b'root'}, {Tile(1, 0, 1): b'child'}]
        self.tiles = self.slices[0] | self.slices[1]
        self.mosaic = write_mosaic(self.root, self.slices)

    def merger(self, reference, output, archive_type, **options):
        return Merger(str(reference), archive_type, output, 5, 0, logger=LOGGER, **options)

    def read_archive(self, output, archive_type):
        if archive_type == 'pmtiles':
            return read_pmtiles(output)
        source = MBTilesSource(output)
        try:
            return dict(source.all())
        finally:
            source.cleanup()

    def test_local_paths_and_file_urls_merge_into_new_directories(self):
        for reference in (str(self.mosaic), self.mosaic.as_uri()):
            for archive_type in ('pmtiles', 'mbtiles'):
                with self.subTest(reference=reference, archive_type=archive_type):
                    kind = 'url' if reference.startswith('file:') else 'path'
                    output = self.root / kind / archive_type / f'merged.{archive_type}'
                    merger = self.merger(reference, output, archive_type)
                    merger.process()
                    merger.cleanup()
                    self.assertEqual(self.read_archive(output, archive_type), self.tiles)
                    self.assertTrue(self.mosaic.exists())

    def test_force_cannot_overwrite_a_manifest_or_source_slice(self):
        for output in (self.mosaic, self.root / 'part0.pmtiles'):
            with self.subTest(output=output):
                original = output.read_bytes()
                merger = self.merger(self.mosaic, output, 'pmtiles', force=True)
                with self.assertRaisesRegex(ValueError, 'overlaps an input'):
                    merger.process()
                self.assertEqual(output.read_bytes(), original)
                self.assertFalse(merger.tracker_file.exists())

    def test_plain_path_filename_preserves_hash_and_percent_characters(self):
        path = self.root / 'map #1%.mosaic.json'
        self.assertEqual(get_filename_from_url(str(path)), path.name)
        self.assertEqual(get_filename_from_url(path.as_uri()), path.name)
