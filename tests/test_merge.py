import json
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from mercantile import Tile

from pmtiles_mosaic.download_mosaic import Merger, get_filename_from_url
from pmtiles_mosaic.mbtiles_source import MBTilesSource
from pmtiles_mosaic.pmtiles_source import PMTilesSource
from tests.helpers import LOGGER, read_pmtiles, write_mbtiles, write_mosaic


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

    def assert_archive_complete(self, output, archive_type):
        self.assertEqual(self.read_archive(output, archive_type), self.tiles)
        source_type = PMTilesSource if archive_type == 'pmtiles' else MBTilesSource
        source = source_type(output)
        try:
            self.assertEqual(len(list(source.all())), len(self.tiles))
        finally:
            source.cleanup()

    def test_resume_after_partial_slice_write(self):
        for archive_type in ('pmtiles', 'mbtiles'):
            with self.subTest(archive_type=archive_type):
                output = self.root / f'partial.{archive_type}'
                merger = self.merger(self.mosaic, output, archive_type)
                write = merger.archive_writer.add_to_archive

                def interrupted_write(zxy, data):
                    write(zxy, data)
                    if data == b'child':
                        raise OSError('interrupted during slice')

                with patch.object(merger.archive_writer, 'add_to_archive', side_effect=interrupted_write):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        merger.process()
                resumed = self.merger(self.mosaic, output, archive_type)
                resumed.process()
                resumed.cleanup()
                self.assert_archive_complete(output, archive_type)

    def test_committed_stages_are_not_replayed_when_tracker_update_fails(self):
        for archive_type in ('pmtiles', 'mbtiles'):
            for stage in ('prepare', 'part0.pmtiles'):
                with self.subTest(archive_type=archive_type, stage=stage):
                    output = self.root / f'tracker-{stage}.{archive_type}'
                    merger = self.merger(self.mosaic, output, archive_type)
                    mark = merger.mark_as_done

                    def interrupted_mark(current):
                        if current == stage:
                            raise OSError('tracker unavailable')
                        mark(current)

                    with patch.object(merger, 'mark_as_done', side_effect=interrupted_mark):
                        with self.assertRaisesRegex(OSError, 'tracker unavailable'):
                            merger.process()
                    resumed = self.merger(self.mosaic, output, archive_type)
                    with patch.object(resumed, 'add_pmtiles', wraps=resumed.add_pmtiles) as add:
                        resumed.process()
                    expected = 1 if stage == 'part0.pmtiles' else 2
                    self.assertEqual(add.call_count, expected)
                    resumed.cleanup()
                    self.assert_archive_complete(output, archive_type)

    def test_completed_outputs_survive_an_interruption_before_cleanup(self):
        for archive_type in ('pmtiles', 'mbtiles'):
            with self.subTest(archive_type=archive_type):
                output = self.root / f'completed.{archive_type}'
                merger = self.merger(self.mosaic, output, archive_type)
                finalize = merger.archive_writer.finalize

                def interrupted_finalize(*args):
                    finalize(*args)
                    raise OSError('interrupted after finalization')

                with patch.object(merger.archive_writer, 'finalize', side_effect=interrupted_finalize):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        merger.process()
                before = output.read_bytes()
                resumed = self.merger(self.mosaic, output, archive_type)
                with patch.object(resumed, 'add_pmtiles', side_effect=AssertionError('must not replay')):
                    resumed.process()
                resumed.cleanup()
                self.assertEqual(output.read_bytes(), before)
                self.assert_archive_complete(output, archive_type)

    def test_pmtiles_finalization_can_retry_without_reading_slices_again(self):
        output = self.root / 'rename.pmtiles'
        merger = self.merger(self.mosaic, output, 'pmtiles')
        replace = os.replace

        def interrupted_replace(source, destination):
            if destination == output:
                raise OSError('rename failed')
            replace(source, destination)

        with patch('pmtiles_mosaic.download_mosaic.os.replace', side_effect=interrupted_replace):
            with self.assertRaisesRegex(OSError, 'rename failed'):
                merger.process()
        self.assertFalse(output.exists())
        resumed = self.merger(self.mosaic, output, 'pmtiles')
        with patch.object(resumed, 'add_pmtiles', side_effect=AssertionError('must not replay')):
            resumed.process()
        resumed.cleanup()
        self.assert_archive_complete(output, 'pmtiles')

    def test_pmtiles_checkpoint_survives_abrupt_process_exit(self):
        output = self.root / 'abrupt.pmtiles'
        script = '''
import os
import sys
from pathlib import Path
from pmtiles_mosaic.download_mosaic import Merger, PmtilesArchiveWriter
original = PmtilesArchiveWriter.dump_meta
def interrupted(self):
    original(self)
    if 'part0.pmtiles' in self.completed_stages:
        os._exit(23)
PmtilesArchiveWriter.dump_meta = interrupted
Merger(sys.argv[1], 'pmtiles', Path(sys.argv[2]), 5, 0).process()
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.mosaic), str(output)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 23, result.stderr)
        resumed = self.merger(self.mosaic, output, 'pmtiles')
        resumed.process()
        resumed.cleanup()
        self.assert_archive_complete(output, 'pmtiles')

    def test_pmtiles_rejects_short_data_without_padding_it(self):
        output = self.root / 'short.pmtiles'
        merger = self.merger(self.mosaic, output, 'pmtiles')
        with patch.object(merger, 'mark_as_done', side_effect=OSError('stop after preparation')):
            with self.assertRaises(OSError):
                merger.process()
        meta_path = output.with_suffix('.meta.pkl')
        meta = pickle.loads(meta_path.read_bytes())
        meta['offset'] = 100
        meta_path.write_bytes(pickle.dumps(meta))
        tile_path = output.with_suffix('.tiles.bin')
        before = tile_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'shorter than its checkpoint'):
            self.merger(self.mosaic, output, 'pmtiles').process()
        self.assertEqual(tile_path.read_bytes(), before)

    def test_resume_rejects_a_changed_mosaic(self):
        for archive_type in ('pmtiles', 'mbtiles'):
            with self.subTest(archive_type=archive_type):
                original = self.mosaic.read_text()
                output = self.root / f'changed.{archive_type}'
                merger = self.merger(self.mosaic, output, archive_type)
                with patch.object(merger, 'mark_as_done', side_effect=OSError('stop')):
                    with self.assertRaises(OSError):
                        merger.process()
                document = json.loads(original)
                document['metadata']['name'] = 'different'
                self.mosaic.write_text(json.dumps(document))
                try:
                    with self.assertRaisesRegex(ValueError, 'Mosaic changed'):
                        self.merger(self.mosaic, output, archive_type).process()
                finally:
                    self.mosaic.write_text(original)

    def test_failed_download_does_not_replace_cached_file(self):
        target = self.root / 'cached.pmtiles'
        target.write_bytes(b'original')
        downloader = Mock(failed=['network error'])

        def partial_download(**kwargs):
            Path(kwargs['file_path']).write_bytes(b'incomplete')

        downloader.start.side_effect = partial_download
        merger = self.merger(self.mosaic, self.root / 'output.pmtiles', 'pmtiles')
        with patch('pmtiles_mosaic.download_mosaic.pypdl.Pypdl', return_value=downloader):
            with self.assertRaisesRegex(OSError, 'Download failed'):
                merger.download_file('https://example.test/part.pmtiles', target)
        self.assertEqual(target.read_bytes(), b'original')

    def test_failed_checkpoint_replacement_preserves_previous_committed_state(self):
        output = self.root / 'checkpoint.pmtiles'
        merger = self.merger(self.mosaic, output, 'pmtiles')
        replace = os.replace

        def fail_second_slice_checkpoint(source, destination):
            if destination == output.with_suffix('.meta.pkl') and 'part1.pmtiles' in merger.archive_writer.completed_stages:
                raise OSError('checkpoint replacement failed')
            replace(source, destination)

        with patch('pmtiles_mosaic.download_mosaic.os.replace', side_effect=fail_second_slice_checkpoint):
            with self.assertRaisesRegex(OSError, 'checkpoint replacement failed'):
                merger.process()
        resumed = self.merger(self.mosaic, output, 'pmtiles')
        resumed.process()
        resumed.cleanup()
        self.assert_archive_complete(output, 'pmtiles')

    def test_legacy_checkpoints_require_an_explicit_restart(self):
        output = self.root / 'legacy.pmtiles'
        merger = self.merger(self.mosaic, output, 'pmtiles')
        with patch.object(merger, 'mark_as_done', side_effect=OSError('stop')):
            with self.assertRaises(OSError):
                merger.process()
        checkpoint = output.with_suffix('.meta.pkl')
        metadata = pickle.loads(checkpoint.read_bytes())
        del metadata['checkpoint_version']
        checkpoint.write_bytes(pickle.dumps(metadata))
        with self.assertRaisesRegex(ValueError, 'Legacy PMTiles checkpoint'):
            self.merger(self.mosaic, output, 'pmtiles').process()

        output = self.root / 'legacy.mbtiles'
        write_mbtiles(output, self.slices[0])
        output.with_suffix('.tracker.txt').write_text('prepare\n')
        before = output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Legacy or missing MBTiles checkpoint'):
            self.merger(self.mosaic, output, 'mbtiles').process()
        self.assertEqual(output.read_bytes(), before)

    def test_http_downloads_round_trip_with_the_real_downloader(self):
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            reference = f'http://127.0.0.1:{server.server_port}/map.mosaic.json'
            for archive_type in ('pmtiles', 'mbtiles'):
                with self.subTest(archive_type=archive_type):
                    output = self.root / 'downloads' / archive_type / f'merged.{archive_type}'
                    merger = self.merger(reference, output, archive_type)
                    merger.process()
                    merger.cleanup()
                    self.assert_archive_complete(output, archive_type)
                    self.assertFalse((output.parent / 'map.mosaic.json').exists())
                    self.assertFalse((output.parent / 'part0.pmtiles').exists())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
