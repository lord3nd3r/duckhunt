"""Smoke tests for DuckHunt Bot core modules.

Run with:  python3 -m unittest discover tests

IMPORTANT: These tests must never touch the real duckhunt.json. Every DuckDB
instance created here is given an absolute path inside a TemporaryDirectory.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import DuckDB
from src.duckhuntbot import DuckHuntBot
from src.error_handling import ErrorRecovery, sanitize_user_input
from src.game import ACHIEVEMENTS, DuckGame
from src.levels import LevelManager
from src.shop import ShopManager
from src.utils import MessageManager, parse_irc_message

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestParseIrcMessage(unittest.TestCase):
    def test_privmsg(self):
        prefix, command, params, trailing = parse_irc_message(
            ":nick!user@host PRIVMSG #chan :!bang extra"
        )
        self.assertEqual(prefix, "nick!user@host")
        self.assertEqual(command, "PRIVMSG")
        self.assertEqual(params, ["#chan"])
        self.assertEqual(trailing, "!bang extra")

    def test_ping(self):
        prefix, command, params, trailing = parse_irc_message("PING :server.example")
        self.assertEqual(command, "PING")
        self.assertEqual(trailing, "server.example")

    def test_garbage_does_not_raise(self):
        parse_irc_message("")
        parse_irc_message(":::::")
        parse_irc_message(":only-prefix")


class TestSanitize(unittest.TestCase):
    def test_strips_newlines(self):
        self.assertNotIn("\n", sanitize_user_input("a\r\nb"))

    def test_allowed_chars(self):
        out = sanitize_user_input("ni<k$", allowed_chars="abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(out, "nik")


class TestMessages(unittest.TestCase):
    def test_repo_messages_json_has_required_keys(self):
        with open(os.path.join(PROJECT_ROOT, "messages.json")) as f:
            msgs = json.load(f)
        for key in (
            "duck_drop_normal",
            "duck_drop_fast",
            "duck_drop_golden",
            "duck_drop_ninja",
            "hunting_dog_retrieves",
            "duck_flock",
            "duck_flock_flies_away",
            "rate_limited",
            "bang_cooldown",
        ):
            self.assertIn(key, msgs, f"messages.json missing {key}")

    def test_missing_key_fallback(self):
        mm = MessageManager("/nonexistent/messages.json")
        self.assertTrue(mm.get("no_such_key").startswith("[Missing"))

    def test_prefix_substitution(self):
        mm = MessageManager("/nonexistent/messages.json", command_prefix="@")
        self.assertIn("@reload", mm.get("bang_no_ammo", nick="x"))


class TestLevels(unittest.TestCase):
    def test_no_free_ammo_on_level_recalc(self):
        lm = LevelManager("/nonexistent/levels.json")
        player = {"xp": 0, "magazines": 1, "current_ammo": 2, "bullets_per_magazine": 6}
        lm.update_player_magazines(player)
        total = player["current_ammo"] + max(0, player["magazines"] - 1) * player[
            "bullets_per_magazine"
        ]
        self.assertLessEqual(total, 2, "level recalc granted free ammo")

    def test_full_reload(self):
        lm = LevelManager("/nonexistent/levels.json")
        player = {"xp": 0, "magazines": 0, "current_ammo": 0, "bullets_per_magazine": 6}
        lm.update_player_magazines(player, full_reload=True)
        self.assertGreater(player["current_ammo"], 0)


class TestShop(unittest.TestCase):
    def setUp(self):
        self.shop = ShopManager("/nonexistent/shop.json", LevelManager("/nonexistent/levels.json"))

    def test_purchase_insufficient_xp(self):
        player = {"xp": 0, "inventory": {}}
        result = self.shop.purchase_item(player, 1, store_in_inventory=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "insufficient_xp")

    def test_purchase_into_inventory(self):
        player = {"xp": 100, "inventory": {}}
        result = self.shop.purchase_item(player, 1, store_in_inventory=True)
        self.assertTrue(result["success"])
        self.assertEqual(player["xp"], 95)
        self.assertEqual(player["inventory"]["1"], 1)
        self.assertEqual(player["total_xp_spent"], 5)

    def test_use_trap_without_target_rejected(self):
        # trap must never apply to the user themselves via !use with no target
        self.shop.items[99] = {"name": "Trap", "price": 1, "type": "trap"}
        player = {"xp": 0, "inventory": {"99": 1}}
        result = self.shop.use_inventory_item(player, 99, target_player=None)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "target_required")
        self.assertEqual(player["inventory"]["99"], 1, "item was consumed on failure")

    def test_steal_ammo_credits_buyer(self):
        self.shop.items[98] = {"name": "Sticky Fingers", "price": 1, "type": "steal_ammo", "amount": 2}
        buyer = {"xp": 0, "inventory": {"98": 1}, "current_ammo": 0, "bullets_per_magazine": 6}
        target = {"current_ammo": 5, "bullets_per_magazine": 6}
        result = self.shop.use_inventory_item(buyer, 98, target_player=target)
        self.assertTrue(result["success"])
        self.assertEqual(target["current_ammo"], 3)
        self.assertEqual(buyer["current_ammo"], 2, "stolen ammo was not credited to the user")

    def test_get_item_name(self):
        self.assertEqual(self.shop.get_item_name(1), "Single Bullet")
        self.assertEqual(self.shop.get_item_name("5"), "Bread")
        self.assertEqual(self.shop.get_item_name(6), "Hunter's Insurance")
        self.assertIsNone(self.shop.get_item_name(9999))

    def test_find_item_by_name_and_id(self):
        # Numeric ID
        found = self.shop.find_item(5)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 5)
        self.assertEqual(found[1]["name"], "Bread")

        # String ID
        found = self.shop.find_item("5")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 5)

        # Name match (case-insensitive)
        found = self.shop.find_item("bread")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 5)

        found = self.shop.find_item("gun brush")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 4)

        # Legacy item name
        found = self.shop.find_item("hunter's insurance")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 6)

    def test_legacy_items_valid_ids(self):
        valid = self.shop.get_all_valid_ids()
        self.assertIn("1", valid)
        self.assertIn("5", valid)
        self.assertIn("6", valid)  # Hunter's Insurance
        self.assertIn("8", valid)  # Bucket of Water


class TestDatabasePersistence(unittest.TestCase):
    """Round-trip test proving player data survives save + reload intact."""

    def test_save_and_reload_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test-duckhunt.json")

            db = DuckDB(db_file=db_path)
            player = db.get_player("hunter", "#ducks")
            player["xp"] = 1234
            player["ducks_shot"] = 56
            player["luck_bonus"] = 7
            player["critical_chance"] = 3
            player["inventory"] = {"1": 2}
            self.assertTrue(db.save_database())
            db.flush_pending_saves(timeout=10.0)

            db2 = DuckDB(db_file=db_path)
            reloaded = db2.get_player_if_exists("hunter", "#ducks")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded["xp"], 1234)
            self.assertEqual(reloaded["ducks_shot"], 56)
            self.assertEqual(reloaded["inventory"], {"1": 2})
            # Shop effect fields must survive the sanitize pass (regression test)
            self.assertEqual(reloaded["luck_bonus"], 7)
            self.assertEqual(reloaded["critical_chance"], 3)

    def test_sanitize_does_not_wipe_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DuckDB(db_file=os.path.join(tmp, "t.json"))
            sanitized = db._sanitize_player_data({"xp": "999", "ducks_shot": 12})
            self.assertEqual(sanitized["xp"], 999)
            self.assertEqual(sanitized["ducks_shot"], 12)


class TestCommandTable(unittest.TestCase):
    def test_table_covers_all_commands(self):
        # Build the table without running __init__ (which would open the real DB)
        bot = object.__new__(DuckHuntBot)
        table = DuckHuntBot._build_command_table(bot)
        expected = {
            "bang", "bef", "befriend", "reload", "shop", "duckstats", "topduck",
            "globaltop", "use", "give", "duckhelp", "daily", "effects",
            "achievements", "inv", "profile", "rearm", "disarm", "ignore",
            "unignore", "ducklaunch", "join", "part", "dh",
        }
        self.assertEqual(set(table.keys()), expected)
        admin_cmds = {c for c, (admin, _h) in table.items() if admin}
        self.assertEqual(
            admin_cmds,
            {"rearm", "disarm", "ignore", "unignore", "ducklaunch", "join", "part", "dh"},
        )


class TestAchievements(unittest.TestCase):
    def _game(self):
        game = object.__new__(DuckGame)
        import logging

        game.logger = logging.getLogger("test")
        return game

    def test_high_roller_awarded_on_xp_spent(self):
        game = self._game()
        player = {"total_xp_spent": 500, "achievements": []}
        awarded = game._check_achievements(player, "xp_spent")
        self.assertEqual([a["id"] for a in awarded], ["high_roller"])

    def test_mystery_lover_awarded(self):
        game = self._game()
        player = {"achievements": []}
        awarded = game._check_achievements(player, "mystery_box")
        self.assertEqual([a["id"] for a in awarded], ["mystery_lover"])

    def test_no_duplicate_awards(self):
        game = self._game()
        player = {"total_xp_spent": 999, "achievements": []}
        game._check_achievements(player, "xp_spent")
        again = game._check_achievements(player, "xp_spent")
        self.assertEqual(again, [])

    def test_all_achievements_have_a_firing_event(self):
        # Every defined achievement id must be reachable from some event branch
        # in _check_achievements (guards against re-introducing dead achievements).
        import inspect

        source = inspect.getsource(DuckGame._check_achievements)
        for ach_id in ACHIEVEMENTS:
            self.assertIn(f'_award("{ach_id}")', source, f"{ach_id} is never awarded")


class TestItemOutput(unittest.TestCase):
    """Tests ensuring no output refers to items as 'item #whatever' or 'Item #<id>'."""

    def _make_bot(self):
        bot = object.__new__(DuckHuntBot)
        bot.command_prefix = "!"
        bot.shop = ShopManager("/nonexistent/shop.json", LevelManager("/nonexistent/levels.json"))
        bot.levels = LevelManager("/nonexistent/levels.json")
        bot.game = object.__new__(DuckGame)
        bot.game._check_achievements = lambda player, event, **kw: []
        bot.db = object.__new__(DuckDB)
        bot.db.save_database = lambda: True
        bot.get_config = lambda key, default=None: default
        bot.sent_messages = []
        bot.send_message = lambda ch, msg: bot.sent_messages.append((ch, msg))
        return bot

    def test_inv_display_uses_real_names_and_no_item_numbers(self):
        import asyncio

        bot = self._make_bot()
        player = {"inventory": {"5": 1, "6": 2, "1": 3}}
        asyncio.run(bot.handle_inv("Alice", "#channel", player))
        self.assertEqual(len(bot.sent_messages), 1)
        ch, msg = bot.sent_messages[0]
        self.assertIn("Bread x1", msg)
        self.assertIn("Hunter's Insurance x2", msg)
        self.assertIn("Single Bullet x3", msg)
        self.assertNotIn("Item #", msg)
        self.assertNotIn("#5", msg)
        self.assertNotIn("#6", msg)
        self.assertNotIn("#1", msg)

    def test_profile_display_uses_real_names(self):
        import asyncio

        bot = self._make_bot()
        player = {
            "xp": 100,
            "ducks_shot": 5,
            "ducks_befriended": 2,
            "inventory": {"5": 1, "6": 1},
        }
        asyncio.run(bot.handle_profile("Alice", "#channel", player))
        # Profile sends PM lines to nick
        items_lines = [msg for target, msg in bot.sent_messages if "Items   :" in msg]
        self.assertEqual(len(items_lines), 1)
        self.assertIn("Bread x1", items_lines[0])
        self.assertIn("Hunter's Insurance x1", items_lines[0])
        self.assertNotIn("#5", items_lines[0])
        self.assertNotIn("#6", items_lines[0])
        self.assertNotIn("Item #", items_lines[0])

    def test_check_item_drop_never_drops_unnamed_or_unknown_item(self):
        import logging

        game = object.__new__(DuckGame)
        game.logger = logging.getLogger("test_game")
        game.bot = self._make_bot()

        # Mock config with an unknown item ID in drop table
        configs = {
            "duck_types.golden.drop_chance": 1.0,
            "item_drops.golden_duck_drops": [{"item_id": 99999, "weight": 100}],
            "limits.max_inventory_items": 20,
        }
        game.bot.get_config = lambda key, default=None: configs.get(key, default)

        player = {"inventory": {}}
        drop = game._check_item_drop(player, "golden")
        self.assertIsNone(drop, "Unknown item should not have dropped")
        self.assertEqual(player["inventory"], {}, "Inventory should not receive unknown item")

        # Valid item drop
        configs["item_drops.golden_duck_drops"] = [{"item_id": 5, "weight": 100}]
        drop = game._check_item_drop(player, "golden")
        self.assertIsNotNone(drop)
        self.assertEqual(drop["item_name"], "Bread")
        self.assertEqual(drop["item_id"], 5)
        self.assertNotIn("Item #", drop["item_name"])
        self.assertNotIn("Item 5", drop["item_name"])
        self.assertEqual(player["inventory"]["5"], 1)

    def test_handle_use_with_item_name(self):
        import asyncio

        bot = self._make_bot()
        bot.messages = MessageManager("/nonexistent/messages.json")
        bot.db = object.__new__(DuckDB)
        bot.db.save_database = lambda: True
        player = {"xp": 100, "inventory": {"5": 1}, "temporary_effects": []}

        asyncio.run(bot.handle_use("Alice", "#channel", player, ["bread"]))
        self.assertEqual(len(bot.sent_messages), 1)
        ch, msg = bot.sent_messages[0]
        self.assertNotIn("Invalid item ID", msg)
        self.assertNotIn("Item #", msg)
        self.assertEqual(player["inventory"].get("5", 0), 0)

    def test_handle_give_with_item_name(self):
        import asyncio

        bot = self._make_bot()
        bot.messages = MessageManager("/nonexistent/messages.json")
        bot.db = object.__new__(DuckDB)
        bot.db.save_database = lambda: True
        bot.get_config = lambda key, default=None: default
        giver = {"xp": 100, "inventory": {"5": 2}}
        receiver = {"xp": 50, "inventory": {}}
        bot._get_validated_target_player = lambda nick, ch, target: (receiver, None)

        asyncio.run(bot.handle_give("Alice", "#channel", giver, ["bread", "Bob"]))
        self.assertEqual(len(bot.sent_messages), 1)
        ch, msg = bot.sent_messages[0]
        self.assertNotIn("Usage: !give <item_id>", msg)
        self.assertNotIn("Item #", msg)
        self.assertIn("Bread", msg)
        self.assertEqual(giver["inventory"]["5"], 1)
        self.assertEqual(receiver["inventory"]["5"], 1)

    def test_handle_shop_buy_with_item_name(self):
        import asyncio

        bot = self._make_bot()
        bot.messages = MessageManager("/nonexistent/messages.json")
        bot._get_validated_target_player = lambda nick, ch, target: (None, None)
        player = {"xp": 100, "inventory": {}}

        asyncio.run(bot.handle_shop("Alice", "#channel", player, ["buy", "bread"]))
        self.assertEqual(len(bot.sent_messages), 1)
        ch, msg = bot.sent_messages[0]
        self.assertIn("Bread", msg)
        self.assertNotIn("Item #", msg)
        self.assertEqual(player["inventory"].get("5", 0), 1)


class TestChannelToggle(unittest.TestCase):
    def _make_bot(self):
        bot = object.__new__(DuckHuntBot)
        import logging
        bot.logger = logging.getLogger("test")
        bot.command_prefix = "!"
        bot.config = {}
        bot.get_config = lambda path, default=None: default
        bot._channel_key = lambda ch: DuckHuntBot._channel_key(bot, ch)
        bot.is_channel_enabled = lambda ch: DuckHuntBot.is_channel_enabled(bot, ch)
        bot.command_handlers = DuckHuntBot._build_command_table(bot)
        bot.sent_messages = []
        bot.send_message = lambda target, msg: bot.sent_messages.append((target, msg))
        bot.channels_joined = {"#testchannel"}
        bot._rate_limiters = {}
        bot._rl_capacity = 5.0
        bot._rl_refill_secs = 2.0
        bot._rl_max_tracked = 500
        bot.error_recovery = ErrorRecovery()
        return bot

    def test_db_channel_enabled_default_and_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            db = DuckDB(db_path)
            # Default is True
            self.assertTrue(db.is_channel_enabled("#testchannel"))
            self.assertTrue(db.is_channel_enabled("#other"))

            # Set disabled
            self.assertTrue(db.set_channel_enabled("#testchannel", False))
            self.assertFalse(db.is_channel_enabled("#testchannel"))
            self.assertTrue(db.is_channel_enabled("#other"))

            # Save and reload
            db.save_database()
            db.flush_pending_saves(timeout=2.0)

            db2 = DuckDB(db_path)
            self.assertFalse(db2.is_channel_enabled("#testchannel"))
            self.assertTrue(db2.is_channel_enabled("#other"))

            # Re-enable
            self.assertTrue(db2.set_channel_enabled("#testchannel", True))
            self.assertTrue(db2.is_channel_enabled("#testchannel"))
            db2.save_database()
            db2.flush_pending_saves(timeout=2.0)

            db3 = DuckDB(db_path)
            self.assertTrue(db3.is_channel_enabled("#testchannel"))
            db3.flush_pending_saves(timeout=2.0)

    def test_admin_dh_toggle_in_channel(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.game = object.__new__(DuckGame)
            bot.game.bot = bot
            bot.game.ducks = {"#testchannel": [{"duck_type": "normal"}]}
            bot.game._channel_spawn_tasks = {}
            bot.game._channel_key = lambda ch: DuckGame._channel_key(bot.game, ch)
            bot.game._get_channel_spawn_config = lambda ch: (60, 120)
            bot.game._get_active_spawn_multiplier = lambda ch: 1.0
            bot.game.spawn_duck = lambda ch: None
            bot.is_admin = lambda user: True

            # Check status
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["status"]))
            self.assertEqual(len(bot.sent_messages), 1)
            self.assertIn("enabled", bot.sent_messages[-1][1])

            # Disable channel
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["off"]))
            self.assertFalse(bot.db.is_channel_enabled("#testchannel"))
            self.assertIn("disabled", bot.sent_messages[-1][1])
            # Active ducks should be cleared
            self.assertNotIn("#testchannel", bot.game.ducks)

            # Try disabling again
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["off"]))
            self.assertIn("already disabled", bot.sent_messages[-1][1])

            # Check status when disabled
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["status"]))
            self.assertIn("disabled", bot.sent_messages[-1][1])

            # Enable channel
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["on"]))
            self.assertTrue(bot.db.is_channel_enabled("#testchannel"))
            self.assertIn("enabled", bot.sent_messages[-1][1])

            # Try enabling again
            asyncio.run(bot.handle_dh("Alice", "#testchannel", ["on"]))
            self.assertIn("already enabled", bot.sent_messages[-1][1])

            for task in list(bot.game._channel_spawn_tasks.values()):
                task.cancel()
            bot.db.flush_pending_saves(timeout=2.0)

    def test_dh_pm_target(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.game = object.__new__(DuckGame)
            bot.game.bot = bot
            bot.game.ducks = {}
            bot.game._channel_spawn_tasks = {}
            bot.game._channel_key = lambda ch: DuckGame._channel_key(bot.game, ch)
            bot.game._get_channel_spawn_config = lambda ch: (60, 120)
            bot.game._get_active_spawn_multiplier = lambda ch: 1.0
            bot.game.spawn_duck = lambda ch: None
            bot.is_admin = lambda user: True

            # PM without channel specified
            asyncio.run(bot.handle_dh("Alice", "Alice", ["off"]))
            self.assertIn("please specify a channel", bot.sent_messages[-1][1])

            # PM with valid channel
            asyncio.run(bot.handle_dh("Alice", "Alice", ["off", "#testchannel"]))
            self.assertFalse(bot.db.is_channel_enabled("#testchannel"))
            # Both PM to admin and notice to channel
            messages_text = [m[1] for m in bot.sent_messages]
            self.assertTrue(any("has been disabled in #testchannel" in m for m in messages_text))

            # PM to enable
            asyncio.run(bot.handle_dh("Alice", "Alice", ["on", "#testchannel"]))
            self.assertTrue(bot.db.is_channel_enabled("#testchannel"))

            for task in list(bot.game._channel_spawn_tasks.values()):
                task.cancel()
            bot.db.flush_pending_saves(timeout=2.0)

    def test_gameplay_commands_blocked_when_channel_disabled(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.db.set_channel_enabled("#testchannel", False)
            bot.is_admin = lambda user: False

            # Regular player attempts !bang in disabled channel
            asyncio.run(bot.handle_command("Bob!user@host", "#testchannel", "!bang"))
            self.assertEqual(len(bot.sent_messages), 1)
            target, msg = bot.sent_messages[0]
            self.assertEqual(target, "#testchannel")
            self.assertIn("DuckHunt is currently disabled in this channel", msg)

            # Regular player attempts !shop in disabled channel
            bot.sent_messages.clear()
            asyncio.run(bot.handle_command("Bob!user@host", "#testchannel", "!shop"))
            self.assertEqual(len(bot.sent_messages), 1)
            self.assertIn("DuckHunt is currently disabled in this channel", bot.sent_messages[0][1])

            # Admin commands are NOT blocked
            bot.is_admin = lambda user: True
            bot.sent_messages.clear()
            asyncio.run(bot.handle_command("Admin!admin@host", "#testchannel", "!dh status"))
            self.assertEqual(len(bot.sent_messages), 1)
            self.assertIn("DuckHunt is currently disabled", bot.sent_messages[0][1])

    def test_non_admin_cannot_use_dh(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.is_admin = lambda user: False

            # Non-admin tries !dh off
            asyncio.run(bot.handle_command("Bob!user@host", "#testchannel", "!dh off"))
            # Silently ignored like all admin commands from non-admins
            self.assertEqual(len(bot.sent_messages), 0)
            self.assertTrue(bot.db.is_channel_enabled("#testchannel"))

    def test_ducklaunch_blocked_when_channel_disabled(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.db.set_channel_enabled("#testchannel", False)
            bot.is_admin = lambda user: True

            asyncio.run(bot.handle_ducklaunch("Admin", "#testchannel", []))
            self.assertEqual(len(bot.sent_messages), 1)
            self.assertIn("DuckHunt is disabled in #testchannel", bot.sent_messages[0][1])


class TestChannelPersistence(unittest.TestCase):
    def _make_bot(self):
        bot = object.__new__(DuckHuntBot)
        import logging
        bot.logger = logging.getLogger("test")
        bot.command_prefix = "!"
        bot.current_nick = "DuckHunt"
        bot.config = {"connection": {"channels": ["#config1", "#config2"]}}
        bot.get_config = lambda path, default=None: (
            bot.config.get("connection", {}).get("channels", default)
            if path == "connection.channels"
            else default
        )
        bot._channel_key = lambda ch: DuckHuntBot._channel_key(bot, ch)
        bot.is_channel_enabled = lambda ch: DuckHuntBot.is_channel_enabled(bot, ch)
        bot.get_channels_to_join = lambda: DuckHuntBot.get_channels_to_join(bot)
        bot.command_handlers = DuckHuntBot._build_command_table(bot)
        bot.sent_messages = []
        bot.raw_messages = []
        bot.send_message = lambda target, msg: bot.sent_messages.append((target, msg))
        bot.send_raw = lambda msg: (bot.raw_messages.append(msg), True)[1]
        bot.channels_joined = set()
        bot.rejoin_tasks = {}
        bot.rejoin_attempts = {}
        bot._rate_limiters = {}
        bot._rl_capacity = 5.0
        bot._rl_refill_secs = 2.0
        bot._rl_max_tracked = 500
        bot.error_recovery = ErrorRecovery()
        return bot

    def test_db_channel_joined_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            db = DuckDB(db_path)
            self.assertIsNone(db.is_channel_joined("#room1"))
            self.assertEqual(db.get_joined_channels(), [])

            # Mark room1 as joined
            db.set_channel_joined("#Room1", True)
            self.assertTrue(db.is_channel_joined("#room1"))
            self.assertEqual(db.get_joined_channels(), ["#Room1"])

            # Save and reload
            db.save_database()
            db.flush_pending_saves(timeout=2.0)

            db2 = DuckDB(db_path)
            self.assertTrue(db2.is_channel_joined("#room1"))
            self.assertEqual(db2.get_joined_channels(), ["#Room1"])

            # Mark room1 as parted
            db2.set_channel_joined("#room1", False)
            self.assertFalse(db2.is_channel_joined("#room1"))
            self.assertEqual(db2.get_joined_channels(), [])

            db2.save_database()
            db2.flush_pending_saves(timeout=2.0)

            db3 = DuckDB(db_path)
            self.assertFalse(db3.is_channel_joined("#room1"))
            self.assertEqual(db3.get_joined_channels(), [])
            db3.flush_pending_saves(timeout=2.0)

    def test_get_channels_to_join(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)

            # Initially: returns config channels
            channels = bot.get_channels_to_join()
            self.assertEqual(set(channels), {"#config1", "#config2"})

            # Admin joined a new channel
            bot.db.set_channel_joined("#dynamicroom", True)
            channels = bot.get_channels_to_join()
            self.assertEqual(set(channels), {"#config1", "#config2", "#dynamicroom"})

            # Admin parted a config channel
            bot.db.set_channel_joined("#config2", False)
            channels = bot.get_channels_to_join()
            self.assertEqual(set(channels), {"#config1", "#dynamicroom"})
            bot.db.flush_pending_saves(timeout=2.0)

    def test_join_event_and_part_command_updates_persistence(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.is_admin = lambda user: True

            # Server sends JOIN event for our bot
            asyncio.run(bot.handle_message("DuckHunt!bot@host", "JOIN", ["#newroom"], None))
            self.assertIn("#newroom", bot.channels_joined)
            self.assertTrue(bot.db.is_channel_joined("#newroom"))

            # Admin executes !part
            asyncio.run(bot.handle_part_channel("Admin", "#newroom", ["#newroom"]))
            self.assertNotIn("#newroom", bot.channels_joined)
            self.assertFalse(bot.db.is_channel_joined("#newroom"))
            bot.db.flush_pending_saves(timeout=2.0)

    def test_reconnect_rejoins_active_channels(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_db.json")
            bot = self._make_bot()
            bot.db = DuckDB(db_path)
            bot.channels_joined = {"#active1", "#active2"}

            # Simulate ping timeout / disconnect logic in run()
            bot._active_channels_before_disconnect = set(bot.channels_joined)
            bot.channels_joined.clear()
            bot.registered = False

            # On reconnect, 001 welcome is received
            asyncio.run(bot.handle_message("server", "001", ["DuckHunt"], "Welcome"))
            # Bot should have sent JOIN for active channels plus any config channels
            join_commands = [msg for msg in bot.raw_messages if msg.startswith("JOIN ")]
            joined_targets = {cmd.split()[1] for cmd in join_commands}
            self.assertTrue({"#active1", "#active2"}.issubset(joined_targets))
            bot.db.flush_pending_saves(timeout=2.0)


if __name__ == "__main__":
    unittest.main()


