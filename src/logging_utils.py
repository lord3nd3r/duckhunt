"""
Logging utilities for DuckHunt Bot
"""

import logging
import logging.handlers


class DetailedColourFormatter(logging.Formatter):
    """Console formatter with colour support"""
    COLOURS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
        'ENDC': '\033[0m'
    }
    
    def format(self, record):
        colour = self.COLOURS.get(record.levelname, '')
        endc = self.COLOURS['ENDC']
        msg = super().format(record)
        return f"{colour}{msg}{endc}"


class DetailedFileFormatter(logging.Formatter):
    """File formatter with extra context but no colours"""
    def format(self, record):
        return super().format(record)


def setup_logger(name="DuckHuntBot"):
    """Setup logger with console and file handlers"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    logger.handlers.clear()
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = DetailedColourFormatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            'duckhunt.log', 
            maxBytes=10*1024*1024,
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = DetailedFileFormatter(
            '%(asctime)s [%(levelname)-8s] %(name)s - %(funcName)s:%(lineno)d: %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        logger.info("Enhanced logging system initialized with file rotation")
    except Exception as e:
        logger.error(f"Failed to setup file logging: {e}")
    
    return logger