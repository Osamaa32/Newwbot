"""
Database Module - SQLite with async support via aiosqlite
Fast, lightweight, and efficient for high-throughput operations
"""
import aiosqlite
import json
import asyncio
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from config import Config
import os


class Database:
    """Async SQLite database manager with connection pooling"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DB_PATH
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._write_queue = asyncio.Queue()
        self._write_task = None
        
    async def initialize(self):
        """Initialize database with all tables"""
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.', exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Accounts table - stores Telegram account sessions
            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE NOT NULL,
                    api_id INTEGER NOT NULL,
                    api_hash TEXT NOT NULL,
                    session_string TEXT,
                    target_group_id INTEGER DEFAULT 0,
                    mode TEXT DEFAULT 'both',
                    enabled INTEGER DEFAULT 1,
                    is_active INTEGER DEFAULT 0,
                    is_command_bot INTEGER DEFAULT 0,
                    forward_count INTEGER DEFAULT 0,
                    reply_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    last_active TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Keywords table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Triggers table (for auto-reply matching)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Auto-reply messages table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS auto_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT UNIQUE NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Blocked phrases table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_phrases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phrase TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Blocked users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    reason TEXT,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    auto_blocked INTEGER DEFAULT 0
                )
            """)
            
            # Groups table (target groups)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER UNIQUE NOT NULL,
                    title TEXT,
                    link TEXT,
                    is_fallback INTEGER DEFAULT 0,
                    is_command_group INTEGER DEFAULT 0,
                    is_excluded INTEGER DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Joined groups table (groups the bot accounts have joined)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS joined_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_phone TEXT NOT NULL,
                    group_id INTEGER NOT NULL,
                    group_title TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    UNIQUE(account_phone, group_id)
                )
            """)
            
            # Auto-reply log table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reply_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    account_phone TEXT,
                    message_id INTEGER,
                    chat_id INTEGER,
                    chat_title TEXT,
                    keyword TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Forward log table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS forward_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_chat_id INTEGER,
                    from_message_id INTEGER,
                    to_chat_id INTEGER,
                    account_phone TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Settings table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Stats table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_type TEXT NOT NULL,
                    stat_value INTEGER DEFAULT 0,
                    stat_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Indexes for performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_reply_log_user ON reply_log(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_reply_log_date ON reply_log(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_forward_log_date ON forward_log(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_joined_groups_phone ON joined_groups(account_phone)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts(phone)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_enabled ON accounts(enabled)")
            
            await db.commit()
            
        # Seed default data if empty
        await self._seed_defaults()
        
        # Start background write processor
        self._write_task = asyncio.create_task(self._process_writes())
        
    async def _seed_defaults(self):
        """Seed default data if tables are empty"""
        async with aiosqlite.connect(self.db_path) as db:
            # Seed keywords
            cursor = await db.execute("SELECT COUNT(*) FROM keywords")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for kw in Config.DEFAULT_KEYWORDS:
                    await db.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw,))
            
            # Seed triggers
            cursor = await db.execute("SELECT COUNT(*) FROM triggers")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for tr in Config.DEFAULT_TRIGGERS:
                    await db.execute("INSERT OR IGNORE INTO triggers (trigger) VALUES (?)", (tr,))
            
            # Seed auto-replies
            cursor = await db.execute("SELECT COUNT(*) FROM auto_replies")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for msg in Config.DEFAULT_AUTO_REPLIES:
                    await db.execute("INSERT OR IGNORE INTO auto_replies (message) VALUES (?)", (msg,))
            
            await db.commit()
    
    async def _process_writes(self):
        """Background task to batch process writes"""
        while True:
            try:
                batch = []
                # Collect writes for 100ms or until queue is empty
                start = time.time()
                while time.time() - start < 0.1:
                    try:
                        item = self._write_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break
                
                if batch:
                    async with aiosqlite.connect(self.db_path) as db:
                        for query, params in batch:
                            await db.execute(query, params)
                        await db.commit()
                        
            except Exception as e:
                print(f"Write processor error: {e}")
                
            await asyncio.sleep(0.05)
    
    # ===== Account Management =====
    
    async def add_account(self, phone: str, api_id: int, api_hash: str, 
                         target_group_id: int = 0, mode: str = 'both',
                         session_string: str = None) -> bool:
        """Add new account"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO accounts 
                    (phone, api_id, api_hash, target_group_id, mode, session_string)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (phone, api_id, api_hash, target_group_id, mode, session_string))
                await db.commit()
            return True
        except Exception as e:
            print(f"Error adding account: {e}")
            return False
    
    async def get_account(self, phone: str) -> Optional[Dict]:
        """Get account by phone"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts WHERE phone = ?", (phone,))
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_all_accounts(self) -> List[Dict]:
        """Get all accounts"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_enabled_accounts(self) -> List[Dict]:
        """Get only enabled accounts"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts WHERE enabled = 1")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def update_account(self, phone: str, **kwargs) -> bool:
        """Update account fields"""
        try:
            allowed_fields = {'target_group_id', 'mode', 'enabled', 'is_active', 
                            'is_command_bot', 'session_string', 'last_error', 
                            'forward_count', 'reply_count', 'error_count'}
            updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
            if not updates:
                return False
                
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [phone]
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f"""
                    UPDATE accounts SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                    WHERE phone = ?
                """, values)
                await db.commit()
            return True
        except Exception as e:
            print(f"Error updating account: {e}")
            return False
    
    async def delete_account(self, phone: str) -> bool:
        """Delete account"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
                await db.commit()
            return True
        except Exception as e:
            print(f"Error deleting account: {e}")
            return False
    
    async def increment_account_stat(self, phone: str, stat_type: str):
        """Increment account statistics (fire and forget)"""
        if stat_type not in ('forward_count', 'reply_count', 'error_count'):
            return
        query = f"UPDATE accounts SET {stat_type} = {stat_type} + 1 WHERE phone = ?"
        await self._write_queue.put((query, (phone,)))
    
    # ===== Keywords Management =====
    
    async def get_keywords(self) -> List[str]:
        """Get all keywords"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT keyword FROM keywords ORDER BY id")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    async def add_keyword(self, keyword: str) -> bool:
        """Add keyword"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
                await db.commit()
            return True
        except Exception:
            return False
    
    async def delete_keyword(self, keyword: str) -> bool:
        """Delete keyword"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
                await db.commit()
            return True
        except Exception:
            return False
    
    # ===== Triggers Management =====
    
    async def get_triggers(self) -> List[str]:
        """Get all trigger phrases"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT trigger FROM triggers ORDER BY id")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    async def add_trigger(self, trigger: str) -> bool:
        """Add trigger phrase"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO triggers (trigger) VALUES (?)", (trigger,))
                await db.commit()
            return True
        except Exception:
            return False
    
    async def delete_trigger(self, trigger: str) -> bool:
        """Delete trigger phrase"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM triggers WHERE trigger = ?", (trigger,))
                await db.commit()
            return True
        except Exception:
            return False
    
    # ===== Auto-Replies Management =====
    
    async def get_auto_replies(self) -> List[str]:
        """Get all auto-reply messages"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT message FROM auto_replies ORDER BY id")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    async def add_auto_reply(self, message: str) -> bool:
        """Add auto-reply message"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO auto_replies (message) VALUES (?)", (message,))
                await db.commit()
            return True
        except Exception:
            return False
    
    async def delete_auto_reply(self, message: str) -> bool:
        """Delete auto-reply message"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM auto_replies WHERE message = ?", (message,))
                await db.commit()
            return True
        except Exception:
            return False
    
    # ===== Blocked Phrases =====
    
    async def get_blocked_phrases(self) -> List[str]:
        """Get blocked phrases"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT phrase FROM blocked_phrases ORDER BY id")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    async def add_blocked_phrase(self, phrase: str) -> bool:
        """Add blocked phrase"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO blocked_phrases (phrase) VALUES (?)", (phrase,))
                await db.commit()
            return True
        except Exception:
            return False
    
    async def delete_blocked_phrase(self, phrase: str) -> bool:
        """Delete blocked phrase"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM blocked_phrases WHERE phrase = ?", (phrase,))
                await db.commit()
            return True
        except Exception:
            return False
    
    # ===== Blocked Users =====
    
    async def get_blocked_users(self) -> Dict[int, Dict]:
        """Get all blocked users as dict"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM blocked_users")
            rows = await cursor.fetchall()
            return {row['user_id']: dict(row) for row in rows}
    
    async def block_user(self, user_id: int, username: str = None, 
                        display_name: str = None, reason: str = None,
                        auto_blocked: bool = False) -> bool:
        """Block a user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO blocked_users 
                    (user_id, username, display_name, reason, auto_blocked)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, display_name, reason, int(auto_blocked)))
                await db.commit()
            return True
        except Exception as e:
            print(f"Error blocking user: {e}")
            return False
    
    async def unblock_user(self, user_id: int) -> bool:
        """Unblock a user"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
                await db.commit()
            return True
        except Exception:
            return False
    
    # ===== Reply Count for Auto-Blocking =====
    
    async def get_reply_count(self, user_id: int, hours: int = 24) -> int:
        """Count auto-replies sent to user in last N hours"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT message_id) as count FROM reply_log WHERE user_id = ? AND created_at > datetime('now', '-{} hours')".format(hours),
                (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def log_reply(self, user_id: int, username: str, display_name: str,
                       account_phone: str, message_id: int, chat_id: int,
                       chat_title: str, keyword: str):
        """Log auto-reply (fire and forget)"""
        query = """
            INSERT INTO reply_log 
            (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self._write_queue.put((query, 
            (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword)))
    
    async def log_forward(self, from_chat_id: int, from_message_id: int,
                         to_chat_id: int, account_phone: str, status: str = 'success'):
        """Log forward (fire and forget)"""
        query = """
            INSERT INTO forward_log 
            (from_chat_id, from_message_id, to_chat_id, account_phone, status)
            VALUES (?, ?, ?, ?, ?)
        """
        await self._write_queue.put((query, 
            (from_chat_id, from_message_id, to_chat_id, account_phone, status)))
    
    # ===== Groups Management =====
    
    async def add_group(self, group_id: int, title: str = None, link: str = None,
                       is_fallback: bool = False, is_command_group: bool = False,
                       is_excluded: bool = False) -> bool:
        """Add a group"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO groups 
                    (group_id, title, link, is_fallback, is_command_group, is_excluded)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (group_id, title, link, int(is_fallback), int(is_command_group), int(is_excluded)))
                await db.commit()
            return True
        except Exception:
            return False
    
    async def get_groups(self, excluded: bool = False) -> List[Dict]:
        """Get groups"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if excluded:
                cursor = await db.execute("SELECT * FROM groups WHERE is_excluded = 1")
            else:
                cursor = await db.execute("SELECT * FROM groups WHERE is_excluded = 0")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_fallback_group(self) -> Optional[int]:
        """Get fallback group ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT group_id FROM groups WHERE is_fallback = 1 LIMIT 1")
            row = await cursor.fetchone()
            return row[0] if row else None
    
    # ===== Settings =====
    
    async def get_setting(self, key: str) -> Optional[str]:
        """Get setting value"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row[0] if row else None
    
    async def set_setting(self, key: str, value: str):
        """Set setting value"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
            """, (key, value))
            await db.commit()
    
    # ===== Statistics =====
    
    async def get_stats(self) -> Dict[str, int]:
        """Get bot statistics"""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}
            
            # Account counts
            cursor = await db.execute("SELECT COUNT(*) FROM accounts")
            stats['total_accounts'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1")
            stats['active_accounts'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE enabled = 1")
            stats['enabled_accounts'] = (await cursor.fetchone())[0]
            
            # Keywords, triggers, auto-replies
            cursor = await db.execute("SELECT COUNT(*) FROM keywords")
            stats['keywords_count'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM triggers")
            stats['triggers_count'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM auto_replies")
            stats['auto_replies_count'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM blocked_phrases")
            stats['blocked_phrases_count'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM blocked_users")
            stats['blocked_users_count'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM groups")
            stats['groups_count'] = (await cursor.fetchone())[0]
            
            # Today's activity
            cursor = await db.execute("""
                SELECT COUNT(*) FROM reply_log 
                WHERE created_at > datetime('now', '-1 day')
            """)
            stats['today_replies'] = (await cursor.fetchone())[0]
            
            cursor = await db.execute("""
                SELECT COUNT(*) FROM forward_log 
                WHERE created_at > datetime('now', '-1 day')
            """)
            stats['today_forwards'] = (await cursor.fetchone())[0]
            
            # Total counts
            cursor = await db.execute("SELECT SUM(forward_count) FROM accounts")
            result = await cursor.fetchone()
            stats['total_forwards'] = result[0] or 0
            
            cursor = await db.execute("SELECT SUM(reply_count) FROM accounts")
            result = await cursor.fetchone()
            stats['total_replies'] = result[0] or 0
            
            return stats
    
    async def get_recent_replies(self, limit: int = 20) -> List[Dict]:
        """Get recent auto-replies"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM reply_log ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_recent_forwards(self, limit: int = 20) -> List[Dict]:
        """Get recent forwards"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM forward_log ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ===== Joined Groups =====
    
    async def add_joined_group(self, account_phone: str, group_id: int, group_title: str = None):
        """Record joined group"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO joined_groups 
                    (account_phone, group_id, group_title, is_active)
                    VALUES (?, ?, ?, 1)
                """, (account_phone, group_id, group_title))
                await db.commit()
        except Exception:
            pass
    
    async def get_joined_groups(self, account_phone: str) -> List[Dict]:
        """Get groups joined by account"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM joined_groups WHERE account_phone = ? AND is_active = 1
            """, (account_phone,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ===== Backup & Restore =====
    
    async def export_data(self) -> Dict:
        """Export all data for backup"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            data = {}
            
            for table in ['accounts', 'keywords', 'triggers', 'auto_replies', 
                         'blocked_phrases', 'blocked_users', 'groups', 'settings']:
                cursor = await db.execute(f"SELECT * FROM {table}")
                rows = await cursor.fetchall()
                data[table] = [dict(row) for row in rows]
            
            return data
    
    async def import_data(self, data: Dict) -> bool:
        """Import data from backup"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                for table, rows in data.items():
                    if table == 'accounts':
                        for row in rows:
                            await db.execute("""
                                INSERT OR REPLACE INTO accounts 
                                (phone, api_id, api_hash, session_string, target_group_id, 
                                 mode, enabled, is_command_bot)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (row.get('phone'), row.get('api_id'), row.get('api_hash'),
                                  row.get('session_string'), row.get('target_group_id'),
                                  row.get('mode', 'both'), row.get('enabled', 1),
                                  row.get('is_command_bot', 0)))
                    elif table == 'keywords':
                        for row in rows:
                            await db.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)",
                                           (row.get('keyword'),))
                    elif table == 'triggers':
                        for row in rows:
                            await db.execute("INSERT OR IGNORE INTO triggers (trigger) VALUES (?)",
                                           (row.get('trigger'),))
                    elif table == 'auto_replies':
                        for row in rows:
                            await db.execute("INSERT OR IGNORE INTO auto_replies (message) VALUES (?)",
                                           (row.get('message'),))
                    elif table == 'blocked_phrases':
                        for row in rows:
                            await db.execute("INSERT OR IGNORE INTO blocked_phrases (phrase) VALUES (?)",
                                           (row.get('phrase'),))
                    elif table == 'blocked_users':
                        for row in rows:
                            await db.execute("""
                                INSERT OR REPLACE INTO blocked_users 
                                (user_id, username, display_name, reason, auto_blocked)
                                VALUES (?, ?, ?, ?, ?)
                            """, (row.get('user_id'), row.get('username'), 
                                  row.get('display_name'), row.get('reason'),
                                  row.get('auto_blocked', 0)))
                    elif table == 'groups':
                        for row in rows:
                            await db.execute("""
                                INSERT OR REPLACE INTO groups 
                                (group_id, title, link, is_fallback, is_command_group, is_excluded)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (row.get('group_id'), row.get('title'), row.get('link'),
                                  row.get('is_fallback', 0), row.get('is_command_group', 0),
                                  row.get('is_excluded', 0)))
                    elif table == 'settings':
                        for row in rows:
                            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                           (row.get('key'), row.get('value')))
                await db.commit()
            return True
        except Exception as e:
            print(f"Import error: {e}")
            return False
    
    async def close(self):
        """Close database connection"""
        if self._write_task:
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
