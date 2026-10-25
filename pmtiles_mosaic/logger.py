import logging

import colorlog

class LoggerMixin:
    """A mixin class to provide logging methods."""

    def _log(self, level, msg, *args, **kwargs):
        logger = getattr(self, 'logger', None) or logging.getLogger(type(self).__module__)
        logger.log(level, msg, *args, **kwargs)

    def log_debug(self, msg, *args, **kwargs):
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def log_info(self, msg, *args, **kwargs):
        self._log(logging.INFO, msg, *args, **kwargs)

    def log_warning(self, msg, *args, **kwargs):
        self._log(logging.WARNING, msg, *args, **kwargs)

    def log_error(self, msg, *args, **kwargs):
        self._log(logging.ERROR, msg, *args, **kwargs)

    def log_critical(self, msg, *args, **kwargs):
        self._log(logging.CRITICAL, msg, *args, **kwargs)


def get_logger(name, level='INFO'):
    """Initializes and returns a logger with colored output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = colorlog.StreamHandler()
        handler.setFormatter(colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(levelname)s - %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            },
        ))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
