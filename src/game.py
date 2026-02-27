"""
Game mechanics for DuckHunt Bot
Handles duck spawning, shooting, befriending, and other game actions
"""

import asyncio
import random
import time
import logging

# ---------------------------------------------------------------------------
# Weather states
# ---------------------------------------------------------------------------
WEATHER_STATES = {
    'clear': {
        'name': '☀️ Clear',      'jam_modifier': 0,   'accuracy_modifier': 0,
        'timeout_modifier': 1.0, 'xp_modifier': 1.0,  'duration': 1800,
    },
    'rain': {
        'name': '🌧️ Rainy',      'jam_modifier': 10,  'accuracy_modifier': -5,
        'timeout_modifier': 1.0, 'xp_modifier': 1.0,  'duration': 1200,
    },
    'fog': {
        'name': '🌫️ Foggy',      'jam_modifier': 0,   'accuracy_modifier': -10,
        'timeout_modifier': 0.5, 'xp_modifier': 1.0,  'duration': 900,
    },
    'storm': {
        'name': '🌪️ Stormy',     'jam_modifier': 15,  'accuracy_modifier': -15,
        'timeout_modifier': 0.75,'xp_modifier': 2.0,  'duration': 600,
    },
}

# ---------------------------------------------------------------------------
# Achievement definitions
# ---------------------------------------------------------------------------
ACHIEVEMENTS = {
    'first_blood':    {'icon': '🩸', 'name': 'First Blood',      'description': 'Shot your first duck'},
    'century_hunter': {'icon': '💯', 'name': 'Century Hunter',   'description': 'Shot 100 ducks'},
    'legendary':      {'icon': '🏆', 'name': 'Legendary Hunter', 'description': 'Shot 500 ducks'},
    'duck_whisperer': {'icon': '🤝', 'name': 'Duck Whisperer',   'description': 'Befriended 50 ducks'},
    'sharpshooter':   {'icon': '🎯', 'name': 'Sharpshooter',     'description': 'Hit 10 ducks in a row without missing'},
    'golden_slayer':  {'icon': '🥇', 'name': 'Golden Slayer',    'description': 'Killed a golden duck'},
    'boss_slayer':    {'icon': '💀', 'name': 'Boss Slayer',       'description': 'Contributed to defeating a boss duck'},
    'ninja_slayer':   {'icon': '🥷', 'name': 'Ninja Slayer',     'description': 'Shot a ninja duck'},
    'trigger_happy':  {'icon': '🤦', 'name': 'Trigger Happy',    'description': 'Got your gun confiscated 10 times'},
    'high_roller':    {'icon': '💸', 'name': 'High Roller',       'description': 'Spent 500 XP in the shop'},
    'daily_devotee':  {'icon': '📅', 'name': 'Daily Devotee',    'description': 'Claimed daily bonus 7 days in a row'},
    'survivor':       {'icon': '🦺', 'name': 'Survivor',          'description': 'Was protected by body armor'},
    'flock_master':   {'icon': '🦆', 'name': 'Flock Master',      'description': 'Shot during a flock event'},
    'storm_hunter':   {'icon': '⛈️', 'name': 'Storm Hunter',     'description': 'Shot a duck during a storm'},
    'mystery_lover':  {'icon': '🎁', 'name': 'Mystery Lover',    'description': 'Opened a mystery box'},
}


