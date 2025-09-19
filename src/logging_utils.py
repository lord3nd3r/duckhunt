#!/usr/bin/env python3
"""
Logging utilities for DuckHunt Bot
"""

import logging
import logging.handlers


class DetailedColorFormatter(logging.Formatter):
    """Console formatter with color support"""
    COLORS = {
        'DEBUG': '\033[94m',    # Blue
        'INFO': '\033[92m',     # Green
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',    # Red
        'CRITICAL': '\033[95m', # Magenta
        'ENDC': '\033[0m'       # End color
    }
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        endc = self.COLORS['ENDC']
        msg = super().format(record)
        return f"{color}{msg}{endc}"


class DetailedFileFormatter(logging.Formatter):
    """File formatter with extra context but no colors"""
    def format(self, record):
        return super().format(record)


def setup_logger(name="DuckHuntBot"):
    """Setup logger with console and file handlers"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = DetailedColorFormatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler with rotation for detailed logs
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            'duckhunt.log', 
            maxBytes=10*1024*1024,  # 10MB per file
            backupCount=5  # Keep 5 backup files
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