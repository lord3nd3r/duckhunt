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
from src.error_handling import sanitize_user_input
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
            "unignore", "ducklaunch", "join", "part",
        }
        self.assertEqual(set(table.keys()), expected)
        admin_cmds = {c for c, (admin, _h) in table.items() if admin}
        self.assertEqual(
            admin_cmds,
            {"rearm", "disarm", "ignore", "unignore", "ducklaunch", "join", "part"},
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


if __name__ == "__main__":
    unittest.main()
