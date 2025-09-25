"""
Enhanced logging utilities for DuckHunt Bot
Features: Colors, emojis, file rotation, structured formatting, configurable debug levels
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime


def load_config():
    """Load configuration from config.json"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"Warning: Could not load config.json: {e}")
        return {
            "debug": {
                "enabled": True,
                "log_level": "DEBUG",
                "console_log_level": "INFO", 
                "file_log_level": "DEBUG",
                "log_everything": True,
                "log_performance": True,
                "unified_format": True
            }
        }


class EnhancedColourFormatter(logging.Formatter):
    """Enhanced console formatter with colors, emojis, and better formatting"""
    
    # ANSI color codes with styles
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green  
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m',       # Reset
        'BOLD': '\033[1m',        # Bold
        'DIM': '\033[2m',         # Dim
        'UNDERLINE': '\033[4m',   # Underline
    }
    
    # Emojis for different log levels
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': '📘', 
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥',
    }
    
    # Component colors
    COMPONENT_COLORS = {
        'DuckHuntBot': '\033[94m',     # Light blue
        'DuckHuntBot.IRC': '\033[96m',  # Light cyan
        'DuckHuntBot.Game': '\033[92m', # Light green
        'DuckHuntBot.Shop': '\033[93m', # Light yellow
        'DuckHuntBot.DB': '\033[95m',   # Light magenta
        'SASL': '\033[97m',            # White
    }
    
    def format(self, record):
        # Get colors
        level_color = self.COLORS.get(record.levelname, '')
        component_color = self.COMPONENT_COLORS.get(record.name, '\033[37m')  # Default gray
        reset = self.COLORS['RESET']
        bold = self.COLORS['BOLD']
        dim = self.COLORS['DIM']
        
        # Get emoji
        emoji = self.EMOJIS.get(record.levelname, '📝')
        
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]
        
        # Format level with padding
        level = f"{record.levelname:<8}"
        
        # Format component name with truncation
        component = record.name
        if len(component) > 20:
            component = component[:17] + "..."
        
        # Build the formatted message
        formatted_msg = (
            f"{dim}{timestamp}{reset} "
            f"{emoji} "
            f"{level_color}{bold}{level}{reset} "
            f"{component_color}{component:<20}{reset} "
            f"{record.getMessage()}"
        )
        
        # Add function/line info for DEBUG level
        if record.levelno == logging.DEBUG:
            func_info = f"{dim}[{record.funcName}:{record.lineno}]{reset}"
            formatted_msg += f" {func_info}"
        
        return formatted_msg


class EnhancedFileFormatter(logging.Formatter):
    """Enhanced file formatter matching console format (no colors)"""
    
    # Emojis for different log levels (same as console)
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': '📘', 
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥',
    }
    
    def format(self, record):
        # Get emoji (same as console)
        emoji = self.EMOJIS.get(record.levelname, '📝')
        
        # Format timestamp (same as console - just time, not date)
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]
        
        # Format level with padding (same as console)
        level = f"{record.levelname:<8}"
        
        # Format component name with truncation (same as console)
        component = record.name
        if len(component) > 20:
            component = component[:17] + "..."
        
        # Build the formatted message (same style as console)
        formatted_msg = (
            f"{timestamp} "
            f"{emoji} "
            f"{level} "
            f"{component:<20} "
            f"{record.getMessage()}"
        )
        
        # Add function/line info for DEBUG level (same as console)
        if record.levelno == logging.DEBUG:
            func_info = f"[{record.funcName}:{record.lineno}]"
            formatted_msg += f" {func_info}"
        
        # Add exception info if present
        if record.exc_info:
            formatted_msg += f"\n{self.formatException(record.exc_info)}"
            
        return formatted_msg


class UnifiedFormatter(logging.Formatter):
    """Unified formatter that works for both console and file output"""
    
    # ANSI color codes (only used when use_colors=True)
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green  
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m',       # Reset
        'BOLD': '\033[1m',        # Bold
        'DIM': '\033[2m',         # Dim
    }
    
    # Emojis for different log levels
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': '📘', 
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥',
    }
    
    # Component colors
    COMPONENT_COLORS = {
        'DuckHuntBot': '\033[94m',     # Light blue
        'DuckHuntBot.IRC': '\033[96m',  # Light cyan
        'DuckHuntBot.Game': '\033[92m', # Light green
        'DuckHuntBot.Shop': '\033[93m', # Light yellow
        'DuckHuntBot.DB': '\033[95m',   # Light magenta
        'SASL': '\033[97m',            # White
    }
    
    def __init__(self, use_colors=False):
        super().__init__()
        self.use_colors = use_colors
    
    def format(self, record):
        # Get emoji
        emoji = self.EMOJIS.get(record.levelname, '📝')
        
        # Format timestamp (same for both)
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]
        
        # Format level with padding
        level = f"{record.levelname:<8}"
        
        # Format component name with truncation
        component = record.name
        if len(component) > 20:
            component = component[:17] + "..."
        
        if self.use_colors:
            # Console version with colors
            level_color = self.COLORS.get(record.levelname, '')
            component_color = self.COMPONENT_COLORS.get(record.name, '\033[37m')
            reset = self.COLORS['RESET']
            bold = self.COLORS['BOLD']
            dim = self.COLORS['DIM']
            
            formatted_msg = (
                f"{dim}{timestamp}{reset} "
                f"{emoji} "
                f"{level_color}{bold}{level}{reset} "
                f"{component_color}{component:<20}{reset} "
                f"{record.getMessage()}"
            )
            
            # Add function/line info for DEBUG level
            if record.levelno == logging.DEBUG:
                func_info = f"{dim}[{record.funcName}:{record.lineno}]{reset}"
                formatted_msg += f" {func_info}"
        else:
            # File version without colors
            formatted_msg = (
                f"{timestamp} "
                f"{emoji} "
                f"{level} "
                f"{component:<20} "
                f"{record.getMessage()}"
            )
            
            # Add function/line info for DEBUG level
            if record.levelno == logging.DEBUG:
                func_info = f"[{record.funcName}:{record.lineno}]"
                formatted_msg += f" {func_info}"
            
            # Add exception info if present
            if record.exc_info:
                formatted_msg += f"\n{self.formatException(record.exc_info)}"
        
        return formatted_msg


