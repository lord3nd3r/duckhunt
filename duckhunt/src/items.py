import random

class DuckTypes:
    COMMON = {
        'name': 'Common Duck',
        'emoji': '🦆',
        'rarity': 70,
        'coins': 1,
        'xp': 10,
        'health': 1
    }
    
    RARE = {
        'name': 'Rare Duck',
        'emoji': '🦆✨',
        'rarity': 20,
        'coins': 3,
        'xp': 25,
        'health': 1
    }
    
    GOLDEN = {
        'name': 'Golden Duck',
        'emoji': '🥇🦆',
        'rarity': 8,
        'coins': 10,
        'xp': 50,
        'health': 2
    }
    
    ARMORED = {
        'name': 'Armored Duck',
        'emoji': '🛡️🦆',
        'rarity': 2,
        'coins': 15,
        'xp': 75,
        'health': 3
    }
    
    @classmethod
    def get_random_duck(cls):
        roll = random.randint(1, 100)
        if roll <= cls.COMMON['rarity']:
            return cls.COMMON
        elif roll <= cls.COMMON['rarity'] + cls.RARE['rarity']:
            return cls.RARE
        elif roll <= cls.COMMON['rarity'] + cls.RARE['rarity'] + cls.GOLDEN['rarity']:
            return cls.GOLDEN
        else:
            return cls.ARMORED

class WeaponTypes:
    BASIC_GUN = {
        'name': 'Basic Gun',
        'accuracy_bonus': 0,
        'durability': 100,
        'max_durability': 100,
        'repair_cost': 5,
        'attachment_slots': 1
    }
    
    SHOTGUN = {
        'name': 'Shotgun',
        'accuracy_bonus': -10,
        'durability': 80,
        'max_durability': 80,
        'repair_cost': 8,
        'attachment_slots': 2,
        'spread_shot': True  # Can hit multiple ducks
    }
    
    RIFLE = {
        'name': 'Rifle',
        'accuracy_bonus': 20,
        'durability': 120,
        'max_durability': 120,
        'repair_cost': 12,
        'attachment_slots': 3
    }

class AmmoTypes:
    STANDARD = {
        'name': 'Standard Ammo',
        'damage': 1,
        'accuracy_modifier': 0,
        'cost': 1
    }
    
    RUBBER = {
        'name': 'Rubber Bullets',
        'damage': 0,  # Non-lethal, for catching
        'accuracy_modifier': 5,
        'cost': 2,
        'special': 'stun'
    }
    
    EXPLOSIVE = {
        'name': 'Explosive Rounds',
        'damage': 2,
        'accuracy_modifier': -5,
        'cost': 5,
        'special': 'area_damage'
    }

class Attachments:
    LASER_SIGHT = {
        'name': 'Laser Sight',
        'accuracy_bonus': 10,
        'cost': 15,
        'durability_cost': 2  # Uses weapon durability faster
    }
    
    EXTENDED_MAG = {
        'name': 'Extended Magazine',
        'ammo_bonus': 2,
        'cost': 20
    }
    
    BIPOD = {
        'name': 'Bipod',
        'accuracy_bonus': 15,
        'reliability_bonus': 5,
        'cost': 25
    }
