"""Offline regressions for jam chance, shop matching, and the safety fixes.

These tests never open a network socket and never touch a real duckhunt.json.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import logging
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.db import DuckDB
from src.duckhuntbot import DuckHuntBot
from src.error_handling import ErrorRecovery, sanitize_hostmask, sanitize_irc_target
from src.game import DuckGame
from src.levels import LevelManager
from src.sasl import SASLHandler
from src.shop import ShopManager
from src.utils import MessageManager, parse_irc_message, split_irc_text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVELS = os.path.join(PROJECT_ROOT, "levels.json")
SHOP = os.path.join(PROJECT_ROOT, "shop.json")
MESSAGES = os.path.join(PROJECT_ROOT, "messages.json")


def _levels():
    return LevelManager(LEVELS)


def _shop():
    return ShopManager(SHOP, _levels())


class TestJamChance(unittest.TestCase):
    def setUp(self):
        self.levels = _levels()

    def _game(self):
        game = object.__new__(DuckGame)
        game.logger = logging.getLogger("test.jam")
        game.ducks = {}
        game.bot = type("B", (), {})()
        game.bot.levels = self.levels
        game.bot.get_config = lambda key, default=None: default
        game.db = type("D", (), {"save_database": lambda self: True})()
        return game

    def _shoot(self, player, roll):
        game = self._game()
        with patch("src.game.random.random", return_value=roll):
            return game.shoot_duck("Peorth", "#pond", player)

    def test_level_base_is_what_unmodified_players_roll_and_display(self):
        # Stored 15 is the historical absolute, not extra jam on top of the level.
        novice = {"xp": 0, "jam_chance": 15}
        whisperer = {"xp": 1016, "jam_chance": 15}
        self.assertEqual(self.levels.get_jam_chance(novice), 0)
        self.assertEqual(self.levels.get_jam_chance(whisperer), 5)
        self.assertEqual(self.levels.calculate_player_level(whisperer), 6)

    def test_zero_never_jams_and_positive_chance_matches_the_roll(self):
        cleaned = {"xp": 1016, "jam_chance": 0, "current_ammo": 6, "gun_confiscated": False}
        # Peorth's saved 0% is the old brush floor (15 -> 0), so the level 5% is cancelled.
        self.assertEqual(self.levels.get_jam_chance(cleaned), 0)
        for roll in (0.0, 0.01, 0.5, 0.99):
            player = dict(cleaned)
            result = self._shoot(player, roll)
            self.assertNotEqual(result["message_key"], "bang_gun_jammed")

        jammed = {
            "xp": 1016,
            "jam_chance": 15,
            "current_ammo": 6,
            "gun_confiscated": False,
            "last_bang_time": 0,
        }
        self.assertEqual(self.levels.get_jam_chance(jammed), 5)
        result = self._shoot(dict(jammed), 0.0)
        self.assertEqual(result["message_key"], "bang_gun_jammed")
        self.assertEqual(result and True, True)
        # The shot that jammed spent one bullet and nothing else.
        spent = dict(jammed)
        self._shoot(spent, 0.049)
        self.assertEqual(spent["current_ammo"], 5)
        spared = dict(jammed)
        spared_result = self._shoot(spared, 0.05)
        self.assertEqual(spared_result["message_key"], "bang_no_duck")
        self.assertNotEqual(spared_result["message_key"], "bang_gun_jammed")

    def test_brush_modifier_survives_level_change_and_reload(self):
        shop = _shop()
        player = {
            "xp": 1016,
            "jam_chance": 15,
            "inventory": {"4": 1},
            "temporary_effects": [],
        }
        self.assertEqual(self.levels.get_jam_chance(player), 5)
        used = shop.use_inventory_item(player, 4)
        self.assertTrue(used["success"])
        self.assertEqual(player["jam_modifier"], -10)
        self.assertEqual(self.levels.get_jam_chance(player), 0)
        # Level 8 base is 8. A -10 brush still covers it. A smaller modifier does not.
        player["xp"] = 5000
        self.assertEqual(self.levels.calculate_player_level(player), 8)
        self.assertEqual(self.levels.get_jam_chance(player), 0)
        player["jam_modifier"] = -3
        self.assertEqual(self.levels.get_jam_chance(player), 5)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            db = DuckDB(db_file=path)
            stored = db.get_player("peorth", "#pond")
            stored.clear()
            stored.update(
                {
                    "nick": "peorth",
                    "xp": 5000,
                    "jam_chance": 0,
                    "jam_modifier": -3,
                    "ducks_shot": 54,
                }
            )
            self.assertTrue(db.save_database())
            db.flush_pending_saves(timeout=5)
            reloaded = DuckDB(db_file=path)
            again = reloaded.get_player("peorth", "#pond")
            self.assertEqual(again["jam_modifier"], -3)
            self.assertEqual(self.levels.get_jam_chance(again), 5)
            self.assertIs(again, reloaded.get_player("peorth", "#pond"))

    def test_legacy_zero_migrates_once_and_is_not_reapplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            db = DuckDB(db_file=path)
            player = db.get_player("peorth", "#pond")
            player.pop("jam_modifier", None)
            player["jam_chance"] = 0
            player["xp"] = 1016
            self.assertTrue(db.save_database())
            db.flush_pending_saves(timeout=5)
            reloaded = DuckDB(db_file=path)
            again = reloaded.get_player("peorth", "#pond")
            self.assertEqual(again["jam_modifier"], -15)
            self.assertEqual(self.levels.get_jam_chance(again), 0)
            # A later save must not treat the displayed 0 as another -15.
            again["jam_chance"] = 0
            self.assertTrue(reloaded.save_database())
            reloaded.flush_pending_saves(timeout=5)
            third = DuckDB(db_file=path).get_player("peorth", "#pond")
            self.assertEqual(third["jam_modifier"], -15)

    def test_brush_already_at_zero_is_not_consumed(self):
        shop = _shop()
        player = {"xp": 1016, "jam_chance": 0, "inventory": {"4": 1}}
        result = shop.use_inventory_item(player, 4)
        self.assertFalse(result["success"])
        self.assertEqual(player["inventory"]["4"], 1)


class TestShopMatching(unittest.TestCase):
    def setUp(self):
        self.shop = _shop()

    def test_no_substring_and_no_superscript_crash(self):
        self.assertIsNone(self.shop.find_item("a"))
        self.assertIsNone(self.shop.find_item("²"))
        self.assertIsNone(self.shop.find_item("gun"))  # Gun Brush and Buy Gun Back

    def test_exact_and_full_word_names(self):
        self.assertEqual(self.shop.find_item("gun brush")[0], 4)
        self.assertEqual(self.shop.find_item("body armor")[0], 14)
        self.assertEqual(self.shop.find_item("single bullet")[0], 1)
        self.assertEqual(self.shop.find_item("buy gun back")[0], 7)
        self.assertEqual(self.shop.find_item("brush")[0], 4)
        self.assertEqual(self.shop.find_item("armor")[0], 14)
        self.assertEqual(self.shop.find_item(4)[0], 4)

    def test_use_gun_brush_is_not_a_target_named_brush(self):
        bot = object.__new__(DuckHuntBot)
        bot.command_prefix = "!"
        bot.shop = self.shop
        bot.levels = self.shop.levels
        bot.messages = MessageManager(MESSAGES)
        bot.db = type("D", (), {"save_database": staticmethod(lambda: True)})()
        bot.game = type("G", (), {"_check_achievements": staticmethod(lambda *a, **k: [])})()
        bot.sent = []
        bot.send_message = lambda ch, msg: bot.sent.append((ch, msg))
        bot._get_validated_target_player = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("target lookup")
        )
        player = {"xp": 1016, "jam_chance": 15, "inventory": {"4": 1}}
        asyncio.run(bot.handle_use("Peorth", "#pond", player, ["gun", "brush"]))
        self.assertEqual(player.get("jam_modifier"), -10)
        self.assertNotIn("4", player.get("inventory", {}))
        self.assertIn("Gun Brush", bot.sent[-1][1])


class TestDatabaseSafety(unittest.TestCase):
    def test_first_run_creates_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            db = DuckDB(db_file=path)
            self.assertFalse(db._writes_blocked)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("channels", data)

    def test_unreadable_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            original = '{"channels": {"#pond": {"players": {"peorth": {"xp": 1016}}}}}'
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
            os.chmod(path, 0)
            try:
                db = DuckDB(db_file=path)
                self.assertTrue(db._writes_blocked)
                self.assertFalse(db.save_database())
            finally:
                os.chmod(path, 0o644)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)

    def test_directory_path_is_not_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            os.mkdir(path)
            db = DuckDB(db_file=path)
            self.assertTrue(db._writes_blocked)
            self.assertTrue(os.path.isdir(path))
            self.assertFalse(db.save_database())

    def test_corrupt_primary_recovers_backup_without_dropping_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            good = {
                "channels": {"#pond": {"players": {"peorth": {"xp": 1016, "nick": "peorth"}}}},
                "version": "2.0",
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write("{")
            with open(path + ".bak", "w", encoding="utf-8") as f:
                json.dump(good, f)
            db = DuckDB(db_file=path)
            player = db.get_player_if_exists("peorth", "#pond")
            self.assertIsNotNone(player)
            self.assertEqual(player["xp"], 1016)
            self.assertTrue(os.path.exists(path + ".bak"))

    def test_player_identity_effects_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DuckDB(db_file=os.path.join(tmp, "duckhunt.json"))
            player = db.get_player("peorth", "#pond")
            player["xp"] = 42
            player["custom_title"] = "whisperer"
            again = db.get_player("peorth", "#pond")
            self.assertIs(player, again)
            self.assertEqual(again["xp"], 42)
            effects = [{"type": f"e{i}"} for i in range(25)]
            sanitized = db._sanitize_player_data(
                {"xp": 1, "temporary_effects": effects, "custom_title": "kept"}
            )
            self.assertEqual(len(sanitized["temporary_effects"]), 20)
            self.assertEqual(sanitized["temporary_effects"][0]["type"], "e5")
            self.assertEqual(sanitized["temporary_effects"][-1]["type"], "e24")
            self.assertEqual(sanitized["custom_title"], "kept")

    def test_saves_coalesce_to_the_latest_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "duckhunt.json")
            db = DuckDB(db_file=path)
            player = db.get_player("peorth", "#pond")
            started = threading.Event()
            release = threading.Event()
            writes = []

            def slow(data):
                writes.append(data)
                started.set()
                release.wait(3)
                return True

            db._write_database_to_disk = slow
            player["xp"] = 1
            self.assertTrue(db.save_database())
            self.assertTrue(started.wait(3))
            player["xp"] = 99
            for _ in range(8):
                self.assertTrue(db.save_database())
            release.set()
            db.flush_pending_saves(timeout=5)
            self.assertLessEqual(len(writes), 3)
            self.assertGreaterEqual(len(writes), 1)
            reloaded = DuckDB(db_file=path)
            # The slow writer did not actually touch disk. Write the latest for real
            # by checking the coalesced payload was the last one built.
            last_players = None
            for payload in writes:
                channels = payload.get("channels", {})
                pond = channels.get("#pond", {}).get("players", {})
                if "peorth" in pond:
                    last_players = pond["peorth"]["xp"]
            self.assertEqual(last_players, 99)
            self.assertIsNotNone(reloaded)


class TestIrcFixes(unittest.TestCase):
    def _bot(self):
        bot = object.__new__(DuckHuntBot)
        bot.logger = logging.getLogger("test.irc")
        bot.current_nick = "DuckHunt"
        bot.sent_raw = []
        bot.send_raw = lambda msg: bot.sent_raw.append(msg) or True
        bot.command_prefix = "!"
        bot.command_handlers = DuckHuntBot._build_command_table(bot)
        bot.error_recovery = ErrorRecovery()
        bot.sent = []
        bot.send_message = lambda ch, msg: bot.sent.append((ch, msg))
        bot._rate_limiters = {}
        bot._rl_capacity = 3
        bot._rl_refill_secs = 10
        bot._rl_max_tracked = 100
        bot._pending_chat_sends = 0
        bot._max_pending_chat = 100
        bot._chat_drop_count = 0
        bot.messages = MessageManager(MESSAGES)
        bot.levels = _levels()
        bot.shop = _shop()
        bot.is_channel_enabled = lambda c: True
        bot.channels_joined = set()
        bot.registered = False
        bot._session_registered = False
        bot.pending_joins = {}
        bot.rejoin_tasks = {}
        bot.rejoin_attempts = {}
        bot.shutdown_requested = False
        bot._shutdown_in_progress = False

        class DB:
            def is_ignored(self, *args):
                return False

            def get_player(self, *args):
                raise AssertionError("PM must not create a player")

            def get_player_if_exists(self, nick, channel):
                return {
                    "xp": 0,
                    "shots_fired": 0,
                    "current_ammo": 6,
                    "magazines": 1,
                    "ducks_shot": 0,
                    "ducks_befriended": 0,
                }

        bot.db = DB()
        bot.is_admin = lambda user: False
        bot.get_config = lambda key, default=None: default
        return bot

    def test_hostmask_keeps_tilde_and_slash_for_admin_match(self):
        bot = self._bot()

        def get_config(path, default=None):
            if path == "admins":
                return [
                    {
                        "nick": "Peorth",
                        "hostmask": "*!~peorth@example.com/duckhunt",
                    }
                ]
            return default

        bot.get_config = get_config
        raw = "Peorth!~peorth@example.com/duckhunt"
        cleaned = sanitize_hostmask(raw)
        self.assertIn("~", cleaned)
        self.assertIn("/", cleaned)
        self.assertTrue(DuckHuntBot.is_admin(bot, cleaned))
        self.assertFalse(DuckHuntBot.is_admin(bot, "peorth!peorth@example.comduckhunt"))

    def test_channel_dot_survives_and_controls_do_not(self):
        self.assertEqual(sanitize_irc_target("#foo.bar"), "#foo.bar")
        self.assertNotIn("\n", sanitize_irc_target("#foo\nPRIVMSG"))
        self.assertNotIn(" ", sanitize_irc_target("#foo bar"))
        self.assertNotIn(",", sanitize_irc_target("#foo,bar"))

    def test_ping_without_colon_is_echoed(self):
        bot = self._bot()
        prefix, command, params, trailing = parse_irc_message("PING server.example")
        asyncio.run(bot.handle_message(prefix, command, params, trailing))
        self.assertEqual(bot.sent_raw, ["PONG :server.example"])

    def test_own_nick_change_updates_current_nick(self):
        bot = self._bot()
        prefix, command, params, trailing = parse_irc_message(
            ":DuckHunt!u@h NICK :DuckHunt2"
        )
        asyncio.run(bot.handle_message(prefix, command, params, trailing))
        self.assertEqual(bot.current_nick, "DuckHunt2")

    def test_pm_game_command_replies_to_sender(self):
        bot = self._bot()
        asyncio.run(bot.handle_command("peorth!~peorth@example.com/duckhunt", "DuckHunt", "!bang"))
        self.assertEqual(bot.sent[0][0], "peorth")
        self.assertIn("only works in a channel", bot.sent[0][1])

    def test_inactive_record_is_not_an_active_hunter(self):
        bot = self._bot()
        ok, _player, _err = bot.validate_target_player("bob", "#pond")
        self.assertFalse(ok)

    def test_duckstats_shows_effective_jam_and_colour_digits(self):
        bot = self._bot()
        player = {
            "xp": 1016,
            "jam_chance": 0,
            "ducks_shot": 54,
            "nick": "Peorth",
            "inventory": {},
        }
        asyncio.run(bot.handle_duckstats("Peorth", "#pond", player))
        text = bot.sent[-1][1]
        self.assertIn("0% jam chance", text)
        self.assertIn("\x033", text)

    def test_duckhelp_does_not_block_on_per_line_sleep(self):
        bot = self._bot()
        started = time.monotonic()
        asyncio.run(bot.handle_duckhelp("peorth", "#pond", {}))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertGreater(len(bot.sent), 40)

    def test_give_error_is_not_double_prefixed(self):
        bot = self._bot()
        bot._get_validated_target_player = lambda nick, ch, target: (
            None,
            f"{nick} > Player '{target}' not found in {ch}.",
        )
        player = {"inventory": {"5": 1}}
        asyncio.run(bot.handle_give("Alice", "#pond", player, ["bread", "Bob"]))
        self.assertEqual(bot.sent[-1][1].count("Alice >"), 1)

    def test_irc_split_uses_utf8_bytes(self):
        text = "🦆" * 80
        parts = split_irc_text(text, 40)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), text)
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-8")), 40)

    def test_rate_limit_denial_is_not_repeated_for_every_command(self):
        bot = self._bot()
        bot.db = type(
            "DB",
            (),
            {
                "is_ignored": lambda *a, **k: False,
                "get_player": lambda *a, **k: {"nick": "peorth", "xp": 1},
            },
        )()
        bot._rl_capacity = 1
        bot._rl_refill_secs = 100
        for _ in range(5):
            asyncio.run(
                bot.handle_command("peorth!u@host", "#pond", "!bang")
            )
        denials = [msg for _ch, msg in bot.sent if "too quickly" in msg or "rate" in msg.lower()]
        # Capacity 1: the first !bang runs (and may send its own line). Extra
        # denials must collapse to a single notice.
        self.assertLessEqual(len(denials), 1)

    def test_disconnect_clears_joined_channels_and_ducks(self):
        bot = self._bot()
        bot.channels_joined = {"#pond"}
        bot._session_registered = True
        bot.registered = True
        bot.game = object.__new__(DuckGame)
        bot.game._channel_spawn_tasks = {}
        bot.game.ducks = {"#pond": [{"id": "d"}]}
        was = bot._pause_after_disconnect()
        self.assertTrue(was)
        self.assertEqual(bot.channels_joined, set())
        self.assertEqual(bot.game.ducks, {})
        self.assertEqual(bot._active_channels_before_disconnect, {"#pond"})

    def test_second_shutdown_request_does_not_cancel_again(self):
        bot = self._bot()
        old_int = signal.getsignal(signal.SIGINT)
        old_term = signal.getsignal(signal.SIGTERM)
        try:
            bot.setup_signal_handlers()
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)
            self.assertTrue(bot.shutdown_requested)
            self.assertTrue(bot._shutdown_in_progress)
            bot.shutdown_requested = False
            handler(signal.SIGTERM, None)
            # The second call returns before flipping the flag back on.
            self.assertFalse(bot.shutdown_requested)
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)

    def test_rejoin_cleanup_keeps_the_replacement_task(self):
        bot = self._bot()

        async def scenario():
            await bot.schedule_rejoin("#pond")
            first = bot.rejoin_tasks["#pond"]
            await bot.schedule_rejoin("#pond")
            second = bot.rejoin_tasks["#pond"]
            await asyncio.sleep(0)
            self.assertIs(bot.rejoin_tasks.get("#pond"), second)
            self.assertIsNot(first, second)
            second.cancel()
            await asyncio.sleep(0)

        asyncio.run(scenario())


class TestSaslNegotiation(unittest.TestCase):
    def _handler(self):
        class Bot:
            registered = False
            sent = []

            def get_config(self, path, default=None):
                return default

            def send_raw(self, msg):
                self.sent.append(msg)

            async def register_user(self):
                self.sent.append("NICKUSER")

        bot = Bot()
        handler = SASLHandler(bot, {})
        handler.cap_negotiating = True
        return handler

    def test_multiline_cap_ls_waits_for_sasl(self):
        handler = self._handler()

        async def run():
            await handler.handle_cap_response(["*", "LS", "*"], "multi-prefix")
            self.assertNotIn("CAP END", handler.bot.sent)
            await handler.handle_cap_response(["*", "LS"], "sasl")

        asyncio.run(run())
        self.assertIn("CAP REQ :sasl", handler.bot.sent)
        self.assertNotIn("NICKUSER", handler.bot.sent)

    def test_907_does_not_resend_registration_when_already_registered(self):
        handler = self._handler()
        handler.bot.registered = True

        async def run():
            await handler.handle_sasl_result("907", ["*"], "already authenticated")

        asyncio.run(run())
        self.assertIn("CAP END", handler.bot.sent)
        self.assertNotIn("NICKUSER", handler.bot.sent)


class TestGameLoopResilience(unittest.TestCase):
    def test_spawn_manager_survives_one_bad_iteration(self):
        game = object.__new__(DuckGame)
        game.logger = logging.getLogger("test.loop")
        game._channel_spawn_tasks = {}

        class Channels:
            def __init__(self):
                self.calls = 0

            def __iter__(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient")
                return iter([])

        game.bot = type("B", (), {"channels_joined": Channels(), "is_channel_enabled": lambda self, c: True})()

        async def scenario():
            real_sleep = asyncio.sleep

            async def quick(_delay):
                await real_sleep(0)

            with patch("src.game.asyncio.sleep", quick):
                task = asyncio.create_task(game._spawn_manager_loop())
                await real_sleep(0.05)
                self.assertFalse(task.done())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
