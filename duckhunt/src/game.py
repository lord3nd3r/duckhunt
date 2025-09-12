import asyncio
import random
from src.items import DuckTypes, WeaponTypes, AmmoTypes, Attachments
from src.auth import AuthSystem

class DuckGame:
    def __init__(self, bot, db):
        self.bot = bot
        self.config = bot.config
        self.logger = getattr(bot, 'logger', None)
        self.db = db
        self.auth = AuthSystem(db)
        self.duck_spawn_min = self.config.get('duck_spawn_min', 30)
        self.duck_spawn_max = self.config.get('duck_spawn_max', 120)
        self.ducks = {}  # channel: duck dict or None
        self.players = {}  # nick: player dict
        self.duck_alerts = set()  # nicks who want duck alerts

    def get_player(self, nick):
        if nick in self.players:
            return self.players[nick]
        data = self.db.load_player(nick)
        if data:
            data['friends'] = set(data.get('friends', []))
            self.players[nick] = data
            return data
        default = {
            'ammo': 1, 'max_ammo': 1, 'friends': set(), 'caught': 0, 'coins': 100,
            'accuracy': 70, 'reliability': 80, 'gun_oil': 0, 'scope': False,
            'silencer': False, 'lucky_charm': False, 'xp': 0, 'level': 1,
            'bank_account': 0, 'insurance': {'active': False, 'claims': 0},
            'weapon': 'basic_gun', 'weapon_durability': 100, 'ammo_type': 'standard',
            'attachments': [], 'hunting_license': {'active': False, 'expires': None},
            'duck_alerts': False, 'auth_method': 'nick'  # 'nick', 'hostmask', 'account'
        }
        self.players[nick] = default
        return default

    def save_player(self, nick, data):
        self.players[nick] = data
        data_to_save = dict(data)
        data_to_save['friends'] = list(data_to_save.get('friends', []))
        self.db.save_player(nick, data_to_save)

    async def spawn_ducks_loop(self):
        while True:
            wait_time = random.randint(self.duck_spawn_min, self.duck_spawn_max)
            if self.logger:
                self.logger.info(f"Waiting {wait_time}s before next duck spawn.")
            await asyncio.sleep(wait_time)
            for chan in self.bot.channels:
                duck = self.ducks.get(chan)
                if not (duck and duck.get('alive')):
                    duck_type = DuckTypes.get_random_duck()
                    self.ducks[chan] = {
                        'alive': True, 
                        'type': duck_type,
                        'health': duck_type['health'],
                        'max_health': duck_type['health']
                    }
                    if self.logger:
                        self.logger.info(f"{duck_type['name']} spawned in {chan}")
                    
                    spawn_msg = f'\033[93m{duck_type["emoji"]} A {duck_type["name"]} appears! Type !bang, !catch, !bef, or !reload!\033[0m'
                    await self.bot.send_message(chan, spawn_msg)
                    
                    # Alert subscribed players
                    if self.duck_alerts:
                        alert_msg = f"🦆 DUCK ALERT: {duck_type['name']} in {chan}!"
                        for alert_nick in self.duck_alerts:
                            try:
                                await self.bot.send_message(alert_nick, alert_msg)
                            except:
                                pass  # User might be offline

    async def handle_command(self, user, channel, message):
        nick = user.split('!')[0] if user else 'unknown'
        hostmask = user if user else 'unknown'
        cmd = message.strip().lower()
        if self.logger:
            self.logger.info(f"{nick}@{channel}: {cmd}")
            
        # Handle private message commands
        if channel == self.bot.nick:  # Private message
            if cmd.startswith('identify '):
                parts = cmd.split(' ', 2)
                if len(parts) == 3:
                    await self.handle_identify(nick, parts[1], parts[2])
                else:
                    await self.bot.send_message(nick, "Usage: identify <username> <password>")
                return
            elif cmd == 'register':
                await self.bot.send_message(nick, "To register: /msg me register <username> <password>")
                return
            elif cmd.startswith('register '):
                parts = cmd.split(' ', 2)
                if len(parts) == 3:
                    await self.handle_register(nick, hostmask, parts[1], parts[2])
                else:
                    await self.bot.send_message(nick, "Usage: register <username> <password>")
                return
        
        # Public channel commands
        if cmd == '!bang':
            await self.handle_bang(nick, channel)
        elif cmd == '!reload':
            await self.handle_reload(nick, channel)
        elif cmd == '!bef':
            await self.handle_bef(nick, channel)
        elif cmd == '!catch':
            await self.handle_catch(nick, channel)
        elif cmd == '!shop':
            await self.handle_shop(nick, channel)
        elif cmd == '!duckstats':
            await self.handle_duckstats(nick, channel)
        elif cmd.startswith('!buy '):
            item_num = cmd.split(' ', 1)[1]
            await self.handle_buy(nick, channel, item_num)
        elif cmd.startswith('!sell '):
            item_num = cmd.split(' ', 1)[1]
            await self.handle_sell(nick, channel, item_num)
        elif cmd == '!stats':
            await self.handle_stats(nick, channel)
        elif cmd == '!help':
            await self.handle_help(nick, channel)
        elif cmd == '!leaderboard' or cmd == '!top':
            await self.handle_leaderboard(nick, channel)
        elif cmd == '!bank':
            await self.handle_bank(nick, channel)
        elif cmd == '!license':
            await self.handle_license(nick, channel)
        elif cmd == '!alerts':
            await self.handle_alerts(nick, channel)
        elif cmd.startswith('!trade '):
            parts = cmd.split(' ', 2)
            if len(parts) >= 2:
                await self.handle_trade(nick, channel, parts[1:])
        elif cmd.startswith('!sabotage '):
            target = cmd.split(' ', 1)[1]
            await self.handle_sabotage(nick, channel, target)

    async def handle_bang(self, nick, channel):
        player = self.get_player(nick)
        duck = self.ducks.get(channel)
        if player['ammo'] <= 0:
            await self.bot.send_message(channel, f'\033[91m{nick}, you need to !reload!\033[0m')
            return
        if duck and duck.get('alive'):
            player['ammo'] -= 1
            
            # Calculate hit chance based on accuracy and upgrades
            base_accuracy = player['accuracy']
            if player['scope']:
                base_accuracy += 15
            if player['lucky_charm']:
                base_accuracy += 10
            
            hit_roll = random.randint(1, 100)
            if hit_roll <= base_accuracy:
                player['caught'] += 1
                coins_earned = 1
                if player['silencer']:
                    coins_earned += 1  # Bonus for silencer
                player['coins'] += coins_earned
                self.ducks[channel] = {'alive': False}
                await self.bot.send_message(channel, f'\033[92m{nick} shot the duck! (+{coins_earned} coin{"s" if coins_earned > 1 else ""})\033[0m')
                if self.logger:
                    self.logger.info(f"{nick} shot a duck in {channel}")
            else:
                await self.bot.send_message(channel, f'\033[93m{nick} missed the duck!\033[0m')
        else:
            await self.bot.send_message(channel, f'No duck to shoot, {nick}!')
        self.save_player(nick, player)

    async def handle_reload(self, nick, channel):
        player = self.get_player(nick)
        
        # Check gun reliability - can fail to reload
        reliability = player['reliability']
        if player['gun_oil'] > 0:
            reliability += 15
            player['gun_oil'] -= 1  # Gun oil gets used up
        
        reload_roll = random.randint(1, 100)
        if reload_roll <= reliability:
            player['ammo'] = player['max_ammo']
            await self.bot.send_message(channel, f'\033[94m{nick} reloaded successfully!\033[0m')
        else:
            await self.bot.send_message(channel, f'\033[91m{nick}\'s gun jammed while reloading! Try again.\033[0m')
        
        self.save_player(nick, player)

    async def handle_bef(self, nick, channel):
        player = self.get_player(nick)
        duck = self.ducks.get(channel)
        if duck and duck.get('alive'):
            player['friends'].add('duck')
            self.ducks[channel] = {'alive': False}
            await self.bot.send_message(channel, f'\033[96m{nick} befriended the duck!\033[0m')
            if self.logger:
                self.logger.info(f"{nick} befriended a duck in {channel}")
        else:
            await self.bot.send_message(channel, f'No duck to befriend, {nick}!')
        self.save_player(nick, player)

    async def handle_catch(self, nick, channel):
        player = self.get_player(nick)
        duck = self.ducks.get(channel)
        if duck and duck.get('alive'):
            player['caught'] += 1
            self.ducks[channel] = {'alive': False}
            await self.bot.send_message(channel, f'\033[92m{nick} caught the duck!\033[0m')
            if self.logger:
                self.logger.info(f"{nick} caught a duck in {channel}")
        else:
            await self.bot.send_message(channel, f'No duck to catch, {nick}!')
        self.save_player(nick, player)

    async def handle_shop(self, nick, channel):
        player = self.get_player(nick)
        coins = player['coins']
        
        shop_items = [
            "🔫 Scope - Improves accuracy by 15% (Cost: 5 coins)",
            "🔇 Silencer - Bonus coin on successful shots (Cost: 8 coins)", 
            "🛢️ Gun Oil - Improves reload reliability for 3 reloads (Cost: 3 coins)",
            "🍀 Lucky Charm - Improves accuracy by 10% (Cost: 10 coins)",
            "📦 Ammo Upgrade - Increases max ammo capacity by 1 (Cost: 12 coins)",
            "🎯 Accuracy Training - Permanently increases accuracy by 5% (Cost: 15 coins)",
            "🔧 Gun Maintenance - Permanently increases reliability by 10% (Cost: 20 coins)"
        ]
        
        shop_msg = f"\033[95m{nick}'s Shop (Coins: {coins}):\033[0m\n"
        for i, item in enumerate(shop_items, 1):
            shop_msg += f"{i}. {item}\n"
        shop_msg += "Use !buy <number> to purchase an item!\n"
        shop_msg += "Use !sell <number> to sell upgrades for coins!"
        
        await self.bot.send_message(channel, shop_msg)
    async def handle_duckstats(self, nick, channel):
        player = self.get_player(nick)
        stats = f"\033[95m{nick}'s Duck Stats:\033[0m\n"
        stats += f"Caught: {player['caught']}\n"
        stats += f"Coins: {player['coins']}\n"
        stats += f"Accuracy: {player['accuracy']}%\n"
        stats += f"Reliability: {player['reliability']}%\n"
        stats += f"Max Ammo: {player['max_ammo']}\n"
        stats += f"Gun Oil: {player['gun_oil']} uses left\n"
        upgrades = []
        if player['scope']: upgrades.append("Scope")
        if player['silencer']: upgrades.append("Silencer") 
        if player['lucky_charm']: upgrades.append("Lucky Charm")
        stats += f"Upgrades: {', '.join(upgrades) if upgrades else 'None'}\n"
        stats += f"Friends: {', '.join(player['friends']) if player['friends'] else 'None'}\n"
        await self.bot.send_message(channel, stats)

    async def handle_buy(self, nick, channel, item_num):
        player = self.get_player(nick)
        
        try:
            item_id = int(item_num)
        except ValueError:
            await self.bot.send_message(channel, f'{nick}, please specify a valid item number!')
            return
            
        shop_items = {
            1: ("scope", 5, "Scope"),
            2: ("silencer", 8, "Silencer"),
            3: ("gun_oil", 3, "Gun Oil"),
            4: ("lucky_charm", 10, "Lucky Charm"),
            5: ("ammo_upgrade", 12, "Ammo Upgrade"),
            6: ("accuracy_training", 15, "Accuracy Training"),
            7: ("gun_maintenance", 20, "Gun Maintenance")
        }
        
        if item_id not in shop_items:
            await self.bot.send_message(channel, f'{nick}, invalid item number!')
            return
            
        item_key, cost, item_name = shop_items[item_id]
        
        if player['coins'] < cost:
            await self.bot.send_message(channel, f'\033[91m{nick}, you need {cost} coins for {item_name}! (You have {player["coins"]})\033[0m')
            return
            
        # Process purchase
        player['coins'] -= cost
        
        if item_key == "scope":
            if player['scope']:
                await self.bot.send_message(channel, f'{nick}, you already have a scope!')
                player['coins'] += cost  # Refund
                return
            player['scope'] = True
        elif item_key == "silencer":
            if player['silencer']:
                await self.bot.send_message(channel, f'{nick}, you already have a silencer!')
                player['coins'] += cost
                return
            player['silencer'] = True
        elif item_key == "gun_oil":
            player['gun_oil'] += 3
        elif item_key == "lucky_charm":
            if player['lucky_charm']:
                await self.bot.send_message(channel, f'{nick}, you already have a lucky charm!')
                player['coins'] += cost
                return
            player['lucky_charm'] = True
        elif item_key == "ammo_upgrade":
            player['max_ammo'] += 1
        elif item_key == "accuracy_training":
            player['accuracy'] = min(95, player['accuracy'] + 5)  # Cap at 95%
        elif item_key == "gun_maintenance":
            player['reliability'] = min(95, player['reliability'] + 10)  # Cap at 95%
            
        await self.bot.send_message(channel, f'\033[92m{nick} purchased {item_name}!\033[0m')
        self.save_player(nick, player)

    async def handle_sell(self, nick, channel, item_num):
        player = self.get_player(nick)
        
        try:
            item_id = int(item_num)
        except ValueError:
            await self.bot.send_message(channel, f'{nick}, please specify a valid item number!')
            return
            
        sellable_items = {
            1: ("scope", 3, "Scope"),
            2: ("silencer", 5, "Silencer"),
            3: ("gun_oil", 1, "Gun Oil (per use)"),
            4: ("lucky_charm", 6, "Lucky Charm")
        }
        
        if item_id not in sellable_items:
            await self.bot.send_message(channel, f'{nick}, invalid item number! Sellable items: 1-4')
            return
            
        item_key, sell_price, item_name = sellable_items[item_id]
        
        if item_key == "scope":
            if not player['scope']:
                await self.bot.send_message(channel, f'{nick}, you don\'t have a scope to sell!')
                return
            player['scope'] = False
            player['coins'] += sell_price
        elif item_key == "silencer":
            if not player['silencer']:
                await self.bot.send_message(channel, f'{nick}, you don\'t have a silencer to sell!')
                return
            player['silencer'] = False
            player['coins'] += sell_price
        elif item_key == "gun_oil":
            if player['gun_oil'] <= 0:
                await self.bot.send_message(channel, f'{nick}, you don\'t have any gun oil to sell!')
                return
            player['gun_oil'] -= 1
            player['coins'] += sell_price
        elif item_key == "lucky_charm":
            if not player['lucky_charm']:
                await self.bot.send_message(channel, f'{nick}, you don\'t have a lucky charm to sell!')
                return
            player['lucky_charm'] = False
            player['coins'] += sell_price
            
        await self.bot.send_message(channel, f'\033[94m{nick} sold {item_name} for {sell_price} coins!\033[0m')
        self.save_player(nick, player)

    async def handle_stats(self, nick, channel):
        player = self.get_player(nick)
        
        # Calculate effective accuracy and reliability
        effective_accuracy = player['accuracy']
        if player['scope']:
            effective_accuracy += 15
        if player['lucky_charm']:
            effective_accuracy += 10
        effective_accuracy = min(100, effective_accuracy)
        
        effective_reliability = player['reliability']
        if player['gun_oil'] > 0:
            effective_reliability += 15
        effective_reliability = min(100, effective_reliability)
        
        stats = f"\033[96m{nick}'s Combat Stats:\033[0m\n"
        stats += f"🎯 Base Accuracy: {player['accuracy']}% (Effective: {effective_accuracy}%)\n"
        stats += f"🔧 Base Reliability: {player['reliability']}% (Effective: {effective_reliability}%)\n"
        stats += f"🔫 Ammo: {player['ammo']}/{player['max_ammo']}\n"
        stats += f"💰 Coins: {player['coins']}\n"
        stats += f"🦆 Ducks Caught: {player['caught']}\n"
        stats += f"🛢️ Gun Oil: {player['gun_oil']} uses\n"
        
        upgrades = []
        if player['scope']: upgrades.append("🔭 Scope")
        if player['silencer']: upgrades.append("🔇 Silencer") 
        if player['lucky_charm']: upgrades.append("🍀 Lucky Charm")
        stats += f"⚡ Active Upgrades: {', '.join(upgrades) if upgrades else 'None'}\n"
        
        friends = list(player['friends'])
        stats += f"🤝 Friends: {', '.join(friends) if friends else 'None'}"
        
        await self.bot.send_message(channel, stats)

    async def handle_register(self, nick, hostmask, username, password):
        if self.auth.register_account(username, password, nick, hostmask):
            await self.bot.send_message(nick, f"✅ Account '{username}' registered successfully! Use 'identify {username} {password}' to login.")
        else:
            await self.bot.send_message(nick, f"❌ Account '{username}' already exists!")

    async def handle_identify(self, nick, username, password):
        if self.auth.authenticate(username, password, nick):
            await self.bot.send_message(nick, f"✅ Authenticated as '{username}'!")
            # Transfer nick-based data to account if exists
            nick_data = self.db.load_player(nick)
            if nick_data:
                account_data = self.db.load_player(username)
                if not account_data:
                    self.db.save_player(username, nick_data)
                    await self.bot.send_message(nick, "📊 Your progress has been transferred to your account!")
        else:
            await self.bot.send_message(nick, "❌ Invalid username or password!")

    async def handle_help(self, nick, channel):
        help_text = """
🦆 **DuckHunt Bot Commands** 🦆

**🎯 Hunting:**
• !bang - Shoot at a duck (requires ammo)
• !reload - Reload your weapon (can fail based on reliability)
• !catch - Catch a duck with your hands
• !bef - Befriend a duck instead of shooting

**🛒 Economy:**
• !shop - View available items for purchase
• !buy <number> - Purchase an item from the shop
• !sell <number> - Sell equipment for coins
• !bank - Access banking services (deposits, loans)
• !trade <player> <item> <amount> - Trade with other players

**📊 Stats & Info:**
• !stats - View detailed combat statistics
• !duckstats - View personal hunting statistics
• !leaderboard - View top players
• !license - Manage hunting license

**⚙️ Settings:**
• !alerts - Toggle duck spawn notifications
• !register - Register an account (via /msg)
• identify <user> <pass> - Login to account (via /msg)

**🎮 Advanced:**
• !sabotage <player> - Attempt to sabotage another hunter
• !help - Show this help message

💡 **Tips:**
- Different duck types give different rewards
- Weapon durability affects performance
- Insurance protects your equipment
- Level up to unlock better gear!
        """
        await self.bot.send_message(nick, help_text)

    async def handle_leaderboard(self, nick, channel):
        leaderboard_data = self.db.get_leaderboard('caught', 10)
        if not leaderboard_data:
            await self.bot.send_message(channel, "No leaderboard data available yet!")
            return
        
        msg = "🏆 **Duck Hunting Leaderboard** 🏆\n"
        for i, (account, caught) in enumerate(leaderboard_data, 1):
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            msg += f"{emoji} {account}: {caught} ducks\n"
        
        await self.bot.send_message(channel, msg)

    async def handle_bank(self, nick, channel):
        player = self.get_player(nick)
        bank_msg = f"""
🏦 **{nick}'s Bank Account** 🏦
💰 Cash on hand: {player['coins']} coins
🏛️ Bank balance: {player['bank_account']} coins
📈 Total wealth: {player['coins'] + player['bank_account']} coins

**Commands:**
• !bank deposit <amount> - Deposit coins (earns 2% daily interest)
• !bank withdraw <amount> - Withdraw coins
• !bank loan <amount> - Take a loan (10% interest)
        """
        await self.bot.send_message(nick, bank_msg)

    async def handle_license(self, nick, channel):
        player = self.get_player(nick)
        license_active = player['hunting_license']['active']
        
        if license_active:
            expires = player['hunting_license']['expires']
            msg = f"🎫 Your hunting license is active until {expires}\n"
            msg += "Licensed hunters get +25% coins and access to rare equipment!"
        else:
            msg = "🎫 You don't have a hunting license.\n"
            msg += "Purchase one for 50 coins to get:\n"
            msg += "• +25% coin rewards\n"
            msg += "• Access to premium shop items\n"
            msg += "• Reduced insurance costs\n"
            msg += "Type '!buy license' to purchase"
        
        await self.bot.send_message(channel, msg)

    async def handle_alerts(self, nick, channel):
        if nick in self.duck_alerts:
            self.duck_alerts.remove(nick)
            await self.bot.send_message(channel, f"🔕 {nick}: Duck alerts disabled")
        else:
            self.duck_alerts.add(nick)
            await self.bot.send_message(channel, f"🔔 {nick}: Duck alerts enabled! You'll be notified when ducks spawn.")

    async def handle_trade(self, nick, channel, args):
        if len(args) < 3:
            await self.bot.send_message(channel, f"{nick}: Usage: !trade <player> <item> <amount>")
            return
        
        target, item, amount = args[0], args[1], args[2]
        player = self.get_player(nick)
        
        try:
            amount = int(amount)
        except ValueError:
            await self.bot.send_message(channel, f"{nick}: Amount must be a number!")
            return
        
        if item == "coins":
            if player['coins'] < amount:
                await self.bot.send_message(channel, f"{nick}: You don't have enough coins!")
                return
            
            trade_data = {
                'type': 'coins',
                'amount': amount,
                'from_nick': nick
            }
            
            trade_id = self.db.save_trade(nick, target, trade_data)
            await self.bot.send_message(channel, f"💸 Trade offer sent to {target}: {amount} coins")
            await self.bot.send_message(target, f"💰 {nick} wants to trade you {amount} coins. Type '!accept {trade_id}' to accept!")
        else:
            await self.bot.send_message(channel, f"{nick}: Only coin trading is available currently!")

    async def handle_sabotage(self, nick, channel, target):
        player = self.get_player(nick)
        target_player = self.get_player(target)
        
        if player['coins'] < 5:
            await self.bot.send_message(channel, f"{nick}: Sabotage costs 5 coins!")
            return
        
        success_chance = 60 + (player['level'] * 5)
        if random.randint(1, 100) <= success_chance:
            player['coins'] -= 5
            target_player['weapon_durability'] = max(0, target_player['weapon_durability'] - 10)
            await self.bot.send_message(channel, f"😈 {nick} successfully sabotaged {target}'s weapon!")
            self.save_player(nick, player)
            self.save_player(target, target_player)
        else:
            player['coins'] -= 5
            await self.bot.send_message(channel, f"😅 {nick}'s sabotage attempt failed!")
            self.save_player(nick, player)
