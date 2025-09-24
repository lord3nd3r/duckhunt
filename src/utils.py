"""
Utility functions for DuckHunt Bot
"""

import re
import json
import os
import random
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
    
    def _get_default_messages(self) -> Dict[str, Any]:
        """Default fallback messages without colors"""
        return {
            "duck_spawn": [
                "・゜゜・。。・゜゜\\_o< QUACK! A duck has appeared! Type !bang to shoot it!",
                "・゜゜・。。・゜゜\\_o< *flap flap* A wild duck landed! Use !bang to hunt it!",
                "🦆 A duck swoops into view! Quick, type !bang before it escapes!",
                "・゜゜・。。・゜゜\\_o< Quack quack! Fresh duck spotted! !bang to bag it!",
                "*rustling* A duck waddles out from the bushes! Fire with !bang!",
                "・゜゜・。。・゜゜\\_o< Splash! A duck surfaces! Shoot it with !bang!"
            ],
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
        """Get a formatted message by key with color placeholder replacement"""
        if key not in self.messages:
            return f"[Missing message: {key}]"
        
        message = self.messages[key]
        
        # If message is an array, randomly select one
        if isinstance(message, list):
            if not message:
                return f"[Empty message array: {key}]"
            message = random.choice(message)
        
        # Ensure message is a string
        if not isinstance(message, str):
            return f"[Invalid message type: {key}]"
        
        # Replace color placeholders with IRC codes
        if "colours" in self.messages and isinstance(self.messages["colours"], dict):
            for color_name, color_code in self.messages["colours"].items():
                placeholder = "{" + color_name + "}"
                message = message.replace(placeholder, color_code)
        
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
    """Parse IRC message format with comprehensive error handling"""
    try:
        # Validate input
        if not isinstance(line, str):
            raise ValueError(f"Expected string, got {type(line)}")
        
        # Handle empty or whitespace-only lines
        if not line or not line.strip():
            return '', '', [], ''
        
        line = line.strip()
        
        # Initialize return values
        prefix = ''
        trailing = ''
        command = ''
        params = []
        
        # Handle prefix (starts with :)
        if line.startswith(':'):
            try:
                if ' ' in line[1:]:
                    prefix, line = line[1:].split(' ', 1)
                else:
                    # Handle malformed IRC line with no space after prefix
                    prefix = line[1:]
                    line = ''
            except ValueError:
                # If split fails, treat entire line as prefix
                prefix = line[1:]
                line = ''
        
        # Handle trailing parameter (starts with ' :')
        if line and ' :' in line:
            try:
                line, trailing = line.split(' :', 1)
            except ValueError:
                # If split fails, keep line as is
                pass
        
        # Parse command and parameters
        if line:
            try:
                parts = line.split()
                command = parts[0] if parts else ''
                params = parts[1:] if len(parts) > 1 else []
            except Exception:
                # If parsing fails, try to extract at least the command
                command = line.split()[0] if line.split() else ''
                params = []
        
        # Validate that we have at least a command
        if not command and not prefix:
            raise ValueError(f"No valid command or prefix found in line: {line[:50]}...")
            
        return prefix, command, params, trailing
        
    except Exception as e:
        # Log the error but return safe defaults to prevent crashes
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error parsing IRC message '{line[:50]}...': {e}")
        return '', 'UNKNOWN', [], ''