class DuckGame:
    """Game mechanics for DuckHunt - shooting, befriending, reloading"""

    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.ducks = {}          # {channel_key: [duck_dict, ...]}
        self.logger = logging.getLogger('DuckHuntBot.Game')
        self.spawn_task = None
        self.timeout_task = None
        self.weather_task = None
        # Per-channel weather: {channel_key: {'state': str, 'expires_at': float}}
        self.weather = {}

    @staticmethod
    def _channel_key(channel: str) -> str:
        """Normalize channel keys (IRC channels are case-insensitive)."""
        if not isinstance(channel, str):
            return ""
        channel = channel.strip()
        if channel.startswith('#') or channel.startswith('&'):
            return channel.lower()
        return channel

    # -----------------------------------------------------------------------
    # Game loops
    # -----------------------------------------------------------------------

    async def start_game_loops(self):
        """Start all game loops"""
        self.spawn_task   = asyncio.create_task(self.duck_spawn_loop())
        self.timeout_task = asyncio.create_task(self.duck_timeout_loop())
        self.weather_task = asyncio.create_task(self.weather_loop())
        try:
            await asyncio.gather(self.spawn_task, self.timeout_task, self.weather_task)
        except asyncio.CancelledError:
            self.logger.info("Game loops cancelled")

    async def duck_spawn_loop(self):
        """Duck spawning loop with responsive shutdown"""
        try:
            while True:
                min_wait = self.bot.get_config('duck_spawning.spawn_min', 300)
                max_wait = self.bot.get_config('duck_spawning.spawn_max', 900)
                spawn_multiplier = self._get_active_spawn_multiplier()
                if spawn_multiplier > 1.0:
                    min_wait = int(min_wait / spawn_multiplier)
                    max_wait = int(max_wait / spawn_multiplier)
                wait_time = random.randint(min_wait, max_wait)
                for _ in range(wait_time):
                    await asyncio.sleep(1)
                channels = list(self.bot.channels_joined)
                if channels:
                    channel = random.choice(channels)
                    await self.spawn_duck(channel)
        except asyncio.CancelledError:
            self.logger.info("Duck spawning loop cancelled")

    async def duck_timeout_loop(self):
        """Remove expired ducks, trigger hunting dog, clean effects"""
        try:
            while True:
                await asyncio.sleep(2)
                current_time = time.time()
                channels_to_clear = []

                for channel, ducks in self.ducks.items():
                    ducks_to_remove = []
                    for duck in ducks:
                        duck_type = duck.get('duck_type', 'normal')
                        base_timeout = self.bot.get_config(f'duck_types.{duck_type}.timeout', 60)
                        weather = self.get_channel_weather(channel)
                        w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
                        effective_timeout = base_timeout * w_cfg['timeout_modifier']
                        if current_time - duck['spawn_time'] > effective_timeout:
                            ducks_to_remove.append(duck)

                    for duck in ducks_to_remove:
                        ducks.remove(duck)
                        duck_type = duck.get('duck_type', 'normal')
                        if self._trigger_hunting_dog(channel, duck):
                            continue  # Dog retrieved it — no fly-away message
                        msg_keys = {
                            'golden': 'golden_duck_flies_away',
                            'fast':   'fast_duck_flies_away',
                            'boss':   'boss_duck_flies_away',
                            'ninja':  'ninja_duck_flies_away',
                            'decoy':  'decoy_duck_flies_away',
                        }
                        msg_key = msg_keys.get(duck_type, 'duck_flies_away')
                        self.bot.send_message(channel, self.bot.messages.get(msg_key))

                    if not ducks:
                        channels_to_clear.append(channel)

                for channel in channels_to_clear:
                    if channel in self.ducks and not self.ducks[channel]:
                        del self.ducks[channel]

                self._clean_expired_effects()
        except asyncio.CancelledError:
            self.logger.info("Duck timeout loop cancelled")

    async def weather_loop(self):
        """Rotate weather for active channels every rotation interval (silent).

        Weather rotations are silent now — no periodic announcements. Weather
        state is displayed when a duck is spawned in the channel.
        """
        try:
            await asyncio.sleep(30)  # Let bot settle first
            while True:
                current_time = time.time()
                for channel in list(self.bot.channels_joined):
                    ck = self._channel_key(channel)
                    w = self.weather.get(ck)
                    if w is None or current_time >= w.get('expires_at', 0):
                        self._rotate_weather(ck)
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.logger.info("Weather loop cancelled")

    # -----------------------------------------------------------------------
    # Weather helpers
    # -----------------------------------------------------------------------

    def get_channel_weather(self, channel: str) -> dict:
        """Return the current weather dict for a channel, rotating if expired."""
        ck = self._channel_key(channel)
        w = self.weather.get(ck)
        if w is None or time.time() >= w.get('expires_at', 0):
            self._rotate_weather(ck)
            w = self.weather[ck]
        return w

    def _rotate_weather(self, channel_key: str):
        """Pick a new random weather state for a channel (silent — no announce)."""
        states  = ['clear', 'rain', 'fog', 'storm']
        weights = [40, 25, 20, 15]
        new_state = random.choices(states, weights=weights, k=1)[0]
        cfg = WEATHER_STATES[new_state]
        self.weather[channel_key] = {
            'state': new_state,
            'expires_at': time.time() + cfg['duration'],
        }

    # -----------------------------------------------------------------------
    # Duck factory helpers
    # -----------------------------------------------------------------------

    def _make_duck(self, duck_type, channel, channel_key, t, **extra):
        prefix = duck_type
        base = {
            'id': f"{prefix}_duck_{int(t)}_{random.randint(1000,9999)}",
            'spawn_time': t, 'channel': channel, 'duck_type': duck_type,
            'max_hp': 1, 'current_hp': 1,
        }
        base.update(extra)
        return base

    # -----------------------------------------------------------------------
    # Duck spawning
    # -----------------------------------------------------------------------

    async def spawn_duck(self, channel):
        """Spawn a duck (or flock) in the channel"""
        channel_key = self._channel_key(channel)
        if channel_key not in self.ducks:
            self.ducks[channel_key] = []
        if self.ducks[channel_key]:
            return

        t = time.time()
        flock_chance = self.bot.get_config('duck_spawning.flock_chance', 0.08)
        if random.random() < flock_chance:
            await self._spawn_flock(channel, channel_key, t)
            return

        boss_chance   = self.bot.get_config('duck_types.boss.chance',   0.05)
        golden_chance = self.bot.get_config('duck_types.golden.chance',  self.bot.get_config('golden_duck_chance', 0.15))
        fast_chance   = self.bot.get_config('duck_types.fast.chance',    self.bot.get_config('fast_duck_chance', 0.20))
        ninja_chance  = self.bot.get_config('duck_types.ninja.chance',   0.10)
        decoy_chance  = self.bot.get_config('duck_types.decoy.chance',   0.07)

        rand = random.random()
        cumulative = 0

        def _hit(chance):
            nonlocal cumulative, rand
            cumulative += chance
            return rand < cumulative

        if _hit(boss_chance):
            min_hp = int(self.bot.get_config('duck_types.boss.min_hp', 8))
            max_hp = int(self.bot.get_config('duck_types.boss.max_hp', 15))
            hp = random.randint(min_hp, max_hp)
            duck = self._make_duck('boss', channel, channel_key, t,
                                   max_hp=hp, current_hp=hp, contributors={})
            self.logger.info(f"Boss duck spawned in {channel_key} with {hp} HP")
            msg = self.bot.messages.get('boss_duck_spawn', hp=hp)
            if msg.startswith('[Missing'):
                msg = f"💀 A BOSS DUCK has appeared with {hp} HP! Everyone !bang to take it down!"
            weather = self.get_channel_weather(channel)
            w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
            msg += f" [Weather: {w_cfg['name']}]"
            self.ducks[channel_key].append(duck)
            self.bot.send_message(channel, msg)
            return
        elif _hit(golden_chance):
            min_hp = int(self.bot.get_config('duck_types.golden.min_hp', self.bot.get_config('golden_duck_min_hp', 3)))
            max_hp = int(self.bot.get_config('duck_types.golden.max_hp', self.bot.get_config('golden_duck_max_hp', 5)))
            hp = random.randint(min_hp, max_hp)
            duck = self._make_duck('golden', channel, channel_key, t, max_hp=hp, current_hp=hp)
            self.logger.info(f"Golden duck spawned in {channel_key} with {hp} HP")
        elif _hit(fast_chance):
            duck = self._make_duck('fast', channel, channel_key, t)
            self.logger.info(f"Fast duck spawned in {channel_key}")
        elif _hit(ninja_chance):
            dodge = float(self.bot.get_config('duck_types.ninja.dodge_chance', 0.35))
            duck = self._make_duck('ninja', channel, channel_key, t, dodge_chance=dodge)
            self.logger.info(f"Ninja duck spawned in {channel_key}")
        elif _hit(decoy_chance):
            duck = self._make_duck('decoy', channel, channel_key, t)
            self.logger.info(f"Decoy duck spawned in {channel_key}")
        else:
            duck = self._make_duck('normal', channel, channel_key, t)
            self.logger.info(f"Normal duck spawned in {channel_key}")

        self.ducks[channel_key].append(duck)
        # Use the preferred spawn template if present (the dotted/ornate prefix)
        msg = self.bot.messages.get_choice('duck_spawn', match='·.¸¸.·´¯`·.¸¸.·´¯`·.')
        weather = self.get_channel_weather(channel)
        w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
        msg += f" [Weather: {w_cfg['name']}]"
        self.bot.send_message(channel, msg)

    async def _spawn_flock(self, channel, channel_key, t):
        """Spawn a flock of 2-4 normal ducks."""
        flock_size = random.randint(2, 4)
        for i in range(flock_size):
            duck = {
                'id': f"flock_duck_{int(t)}_{i}_{random.randint(100,999)}",
                'spawn_time': t, 'channel': channel,
                'duck_type': 'flock', 'max_hp': 1, 'current_hp': 1, 'is_flock': True,
            }
            self.ducks[channel_key].append(duck)
        msg = self.bot.messages.get('duck_flock', count=flock_size)
        if msg.startswith('[Missing'):
            msg = f"🦆🦆🦆 A flock of {flock_size} ducks has landed! Type !bang to pick them off!"
        weather = self.get_channel_weather(channel)
        w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
        msg += f" [Weather: {w_cfg['name']}]"
        self.bot.send_message(channel, msg)
        self.logger.info(f"Flock of {flock_size} ducks spawned in {channel_key}")

    # -----------------------------------------------------------------------
    # Shooting
    # -----------------------------------------------------------------------

    def shoot_duck(self, nick, channel, player):
        """Handle !bang command"""
        channel_key = self._channel_key(channel)

        # Bang cooldown
        cooldown = float(self.bot.get_config('gameplay.bang_cooldown', 1.5) or 1.5)
        if time.time() - player.get('last_bang_time', 0) < cooldown:
            return {'success': False, 'message_key': 'bang_cooldown',
                    'message_args': {'nick': nick}}
        player['last_bang_time'] = time.time()

        # Pre-shot checks
        if player.get('gun_confiscated', False):
            return {'success': False, 'message_key': 'bang_not_armed', 'message_args': {'nick': nick}}
        if self._is_player_wet(player):
            return {'success': False, 'message_key': 'bang_wet_clothes', 'message_args': {'nick': nick}}
        if player.get('current_ammo', 0) <= 0:
            return {'success': False, 'message_key': 'bang_no_ammo', 'message_args': {'nick': nick}}

        # Weather
        weather = self.get_channel_weather(channel)
        w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])

        # Gun jam check
        base_jam = self.bot.levels.get_jam_chance(player) + w_cfg['jam_modifier']
        if random.random() < max(0, min(100, base_jam)) / 100.0:
            player['current_ammo'] = player.get('current_ammo', 1) - 1
            self.db.save_database()
            return {'success': False, 'message_key': 'bang_gun_jammed', 'message_args': {'nick': nick}}

        # Wild shot (no duck)?
        if channel_key not in self.ducks or not self.ducks[channel_key]:
            player['shots_fired']  = player.get('shots_fired', 0) + 1
            player['shots_missed'] = player.get('shots_missed', 0) + 1
            player['confiscated_ammo']      = player.get('current_ammo', 1) - 1
            player['confiscated_magazines'] = player.get('magazines', 0)
            player['current_ammo']    = 0
            player['gun_confiscated'] = True
            player['gun_confiscated_count'] = player.get('gun_confiscated_count', 0) + 1
            player['current_streak'] = 0
            self.db.save_database()
            result = {'success': False, 'message_key': 'bang_no_duck', 'message_args': {'nick': nick}}
            new_ach = self._check_achievements(player, 'confiscated')
            if new_ach:
                result['new_achievements'] = new_ach
            return result

        duck = self.ducks[channel_key][0]
        duck_type = duck.get('duck_type', 'normal')

        # Decoy — bang confiscates your gun
        if duck_type == 'decoy':
            self.ducks[channel_key].pop(0)
            player['shots_fired']  = player.get('shots_fired', 0) + 1
            player['shots_missed'] = player.get('shots_missed', 0) + 1
            player['current_ammo'] = player.get('current_ammo', 1) - 1
            player['confiscated_ammo']      = player.get('current_ammo', 0)
            player['confiscated_magazines'] = player.get('magazines', 0)
            player['current_ammo']    = 0
            player['gun_confiscated'] = True
            player['gun_confiscated_count'] = player.get('gun_confiscated_count', 0) + 1
            player['current_streak'] = 0
            self.db.save_database()
            return {'success': False, 'message_key': 'bang_decoy',
                    'message_args': {'nick': nick}}

        player['current_ammo'] = player.get('current_ammo', 1) - 1
        player['shots_fired']  = player.get('shots_fired', 0) + 1

        # Accuracy calculation
        base_acc   = self.bot.levels.get_modified_accuracy(player) + w_cfg['accuracy_modifier']
        scope_bonus = self._apply_scope_effect(player)
        hit_chance  = max(5, min(100, base_acc + scope_bonus)) / 100.0

        clover = self._get_active_effect(player, 'clover_luck')
        if clover:
            try:
                min_hit = float(clover.get('min_hit_chance', 0.0) or 0.0)
            except (ValueError, TypeError):
                min_hit = 0.0
            hit_chance = max(hit_chance, max(0.0, min(min_hit, 1.0)))

        # Ninja dodge
        if duck_type == 'ninja':
            dodge = float(duck.get('dodge_chance', 0.35))
            if random.random() < dodge:
                player['shots_missed'] = player.get('shots_missed', 0) + 1
                player['xp'] = max(0, player.get('xp', 0) - 1)
                player['current_streak'] = 0
                self.db.save_database()
                return {'success': True, 'hit': False,
                        'message_key': 'bang_ninja_dodge',
                        'message_args': {'nick': nick}}

        if random.random() < hit_chance:
            return self._process_hit(nick, channel, channel_key, player, duck, duck_type, w_cfg)
        else:
            return self._process_miss(nick, channel, channel_key, player)

    def _process_hit(self, nick, channel, channel_key, player, duck, duck_type, w_cfg):
        """Handle a successful shot."""
        xp_mod = w_cfg['xp_modifier']
        is_flock = duck.get('is_flock', False)

        # Boss duck — multi-contributor
        if duck_type == 'boss':
            duck['contributors'][nick] = duck['contributors'].get(nick, 0) + 1
            duck['current_hp'] -= 1
            xp_per_hit = int(self.bot.get_config('duck_types.boss.xp_per_hit', 5) * xp_mod)
            player['xp'] = player.get('xp', 0) + xp_per_hit
            if duck['current_hp'] > 0:
                self.db.save_database()
                return {'success': True, 'hit': True,
                        'message_key': 'bang_hit_boss',
                        'message_args': {'nick': nick, 'hp_remaining': duck['current_hp'],
                                         'xp_gained': xp_per_hit}}
            else:
                self.ducks[channel_key].pop(0)
                total_bonus_xp = int(self.bot.get_config('duck_types.boss.kill_bonus_xp', 50) * xp_mod)
                contributors = duck['contributors']
                total_hits = sum(contributors.values())
                self._distribute_boss_xp(contributors, total_hits, total_bonus_xp)
                new_ach = self._check_achievements(player, 'duck_shot', duck_type='boss',
                                                   weather_state=w_cfg.get('name', 'clear'))
                self.db.save_database()
                return {'success': True, 'hit': True,
                        'message_key': 'bang_hit_boss_killed',
                        'message_args': {'nick': nick, 'xp_gained': xp_per_hit},
                        'boss_contributors': contributors,
                        'boss_bonus_xp': total_bonus_xp,
                        'new_achievements': new_ach or []}

        # Golden duck — multi-hit
        if duck_type == 'golden':
            duck['current_hp'] -= 1
            xp_gained = int(self.bot.get_config('golden_duck_xp', 15) * xp_mod)
            if duck['current_hp'] > 0:
                player['accuracy'] = min(player.get('accuracy', 75) +
                                         self.bot.get_config('accuracy_gain_on_hit', 1), 100)
                self.db.save_database()
                return {'success': True, 'hit': True, 'message_key': 'bang_hit_golden',
                        'message_args': {'nick': nick,
                                         'hp_remaining': duck['current_hp'], 'xp_gained': xp_gained}}
            # Killed golden duck
            self.ducks[channel_key].pop(0)
            xp_gained *= duck['max_hp']
            message_key = 'bang_hit_golden_killed'
        elif duck_type in ('fast',):
            self.ducks[channel_key].pop(0)
            xp_gained = int(self.bot.get_config('fast_duck_xp', 12) * xp_mod)
            message_key = 'bang_hit_fast'
        elif duck_type == 'ninja':
            self.ducks[channel_key].pop(0)
            xp_gained = int(self.bot.get_config('duck_types.ninja.xp', 14) * xp_mod)
            message_key = 'bang_hit_ninja'
        elif duck_type == 'flock':
            self.ducks[channel_key].pop(0)
            xp_gained = int(self.bot.get_config('normal_duck_xp', 10) * xp_mod)
            message_key = 'bang_hit_flock'
        else:  # normal
            self.ducks[channel_key].pop(0)
            xp_gained = int(self.bot.get_config('normal_duck_xp', 10) * xp_mod)
            message_key = 'bang_hit'

        # Apply XP / stats
        old_level = self.bot.levels.calculate_player_level(player)
        player['xp'] = player.get('xp', 0) + xp_gained
        player['ducks_shot'] = player.get('ducks_shot', 0) + 1
        player['current_streak'] = player.get('current_streak', 0) + 1
        if player['current_streak'] > player.get('best_streak', 0):
            player['best_streak'] = player['current_streak']
        player['accuracy'] = min(
            player.get('accuracy', 75) + self.bot.get_config('accuracy_gain_on_hit', 1),
            self.bot.get_config('max_accuracy', 100)
        )
        new_level = self.bot.levels.calculate_player_level(player)
        if new_level != old_level:
            self.bot.levels.update_player_magazines(player)
        if self.bot.get_config('duck_spawning.rearm_on_duck_shot', False):
            self._rearm_all_disarmed_players(channel)

        dropped_item = self._check_item_drop(player, duck_type)
        new_ach = self._check_achievements(player, 'duck_shot', duck_type=duck_type,
                                           weather_state=w_cfg.get('name', 'clear'))
        self.db.save_database()

        # Global announcement for golden duck kill
        if duck_type == 'golden' and message_key == 'bang_hit_golden_killed':
            if self.bot.get_config('gameplay.global_announcements', False):
                for ch in list(self.bot.channels_joined):
                    if self._channel_key(ch) != self._channel_key(channel):
                        self.bot.send_message(ch,
                            f"📢 [Global] {nick} just slayed a Golden Duck in {channel}!")

        result = {
            'success': True, 'hit': True,
            'message_key': message_key,
            'message_args': {
                'nick': nick, 'xp_gained': xp_gained,
                'ducks_shot': player['ducks_shot'],
            },
        }
        if is_flock or duck_type == 'flock':
            result['message_args']['remaining_flock'] = len(self.ducks.get(channel_key, []))
        if dropped_item:
            result['dropped_item'] = dropped_item
        if new_ach:
            result['new_achievements'] = new_ach
        return result

    def _process_miss(self, nick, channel, channel_key, player):
        """Handle a miss."""
        player['shots_missed'] = player.get('shots_missed', 0) + 1
        player['current_streak'] = 0

        # Check body armor before applying XP loss
        if self._consume_body_armor(player):
            self.db.save_database()
            new_ach = self._check_achievements(player, 'armor_used')
            result = {'success': True, 'hit': False,
                      'message_key': 'bang_miss_armored',
                      'message_args': {'nick': nick}}
            if new_ach:
                result['new_achievements'] = new_ach
            return result

        player['xp'] = max(0, player.get('xp', 0) - 1)
        accuracy_loss = self.bot.get_config('gameplay.accuracy_loss_on_miss', 2)
        min_accuracy  = self.bot.get_config('gameplay.min_accuracy', 10)
        player['accuracy'] = max(player.get('accuracy', 75) - accuracy_loss, min_accuracy)

        # Friendly fire chance
        friendly_fire_chance = 0.15
        if random.random() < friendly_fire_chance:
            armed_players = [
                (n, p) for n, p in self.db.get_players_for_channel(channel).items()
                if str(n).lower() != nick.lower()
                and not p.get('gun_confiscated', False)
                and p.get('current_ammo', 0) > 0
            ]
            if armed_players:
                victim_nick, victim_player = random.choice(armed_players)
                if self._check_insurance_protection(player, 'friendly_fire'):
                    self.db.save_database()
                    return {'success': True, 'hit': False, 'friendly_fire': True,
                            'victim': victim_nick,
                            'message_key': 'bang_friendly_fire_insured',
                            'message_args': {'nick': nick, 'victim': victim_nick}}
                xp_loss = min(player.get('xp', 0) // 4, 25)
                player['xp'] = max(0, player.get('xp', 0) - xp_loss)
                player['confiscated_ammo']      = player.get('current_ammo', 0)
                player['confiscated_magazines'] = player.get('magazines', 0)
                player['current_ammo']    = 0
                player['gun_confiscated'] = True
                player['gun_confiscated_count'] = player.get('gun_confiscated_count', 0) + 1
                self.db.save_database()
                return {'success': True, 'hit': False, 'friendly_fire': True,
                        'victim': victim_nick,
                        'message_key': 'bang_friendly_fire_penalty',
                        'message_args': {'nick': nick, 'victim': victim_nick, 'xp_lost': xp_loss}}

        self.db.save_database()
        return {'success': True, 'hit': False,
                'message_key': 'bang_miss', 'message_args': {'nick': nick}}

    def _distribute_boss_xp(self, contributors: dict, total_hits: int, bonus_xp: int):
        """Award proportional bonus XP to all boss duck contributors."""
        if total_hits == 0:
            return
        for nick, hits in contributors.items():
            try:
                share = int(bonus_xp * hits / total_hits)
                # Find player across channels and add XP
                for ch_key, ch_data in (self.db.channels or {}).items():
                    players = ch_data.get('players', {})
                    if nick.lower() in players:
                        players[nick.lower()]['xp'] = players[nick.lower()].get('xp', 0) + share
                        break
            except Exception as e:
                self.logger.debug(f"Error distributing boss XP to {nick}: {e}")

    # -----------------------------------------------------------------------
    # Befriending
    # -----------------------------------------------------------------------

    def befriend_duck(self, nick, channel, player):
        """Handle !bef command"""
        channel_key = self._channel_key(channel)

        if channel_key not in self.ducks or not self.ducks[channel_key]:
            return {'success': False, 'message_key': 'bef_no_duck', 'message_args': {'nick': nick}}

        duck = self.ducks[channel_key][0]
        duck_type = duck.get('duck_type', 'normal')

        # Decoy duck: !bef succeeds and gives a reward
        if duck_type == 'decoy':
            self.ducks[channel_key].pop(0)
            xp_gained = self.bot.get_config('duck_types.decoy.bef_xp', 5)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            self.db.save_database()
            return {'success': True, 'befriended': True,
                    'message_key': 'bef_decoy',
                    'message_args': {'nick': nick, 'xp_gained': xp_gained}}

        # Trap effect on player: !bef fails, XP penalty
        trap = self._get_active_effect(player, 'trap')
        if trap:
            player['temporary_effects'] = [
                e for e in player.get('temporary_effects', []) if e is not trap
            ]
            xp_loss = int(self.bot.get_config('duck_types.trap.xp_penalty', 5))
            player['xp'] = max(0, player.get('xp', 0) - xp_loss)
            self.db.save_database()
            return {'success': False,
                    'message_key': 'bef_trapped',
                    'message_args': {'nick': nick, 'xp_lost': xp_loss,
                                     'set_by': trap.get('set_by', 'someone')}}

        # Boss duck can be befriended (low chance) or simply fail
        if duck_type == 'boss':
            return {'success': False, 'message_key': 'bef_boss_not_interested',
                    'message_args': {'nick': nick}}

        base_rate = self.bot.get_config('gameplay.befriend_success_rate', 75)
        try:
            base_rate = float(base_rate) if base_rate is not None else 75.0
        except (ValueError, TypeError):
            base_rate = 75.0
        level_modified_rate = self.bot.levels.get_modified_befriend_rate(player, base_rate)
        success_rate = level_modified_rate / 100.0

        clover = self._get_active_effect(player, 'clover_luck')
        if clover:
            try:
                min_bef = float(clover.get('min_befriend_chance', 0.0) or 0.0)
            except (ValueError, TypeError):
                min_bef = 0.0
            success_rate = max(success_rate, max(0.0, min(min_bef, 1.0)))

        if random.random() < success_rate:
            self.ducks[channel_key].pop(0)
            xp_gained = self.bot.get_config('gameplay.befriend_xp', 5)
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            player['current_streak'] = player.get('current_streak', 0) + 1
            if player['current_streak'] > player.get('best_streak', 0):
                player['best_streak'] = player['current_streak']
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            if self.bot.get_config('rearm_on_duck_shot', False):
                self._rearm_all_disarmed_players(channel)
            new_ach = self._check_achievements(player, 'duck_befriended', duck_type=duck_type)
            self.db.save_database()
            result = {'success': True, 'befriended': True,
                      'message_key': 'bef_success',
                      'message_args': {'nick': nick, 'xp_gained': xp_gained,
                                       'ducks_befriended': player['ducks_befriended']}}
            if new_ach:
                result['new_achievements'] = new_ach
            return result
        else:
            player['current_streak'] = 0
            self.db.save_database()
            return {'success': True, 'befriended': False,
                    'message_key': 'bef_failed', 'message_args': {'nick': nick}}

    # -----------------------------------------------------------------------
    # Reloading
    # -----------------------------------------------------------------------

    def reload_gun(self, nick, channel, player):
        """Handle !reload command"""
        if player.get('gun_confiscated', False):
            return {'success': False, 'message_key': 'reload_not_armed', 'message_args': {'nick': nick}}
        current_ammo    = player.get('current_ammo', 0)
        bullets_per_mag = player.get('bullets_per_magazine', 6)
        if current_ammo >= bullets_per_mag:
            return {'success': False, 'message_key': 'reload_already_loaded', 'message_args': {'nick': nick}}
        
        # Check if we need to auto-use a magazine from inventory
        if player.get('magazines', 1) <= 1:
            inventory = player.get('inventory', {})
            magazine_item_id = None
            if hasattr(self.bot, 'shop') and self.bot.shop:
                for item_id_str, qty in inventory.items():
                    if qty > 0:
                        try:
                            item = self.bot.shop.get_item(int(item_id_str))
                            if item and item.get('type') == 'magazine':
                                magazine_item_id = item_id_str
                                break
                        except ValueError:
                            pass
            
            if magazine_item_id:
                # Auto consume 1 magazine item
                inventory[magazine_item_id] -= 1
                if inventory[magazine_item_id] <= 0:
                    del inventory[magazine_item_id]
                player['inventory'] = inventory
                
                item_amount = self.bot.shop.get_item(int(magazine_item_id)).get('amount', 1)
                player['magazines'] = player.get('magazines', 1) + item_amount
            else:
                return {'success': False, 'message_key': 'reload_no_chargers', 'message_args': {'nick': nick}}
                
        player['current_ammo'] = bullets_per_mag
        player['magazines'] = player.get('magazines', 1) - 1
        
        # Count spare magazines after reload: level-slots + inventory Magazine items (by count, not amount)
        active_spares = max(0, player.get('magazines', 1) - 1)
        inv_mags = 0
        if hasattr(self.bot, 'shop') and self.bot.shop:
            inventory = player.get('inventory', {})
            for item_id_str, qty in inventory.items():
                if qty > 0:
                    try:
                        item = self.bot.shop.get_item(int(item_id_str))
                        if item and item.get('type') == 'magazine':
                            inv_mags += qty
                    except ValueError:
                        pass
        total_spares = active_spares + inv_mags
        
        self.db.save_database()
        return {'success': True, 'message_key': 'reload_success',
                'message_args': {'nick': nick, 'ammo': player['current_ammo'],
                                 'max_ammo': bullets_per_mag,
                                 'chargers': total_spares}}

    # -----------------------------------------------------------------------
    # Achievement system
    # -----------------------------------------------------------------------

    def _check_achievements(self, player, event: str, **context) -> list:
        """Check and award any newly earned achievements. Returns list of new achievement dicts."""
        earned_ids = {a['id'] for a in player.get('achievements', []) if isinstance(a, dict)}
        new_achievements = []

        def _award(ach_id):
            if ach_id not in earned_ids and ach_id in ACHIEVEMENTS:
                ach = dict(ACHIEVEMENTS[ach_id])
                ach['id'] = ach_id
                ach['earned_at'] = time.time()
                player.setdefault('achievements', []).append(ach)
                earned_ids.add(ach_id)
                new_achievements.append(ach)
                self.logger.info(f"Achievement unlocked for {player.get('nick', '?')}: {ach['name']}")

        ducks_shot      = player.get('ducks_shot', 0)
        ducks_befriended= player.get('ducks_befriended', 0)
        streak          = player.get('current_streak', 0)
        confiscations   = player.get('gun_confiscated_count', 0)
        xp_spent        = player.get('total_xp_spent', 0)
        daily_streak    = player.get('daily_streak', 0)
        duck_type       = context.get('duck_type', '')
        weather_state   = context.get('weather_state', '')

        if event == 'duck_shot':
            if ducks_shot >= 1:   _award('first_blood')
            if ducks_shot >= 100: _award('century_hunter')
            if ducks_shot >= 500: _award('legendary')
            if streak >= 10:      _award('sharpshooter')
            if duck_type == 'golden': _award('golden_slayer')
            if duck_type == 'boss':   _award('boss_slayer')
            if duck_type == 'ninja':  _award('ninja_slayer')
            if duck_type in ('flock',): _award('flock_master')
            if 'Storm' in weather_state or 'storm' in weather_state: _award('storm_hunter')
        elif event == 'duck_befriended':
            if ducks_befriended >= 50: _award('duck_whisperer')
        elif event == 'confiscated':
            if confiscations >= 10: _award('trigger_happy')
        elif event == 'xp_spent':
            if xp_spent >= 500: _award('high_roller')
        elif event == 'daily':
            if daily_streak >= 7: _award('daily_devotee')
        elif event == 'armor_used':
            _award('survivor')
        elif event == 'mystery_box':
            _award('mystery_lover')

        return new_achievements

    # -----------------------------------------------------------------------
    # Hunting dog
    # -----------------------------------------------------------------------

    def _trigger_hunting_dog(self, channel: str, duck: dict) -> bool:
        """
        Check if any player in the channel has a hunting dog effect active.
        If so, consume one use and immediately re-spawn the duck.
        Returns True if the dog retrieved the duck (caller skips fly-away msg).
        """
        try:
            current_time = time.time()
            for _nick, player_data in self.db.get_players_for_channel(channel).items():
                effects = player_data.get('temporary_effects', [])
                for effect in effects:
                    if (effect.get('type') == 'second_chance'
                            and effect.get('expires_at', 0) > current_time):
                        # Consume the dog
                        player_data['temporary_effects'] = [
                            e for e in effects if e is not effect
                        ]
                        # Re-add the duck to the channel
                        channel_key = self._channel_key(channel)
                        if channel_key not in self.ducks:
                            self.ducks[channel_key] = []
                        new_duck = dict(duck)
                        new_duck['spawn_time'] = current_time  # Reset timeout
                        self.ducks[channel_key].append(new_duck)
                        msg = self.bot.messages.get('hunting_dog_retrieves')
                        if msg.startswith('[Missing'):
                            msg = "🐕 A hunting dog fetches the duck back! It's still out there!"
                        self.bot.send_message(channel, msg)
                        return True
        except Exception as e:
            self.logger.error(f"Error checking hunting dog: {e}")
        return False

    # -----------------------------------------------------------------------
    # Effect helpers
    # -----------------------------------------------------------------------

    def _apply_scope_effect(self, player) -> int:
        """Return scope accuracy bonus and decrement shots_remaining."""
        effect = self._get_active_effect(player, 'temporary_accuracy')
        if not effect:
            return 0
        bonus = int(effect.get('accuracy_bonus', 20))
        shots_left = effect.get('shots_remaining', 0) - 1
        if shots_left <= 0:
            player['temporary_effects'] = [
                e for e in player.get('temporary_effects', []) if e is not effect
            ]
        else:
            effect['shots_remaining'] = shots_left
        return bonus

    def _consume_body_armor(self, player) -> bool:
        """If player has active body armor, consume it and return True."""
        effect = self._get_active_effect(player, 'xp_shield')
        if not effect:
            return False
        player['temporary_effects'] = [
            e for e in player.get('temporary_effects', []) if e is not effect
        ]
        return True

    def _get_active_spawn_multiplier(self):
        """Get the current spawn rate multiplier from active bread effects."""
        max_multiplier = 1.0
        current_time = time.time()
        try:
            for _ch, _pn, player_data in self.db.iter_all_players():
                for effect in player_data.get('temporary_effects', []):
                    if (effect.get('type') == 'attract_ducks'
                            and effect.get('expires_at', 0) > current_time):
                        max_multiplier = max(max_multiplier,
                                             effect.get('spawn_multiplier', 1.0))
        except Exception as e:
            self.logger.error(f"Error getting spawn multiplier: {e}")
        return max_multiplier

    def _is_player_wet(self, player):
        current_time = time.time()
        for effect in player.get('temporary_effects', []):
            if effect.get('type') == 'wet_clothes' and effect.get('expires_at', 0) > current_time:
                return True
        return False

    def _check_insurance_protection(self, player, protection_type):
        current_time = time.time()
        try:
            for effect in player.get('temporary_effects', []):
                if (effect.get('type') == 'insurance'
                        and effect.get('protection') == protection_type
                        and effect.get('expires_at', 0) > current_time):
                    return True
        except Exception as e:
            self.logger.error(f"Error checking insurance: {e}")
        return False

    def _clean_expired_effects(self):
        """Remove expired temporary effects from all players."""
        current_time = time.time()
        try:
            for _ch, player_name, player_data in self.db.iter_all_players():
                effects = player_data.get('temporary_effects', [])
                active = [e for e in effects if e.get('expires_at', 0) > current_time]
                if len(active) != len(effects):
                    player_data['temporary_effects'] = active
                    self.logger.debug(f"Cleaned expired effects for {player_name}")
        except Exception as e:
            self.logger.error(f"Error cleaning expired effects: {e}")

    def _get_active_effect(self, player, effect_type: str):
        """Return the first active effect matching effect_type, or None."""
        try:
            current_time = time.time()
            for effect in player.get('temporary_effects', []):
                if (isinstance(effect, dict)
                        and effect.get('type') == effect_type
                        and effect.get('expires_at', 0) > current_time):
                    return effect
        except Exception:
            pass
        return None

    # -----------------------------------------------------------------------
    # Item drops
    # -----------------------------------------------------------------------

    def _check_item_drop(self, player, duck_type):
        """Check for item drops and add to player inventory. Returns drop info or None."""
        try:
            drop_chance = self.bot.get_config(f'duck_types.{duck_type}.drop_chance', 0.0)
            if random.random() > drop_chance:
                return None
            drop_table = self.bot.get_config(f'item_drops.{duck_type}_duck_drops', [])
            if not drop_table:
                return None
            total_weight = sum(item.get('weight', 1) for item in drop_table)
            if total_weight <= 0:
                return None
            random_weight = random.randint(1, total_weight)
            current_weight = 0
            for drop_item in drop_table:
                current_weight += drop_item.get('weight', 1)
                if random_weight <= current_weight:
                    item_id = drop_item.get('item_id')
                    if item_id:
                        inventory = player.get('inventory', {})
                        inventory[str(item_id)] = inventory.get(str(item_id), 0) + 1
                        player['inventory'] = inventory
                        item_info = self.bot.shop.get_item(item_id)
                        item_name = item_info.get('name', f'Item {item_id}') if item_info else f'Item {item_id}'
                        self.logger.info(f"Duck dropped {item_name} for {player.get('nick', '?')}")
                        return {'item_id': item_id, 'item_name': item_name, 'duck_type': duck_type}
                    break
        except Exception as e:
            self.logger.error(f"Error in _check_item_drop: {e}")
        return None

    # -----------------------------------------------------------------------
    # Misc helpers
    # -----------------------------------------------------------------------

    def _rearm_all_disarmed_players(self, channel):
        try:
            rearmed = 0
            for _pn, player_data in self.db.get_players_for_channel(channel).items():
                if player_data.get('gun_confiscated', False):
                    player_data['gun_confiscated'] = False
                    self.bot.levels.update_player_magazines(player_data)
                    player_data['current_ammo'] = player_data.get('bullets_per_magazine', 6)
                    rearmed += 1
            if rearmed > 0:
                self.logger.info(f"Auto-rearmed {rearmed} players after duck shot")
        except Exception as e:
            self.logger.error(f"Error in _rearm_all_disarmed_players: {e}")