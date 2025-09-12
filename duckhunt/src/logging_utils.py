import logging
import sys
from functools import partial

class ColorFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
        'ENDC': '\033[0m',
    }
    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        endc = self.COLORS['ENDC']
        msg = super().format(record)
        return f"{color}{msg}{endc}"

def setup_logger(name='DuckHuntBot', level=logging.INFO):
    logger = logging.getLogger(name)
    handler = logging.StreamHandler(sys.stdout)
    formatter = ColorFormatter('[%(asctime)s] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
