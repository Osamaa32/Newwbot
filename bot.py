"""
Telegram Bot Manager - Main Module
Multi-account Telegram bot with auto-reply and forward capabilities
Optimized for high-speed processing across thousands of groups
Railway-compatible with PostgreSQL persistent storage

Auth Flow:
  1. send_code: creates client, sends SendCodeRequest, saves session_string + phone_code_hash to DB
  2. sign_in: creates NEW client from session_string, sends SignInRequest with explicit phone_code_hash
  This works even after Railway restart because all state is in the database!
"""
import os
import sys
import asyncio
import json
import logging
import time
import signal
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import deque

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Channel, Chat, User
from telethon.tl.functions.auth import SendCodeRequest, SignInRequest
from telethon.errors import (
    FloodWaitError, UserIsBlockedError, MessageTooLongError,
    AuthKeyDuplicatedError, UserAlreadyParticipantError,
    ChannelInvalidError, ChannelPrivateError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, SessionPasswordNeededError, PasswordHashInvalidError
)

from config import Config
from database import Database
from utils import TextProcessor, Cache, RateLimiter, MetricsCollector, async_retry, format_phone

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# ============== Account Session ==============

class AccountSession:
    """Represents a single Telegram account session"""

    def __init__(self, phone: str, api_id: int, api_hash: str,
                 target_group_id: int, mode: str, db: Database,
                 session_string: str = None):
        self.phone = phone
        self.api_id = api_id
        self.api_hash = api_hash
        self.target_group_id = target_group_id
        self.mode = mode
        self.db = db
        self.session_string = session_string

        self.client: Optional[TelegramClient] = None
        self.is_active = False
        self.is_command_bot = False
        self.me_id: Optional[int] = None
        self.forward_count = 0
        self.reply_count = 0
        self.error_count = 0
        self.last_error: Optional[str] = None
        self.joined_groups: Set[int] = set()

    # ---- Auth State (stored in DB, survives restart) ----

    async def load_auth_state(self) -> Optional[Dict]:
        """Load pending auth state from database (survives restart)"""
        try:
            raw = await self.db.get_setting(f"auth_state_{self.phone}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def save_auth_state(self, state: Dict):
        """Save auth state to database (survives restart)"""
        try:
            await self.db.set_setting(f"auth_state_{self.phone}", json.dumps(state))
        except Exception as e:
            logger.error(f"Failed to save auth state: {e}")

    async def clear_auth_state(self):
        """Clear auth state after successful login"""
        try:
            await self.db.set_setting(f"auth_state_{self.phone}", "")
        except Exception:
            pass

    # ---- Client Lifecycle ----

    def _make_client(self, session_str: str = None) -> TelegramClient:
        """Create TelegramClient from session string"""
        s = session_str or self.session_string
        session = StringSession(s) if s else StringSession()
        return TelegramClient(session, self.api_id, self.api_hash)

    async def create_client(self) -> bool:
        """Connect existing authorized client"""
        try:
            self.client = self._make_client()
            await self.client.connect()

            if not await self.client.is_user_authorized():
                logger.warning(f"Session not authorized for {self.phone}")
                return False

            me = await self.client.get_me()
            self.me_id = me.id
            self.is_active = True

            # Save final session
            self.session_string = self.client.session.save()
            await self.db.update_account(
                self.phone,
                session_string=self.session_string,
                is_active=1,
                last_error=None
            )

            logger.info(f"Account {self.phone} connected (ID: {self.me_id})")
            return True

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to connect {self.phone}: {e}")
            await self.db.update_account(self.phone, last_error=str(e)[:200], is_active=0)
            return False

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.is_active = False
        await self.db.update_account(self.phone, is_active=0)

    # ---- Auth Flow (using RAW API - survives restart) ----

    async def send_code(self) -> Tuple[bool, str, str]:
        """Send verification code using RAW API.
        Returns: (success, phone_code_hash, session_string)"""
        try:
            # Fresh client with empty session
            self.client = self._make_client(None)
            await self.client.connect()

            # Send code using RAW API (not the convenience method)
            result = await self.client(SendCodeRequest(
                phone_number=self.phone,
                api_id=self.api_id,
                api_hash=self.api_hash,
                settings=types.CodeSettings(allow_flashcall=False, current_number=False, allow_app_hash=False)
            ))

            phone_code_hash = result.phone_code_hash
            session_string = self.client.session.save()

            # Save to DB so it survives restart!
            await self.save_auth_state({
                'phone_code_hash': phone_code_hash,
                'session_string': session_string,
                'created_at': time.time()
            })

            logger.info(f"Code sent to {self.phone}, hash stored in DB")
            return True, phone_code_hash, session_string

        except Exception as e:
            logger.error(f"Failed to send code to {self.phone}: {e}")
            return False, str(e), ""

    async def sign_in(self, code: str, phone_code_hash: str = None,
                      session_string: str = None) -> Tuple[bool, str]:
        """Sign in using RAW API with explicit phone_code_hash.
        This recreates client from session_string and passes hash explicitly."""
        try:
            # Load from DB if not provided
            if not phone_code_hash or not session_string:
                state = await self.load_auth_state()
                if not state:
                    return False, "No auth state found. Please start over with /startacc"
                phone_code_hash = state.get('phone_code_hash')
                session_string = state.get('session_string')
                if not phone_code_hash:
                    return False, "Auth state incomplete. Please start over."

            # Recreate client from saved session
            self.client = self._make_client(session_string)
            await self.client.connect()

            # Sign in using RAW API with explicit hash!
            result = await self.client(SignInRequest(
                phone_number=self.phone,
                phone_code_hash=phone_code_hash,
                phone_code=code
            ))

            # Check if user is authorized
            if result.user:
                self.me_id = result.user.id
                self.is_active = True

                # Save final session
                self.session_string = self.client.session.save()
                await self.db.update_account(
                    self.phone,
                    session_string=self.session_string,
                    is_active=1,
                    last_error=None
                )
                await self.clear_auth_state()
                return True, "Successfully signed in"

            return False, "Unknown response from server"

        except SessionPasswordNeededError:
            # Save session for 2FA step
            self.session_string = self.client.session.save()
            await self.save_auth_state({
                'phone_code_hash': phone_code_hash,
                'session_string': self.session_string,
                'needs_2fa': True
            })
            return False, "2FA_REQUIRED"
        except PhoneCodeInvalidError:
            return False, "Invalid code"
        except PhoneCodeExpiredError:
            return False, "Code expired - please start over with /startacc"
        except Exception as e:
            return False, str(e)

    async def sign_in_2fa(self, password: str) -> Tuple[bool, str]:
        """Complete 2FA sign in"""
        try:
            if not self.client:
                # Try to load from saved state
                state = await self.load_auth_state()
                if state and state.get('session_string'):
                    self.client = self._make_client(state['session_string'])
                    await self.client.connect()
                else:
                    return False, "Session lost. Please start over."

            await self.client.sign_in(password=password)

            me = await self.client.get_me()
            self.me_id = me.id
            self.is_active = True

            self.session_string = self.client.session.save()
            await self.db.update_account(
                self.phone,
                session_string=self.session_string,
                is_active=1,
                last_error=None
            )
            await self.clear_auth_state()
            return True, "Successfully signed in with 2FA"

        except PasswordHashInvalidError:
            return False, "Invalid password"
        except Exception as e:
            return False, str(e)

    # ---- Operations ----

    async def join_group(self, group_link: str) -> Tuple[bool, str]:
        try:
            entity = await self.client.get_entity(group_link)
            await self.client(JoinChannelRequest(entity))
            self.joined_groups.add(entity.id)
            await self.db.add_joined_group(self.phone, entity.id, getattr(entity, 'title', ''))
            return True, f"Joined {getattr(entity, 'title', group_link)}"
        except UserAlreadyParticipantError:
            return True, "Already a member"
        except Exception as e:
            return False, str(e)

    async def forward_message(self, from_chat_id: int, message_id: int,
                             target_id: int) -> bool:
        try:
            await self.client.forward_messages(target_id, message_id, from_chat_id)
            self.forward_count += 1
            await self.db.increment_account_stat(self.phone, 'forward_count')
            return True
        except Exception as e:
            self.error_count += 1
            logger.debug(f"Forward error: {e}")
            return False

    async def send_message(self, user_id: int, text: str) -> Optional[Any]:
        try:
            msg = await self.client.send_message(user_id, text)
            self.reply_count += 1
            await self.db.increment_account_stat(self.phone, 'reply_count')
            return msg
        except UserIsBlockedError:
            return None
        except MessageTooLongError:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            last_msg = None
            for part in parts:
                try:
                    last_msg = await self.client.send_message(user_id, part)
                except Exception:
                    break
            return last_msg
        except Exception as e:
            self.error_count += 1
            logger.debug(f"Send error: {e}")
            return None


# ============== Message Dispatcher ==============

class MessageDispatcher:
    """High-speed message dispatcher with parallel processing"""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.accounts: Dict[str, AccountSession] = {}
        self.forward_queue: asyncio.Queue = asyncio.Queue(maxsize=config.QUEUE_SIZE)
        self.reply_queue: asyncio.Queue = asyncio.Queue(maxsize=config.QUEUE_SIZE)
        self._processed_ids: Set[str] = set()
        self._processed_timestamps: deque = deque(maxlen=100000)
        self._forward_workers: List[asyncio.Task] = []
        self._reply_workers: List[asyncio.Task] = []
        self._running = False

        # Runtime data
        self.keywords: List[str] = []
        self.triggers: List[str] = []
        self.auto_replies: List[str] = []
        self.blocked_phrases: List[str] = []
        self.blocked_users: Dict[int, Dict] = {}
        self.excluded_groups: Set[int] = set()

        self._auto_reply_index = 0
        self._keyword_regex = None
        self.metrics = MetricsCollector()
        self.rate_limiter = RateLimiter(max_requests=5, window=3600)
        self._admin_cache = Cache(ttl=300)

    async def initialize(self):
        await self.reload_data()
        self._running = True
        for i in range(self.config.WORKERS):
            self._forward_workers.append(asyncio.create_task(self._forward_worker(i)))
            self._reply_workers.append(asyncio.create_task(self._reply_worker(i)))
        asyncio.create_task(self._cleanup_old_ids())
        logger.info(f"Dispatcher initialized with {self.config.WORKERS} workers")

    async def shutdown(self):
        self._running = False
        for task in self._forward_workers + self._reply_workers:
            task.cancel()
        logger.info("Dispatcher shutdown complete")

    async def reload_data(self):
        self.keywords = await self.db.get_keywords()
        self.triggers = await self.db.get_triggers()
        self.auto_replies = await self.db.get_auto_replies()
        self.blocked_phrases = await self.db.get_blocked_phrases()
        self.blocked_users = await self.db.get_blocked_users()
        self.excluded_groups = {g['group_id'] for g in await self.db.get_groups(excluded=True)}
        self.config.compile_keywords(self.keywords)
        self._keyword_regex = self.config.KEYWORDS_REGEX
        logger.info(f"Loaded {len(self.keywords)} keywords, {len(self.triggers)} triggers, "
                   f"{len(self.auto_replies)} auto-replies, {len(self.blocked_users)} blocked users")

    def register_account(self, account: AccountSession):
        self.accounts[account.phone] = account

    def unregister_account(self, phone: str):
        self.accounts.pop(phone, None)

    async def process_message(self, event: events.NewMessage.Event):
        start_time = time.time()
        try:
            if not self.accounts or not self._running:
                return

            chat_id = event.chat_id
            message_id = event.message.id
            text = event.message.message or ""
            sender_id = event.message.sender_id

            if chat_id in self.excluded_groups:
                return

            sender = event.message.sender
            if sender and getattr(sender, 'bot', False):
                return

            if sender_id in self.blocked_users:
                return

            if TextProcessor.should_skip(text):
                return

            msg_id = f"{chat_id}:{message_id}:{sender_id}"
            if msg_id in self._processed_ids:
                return
            self._processed_ids.add(msg_id)
            self._processed_timestamps.append((msg_id, time.time()))

            keyword_match = TextProcessor.keyword_match(text, self._keyword_regex)
            if not keyword_match:
                return

            self.metrics.increment('messages_matched')

            username = getattr(sender, 'username', '') or '' if sender else ''
            display_name = ''
            if sender:
                display_name = f"{(getattr(sender, 'first_name', '') or '')} {(getattr(sender, 'last_name', '') or '')}".strip()

            chat = event.chat
            chat_title = getattr(chat, 'title', '') or '' if chat else ''

            await self.forward_queue.put({
                'event': event, 'chat_id': chat_id, 'message_id': message_id,
                'text': text, 'sender_id': sender_id, 'username': username,
                'display_name': display_name, 'chat_title': chat_title,
            })

            if TextProcessor.fuzzy_match(text, self.triggers):
                normalized = TextProcessor.normalize(text)
                blocked = any(TextProcessor.normalize(phrase) in normalized for phrase in self.blocked_phrases)
                if not blocked:
                    await self.reply_queue.put({
                        'event': event, 'chat_id': chat_id, 'message_id': message_id,
                        'text': text, 'sender_id': sender_id, 'username': username,
                        'display_name': display_name, 'chat_title': chat_title,
                    })

            elapsed = time.time() - start_time
            self.metrics.start_timer('process')
            self.metrics.end_timer('process')

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _forward_worker(self, worker_id: int):
        logger.info(f"Forward worker {worker_id} started")
        while self._running:
            try:
                data = await asyncio.wait_for(self.forward_queue.get(), timeout=1.0)
                await self._do_forward(data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Forward worker {worker_id} error: {e}")
            finally:
                try:
                    self.forward_queue.task_done()
                except Exception:
                    pass

    async def _reply_worker(self, worker_id: int):
        logger.info(f"Reply worker {worker_id} started")
        while self._running:
            try:
                data = await asyncio.wait_for(self.reply_queue.get(), timeout=1.0)
                await self._do_reply(data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Reply worker {worker_id} error: {e}")
            finally:
                try:
                    self.reply_queue.task_done()
                except Exception:
                    pass

    async def _do_forward(self, data: dict):
        try:
            tasks = []
            for phone, account in self.accounts.items():
                if not account.is_active or account.mode not in ('forward', 'both'):
                    continue
                if account.target_group_id and account.target_group_id != 0:
                    tasks.append(self._forward_single(account, data['chat_id'], data['message_id'], account.target_group_id, data))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                self.metrics.increment('forwards_sent', len(tasks))
        except Exception as e:
            logger.error(f"Forward error: {e}")

    async def _forward_single(self, account: AccountSession, from_chat: int,
                              message_id: int, target_id: int, data: dict):
        try:
            success = await account.forward_message(from_chat, message_id, target_id)
            if success:
                await self.db.log_forward(from_chat, message_id, target_id, account.phone)
        except FloodWaitError as e:
            await asyncio.sleep(min(e.seconds, 30))
        except Exception:
            pass

    async def _do_reply(self, data: dict):
        try:
            sender_id = data['sender_id']
            if not self.rate_limiter.is_allowed(sender_id):
                return

            recent_count = await self.db.get_reply_count(sender_id, self.config.AUTO_BLOCK_HOURS)
            if recent_count >= self.config.AUTO_BLOCK_THRESHOLD:
                await self.db.block_user(sender_id, data.get('username'), data.get('display_name'),
                                        f"Auto-blocked after {recent_count} replies", auto_blocked=True)
                self.blocked_users[sender_id] = {'user_id': sender_id, 'username': data.get('username'),
                                                  'display_name': data.get('display_name')}
                return

            reply_account = None
            for phone, account in self.accounts.items():
                if account.is_active and account.mode in ('reply', 'both'):
                    reply_account = account
                    break
            if not reply_account:
                return

            msg1 = await reply_account.send_message(sender_id, data['text'])
            if msg1:
                await asyncio.sleep(0.5)
                reply_text = self._get_next_auto_reply()
                msg2 = await reply_account.send_message(sender_id, reply_text)
                if msg2:
                    self.rate_limiter.add_request(sender_id)
                    self.metrics.increment('replies_sent')
                    await self.db.log_reply(sender_id, data.get('username'), data.get('display_name'),
                                           reply_account.phone, msg2.id if msg2 else None,
                                           data['chat_id'], data.get('chat_title'), '')
        except Exception as e:
            logger.error(f"Reply error: {e}")

    def _get_next_auto_reply(self) -> str:
        if not self.auto_replies:
            return "مرحباً! 👋\n\nأنا هنا لمساعدتك 📚✨\n\nارسل التفاصيل و راح أساعدك إن شاء الله 😊"
        msg = self.auto_replies[self._auto_reply_index]
        self._auto_reply_index = (self._auto_reply_index + 1) % len(self.auto_replies)
        return msg

    async def _cleanup_old_ids(self):
        while self._running:
            await asyncio.sleep(600)
            cutoff = time.time() - 1800
            to_remove = []
            for msg_id, timestamp in list(self._processed_timestamps):
                if timestamp < cutoff:
                    to_remove.append(msg_id)
            for msg_id in to_remove:
                self._processed_ids.discard(msg_id)
            self._admin_cache.cleanup()

    def get_stats(self) -> Dict[str, Any]:
        stats = self.metrics.get_stats()
        stats['forward_queue_size'] = self.forward_queue.qsize()
        stats['reply_queue_size'] = self.reply_queue.qsize()
        stats['processed_ids_count'] = len(self._processed_ids)
        stats['active_accounts'] = sum(1 for a in self.accounts.values() if a.is_active)
        return stats


# ============== Control Bot ==============

class ControlBot:
    """Control bot for managing the system via Telegram"""

    def __init__(self, token: str, api_id: int, api_hash: str, db: Database,
                 dispatcher: MessageDispatcher, config: Config):
        self.token = token
        self.api_id = api_id
        self.api_hash = api_hash
        self.db = db
        self.dispatcher = dispatcher
        self.config = config
        self.client: Optional[TelegramClient] = None
        self.owner_id: int = config.OWNER_ID
        self._pending_2fa: Dict[str, dict] = {}
        self._pending_ops: Dict[int, dict] = {}

    async def start(self):
        try:
            self.client = TelegramClient(StringSession(), self.api_id, self.api_hash)
            await self.client.start(bot_token=self.token)
            me = await self.client.get_me()
            logger.info(f"Control bot started: @{me.username}")
            self._setup_handlers()
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Control bot error: {e}")

    def _setup_handlers(self):
        @self.client.on(events.NewMessage)
        async def handler(event):
            await self._handle_message(event)

    async def _handle_message(self, event: events.NewMessage.Event):
        try:
            if not event.is_private:
                return
            sender_id = event.sender_id
            text = event.message.message or ""
            if sender_id != self.owner_id and self.owner_id != 0:
                if text.startswith('/'):
                    await event.reply("⚠️ أنت غير مصرح لك باستخدام هذا البوت.")
                return
            if sender_id in self._pending_ops:
                await self._handle_pending_op(sender_id, text, event)
                return
            if text.startswith('/'):
                await self._handle_command(event)
        except Exception as e:
            logger.error(f"Message handler error: {e}")

    async def _handle_command(self, event: events.NewMessage.Event):
        text = event.message.message or ""
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        handlers = {
            '/start': self._cmd_start,
            '/help': self._cmd_help,
            '/addaccount': self._cmd_add_account,
            '/accounts': self._cmd_list_accounts,
            '/startacc': self._cmd_start_account,
            '/stopacc': self._cmd_stop_account,
            '/delacc': self._cmd_delete_account,
            '/setmode': self._cmd_set_mode,
            '/settarget': self._cmd_set_target,
            '/addkeyword': self._cmd_add_keyword,
            '/delkeyword': self._cmd_del_keyword,
            '/keywords': self._cmd_list_keywords,
            '/addtrigger': self._cmd_add_trigger,
            '/deltrigger': self._cmd_del_trigger,
            '/triggers': self._cmd_list_triggers,
            '/addreply': self._cmd_add_reply,
            '/delreply': self._cmd_del_reply,
            '/replies': self._cmd_list_replies,
            '/addblocked': self._cmd_add_blocked,
            '/delblocked': self._cmd_del_blocked,
            '/blocked': self._cmd_list_blocked,
            '/blockuser': self._cmd_block_user,
            '/unblockuser': self._cmd_unblock_user,
            '/addgroup': self._cmd_add_group,
            '/groups': self._cmd_list_groups,
            '/setfallback': self._cmd_set_fallback,
            '/joingroup': self._cmd_join_group,
            '/stats': self._cmd_stats,
            '/reload': self._cmd_reload,
            '/backup': self._cmd_backup,
            '/restore': self._cmd_restore,
            '/setthreshold': self._cmd_set_threshold,
            '/metrics': self._cmd_metrics,
            '/broadcast': self._cmd_broadcast,
            '/verify': self._cmd_verify,
            '/verify2fa': self._cmd_verify_2fa,
            '/cancel': self._cmd_cancel,
        }

        handler = handlers.get(command)
        if handler:
            await handler(event, args)
        else:
            await event.reply("❓ أمر غير معروف. اكتب /help لعرض الأوامر المتاحة.")

    # ===== Command Handlers =====

    async def _cmd_start(self, event, args):
        welcome = (
            "👋 **أهلاً بك في بوت الإدارة المتقدم!**\n\n"
            "هذا البوت يتيح لك التحكم الكامل في:\n"
            "• 🤖 حسابات التليجرام\n"
            "• 🔍 الكلمات المفتاحية\n"
            "• 💬 الردود التلقائية\n"
            "• 📊 الإحصائيات والمراقبة\n\n"
            "اكتب /help لعرض جميع الأوامر."
        )
        await event.reply(welcome)

    async def _cmd_help(self, event, args):
        help_text = (
            "📋 **قائمة الأوامر**\n\n"
            "**إدارة الحسابات:**\n"
            "• `/addaccount` - إضافة حساب جديد\n"
            "• `/accounts` - عرض الحسابات\n"
            "• `/startacc <phone>` - تشغيل/توثيق حساب\n"
            "• `/stopacc <phone>` - إيقاف حساب\n"
            "• `/delacc <phone>` - حذف حساب\n"
            "• `/setmode <phone> <forward/reply/both/self>`\n"
            "• `/settarget <phone> <group_id>`\n\n"
            "**إدارة الكلمات والردود:**\n"
            "• `/keywords` - عرض الكلمات المفتاحية\n"
            "• `/addkeyword <word>` - إضافة كلمة\n"
            "• `/triggers` - عرض المحفزات\n"
            "• `/replies` - عرض الردود التلقائية\n"
            "• `/addreply <message>` - إضافة رد\n\n"
            "**الحظر والحماية:**\n"
            "• `/blocked` - عرض المحظورين\n"
            "• `/blockuser <user_id>`\n"
            "• `/setthreshold <number>`\n\n"
            "**أدوات:**\n"
            "• `/stats` - الإحصائيات\n"
            "• `/metrics` - مقاييس الأداء\n"
            "• `/reload` - إعادة تحميل\n"
            "• `/backup` - نسخة احتياطية\n"
            "• `/cancel` - إلغاء العملية"
        )
        await event.reply(help_text)

    async def _cmd_cancel(self, event, args):
        user_id = event.sender_id
        if user_id in self._pending_ops:
            del self._pending_ops[user_id]
            await event.reply("❌ تم إلغاء العملية.")
        else:
            await event.reply("📭 لا توجد عملية معلقة.")

    async def _cmd_add_account(self, event, args):
        self._pending_ops[event.sender_id] = {'op': 'add_account', 'step': 1, 'data': {}}
        await event.reply("🆕 **إضافة حساب جديد**\n\nالخطوة 1/4: أرسل API ID:\n(أرسل /cancel للإلغاء)")

    async def _cmd_list_accounts(self, event, args):
        accounts = await self.db.get_all_accounts()
        if not accounts:
            await event.reply("📭 لا توجد حسابات مسجلة.")
            return
        lines = ["📱 **الحسابات المسجلة:**\n"]
        for acc in accounts:
            status = "🟢" if acc['is_active'] else "🔴"
            session_ok = "📲 جلسة" if acc.get('session_string') else "❌ لا جلسة"
            lines.append(f"{status} `{acc['phone']}` | `{acc['mode']}` | {session_ok} | 📤 {acc['forward_count']}")
        await event.reply("\n".join(lines[:50]))

    async def _cmd_start_account(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/startacc <phone>`")
            return

        phone = format_phone(args[0])
        account_data = await self.db.get_account(phone)
        if not account_data:
            await event.reply(f"❌ الحساب `{phone}` غير موجود. أضفه أولاً بـ `/addaccount`")
            return

        # Check if already active
        if phone in self.dispatcher.accounts and self.dispatcher.accounts[phone].is_active:
            await event.reply(f"✅ الحساب `{phone}` نشط بالفعل!")
            return

        account = AccountSession(
            phone=phone,
            api_id=account_data['api_id'],
            api_hash=account_data['api_hash'],
            target_group_id=account_data.get('target_group_id', 0),
            mode=account_data.get('mode', 'both'),
            db=self.db,
            session_string=account_data.get('session_string')
        )

        # Try existing session first
        success = await account.create_client()
        if success:
            self.dispatcher.register_account(account)
            await self.db.update_account(phone, is_active=1, enabled=1)
            await event.reply(f"✅ تم تشغيل الحساب `{phone}` بنجاح!")
            return

        # Need authentication - send code using RAW API
        success, msg, session_str = await account.send_code()
        if success:
            await event.reply(
                f"📩 تم إرسال كود التحقق إلى `{phone}`.\n"
                f"أرسل الكود الآن: `/verify {phone} 12345`\n\n"
                f"⚡ **اكتب الكود فوراً!** صالح لدقيقتين."
            )
        else:
            await event.reply(f"❌ فشل إرسال الكود: `{msg}`")

    async def _cmd_verify(self, event, args):
        if len(args) < 2:
            await event.reply("⚠️ استخدم: `/verify <phone> <code>`")
            return

        phone = format_phone(args[0])
        code = args[1]

        # Get account from DB
        account_data = await self.db.get_account(phone)
        if not account_data:
            await event.reply(f"❌ الحساب `{phone}` غير موجود.")
            return

        # Create account object
        account = AccountSession(
            phone=phone,
            api_id=account_data['api_id'],
            api_hash=account_data['api_hash'],
            target_group_id=account_data.get('target_group_id', 0),
            mode=account_data.get('mode', 'both'),
            db=self.db,
            session_string=account_data.get('session_string')
        )

        # Sign in using RAW API - state loaded from DB automatically
        success, result = await account.sign_in(code)

        if success:
            self.dispatcher.register_account(account)
            await self.db.update_account(phone, is_active=1, enabled=1)
            await event.reply(f"✅ تم توثيق وتشغيل الحساب `{phone}` بنجاح!")
        elif result == "2FA_REQUIRED":
            self._pending_2fa[phone] = {'account': account}
            await event.reply(
                f"🔐 الحساب `{phone}` يتطلب 2FA.\n"
                f"أرسل: `/verify2fa {phone} <password>`"
            )
        else:
            await event.reply(
                f"❌ فشل التوثيق: `{result}`\n\n"
                f"💡 أعد المحاولة بـ `/startacc {phone}`"
            )

    async def _cmd_verify_2fa(self, event, args):
        if len(args) < 2:
            await event.reply("⚠️ استخدم: `/verify2fa <phone> <password>`")
            return

        phone = format_phone(args[0])
        password = args[1]

        if phone not in self._pending_2fa:
            await event.reply("⚠️ لا يوجد طلب 2FA معلق.")
            return

        account = self._pending_2fa[phone]['account']
        success, result = await account.sign_in_2fa(password)

        if success:
            self.dispatcher.register_account(account)
            await self.db.update_account(phone, is_active=1, enabled=1)
            del self._pending_2fa[phone]
            await event.reply(f"✅ تم توثيق الحساب `{phone}` بنجاح مع 2FA!")
        else:
            await event.reply(f"❌ فشل: `{result}`")

    async def _cmd_stop_account(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/stopacc <phone>`")
            return
        phone = format_phone(args[0])
        for p, acc in list(self.dispatcher.accounts.items()):
            if p == phone:
                await acc.disconnect()
                self.dispatcher.unregister_account(phone)
                await self.db.update_account(phone, is_active=0, enabled=0)
                await event.reply(f"⏹️ تم إيقاف الحساب `{phone}`.")
                return
        await event.reply(f"⚠️ الحساب `{phone}` غير نشط.")

    async def _cmd_delete_account(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/delacc <phone>`")
            return
        phone = format_phone(args[0])
        for p, acc in list(self.dispatcher.accounts.items()):
            if p == phone:
                await acc.disconnect()
                self.dispatcher.unregister_account(phone)
                break
        success = await self.db.delete_account(phone)
        if success:
            await event.reply(f"🗑️ تم حذف الحساب `{phone}`.")
        else:
            await event.reply(f"❌ فشل الحذف.")

    async def _cmd_set_mode(self, event, args):
        if len(args) < 2:
            await event.reply("⚠️ استخدم: `/setmode <phone> <mode>`")
            return
        phone = format_phone(args[0])
        mode = args[1].lower()
        if mode not in self.config.VALID_MODES:
            await event.reply(f"❌ المتاح: {', '.join(self.config.VALID_MODES)}")
            return
        success = await self.db.update_account(phone, mode=mode)
        if success:
            if phone in self.dispatcher.accounts:
                self.dispatcher.accounts[phone].mode = mode
            await event.reply(f"✅ وضع `{phone}` ← `{mode}`")
            await self.dispatcher.reload_data()
        else:
            await event.reply("❌ فشل.")

    async def _cmd_set_target(self, event, args):
        if len(args) < 2:
            await event.reply("⚠️ استخدم: `/settarget <phone> <group_id>`")
            return
        phone = format_phone(args[0])
        try:
            group_id = int(args[1])
        except ValueError:
            await event.reply("❌ رقم غير صالح.")
            return
        success = await self.db.update_account(phone, target_group_id=group_id)
        if success:
            if phone in self.dispatcher.accounts:
                self.dispatcher.accounts[phone].target_group_id = group_id
            await event.reply(f"✅ target `{phone}` ← `{group_id}`")
        else:
            await event.reply("❌ فشل.")

    async def _cmd_add_keyword(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/addkeyword <word>`")
            return
        success = await self.db.add_keyword(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الإضافة.")
        else:
            await event.reply("⚠️ موجود مسبقاً.")

    async def _cmd_del_keyword(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/delkeyword <word>`")
            return
        success = await self.db.delete_keyword(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الحذف.")
        else:
            await event.reply("❌ فشل.")

    async def _cmd_list_keywords(self, event, args):
        keywords = await self.db.get_keywords()
        if not keywords:
            await event.reply("📭 لا توجد كلمات.")
            return
        text = f"🔍 **الكلمات ({len(keywords)}):**\n\n"
        text += "\n".join([f"• `{kw}`" for kw in keywords[:100]])
        await event.reply(text)

    async def _cmd_add_trigger(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/addtrigger <phrase>`")
            return
        success = await self.db.add_trigger(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الإضافة.")

    async def _cmd_del_trigger(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/deltrigger <phrase>`")
            return
        success = await self.db.delete_trigger(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الحذف.")

    async def _cmd_list_triggers(self, event, args):
        triggers = await self.db.get_triggers()
        if not triggers:
            await event.reply("📭 لا توجد محفزات.")
            return
        text = f"⚡ **المحفزات ({len(triggers)}):**\n\n"
        text += "\n".join([f"• `{tr}`" for tr in triggers[:100]])
        await event.reply(text)

    async def _cmd_add_reply(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/addreply <message>`")
            return
        success = await self.db.add_auto_reply(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الإضافة.")

    async def _cmd_del_reply(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/delreply <message>`")
            return
        success = await self.db.delete_auto_reply(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الحذف.")

    async def _cmd_list_replies(self, event, args):
        replies = await self.db.get_auto_replies()
        if not replies:
            await event.reply("📭 لا توجد ردود.")
            return
        text = f"💬 **الردود ({len(replies)}):**\n\n"
        for i, reply in enumerate(replies[:20], 1):
            preview = reply[:50] + "..." if len(reply) > 50 else reply
            text += f"{i}. {preview}\n\n"
        await event.reply(text)

    async def _cmd_add_blocked(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/addblocked <phrase>`")
            return
        success = await self.db.add_blocked_phrase(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الإضافة.")

    async def _cmd_del_blocked(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/delblocked <phrase>`")
            return
        success = await self.db.delete_blocked_phrase(" ".join(args))
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الحذف.")

    async def _cmd_list_blocked(self, event, args):
        phrases = await self.db.get_blocked_phrases()
        users = await self.db.get_blocked_users()
        text = "🚫 **قائمة الحظر:**\n\n"
        if phrases:
            text += f"**العبارات ({len(phrases)}):**\n" + "\n".join([f"• `{p}`" for p in phrases[:50]]) + "\n\n"
        if users:
            text += f"**المستخدمون ({len(users)}):**\n"
            for uid, info in list(users.items())[:50]:
                text += f"• `{uid}` - {info.get('display_name', 'N/A')}\n"
        if not phrases and not users:
            text += "📭 لا توجد عناصر محظورة."
        await event.reply(text)

    async def _cmd_block_user(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/blockuser <user_id>`")
            return
        try:
            user_id = int(args[0])
        except ValueError:
            await event.reply("❌ رقم غير صالح.")
            return
        reason = " ".join(args[1:]) if len(args) > 1 else None
        success = await self.db.block_user(user_id, reason=reason)
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم الحظر.")

    async def _cmd_unblock_user(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/unblockuser <user_id>`")
            return
        try:
            user_id = int(args[0])
        except ValueError:
            return
        success = await self.db.unblock_user(user_id)
        if success:
            await self.dispatcher.reload_data()
            await event.reply("✅ تم إلغاء الحظر.")

    async def _cmd_add_group(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/addgroup <group_id> [title]`")
            return
        try:
            group_id = int(args[0])
        except ValueError:
            await event.reply("❌ رقم غير صالح.")
            return
        title = " ".join(args[1:]) if len(args) > 1 else None
        success = await self.db.add_group(group_id, title=title)
        if success:
            await event.reply("✅ تم الإضافة.")

    async def _cmd_list_groups(self, event, args):
        groups = await self.db.get_groups()
        if not groups:
            await event.reply("📭 لا توجد مجموعات.")
            return
        text = f"📋 **المجموعات ({len(groups)}):**\n\n"
        for g in groups[:50]:
            text += f"• `{g['group_id']}` - {g.get('title', 'N/A')}\n"
        await event.reply(text)

    async def _cmd_set_fallback(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/setfallback <group_id>`")
            return
        try:
            group_id = int(args[0])
        except ValueError:
            await event.reply("❌ رقم غير صالح.")
            return
        success = await self.db.add_group(group_id, is_fallback=True)
        if success:
            await self.db.set_setting('fallback_group_id', str(group_id))
            await event.reply(f"✅ fallback ← `{group_id}`")

    async def _cmd_join_group(self, event, args):
        if len(args) < 2:
            await event.reply("⚠️ استخدم: `/joingroup <phone> <link>`")
            return
        phone = format_phone(args[0])
        group_link = args[1]
        if phone not in self.dispatcher.accounts:
            await event.reply(f"❌ الحساب `{phone}` غير نشط.")
            return
        account = self.dispatcher.accounts[phone]
        success, message = await account.join_group(group_link)
        if success:
            await event.reply(f"✅ {message}")
        else:
            await event.reply(f"❌ {message}")

    async def _cmd_stats(self, event, args):
        stats = await self.db.get_stats()
        text = (
            f"📊 **الإحصائيات**\n\n"
            f"**الحسابات:** الكل: {stats.get('total_accounts',0)} | النشطة: {stats.get('active_accounts',0)}\n\n"
            f"**المحتوى:** كلمات: {stats.get('keywords_count',0)} | محفزات: {stats.get('triggers_count',0)} | ردود: {stats.get('auto_replies_count',0)}\n\n"
            f"**الحماية:** محظورون: {stats.get('blocked_users_count',0)}\n\n"
            f"**النشاط اليوم:** تحويلات: {stats.get('today_forwards',0)} | ردود: {stats.get('today_replies',0)}\n\n"
            f"**الإجمالي:** تحويلات: {stats.get('total_forwards',0)} | ردود: {stats.get('total_replies',0)}"
        )
        await event.reply(text)

    async def _cmd_reload(self, event, args):
        await self.dispatcher.reload_data()
        await event.reply("🔄 تم إعادة التحميل!")

    async def _cmd_backup(self, event, args):
        try:
            data = await self.db.export_data()
            filename = f"backup_{int(time.time())}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            await self.client.send_file(event.chat_id, filename, caption="📦 نسخة احتياطية")
            if os.path.exists(filename):
                os.remove(filename)
        except Exception as e:
            await event.reply(f"❌ فشل: {e}")

    async def _cmd_restore(self, event, args):
        if not event.message.media:
            await event.reply("📎 أرسل ملف النسخة مع الأمر /restore")
            return
        try:
            path = await event.message.download_media()
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            success = await self.db.import_data(data)
            if success:
                await self.dispatcher.reload_data()
                await event.reply("✅ تم الاستعادة!")
            else:
                await event.reply("❌ فشل الاستعادة.")
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            await event.reply(f"❌ فشل: {e}")

    async def _cmd_set_threshold(self, event, args):
        if not args:
            await event.reply(f"العتبة الحالية: `{self.config.AUTO_BLOCK_THRESHOLD}`")
            return
        try:
            threshold = int(args[0])
        except ValueError:
            await event.reply("❌ رقم غير صالح.")
            return
        self.config.AUTO_BLOCK_THRESHOLD = threshold
        await self.db.set_setting('auto_block_threshold', str(threshold))
        await event.reply(f"✅ عتبة الحظر ← `{threshold}`")

    async def _cmd_metrics(self, event, args):
        stats = self.dispatcher.get_stats()
        text = (
            f"⚡ **مقاييس الأداء**\n\n"
            f"• رسائل مطابقة: {stats.get('messages_matched', 0)}\n"
            f"• تحويلات: {stats.get('forwards_sent', 0)}\n"
            f"• ردود: {stats.get('replies_sent', 0)}\n"
            f"• حسابات نشطة: {stats.get('active_accounts', 0)}"
        )
        await event.reply(text)

    async def _cmd_broadcast(self, event, args):
        if not args:
            await event.reply("⚠️ استخدم: `/broadcast <message>`")
            return
        message = " ".join(args)
        sent = 0
        for phone, account in self.dispatcher.accounts.items():
            if account.is_active and account.target_group_id:
                try:
                    await account.client.send_message(account.target_group_id, message)
                    sent += 1
                except Exception:
                    pass
        await event.reply(f"✅ تم الإرسال إلى {sent} مجموعة.")

    async def _handle_pending_op(self, user_id: int, text: str, event: events.NewMessage.Event):
        op = self._pending_ops[user_id]
        op_type = op.get('op')
        step = op.get('step', 1)
        if text == '/cancel':
            del self._pending_ops[user_id]
            await event.reply("❌ تم الإلغاء.")
            return
        if op_type == 'add_account':
            if step == 1:
                try:
                    op['data']['api_id'] = int(text)
                    op['step'] = 2
                    await event.reply("الخطوة 2/4: أرسل API HASH:")
                except ValueError:
                    await event.reply("❌ API ID يجب أن يكون رقماً.")
            elif step == 2:
                op['data']['api_hash'] = text
                op['step'] = 3
                await event.reply("الخطوة 3/4: أرسل رقم الهاتف (+966XXXXXXXXX):")
            elif step == 3:
                op['data']['phone'] = format_phone(text)
                op['step'] = 4
                await event.reply("الخطوة 4/4: أرسل معرف مجموعة الهدف (أو 0):")
            elif step == 4:
                try:
                    target = int(text)
                    op['data']['target_group_id'] = target
                    success = await self.db.add_account(
                        phone=op['data']['phone'],
                        api_id=op['data']['api_id'],
                        api_hash=op['data']['api_hash'],
                        target_group_id=target, mode='both'
                    )
                    del self._pending_ops[user_id]
                    phone = op['data']['phone']
                    if success:
                        await event.reply(f"✅ **تم الإضافة!**\n📱 `{phone}`\nاستخدم `/startacc {phone}` لتشغيله.")
                    else:
                        await event.reply("❌ فشل.")
                except ValueError:
                    await event.reply("❌ يجب أن يكون رقماً.")


# ============== Main Manager ==============

class TelegramBotManager:
    """Main manager class - orchestrates everything"""

    def __init__(self):
        self.config = Config()
        self.db = Database(self.config.DB_PATH)
        self.dispatcher = MessageDispatcher(self.db, self.config)
        self.control_bot: Optional[ControlBot] = None
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        logger.info("=" * 50)
        logger.info("Bot Starting...")
        logger.info("=" * 50)

        os.makedirs("sessions", exist_ok=True)
        os.makedirs("backups", exist_ok=True)

        await self.db.initialize()
        logger.info("Database initialized")

        await self.dispatcher.initialize()

        if self.config.BOT_TOKEN and self.config.API_ID and self.config.API_HASH:
            self.control_bot = ControlBot(
                self.config.BOT_TOKEN, self.config.API_ID, self.config.API_HASH,
                self.db, self.dispatcher, self.config
            )
            asyncio.create_task(self.control_bot.start())
            asyncio.create_task(self._start_saved_accounts())
        else:
            logger.warning("Control bot not configured.")

        self._running = True
        logger.info("Bot manager started!")
        await self._shutdown_event.wait()

    async def _start_saved_accounts(self):
        await asyncio.sleep(3)
        accounts = await self.db.get_enabled_accounts()
        logger.info(f"Found {len(accounts)} enabled accounts to auto-start")
        for acc_data in accounts:
            try:
                if not acc_data.get('session_string'):
                    logger.info(f"Account {acc_data['phone']} has no session, skipping")
                    continue
                account = AccountSession(
                    phone=acc_data['phone'],
                    api_id=acc_data['api_id'],
                    api_hash=acc_data['api_hash'],
                    target_group_id=acc_data.get('target_group_id', 0),
                    mode=acc_data.get('mode', 'both'),
                    db=self.db,
                    session_string=acc_data['session_string']
                )
                success = await account.create_client()
                if success:
                    self.dispatcher.register_account(account)
                    logger.info(f"Auto-started: {account.phone}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Failed to auto-start {acc_data.get('phone', '?')}: {e}")

    async def stop(self):
        logger.info("Shutting down...")
        self._running = False
        self._shutdown_event.set()
        await self.dispatcher.shutdown()
        for phone, account in list(self.dispatcher.accounts.items()):
            try:
                await account.disconnect()
            except Exception:
                pass
        await self.db.close()
        logger.info("Bot stopped")


# ============== Entry Point ==============

if __name__ == "__main__":
    manager = TelegramBotManager()

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(manager.stop())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(manager.start())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
