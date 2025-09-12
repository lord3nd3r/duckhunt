import sqlite3
import json
import datetime

class DuckDB:
    def __init__(self, db_path='duckhunt.db'):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            # Player data table
            self.conn.execute('''CREATE TABLE IF NOT EXISTS players (
                nick TEXT PRIMARY KEY,
                data TEXT
            )''')
            
            # Account system table
            self.conn.execute('''CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Leaderboards table
            self.conn.execute('''CREATE TABLE IF NOT EXISTS leaderboard (
                account TEXT,
                stat_type TEXT,
                value INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account, stat_type)
            )''')
            
            # Trading table
            self.conn.execute('''CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_account TEXT,
                to_account TEXT,
                trade_data TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

    def save_player(self, nick, data):
        with self.conn:
            self.conn.execute('''INSERT OR REPLACE INTO players (nick, data) VALUES (?, ?)''',
                              (nick, json.dumps(data)))

    def load_player(self, nick):
        cur = self.conn.cursor()
        cur.execute('SELECT data FROM players WHERE nick=?', (nick,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def get_all_players(self):
        cur = self.conn.cursor()
        cur.execute('SELECT nick, data FROM players')
        return {nick: json.loads(data) for nick, data in cur.fetchall()}
    
    def save_account(self, username, data):
        with self.conn:
            self.conn.execute('''INSERT OR REPLACE INTO accounts (username, data) VALUES (?, ?)''',
                              (username, json.dumps(data)))
    
    def load_account(self, username):
        cur = self.conn.cursor()
        cur.execute('SELECT data FROM accounts WHERE username=?', (username,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None
    
    def update_leaderboard(self, account, stat_type, value):
        with self.conn:
            self.conn.execute('''INSERT OR REPLACE INTO leaderboard (account, stat_type, value) VALUES (?, ?, ?)''',
                              (account, stat_type, value))
    
    def get_leaderboard(self, stat_type, limit=10):
        cur = self.conn.cursor()
        cur.execute('SELECT account, value FROM leaderboard WHERE stat_type=? ORDER BY value DESC LIMIT ?', 
                    (stat_type, limit))
        return cur.fetchall()
    
    def save_trade(self, from_account, to_account, trade_data):
        with self.conn:
            cur = self.conn.cursor()
            cur.execute('''INSERT INTO trades (from_account, to_account, trade_data) VALUES (?, ?, ?)''',
                        (from_account, to_account, json.dumps(trade_data)))
            return cur.lastrowid
    
    def get_pending_trades(self, account):
        cur = self.conn.cursor()
        cur.execute('''SELECT id, from_account, trade_data FROM trades 
                       WHERE to_account=? AND status='pending' ''', (account,))
        return [(trade_id, from_acc, json.loads(data)) for trade_id, from_acc, data in cur.fetchall()]
    
    def complete_trade(self, trade_id):
        with self.conn:
            self.conn.execute('UPDATE trades SET status=? WHERE id=?', ('completed', trade_id))
