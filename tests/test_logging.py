import io
import logging
import unittest
from contextlib import redirect_stderr, redirect_stdout

from pmtiles_mosaic.logger import LoggerMixin, get_logger


class Example(LoggerMixin):
    pass


class LoggingTests(unittest.TestCase):
    def test_fallback_uses_logging_levels_formatting_and_exception_info(self):
        example = Example()
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertLogs(__name__, level='WARNING') as captured:
            example.log_debug('hidden debug message')
            example.log_warning('Tile %s is missing', '1/2/3')
            try:
                raise ValueError('bad tile')
            except ValueError:
                example.log_error('Cannot read tile', exc_info=True)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(len(captured.records), 2)
        self.assertEqual(captured.records[0].getMessage(), 'Tile 1/2/3 is missing')
        self.assertIs(captured.records[1].exc_info[0], ValueError)

    def test_repeated_cli_logger_setup_emits_one_message_to_stderr(self):
        logger = logging.getLogger('pmtiles_mosaic.test_cli_logging')
        original = (logger.handlers[:], logger.level, logger.propagate)

        def restore():
            logger.handlers, logger.level, logger.propagate = original

        self.addCleanup(restore)
        logger.handlers = []
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            first = get_logger(logger.name, 'DEBUG')
            second = get_logger(logger.name, 'WARNING')
            self.assertIs(first, second)
            first.info('hidden info message')
            second.warning('one warning')
        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(stderr.getvalue().count('one warning'), 1)
        self.assertNotIn('hidden info message', stderr.getvalue())
        self.assertEqual(stdout.getvalue(), '')
