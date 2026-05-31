"""
Database Module - Supports both PostgreSQL (Railway) and SQLite (local)
PostgreSQL is used when DATABASE_URL env var is set (Railway provides this automatically)
"""
import os
import json
import asyncio
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from config import Config

logger = logging.getLogger(__name__)

# Try to import asyncpg for PostgreSQL
try:
    import asyncpg
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# Always import aiosqlite for SQLite fallback
import aiosqlite


class Database:
    """Database manager with PostgreSQL primary and SQLite fallback"""

    def __init__(self, db_path: str = None):
        self.config = Config()
        self.db_path = db_path or self.config.DB_PATH
        self._postgres_pool = None
        self._use_postgres = False
        self._write_queue = asyncio.Queue()
        self._write_task = None

    def _get_database_url(self) -> Optional[str]:
        """Get PostgreSQL URL from Railway or other providers"""
        # Railway provides DATABASE_URL automatically when you add PostgreSQL
        url = os.getenv('DATABASE_URL')
        if url:
            # asyncpg uses postgresql:// not postgres://
            url = url.replace('postgres://', 'postgresql://')
            url = url.replace('postgresql://', 'postgresql://')
        return url

    async def initialize(self):
        """Initialize database - PostgreSQL preferred, SQLite fallback"""
        pg_url = self._get_database_url()

        if pg_url and POSTGRES_AVAILABLE:
            try:
                await self._init_postgres(pg_url)
                self._use_postgres = True
                logger.info("Using PostgreSQL database - data is persistent!")
                return
            except Exception as e:
                logger.warning(f"Failed to connect to PostgreSQL: {e}")
                logger.warning("Falling back to SQLite...")

        # Fallback to SQLite
        await self._init_sqlite()
        logger.info("Using SQLite database (local only - data may be lost on redeploy)")
        logger.warning("For persistent data on Railway, add PostgreSQL from Railway dashboard!")

    # ============ PostgreSQL Implementation ============

    async def _init_postgres(self, url: str):
        """Initialize PostgreSQL database"""
        self._postgres_pool = await asyncpg.create_pool(url, min_size=2, max_size=10)

        async with self._postgres_pool.acquire() as conn:
            # Create all tables
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    phone TEXT UNIQUE NOT NULL,
                    api_id INTEGER NOT NULL,
                    api_hash TEXT NOT NULL,
                    session_string TEXT,
                    target_group_id BIGINT DEFAULT 0,
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

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id SERIAL PRIMARY KEY,
                    keyword TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    id SERIAL PRIMARY KEY,
                    trigger TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_replies (
                    id SERIAL PRIMARY KEY,
                    message TEXT UNIQUE NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_phrases (
                    id SERIAL PRIMARY KEY,
                    phrase TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    reason TEXT,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    auto_blocked INTEGER DEFAULT 0
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups_table (
                    id SERIAL PRIMARY KEY,
                    group_id BIGINT UNIQUE NOT NULL,
                    title TEXT,
                    link TEXT,
                    is_fallback INTEGER DEFAULT 0,
                    is_command_group INTEGER DEFAULT 0,
                    is_excluded INTEGER DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS joined_groups (
                    id SERIAL PRIMARY KEY,
                    account_phone TEXT NOT NULL,
                    group_id BIGINT NOT NULL,
                    group_title TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    UNIQUE(account_phone, group_id)
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reply_log (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    account_phone TEXT,
                    message_id BIGINT,
                    chat_id BIGINT,
                    chat_title TEXT,
                    keyword TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS forward_log (
                    id SERIAL PRIMARY KEY,
                    from_chat_id BIGINT,
                    from_message_id BIGINT,
                    to_chat_id BIGINT,
                    account_phone TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reply_log_user ON reply_log(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reply_log_date ON reply_log(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts(phone)")

        await self._seed_defaults_postgres()
        self._write_task = asyncio.create_task(self._process_writes_pg())

    async def _seed_defaults_postgres(self):
        """Seed default data in PostgreSQL"""
        async with self._postgres_pool.acquire() as conn:
            # Seed keywords
            count = await conn.fetchval("SELECT COUNT(*) FROM keywords")
            if count == 0:
                for kw in Config.DEFAULT_KEYWORDS:
                    await conn.execute("INSERT INTO keywords (keyword) VALUES ($1) ON CONFLICT DO NOTHING", kw)

            # Seed triggers
            count = await conn.fetchval("SELECT COUNT(*) FROM triggers")
            if count == 0:
                for tr in Config.DEFAULT_TRIGGERS:
                    await conn.execute("INSERT INTO triggers (trigger) VALUES ($1) ON CONFLICT DO NOTHING", tr)

            # Seed auto-replies
            count = await conn.fetchval("SELECT COUNT(*) FROM auto_replies")
            if count == 0:
                for msg in Config.DEFAULT_AUTO_REPLIES:
                    await conn.execute("INSERT INTO auto_replies (message) VALUES ($1) ON CONFLICT DO NOTHING", msg)

    async def _process_writes_pg(self):
        """Background write processor for PostgreSQL"""
        while True:
            try:
                batch = []
                start = time.time()
                while time.time() - start < 0.1:
                    try:
                        item = self._write_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                if batch and self._postgres_pool:
                    async with self._postgres_pool.acquire() as conn:
                        for query, params in batch:
                            try:
                                await conn.execute(query, *params)
                            except Exception:
                                pass
            except Exception:
                pass
            await asyncio.sleep(0.05)

    # ============ SQLite Implementation ============

    async def _init_sqlite(self):
        """Initialize SQLite database"""
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.', exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

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

            await db.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS auto_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT UNIQUE NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_phrases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phrase TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

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

            # Use 'groups_table' name to avoid SQL keyword conflict
            await db.execute("""
                CREATE TABLE IF NOT EXISTS groups_table (
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

            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("CREATE INDEX IF NOT EXISTS idx_reply_log_user ON reply_log(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts(phone)")
            await db.commit()

        await self._seed_defaults_sqlite()
        self._write_task = asyncio.create_task(self._process_writes_sqlite())

    async def _seed_defaults_sqlite(self):
        """Seed default data in SQLite"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM keywords")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for kw in Config.DEFAULT_KEYWORDS:
                    await db.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (kw,))

            cursor = await db.execute("SELECT COUNT(*) FROM triggers")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for tr in Config.DEFAULT_TRIGGERS:
                    await db.execute("INSERT OR IGNORE INTO triggers (trigger) VALUES (?)", (tr,))

            cursor = await db.execute("SELECT COUNT(*) FROM auto_replies")
            count = (await cursor.fetchone())[0]
            if count == 0:
                for msg in Config.DEFAULT_AUTO_REPLIES:
                    await db.execute("INSERT OR IGNORE INTO auto_replies (message) VALUES (?)", (msg,))

            await db.commit()

    async def _process_writes_sqlite(self):
        """Background write processor for SQLite"""
        while True:
            try:
                batch = []
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
            except Exception:
                pass
            await asyncio.sleep(0.05)

    # ============ Unified Public Methods ============

    async def add_account(self, phone: str, api_id: int, api_hash: str,
                         target_group_id: int = 0, mode: str = 'both',
                         session_string: str = None) -> bool:
        """Add new account"""
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO accounts (phone, api_id, api_hash, target_group_id, mode, session_string)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (phone) DO UPDATE SET
                            api_id = EXCLUDED.api_id,
                            api_hash = EXCLUDED.api_hash,
                            target_group_id = EXCLUDED.target_group_id,
                            mode = EXCLUDED.mode,
                            session_string = COALESCE(EXCLUDED.session_string, accounts.session_string),
                            updated_at = CURRENT_TIMESTAMP
                    """, phone, api_id, api_hash, target_group_id, mode, session_string)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT OR REPLACE INTO accounts
                        (phone, api_id, api_hash, target_group_id, mode, session_string)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (phone, api_id, api_hash, target_group_id, mode, session_string))
                    await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding account: {e}")
            return False

    async def get_account(self, phone: str) -> Optional[Dict]:
        """Get account by phone"""
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT * FROM accounts WHERE phone = $1", phone)
                    return dict(row) if row else None
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute("SELECT * FROM accounts WHERE phone = ?", (phone,))
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting account: {e}")
            return None

    async def get_all_accounts(self) -> List[Dict]:
        """Get all accounts"""
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT * FROM accounts ORDER BY created_at DESC")
                    return [dict(row) for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute("SELECT * FROM accounts ORDER BY created_at DESC")
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error listing accounts: {e}")
            return []

    async def get_enabled_accounts(self) -> List[Dict]:
        """Get only enabled accounts"""
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT * FROM accounts WHERE enabled = 1")
                    return [dict(row) for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute("SELECT * FROM accounts WHERE enabled = 1")
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception:
            return []

    async def update_account(self, phone: str, **kwargs) -> bool:
        """Update account fields"""
        try:
            allowed_fields = {'target_group_id', 'mode', 'enabled', 'is_active',
                            'is_command_bot', 'session_string', 'last_error',
                            'forward_count', 'reply_count', 'error_count'}
            updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
            if not updates:
                return False

            if self._use_postgres:
                set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
                values = list(updates.values()) + [phone]
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute(
                        f"UPDATE accounts SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE phone = $1",
                        *values
                    )
            else:
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                values = list(updates.values()) + [phone]
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        f"UPDATE accounts SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE phone = ?",
                        values
                    )
                    await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating account: {e}")
            return False

    async def delete_account(self, phone: str) -> bool:
        """Delete account"""
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM accounts WHERE phone = $1", phone)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
                    await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting account: {e}")
            return False

    async def increment_account_stat(self, phone: str, stat_type: str):
        """Increment account statistics"""
        if stat_type not in ('forward_count', 'reply_count', 'error_count'):
            return
        query = f"UPDATE accounts SET {stat_type} = {stat_type} + 1 WHERE phone = ?"
        await self._write_queue.put((query, (phone,)))

    # ===== Keywords =====

    async def get_keywords(self) -> List[str]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT keyword FROM keywords ORDER BY id")
                    return [row['keyword'] for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("SELECT keyword FROM keywords ORDER BY id")
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception:
            return []

    async def add_keyword(self, keyword: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("INSERT INTO keywords (keyword) VALUES ($1) ON CONFLICT DO NOTHING", keyword)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
                    await db.commit()
            return True
        except Exception:
            return False

    async def delete_keyword(self, keyword: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM keywords WHERE keyword = $1", keyword)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
                    await db.commit()
            return True
        except Exception:
            return False

    # ===== Triggers =====

    async def get_triggers(self) -> List[str]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT trigger FROM triggers ORDER BY id")
                    return [row['trigger'] for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("SELECT trigger FROM triggers ORDER BY id")
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception:
            return []

    async def add_trigger(self, trigger: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("INSERT INTO triggers (trigger) VALUES ($1) ON CONFLICT DO NOTHING", trigger)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT OR IGNORE INTO triggers (trigger) VALUES (?)", (trigger,))
                    await db.commit()
            return True
        except Exception:
            return False

    async def delete_trigger(self, trigger: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM triggers WHERE trigger = $1", trigger)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM triggers WHERE trigger = ?", (trigger,))
                    await db.commit()
            return True
        except Exception:
            return False

    # ===== Auto-Replies =====

    async def get_auto_replies(self) -> List[str]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT message FROM auto_replies ORDER BY id")
                    return [row['message'] for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("SELECT message FROM auto_replies ORDER BY id")
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception:
            return []

    async def add_auto_reply(self, message: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("INSERT INTO auto_replies (message) VALUES ($1) ON CONFLICT DO NOTHING", message)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT OR IGNORE INTO auto_replies (message) VALUES (?)", (message,))
                    await db.commit()
            return True
        except Exception:
            return False

    async def delete_auto_reply(self, message: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM auto_replies WHERE message = $1", message)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM auto_replies WHERE message = ?", (message,))
                    await db.commit()
            return True
        except Exception:
            return False

    # ===== Blocked Phrases =====

    async def get_blocked_phrases(self) -> List[str]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT phrase FROM blocked_phrases ORDER BY id")
                    return [row['phrase'] for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("SELECT phrase FROM blocked_phrases ORDER BY id")
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception:
            return []

    async def add_blocked_phrase(self, phrase: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("INSERT INTO blocked_phrases (phrase) VALUES ($1) ON CONFLICT DO NOTHING", phrase)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT OR IGNORE INTO blocked_phrases (phrase) VALUES (?)", (phrase,))
                    await db.commit()
            return True
        except Exception:
            return False

    async def delete_blocked_phrase(self, phrase: str) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM blocked_phrases WHERE phrase = $1", phrase)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM blocked_phrases WHERE phrase = ?", (phrase,))
                    await db.commit()
            return True
        except Exception:
            return False

    # ===== Blocked Users =====

    async def get_blocked_users(self) -> Dict[int, Dict]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT * FROM blocked_users")
                    return {row['user_id']: dict(row) for row in rows}
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute("SELECT * FROM blocked_users")
                    rows = await cursor.fetchall()
                    return {row['user_id']: dict(row) for row in rows}
        except Exception:
            return {}

    async def block_user(self, user_id: int, username: str = None,
                        display_name: str = None, reason: str = None,
                        auto_blocked: bool = False) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO blocked_users (user_id, username, display_name, reason, auto_blocked)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (user_id) DO UPDATE SET
                            username = EXCLUDED.username,
                            display_name = EXCLUDED.display_name,
                            reason = EXCLUDED.reason,
                            auto_blocked = EXCLUDED.auto_blocked,
                            blocked_at = CURRENT_TIMESTAMP
                    """, user_id, username, display_name, reason, int(auto_blocked))
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT OR REPLACE INTO blocked_users (user_id, username, display_name, reason, auto_blocked)
                        VALUES (?, ?, ?, ?, ?)
                    """, (user_id, username or "", display_name or "", reason or "", int(auto_blocked)))
                    await db.commit()
            return True
        except Exception:
            return False

    async def unblock_user(self, user_id: int) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("DELETE FROM blocked_users WHERE user_id = $1", user_id)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
                    await db.commit()
            return True
        except Exception:
            return False

    # ===== Reply Count =====

    async def get_reply_count(self, user_id: int, hours: int = 24) -> int:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    return await conn.fetchval(
                        "SELECT COUNT(DISTINCT message_id) FROM reply_log WHERE user_id = $1 AND created_at > NOW() - INTERVAL '{} hours'".format(hours),
                        user_id
                    ) or 0
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute(
                        "SELECT COUNT(DISTINCT message_id) FROM reply_log WHERE user_id = ? AND created_at > datetime('now', '-{} hours')".format(hours),
                        (user_id,)
                    )
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception:
            return 0

    async def log_reply(self, user_id: int, username: str, display_name: str,
                       account_phone: str, message_id: int, chat_id: int,
                       chat_title: str, keyword: str):
        if self._use_postgres:
            query = "INSERT INTO reply_log (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
            await self._write_queue.put((query, (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword)))
        else:
            query = "INSERT INTO reply_log (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            await self._write_queue.put((query, (user_id, username, display_name, account_phone, message_id, chat_id, chat_title, keyword)))

    async def log_forward(self, from_chat_id: int, from_message_id: int,
                         to_chat_id: int, account_phone: str, status: str = 'success'):
        if self._use_postgres:
            query = "INSERT INTO forward_log (from_chat_id, from_message_id, to_chat_id, account_phone, status) VALUES ($1, $2, $3, $4, $5)"
            await self._write_queue.put((query, (from_chat_id, from_message_id, to_chat_id, account_phone, status)))
        else:
            query = "INSERT INTO forward_log (from_chat_id, from_message_id, to_chat_id, account_phone, status) VALUES (?, ?, ?, ?, ?)"
            await self._write_queue.put((query, (from_chat_id, from_message_id, to_chat_id, account_phone, status)))

    # ===== Groups =====

    async def add_group(self, group_id: int, title: str = None, link: str = None,
                       is_fallback: bool = False, is_command_group: bool = False,
                       is_excluded: bool = False) -> bool:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO groups_table (group_id, title, link, is_fallback, is_command_group, is_excluded)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (group_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            link = EXCLUDED.link,
                            is_fallback = EXCLUDED.is_fallback,
                            is_command_group = EXCLUDED.is_command_group,
                            is_excluded = EXCLUDED.is_excluded
                    """, group_id, title, link, int(is_fallback), int(is_command_group), int(is_excluded))
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT OR REPLACE INTO groups_table (group_id, title, link, is_fallback, is_command_group, is_excluded)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (group_id, title, link, int(is_fallback), int(is_command_group), int(is_excluded)))
                    await db.commit()
            return True
        except Exception:
            return False

    async def get_groups(self, excluded: bool = False) -> List[Dict]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    if excluded:
                        rows = await conn.fetch("SELECT * FROM groups_table WHERE is_excluded = 1")
                    else:
                        rows = await conn.fetch("SELECT * FROM groups_table WHERE is_excluded = 0")
                    return [dict(row) for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    if excluded:
                        cursor = await db.execute("SELECT * FROM groups_table WHERE is_excluded = 1")
                    else:
                        cursor = await db.execute("SELECT * FROM groups_table WHERE is_excluded = 0")
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception:
            return []

    # ===== Settings =====

    async def get_setting(self, key: str) -> Optional[str]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
                    return row['value'] if row else None
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception:
            return None

    async def set_setting(self, key: str, value: str):
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO settings (key, value) VALUES ($1, $2)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                    """, key, value)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
                    await db.commit()
        except Exception:
            pass

    # ===== Joined Groups =====

    async def add_joined_group(self, account_phone: str, group_id: int, group_title: str = None):
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO joined_groups (account_phone, group_id, group_title, is_active)
                        VALUES ($1, $2, $3, 1)
                        ON CONFLICT (account_phone, group_id) DO UPDATE SET is_active = 1, group_title = EXCLUDED.group_title
                    """, account_phone, group_id, group_title)
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT OR REPLACE INTO joined_groups (account_phone, group_id, group_title, is_active)
                        VALUES (?, ?, ?, 1)
                    """, (account_phone, group_id, group_title))
                    await db.commit()
        except Exception:
            pass

    # ===== Statistics =====

    async def get_stats(self) -> Dict[str, int]:
        try:
            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    stats = {}
                    stats['total_accounts'] = await conn.fetchval("SELECT COUNT(*) FROM accounts") or 0
                    stats['active_accounts'] = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE is_active = 1") or 0
                    stats['enabled_accounts'] = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE enabled = 1") or 0
                    stats['keywords_count'] = await conn.fetchval("SELECT COUNT(*) FROM keywords") or 0
                    stats['triggers_count'] = await conn.fetchval("SELECT COUNT(*) FROM triggers") or 0
                    stats['auto_replies_count'] = await conn.fetchval("SELECT COUNT(*) FROM auto_replies") or 0
                    stats['blocked_phrases_count'] = await conn.fetchval("SELECT COUNT(*) FROM blocked_phrases") or 0
                    stats['blocked_users_count'] = await conn.fetchval("SELECT COUNT(*) FROM blocked_users") or 0
                    stats['groups_count'] = await conn.fetchval("SELECT COUNT(*) FROM groups_table") or 0
                    stats['today_replies'] = await conn.fetchval("SELECT COUNT(*) FROM reply_log WHERE created_at > NOW() - INTERVAL '1 day'") or 0
                    stats['today_forwards'] = await conn.fetchval("SELECT COUNT(*) FROM forward_log WHERE created_at > NOW() - INTERVAL '1 day'") or 0
                    stats['total_forwards'] = await conn.fetchval("SELECT COALESCE(SUM(forward_count), 0) FROM accounts") or 0
                    stats['total_replies'] = await conn.fetchval("SELECT COALESCE(SUM(reply_count), 0) FROM accounts") or 0
                    return stats
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    stats = {}
                    stats['total_accounts'] = (await db.execute("SELECT COUNT(*) FROM accounts")).fetchone()[0]
                    stats['active_accounts'] = (await db.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1")).fetchone()[0]
                    stats['enabled_accounts'] = (await db.execute("SELECT COUNT(*) FROM accounts WHERE enabled = 1")).fetchone()[0]
                    stats['keywords_count'] = (await db.execute("SELECT COUNT(*) FROM keywords")).fetchone()[0]
                    stats['triggers_count'] = (await db.execute("SELECT COUNT(*) FROM triggers")).fetchone()[0]
                    stats['auto_replies_count'] = (await db.execute("SELECT COUNT(*) FROM auto_replies")).fetchone()[0]
                    stats['blocked_phrases_count'] = (await db.execute("SELECT COUNT(*) FROM blocked_phrases")).fetchone()[0]
                    stats['blocked_users_count'] = (await db.execute("SELECT COUNT(*) FROM blocked_users")).fetchone()[0]
                    stats['groups_count'] = (await db.execute("SELECT COUNT(*) FROM groups_table")).fetchone()[0]
                    stats['today_replies'] = (await db.execute("SELECT COUNT(*) FROM reply_log WHERE created_at > datetime('now', '-1 day')")).fetchone()[0]
                    stats['today_forwards'] = (await db.execute("SELECT COUNT(*) FROM forward_log WHERE created_at > datetime('now', '-1 day')")).fetchone()[0]
                    stats['total_forwards'] = (await db.execute("SELECT COALESCE(SUM(forward_count), 0) FROM accounts")).fetchone()[0]
                    stats['total_replies'] = (await db.execute("SELECT COALESCE(SUM(reply_count), 0) FROM accounts")).fetchone()[0]
                    return stats
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}

    # ===== Backup & Restore =====

    async def export_data(self) -> Dict:
        try:
            data = {}
            tables = ['accounts', 'keywords', 'triggers', 'auto_replies',
                     'blocked_phrases', 'blocked_users', 'groups_table', 'settings']

            if self._use_postgres:
                async with self._postgres_pool.acquire() as conn:
                    for table in tables:
                        rows = await conn.fetch(f"SELECT * FROM {table}")
                        data[table] = [dict(row) for row in rows]
            else:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    for table in tables:
                        cursor = await db.execute(f"SELECT * FROM {table}")
                        rows = await cursor.fetchall()
                        data[table] = [dict(row) for row in rows]

            return data
        except Exception as e:
            logger.error(f"Export error: {e}")
            return {}

    async def import_data(self, data: Dict) -> bool:
        try:
            for table, rows in data.items():
                for row in rows:
                    if table == 'accounts':
                        await self.add_account(
                            phone=row.get('phone'),
                            api_id=row.get('api_id'),
                            api_hash=row.get('api_hash'),
                            target_group_id=row.get('target_group_id', 0),
                            mode=row.get('mode', 'both'),
                            session_string=row.get('session_string')
                        )
                    elif table == 'keywords':
                        await self.add_keyword(row.get('keyword'))
                    elif table == 'triggers':
                        await self.add_trigger(row.get('trigger'))
                    elif table == 'auto_replies':
                        await self.add_auto_reply(row.get('message'))
                    elif table == 'blocked_phrases':
                        await self.add_blocked_phrase(row.get('phrase'))
                    elif table == 'blocked_users':
                        await self.block_user(
                            row.get('user_id'),
                            row.get('username'),
                            row.get('display_name'),
                            row.get('reason'),
                            row.get('auto_blocked', 0)
                        )
                    elif table == 'groups_table':
                        await self.add_group(
                            row.get('group_id'),
                            row.get('title'),
                            row.get('link'),
                            row.get('is_fallback', 0),
                            row.get('is_command_group', 0),
                            row.get('is_excluded', 0)
                        )
                    elif table == 'settings':
                        await self.set_setting(row.get('key'), row.get('value'))
            return True
        except Exception as e:
            logger.error(f"Import error: {e}")
            return False

    async def close(self):
        """Close database connection"""
        if self._write_task:
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
        if self._postgres_pool:
            await self._postgres_pool.close()
