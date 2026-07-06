"""
SASL authentication module for IRC connections.
"""

import asyncio
import base64

from .logging_utils import setup_logger

NULL_BYTE = "\x00"
ENCODING = "UTF-8"
# How long to wait for the server to respond during CAP/SASL negotiation before
# giving up and falling back to normal (non-SASL) registration.
NEGOTIATION_TIMEOUT_SECONDS = 15


class SASLHandler:
    """Handles SASL authentication for IRC connections."""

    def __init__(self, bot, config):
        self.bot = bot
        self.logger = setup_logger("SASL")
        # Use bot's get_config method for nested config access
        self.enabled = bot.get_config("sasl.enabled", False)
        self.username = bot.get_config(
            "sasl.username", bot.get_config("connection.nick", "")
        )
        self.password = bot.get_config("sasl.password", "")
        self.authenticated = False
        self.cap_negotiating = False
        self._timeout_task = None

    def reset(self):
        """Reset per-connection negotiation state.

        Called by the bot's reconnect loop before each new connection so a stale
        `authenticated`/`cap_negotiating` flag from a previous connection can't
        short-circuit or confuse the fresh negotiation.
        """
        self._cancel_timeout_watchdog()
        self.authenticated = False
        self.cap_negotiating = False

    def is_enabled(self):
        """Check if SASL is enabled and properly configured."""
        return (
            self.enabled
            and self.password
            and self.password not in ["", "your_password_here", "your_actual_password"]
        )

    def should_authenticate(self):
        """Check if we should attempt SASL authentication."""
        if not self.is_enabled():
            if self.enabled and not self.password:
                self.logger.warning("SASL enabled but no password configured")
            elif self.enabled and self.password in [
                "",
                "your_password_here",
                "your_actual_password",
            ]:
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
        self._start_timeout_watchdog()
        return True

    def _start_timeout_watchdog(self):
        """Guard against a server that never responds during CAP/SASL negotiation.

        Without this, an unresponsive server could leave the bot stuck before
        registration forever, since none of the negotiation handlers have a timeout.
        """
        self._cancel_timeout_watchdog()
        self._timeout_task = asyncio.create_task(self._negotiation_timeout())

    def _cancel_timeout_watchdog(self):
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    async def _negotiation_timeout(self):
        try:
            await asyncio.sleep(NEGOTIATION_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        if self.cap_negotiating and not self.authenticated:
            self.logger.error(
                f"SASL/CAP negotiation timed out after {NEGOTIATION_TIMEOUT_SECONDS}s with "
                "no server response; aborting SASL and continuing with normal registration."
            )
            await self._abort_negotiation()

    async def _abort_negotiation(self):
        """End CAP negotiation and fall back to normal registration.

        Idempotent and safe to call from any negotiation-failure path (LS/ACK without sasl,
        NAK, 904-906, unsupported mechanism, or the timeout watchdog above) - consolidates
        what used to be six near-identical "CAP END + register_user()" blocks.
        """
        self._cancel_timeout_watchdog()
        if not self.cap_negotiating:
            return
        self.end_negotiation()
        self.bot.send_raw("CAP END")
        await self.bot.register_user()

    async def handle_cap_response(self, params, trailing):
        """Handle CAP responses for SASL negotiation."""
        if len(params) < 2:
            return False

        subcommand = params[1]

        if subcommand == "LS":
            caps = trailing.split() if trailing else []
            self.logger.info(f"Server capabilities: {caps}")
            if self._has_sasl_cap(caps):
                self.logger.info("SASL capability available")
                self.bot.send_raw("CAP REQ :sasl")
                return True
            else:
                self.logger.warning("SASL not supported by server")
                await self._abort_negotiation()
                return False

        elif subcommand == "ACK":
            caps = trailing.split() if trailing else []
            self.logger.info("SASL capability acknowledged")
            if self._has_sasl_cap(caps):
                self.logger.info(f"Authenticating via SASL as {self.username}")
                await self.handle_sasl()
                return True
            else:
                await self._abort_negotiation()
                return False

        elif subcommand == "NAK":
            self.logger.warning("SASL capability rejected")
            await self._abort_negotiation()
            return False

        return False

    @staticmethod
    def _has_sasl_cap(caps):
        """Check whether the `sasl` capability is present among advertised/acked tokens.

        A CAP LS 302 response may advertise capability values, e.g. `sasl=PLAIN,EXTERNAL`,
        not just the bare `sasl` token - matching only `"sasl" in caps` would incorrectly
        conclude SASL isn't supported on such (spec-compliant) servers.
        """
        for cap in caps:
            name = cap.split("=", 1)[0]
            if name == "sasl":
                return True
        return False

    async def handle_sasl(self):
        """
        Handles SASL authentication by sending an AUTHENTICATE command.
        """
        self.logger.info("Sending AUTHENTICATE PLAIN")
        self.bot.send_raw("AUTHENTICATE PLAIN")
        await asyncio.sleep(0.1)

    async def handle_authenticate_response(self, params):
        """
        Handles the AUTHENTICATE command response.
        """
        if params and params[0] == "+":
            self.logger.info("Server ready for SASL authentication")
            if self.username and self.password:
                authpass = f"{self.username}{NULL_BYTE}{self.username}{NULL_BYTE}{self.password}"
                # NOTE: Never log the password, the auth string, its length, or any prefix
                # of the base64-encoded blob. All of those leak information about the
                # credentials (base64 is trivially reversible, and lengths narrow a
                # brute-force search space) into logs/duckhunt.log.
                ap_encoded = base64.b64encode(authpass.encode(ENCODING)).decode(
                    ENCODING
                )
                self.bot.send_raw(f"AUTHENTICATE {ap_encoded}")
                return True
            else:
                self.logger.error("SASL username and/or password not configured")
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
            self.logger.error(
                "SASL authentication failed! (904 - Invalid credentials or account not found)"
            )
            self.logger.error(f"Attempted username: {self.username}")
            if len(params) > 1:
                self.logger.error(f"Server reason: {' '.join(params[1:])}")
            if trailing:
                self.logger.error(f"Server message: {trailing}")
            await self._abort_negotiation()
            return False

        elif command == "905":
            self.logger.error("SASL authentication string too long")
            await self._abort_negotiation()
            return False

        elif command == "906":
            self.logger.error("SASL authentication aborted")
            await self._abort_negotiation()
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
                await self._abort_negotiation()
                return False

        return False

    async def handle_903(self):
        """
        Handles the 903 command by sending a CAP END command and triggering registration.
        """
        self._cancel_timeout_watchdog()
        self.bot.send_raw("CAP END")
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
