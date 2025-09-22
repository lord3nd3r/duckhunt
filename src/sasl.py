"""
SASL authentication module for IRC connections.
"""
import base64
import asyncio
from .logging_utils import setup_logger

NULL_BYTE = '\x00'
ENCODING = 'UTF-8'

class SASLHandler:
    """Handles SASL authentication for IRC connections."""
    
    def __init__(self, bot, config):
        self.bot = bot
        self.logger = setup_logger("SASL")
        sasl_config = config.get("sasl", {})
        self.enabled = sasl_config.get("enabled", False)
        self.username = sasl_config.get("username", config.get("nick", ""))
        self.password = sasl_config.get("password", "")
        self.authenticated = False
        self.cap_negotiating = False
    
    def is_enabled(self):
        """Check if SASL is enabled and properly configured."""
        return (self.enabled and 
                self.password and 
                self.password not in ["", "your_password_here", "your_actual_password"])
    
    def should_authenticate(self):
        """Check if we should attempt SASL authentication."""
        if not self.is_enabled():
            if self.enabled and not self.password:
                self.logger.warning("SASL enabled but no password configured")
            elif self.enabled and self.password in ["", "your_password_here", "your_actual_password"]:
                self.logger.warning("SASL enabled but using placeholder password")
            return False
        return True
    
    async def start_negotiation(self):
        """Start CAP negotiation for SASL."""
        if not self.should_authenticate():
            return False
        
        self.logger.info("SASL authentication enabled")
        self.cap_negotiating = True
        self.bot.send_raw("CAP LS 302")
        return True
    
    async def handle_cap_response(self, params, trailing):
        """Handle CAP responses for SASL negotiation."""
        if len(params) < 2:
            return False
            
        subcommand = params[1]
        
        if subcommand == "LS":
            caps = trailing.split() if trailing else []
            self.logger.info(f"Server capabilities: {caps}")
            if "sasl" in caps:
                self.logger.info("SASL capability available")
                self.bot.send_raw("CAP REQ :sasl")
                return True
            else:
                self.logger.warning("SASL not supported by server")
                self.bot.send_raw("CAP END")
                await self.bot.register_user()
                return False
                
        elif subcommand == "ACK":
            caps = trailing.split() if trailing else []
            self.logger.info("SASL capability acknowledged")
            if "sasl" in caps:
                self.logger.info(f"Authenticating via SASL as {self.username}")
                await self.handle_sasl()
                return True
            else:
                self.bot.send_raw("CAP END")
                await self.bot.register_user()
                return False
                
        elif subcommand == "NAK":
            self.logger.warning("SASL capability rejected")
            self.bot.send_raw("CAP END")
            await self.bot.register_user()
            return False
        
        return False
    
    async def handle_sasl(self):
        """
        Handles SASL authentication by sending an AUTHENTICATE command.
        """
        self.logger.info("Sending AUTHENTICATE PLAIN")
        self.bot.send_raw('AUTHENTICATE PLAIN')
        await asyncio.sleep(0.1)
    
    async def handle_authenticate_response(self, params):
        """
        Handles the AUTHENTICATE command response.
        """
        if params and params[0] == '+':
            self.logger.info("Server ready for SASL authentication")
            if self.username and self.password:
                authpass = f'{self.username}{NULL_BYTE}{self.username}{NULL_BYTE}{self.password}'
                self.logger.debug(f"Auth string length: {len(authpass)} chars")
                self.logger.debug(f"Auth components: user='{self.username}', pass='{self.password[:3]}...'")
                
                ap_encoded = base64.b64encode(authpass.encode(ENCODING)).decode(ENCODING)
                self.logger.debug(f"Base64 encoded length: {len(ap_encoded)} chars")
                self.logger.debug(f"Sending: AUTHENTICATE {ap_encoded[:20]}...")
                
                self.bot.send_raw(f'AUTHENTICATE {ap_encoded}')
                return True
            else:
                self.logger.error('SASL username and/or password not configured')
                return False
        return False
    
    async def handle_sasl_result(self, command, params, trailing):
        """Handle SASL authentication result."""
        if command == "903":
            self.logger.info("SASL authentication successful!")
            self.authenticated = True
            await self.handle_903()
            return True
            
        elif command == "904":
            self.logger.error("SASL authentication failed! (904 - Invalid credentials or account not found)")
            self.logger.error(f"Attempted username: {self.username}")
            self.logger.error(f"Password length: {len(self.password)} chars")
            if len(params) > 1:
                self.logger.error(f"Server reason: {' '.join(params[1:])}")
            if trailing:
                self.logger.error(f"Server message: {trailing}")
            self.bot.send_raw("CAP END")
            await self.bot.register_user()
            return False
            
        elif command == "905":
            self.logger.error("SASL authentication string too long")
            self.bot.send_raw("CAP END")
            await self.bot.register_user()
            return False
            
        elif command == "906":
            self.logger.error("SASL authentication aborted")
            self.bot.send_raw("CAP END")
            await self.bot.register_user()
            return False
            
        elif command == "907":
            self.logger.info("Already authenticated via SASL")
            self.authenticated = True
            await self.handle_903()
            return True
            
        elif command == "908":
            mechanisms = trailing.split() if trailing else []
            self.logger.info(f"Available SASL mechanisms: {mechanisms}")
            if "PLAIN" not in mechanisms:
                self.logger.error("PLAIN mechanism not supported")
                self.bot.send_raw("CAP END")
                await self.bot.register_user()
                return False
        
        return False
    
    async def handle_903(self):
        """
        Handles the 903 command by sending a CAP END command and triggering registration.
        """
        self.bot.send_raw('CAP END')
        await self.bot.register_user()
    
    def is_authenticated(self):
        """Check if SASL authentication was successful."""
        return self.authenticated
    
    def is_negotiating(self):
        """Check if CAP negotiation is in progress."""
        return self.cap_negotiating
    
    def end_negotiation(self):
        """End CAP negotiation."""
        self.cap_negotiating = False
