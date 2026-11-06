"""Item drops for every duck type that can be killed.

Uses config.json.example for the documented chances and tables. No network,
no player database, and no live config.json.
"""

import copy
import json
import logging
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.game import DuckGame
from src.levels import LevelManager
from src.shop import ShopManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(PROJECT_ROOT, "config.json.example")


def _example_config():
    with open(EXAMPLE, encoding="utf-8") as handle:
        return json.load(handle)


def _lookup(config, path, default=None):
    value = config
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


class TestDuckDrops(unittest.TestCase):
    def setUp(self):
        self.config = _example_config()
        self.game = self._game(self.config)

    def _game(self, config):
        game = object.__new__(DuckGame)
        game.logger = logging.getLogger("test.drops")
        game.ducks = {}
        game.bot = type("Bot", (), {})()
        game.bot.config = config
        game.bot.get_config = lambda path, default=None, cfg=config: _lookup(
            cfg, path, default
        )
        game.bot.levels = LevelManager(os.path.join(PROJECT_ROOT, "levels.json"))
        game.bot.shop = ShopManager(
            os.path.join(PROJECT_ROOT, "shop.json"), game.bot.levels, config=config
        )
        game.bot.channels_joined = set()
        game.bot.messages = type(
            "Messages",
            (),
            {"get": staticmethod(lambda key, **kwargs: kwargs.get("key", "fly"))},
        )()
        game.bot.send_message = lambda *args, **kwargs: None
        game.db = type(
            "DB",
            (),
            {
                "save_database": staticmethod(lambda: True),
                "get_players_for_channel": staticmethod(lambda channel: {}),
                "iter_all_players": staticmethod(lambda: []),
            },
        )()
        return game

    def _player(self, **extra):
        player = {
            "nick": "Peorth",
            "xp": 0,
            "accuracy": 100,
            "current_ammo": 6,
            "gun_confiscated": False,
            "last_bang_time": 0,
            "jam_modifier": 0,
            "jam_chance": 15,
            "inventory": {},
            "temporary_effects": [],
            "shots_fired": 0,
            "shots_missed": 0,
            "ducks_shot": 0,
            "ducks_befriended": 0,
            "current_streak": 0,
            "best_streak": 0,
            "achievements": [],
        }
        player.update(extra)
        return player

    def _duck(self, duck_type, **extra):
        duck = {
            "id": f"{duck_type}-1",
            "duck_type": duck_type,
            "spawn_time": time.time(),
            "channel": "#pond",
            "max_hp": 1,
            "current_hp": 1,
        }
        duck.update(extra)
        return duck

    def test_example_settings_and_fallback(self):
        # Documented tables stay on their own chance. Flock and any unlisted
        # type borrow the normal duck's 15% and normal_duck_drops.
        self.assertEqual(self.game._resolve_drop_settings("normal"), (
            0.15,
            self.config["item_drops"]["normal_duck_drops"],
        ))
        self.assertEqual(
            self.game._resolve_drop_settings("fast")[0], 0.25
        )
        self.assertEqual(
            self.game._resolve_drop_settings("ninja")[0], 0.3
        )
        self.assertEqual(
            self.game._resolve_drop_settings("golden")[0], 0.5
        )
        self.assertEqual(
            self.game._resolve_drop_settings("ninja")[1][0]["item_id"], 13
        )
        flock_chance, flock_table = self.game._resolve_drop_settings("flock")
        self.assertEqual(flock_chance, 0.15)
        self.assertEqual(flock_table, self.config["item_drops"]["normal_duck_drops"])
        other_chance, other_table = self.game._resolve_drop_settings("unused")
        self.assertEqual(other_chance, 0.15)
        self.assertEqual(other_table, flock_table)

    def test_explicit_zero_chance_is_not_replaced_by_normal(self):
        config = copy.deepcopy(self.config)
        config["duck_types"]["ninja"]["drop_chance"] = 0
        game = self._game(config)
        chance, table = game._resolve_drop_settings("ninja")
        self.assertEqual(chance, 0)
        self.assertEqual(table[0]["item_id"], 13)
        player = self._player()
        with patch("src.game.random.random", return_value=0.5):
            self.assertIsNone(game._check_item_drop(player, "ninja"))
        self.assertEqual(player["inventory"], {})

    def test_ninja_drop_success_and_miss_use_ninja_table(self):
        player = self._player()
        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = self.game._check_item_drop(player, "ninja")
        self.assertEqual(dropped["item_id"], 13)
        self.assertEqual(dropped["item_name"], "Scope")
        self.assertEqual(dropped["duck_type"], "ninja")
        self.assertEqual(player["inventory"], {"13": 1})

        quiet = self._player()
        with patch("src.game.random.random", return_value=0.31):
            self.assertIsNone(self.game._check_item_drop(quiet, "ninja"))
        self.assertEqual(quiet["inventory"], {})

    def test_ninja_without_a_table_uses_normal_loot(self):
        config = copy.deepcopy(self.config)
        del config["item_drops"]["ninja_duck_drops"]
        del config["duck_types"]["ninja"]["drop_chance"]
        game = self._game(config)
        player = self._player()
        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = game._check_item_drop(player, "ninja")
        self.assertEqual(dropped["item_id"], 1)
        self.assertEqual(dropped["duck_type"], "ninja")
        self.assertEqual(player["inventory"]["1"], 1)

    def test_flock_member_drop_success_and_miss(self):
        player = self._player()
        with patch("src.game.random.random", return_value=0.1), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = self.game._check_item_drop(player, "flock")
        self.assertEqual(dropped["item_id"], 1)
        self.assertEqual(dropped["item_name"], "Single Bullet")
        self.assertEqual(dropped["duck_type"], "flock")
        self.assertEqual(player["nick"], "Peorth")
        self.assertEqual(player["inventory"], {"1": 1})

        # 0.2 is above the normal 15% chance, so this member drops nothing.
        with patch("src.game.random.random", return_value=0.2):
            self.assertIsNone(self.game._check_item_drop(player, "flock"))
        self.assertEqual(player["inventory"], {"1": 1})

    def test_one_drop_per_killed_flock_member(self):
        player = self._player()
        self.game.ducks["#pond"] = [
            self._duck("flock", id="a", is_flock=True),
            self._duck("flock", id="b", is_flock=True),
            self._duck("flock", id="c", is_flock=True),
        ]
        rolls = {"n": 0}
        real_check = self.game._check_item_drop

        def counted(hunter, duck_type):
            rolls["n"] += 1
            return real_check(hunter, duck_type)

        self.game._check_item_drop = counted
        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            result = self.game.shoot_duck("Peorth", "#pond", player)
        self.assertEqual(result["message_key"], "bang_hit_flock")
        self.assertEqual(rolls["n"], 1)
        self.assertEqual(player["inventory"], {"1": 1})
        self.assertEqual(player["ducks_shot"], 1)
        self.assertEqual(len(self.game.ducks["#pond"]), 2)
        self.assertEqual(result["dropped_item"]["duck_type"], "flock")

    def test_inventory_cap_discards_the_drop_for_every_type(self):
        full = {"1": 20}
        for duck_type in ("normal", "fast", "golden", "ninja", "flock", "unused"):
            player = self._player(inventory=dict(full))
            with patch("src.game.random.random", return_value=0.0), patch(
                "src.game.random.randint", return_value=1
            ):
                dropped = self.game._check_item_drop(player, duck_type)
            self.assertIsNone(dropped, duck_type)
            self.assertEqual(player["inventory"], full)

    def test_normal_fast_and_golden_keep_their_own_tables(self):
        # 0.2 misses normal (15%) and a flock fallback, and hits fast (25%)
        # and golden (50%).
        normal = self._player()
        with patch("src.game.random.random", return_value=0.2):
            self.assertIsNone(self.game._check_item_drop(normal, "normal"))
        self.assertEqual(normal["inventory"], {})

        fast = self._player()
        with patch("src.game.random.random", return_value=0.2), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = self.game._check_item_drop(fast, "fast")
        self.assertEqual(dropped["item_id"], 1)
        self.assertEqual(dropped["duck_type"], "fast")

        golden = self._player()
        with patch("src.game.random.random", return_value=0.2), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = self.game._check_item_drop(golden, "golden")
        self.assertEqual(dropped["item_id"], 5)
        self.assertEqual(dropped["item_name"], "Bread")
        self.assertEqual(dropped["duck_type"], "golden")

    def test_golden_partial_hit_does_not_drop_killing_blow_does(self):
        player = self._player()
        self.game.ducks["#pond"] = [
            self._duck("golden", current_hp=2, max_hp=2)
        ]
        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            partial = self.game.shoot_duck("Peorth", "#pond", player)
        self.assertEqual(partial["message_key"], "bang_hit_golden")
        self.assertNotIn("dropped_item", partial)
        self.assertEqual(player["inventory"], {})
        self.assertEqual(player.get("ducks_shot", 0), 0)
        player["last_bang_time"] = 0

        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            killed = self.game.shoot_duck("Peorth", "#pond", player)
        self.assertEqual(killed["message_key"], "bang_hit_golden_killed")
        self.assertEqual(killed["dropped_item"]["item_id"], 5)
        self.assertEqual(player["inventory"], {"5": 1})
        self.assertEqual(player["ducks_shot"], 1)

    def test_dodge_miss_befriend_and_flyaway_do_not_drop(self):
        # Ninja dodge spends the bullet and awards no item.
        player = self._player(xp=10)
        self.game.ducks["#pond"] = [self._duck("ninja", dodge_chance=1.0)]
        with patch("src.game.random.random", return_value=0.0):
            dodged = self.game.shoot_duck("Peorth", "#pond", player)
        self.assertEqual(dodged["message_key"], "bang_ninja_dodge")
        self.assertNotIn("dropped_item", dodged)
        self.assertEqual(player["inventory"], {})
        self.assertEqual(len(self.game.ducks["#pond"]), 1)

        # A miss (roll above the hit chance) does not drop. Level 1 adds 25
        # accuracy, so 100 accuracy is a certain hit; 10 base becomes 35%.
        misser = self._player(accuracy=10, xp=10)
        self.game.ducks["#pond"] = [self._duck("flock", is_flock=True)]
        with patch("src.game.random.random", return_value=0.99):
            missed = self.game.shoot_duck("Peorth", "#pond", misser)
        self.assertEqual(missed["message_key"], "bang_miss")
        self.assertEqual(misser["inventory"], {})

        # Befriending still grants no item, for flock and ninja alike.
        for duck_type in ("normal", "ninja", "flock", "fast"):
            friend = self._player(xp=10)
            self.game.ducks["#pond"] = [self._duck(duck_type)]
            with patch("src.game.random.random", return_value=0.0):
                result = self.game.befriend_duck("Peorth", "#pond", friend)
            self.assertTrue(result.get("befriended"), duck_type)
            self.assertNotIn("dropped_item", result)
            self.assertEqual(friend["inventory"], {})
            self.assertEqual(friend["ducks_befriended"], 1)

        # A flock that flies away leaves no items behind.
        calls = []
        self.game._check_item_drop = lambda *args, **kwargs: calls.append(args)
        self.game.ducks["#pond"] = [
            self._duck("flock", spawn_time=0, is_flock=True),
            self._duck("ninja", spawn_time=0),
        ]
        self.game._expire_ducks()
        self.assertEqual(self.game.ducks, {})
        self.assertEqual(calls, [])

    def test_configured_flock_table_overrides_the_normal_fallback(self):
        config = copy.deepcopy(self.config)
        config["duck_types"]["flock"] = {"drop_chance": 1}
        config["item_drops"]["flock_duck_drops"] = [{"item_id": 13, "weight": 1}]
        game = self._game(config)
        player = self._player()
        with patch("src.game.random.random", return_value=0.0), patch(
            "src.game.random.randint", return_value=1
        ):
            dropped = game._check_item_drop(player, "flock")
        self.assertEqual(dropped["item_id"], 13)
        self.assertEqual(player["inventory"], {"13": 1})


if __name__ == "__main__":
    unittest.main()
