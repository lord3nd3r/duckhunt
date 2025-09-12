import hashlib
import secrets
import asyncio
from typing import Optional
from src.db import DuckDB

class AuthSystem:
    def __init__(self, db):
        self.db = db
        self.bot = None  # Will be set by the bot
        self.authenticated_users = {}  # nick -> account_name
        self.pending_registrations = {}  # nick -> temp_data
    
    def hash_password(self, password: str, salt: Optional[str] = None) -> tuple:
        if salt is None:
            salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return hashed.hex(), salt
    
    def verify_password(self, password: str, hashed: str, salt: str) -> bool:
        test_hash, _ = self.hash_password(password, salt)
        return test_hash == hashed
    
    def register_account(self, username: str, password: str, nick: str, hostmask: str) -> bool:
        # Check if account exists
        existing = self.db.load_account(username)
        if existing:
            return False
        
        hashed_pw, salt = self.hash_password(password)
        account_data = {
            'username': username,
            'password_hash': hashed_pw,
            'salt': salt,
            'primary_nick': nick,
            'hostmask': hostmask,
            'created_at': None,  # Set by DB
            'auth_method': 'password'  # 'password', 'nickserv', 'hostmask'
        }
        
        self.db.save_account(username, account_data)
        return True
    
    def authenticate(self, username: str, password: str, nick: str) -> bool:
        account = self.db.load_account(username)
        if not account:
            return False
        
        if self.verify_password(password, account['password_hash'], account['salt']):
            self.authenticated_users[nick] = username
            return True
        return False
    
    def get_account_for_nick(self, nick: str) -> str:
        return self.authenticated_users.get(nick, "")
    
    def is_authenticated(self, nick: str) -> bool:
        return nick in self.authenticated_users
    
    def logout(self, nick: str):
        if nick in self.authenticated_users:
            del self.authenticated_users[nick]
    
    def set_bot(self, bot):
        """Set the bot instance for sending messages"""
        self.bot = bot
    
    async def attempt_nickserv_auth(self):
        """Attempt NickServ identification as fallback"""
        if not self.bot:
            return
            
        sasl_config = self.bot.config.get('sasl', {})
        username = sasl_config.get('username', '')
        password = sasl_config.get('password', '')
        
        if username and password:
            self.bot.logger.info(f"Attempting NickServ identification for {username}")
            # Try both common NickServ commands
            self.bot.send_raw(f'PRIVMSG NickServ :IDENTIFY {username} {password}')
            # Some networks use just the password if nick matches
            await asyncio.sleep(1)
            self.bot.send_raw(f'PRIVMSG NickServ :IDENTIFY {password}')
            self.bot.logger.info("NickServ identification commands sent")
        else:
            self.bot.logger.debug("No SASL credentials available for NickServ fallback")
    
    async def handle_nickserv_response(self, message):
        """Handle responses from NickServ"""
        if not self.bot:
            return
            
        message_lower = message.lower()
        
        if any(phrase in message_lower for phrase in [
            'you are now identified', 'password accepted', 'you are already identified',
            'authentication successful', 'you have been identified'
        ]):
            self.bot.logger.info("NickServ identification successful!")
            
        elif any(phrase in message_lower for phrase in [
            'invalid password', 'incorrect password', 'access denied',
            'authentication failed', 'not registered', 'nickname is not registered'
        ]):
            self.bot.logger.error(f"NickServ identification failed: {message}")
            
        else:
            self.bot.logger.debug(f"NickServ message: {message}")