class PerformanceFileFormatter(logging.Formatter):
    """Separate formatter for performance/metrics logging"""
    
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        
        # Extract performance metrics if available
        metrics = []
        for attr in ['duration', 'memory_usage', 'cpu_usage', 'users_count', 'channels_count']:
            if hasattr(record, attr):
                metrics.append(f"{attr}={getattr(record, attr)}")
        
        metrics_str = f" METRICS[{', '.join(metrics)}]" if metrics else ""
        
        return f"{timestamp} PERF | {record.getMessage()}{metrics_str}"


def setup_logger(name="DuckHuntBot", console_level=None, file_level=None):
    """Setup enhanced logger with multiple handlers and beautiful formatting"""
    # Load configuration
    config = load_config()
    debug_config = config.get("debug", {})
    
    # Determine if debug is enabled
    debug_enabled = debug_config.get("enabled", True)
    log_everything = debug_config.get("log_everything", True) if debug_enabled else False
    unified_format = debug_config.get("unified_format", True)
    
    # Set logging levels based on config
    if console_level is None:
        if debug_enabled and log_everything:
            console_level = getattr(logging, debug_config.get("console_log_level", "DEBUG"), logging.DEBUG)
        else:
            console_level = logging.WARNING  # Minimal logging
    
    if file_level is None:
        if debug_enabled and log_everything:
            file_level = getattr(logging, debug_config.get("file_log_level", "DEBUG"), logging.DEBUG)
        else:
            file_level = logging.ERROR  # Only errors
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug_enabled else logging.WARNING)
    
    # Clear existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # === CONSOLE HANDLER ===
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    
    # Use unified format if configured, otherwise use colorful console format
    if unified_format:
        console_formatter = UnifiedFormatter(use_colors=True)
    else:
        console_formatter = EnhancedColourFormatter()
    
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # Create logs directory if it doesn't exist
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    try:
        # === MAIN LOG FILE (Rotating) ===
        main_log_handler = logging.handlers.RotatingFileHandler(
            os.path.join(logs_dir, 'duckhunt.log'), 
            maxBytes=20*1024*1024,  # 20MB
            backupCount=10,
            encoding='utf-8'
        )
        main_log_handler.setLevel(file_level)
        if unified_format:
            main_log_formatter = UnifiedFormatter(use_colors=False)
        else:
            main_log_formatter = EnhancedFileFormatter()
        main_log_handler.setFormatter(main_log_formatter)
        logger.addHandler(main_log_handler)
        
        # Log initialization success with config info
        logger.info("Unified logging system initialized: all logs in duckhunt.log")
        logger.info(f"Debug mode: {'ON' if debug_enabled else 'OFF'}")
        logger.info(f"Log everything: {'YES' if log_everything else 'NO'}")
        logger.info(f"Unified format: {'YES' if unified_format else 'NO'}")
        logger.info(f"Console level: {logging.getLevelName(console_level)}")
        logger.info(f"File level: {logging.getLevelName(file_level)}")
        logger.info(f"Main log: {main_log_handler.baseFilename}")
        
    except Exception as e:
        # Fallback to simple file logging
        try:
            simple_handler = logging.FileHandler('duckhunt_fallback.log', encoding='utf-8')
            simple_handler.setLevel(logging.DEBUG)
            simple_formatter = logging.Formatter(
                '%(asctime)s [%(levelname)-8s] %(name)s: %(message)s'
            )
            simple_handler.setFormatter(simple_formatter)
            logger.addHandler(simple_handler)
            logger.error(f"❌ Failed to setup enhanced file logging: {e}")
            logger.info("📝 Using fallback file logging")
        except Exception as fallback_error:
            logger.error(f"💥 Complete logging setup failure: {fallback_error}")
    
    return logger


def get_performance_logger():
    """Get a specialized logger for performance metrics"""
    return setup_logger("DuckHuntBot.Performance", console_level=logging.WARNING)


def log_with_context(logger, level, message, **context):
    """Log a message with additional context information"""
    record = logger.makeRecord(
        logger.name, level, '', 0, message, (), None
    )
    
    # Add context attributes to the record
    for key, value in context.items():
        setattr(record, key, value)
    
    logger.handle(record)