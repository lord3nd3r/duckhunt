"""
Utility functions for DuckHunt Bot
"""

import re
import json
import os
from typing import Optional, Tuple, List, Dict, Any


class MessageManager:
    """Manages customizable IRC messages with color support"""
    
    def __init__(self, messages_file: str = "messages.json"):
        self.messages_file = messages_file
        self.messages = {}
        self.load_messages()
    
    def load_messages(self):
        """Load messages from JSON file"""
        try:
            if os.path.exists(self.messages_file):
                with open(self.messages_file, 'r', encoding='utf-8') as f:
                    self.messages = json.load(f)
            else:
                # Fallback messages if file doesn't exist
                self.messages = self._get_default_messages()
        except Exception as e:
            print(f"Error loading messages: {e}, using defaults")
            self.messages = self._get_default_messages()
    
    def _get_default_messages(self) -> Dict[str, str]:
        """Default fallback messages without colors"""
        return {
            "duck_spawn": "・゜゜・。。・゜゜\\_o< QUACK! A duck has appeared! Type !bang to shoot it!",
            "duck_flies_away": "The duck flies away. ·°'`'°-.,¸¸.·°'`",
            "bang_hit": "{nick} > *BANG* You shot the duck! [+{xp_gained} xp] [Total ducks: {ducks_shot}]",
            "bang_miss": "{nick} > *BANG* You missed the duck!",
            "bang_no_duck": "{nick} > *BANG* What did you shoot at? There is no duck in the area... [GUN CONFISCATED]",
            "bang_no_ammo": "{nick} > *click* You're out of ammo! Use !reload",
            "bang_not_armed": "{nick} > You are not armed.",
            "reload_success": "{nick} > *click* Reloaded! [Ammo: {ammo}/{max_ammo}] [Chargers: {chargers}]",
            "reload_already_loaded": "{nick} > Your gun is already loaded!",
            "reload_no_chargers": "{nick} > You're out of chargers!",
            "reload_not_armed": "{nick} > You are not armed.",
            "shop_display": "DuckHunt Shop: {items} | You have {xp} XP",
            "shop_item_format": "({id}) {name} - {price} XP",
            "help_header": "DuckHunt Commands:",
            "help_user_commands": "!bang - Shoot at ducks | !reload - Reload your gun | !shop - View the shop",
            "help_help_command": "!duckhelp - Show this help",
            "help_admin_commands": "Admin: !rearm <player> | !disarm <player> | !ignore <player> | !unignore <player> | !ducklaunch",
            "admin_rearm_player": "[ADMIN] {target} has been rearmed by {admin}",
            "admin_rearm_all": "[ADMIN] All players have been rearmed by {admin}",
            "admin_disarm": "[ADMIN] {target} has been disarmed by {admin}",
            "admin_ignore": "[ADMIN] {target} is now ignored by {admin}",
            "admin_unignore": "[ADMIN] {target} is no longer ignored by {admin}",
            "admin_ducklaunch": "[ADMIN] A duck has been launched by {admin}",
            "admin_ducklaunch_not_enabled": "[ADMIN] This channel is not enabled for duckhunt",
            "usage_rearm": "Usage: !rearm <player>",
            "usage_disarm": "Usage: !disarm <player>",
            "usage_ignore": "Usage: !ignore <player>",
            "usage_unignore": "Usage: !unignore <player>"
        }
    
    def get(self, key: str, **kwargs) -> str:
        """Get a formatted message by key"""
        if key not in self.messages:
            return f"[Missing message: {key}]"
        
        message = self.messages[key]
        
        # Format with provided variables
        try:
            return message.format(**kwargs)
        except KeyError as e:
            return f"[Message format error: {e}]"
        except Exception as e:
            return f"[Message error: {e}]"
    
    def reload(self):
        """Reload messages from file"""
        self.load_messages()


class InputValidator:
    """Input validation utilities"""
    
    @staticmethod
    def validate_nickname(nick: str) -> bool:
        """Validate IRC nickname format"""
        if not nick or len(nick) > 30:
            return False
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
        sanitized = ''.join(char for char in message if ord(char) >= 32 or char in '\t\n')
        return sanitized[:500]


def parse_irc_message(line: str) -> Tuple[str, str, List[str], str]:
    """Parse IRC message format"""
    prefix = ''
    trailing = ''
    if line.startswith(':'):
        if ' ' in line[1:]:
            prefix, line = line[1:].split(' ', 1)
        else:
            # Handle malformed IRC line with no space after prefix
            prefix = line[1:]
            line = ''
    if ' :' in line:
        line, trailing = line.split(' :', 1)
    parts = line.split()
    command = parts[0] if parts else ''
    params = parts[1:] if len(parts) > 1 else []
    return prefix, command, params, trailing