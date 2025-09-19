#!/usr/bin/env python3
"""
Utility functions for DuckHunt Bot
"""

import re
from typing import Optional


class InputValidator:
    """Input validation utilities"""
    
    @staticmethod
    def validate_nickname(nick: str) -> bool:
        """Validate IRC nickname format"""
        if not nick or len(nick) > 30:
            return False
        # RFC 2812 nickname pattern
        pattern = r'^[a-zA-Z\[\]\\`_^{|}][a-zA-Z0-9\[\]\\`_^{|}\-]*$'
        return bool(re.match(pattern, nick))
    
    @staticmethod
    def validate_channel(channel: str) -> bool:
        """Validate IRC channel format"""
        if not channel or len(channel) > 50:
            return False
        return channel.startswith('#') and ' ' not in channel
    
    @staticmethod
    def validate_numeric_input(value: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> Optional[int]:
        """Safely parse and validate numeric input"""
        try:
            num = int(value)
            if min_val is not None and num < min_val:
                return None
            if max_val is not None and num > max_val:
                return None
            return num
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def sanitize_message(message: str) -> str:
        """Sanitize user input message"""
        if not message:
            return ""
        # Remove control characters and limit length
        sanitized = ''.join(char for char in message if ord(char) >= 32 or char in '\t\n')
        return sanitized[:500]  # Limit message length


def parse_message(line):
    """Parse IRC message format"""
    prefix = ''
    trailing = ''
    if line.startswith(':'):
        prefix, line = line[1:].split(' ', 1)
    if ' :' in line:
        line, trailing = line.split(' :', 1)
    parts = line.split()
    command = parts[0] if parts else ''
    params = parts[1:] if len(parts) > 1 else []
    return prefix, command, params, trailing