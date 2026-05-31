"""
Utilities Module - Text processing, normalization, and helper functions
Optimized for speed with caching and compiled regex
"""
import re
import unicodedata
import hashlib
import time
from typing import Dict, Set, List, Optional, Tuple, Any
from rapidfuzz import fuzz
import functools
import asyncio


class TextProcessor:
    """Fast text processing with compiled patterns"""
    
    # Compiled patterns for performance
    URL_PATTERN = re.compile(r'https?://\S+|t\.me/\S+|@\w+')
    MENTION_PATTERN = re.compile(r'@\w{5,}')
    DIGIT_PATTERN = re.compile(r'\d')
    WHITESPACE_PATTERN = re.compile(r'\s+')
    WORD_COUNT_PATTERN = re.compile(r'\S+')
    
    @classmethod
    def normalize(cls, text: str) -> str:
        """Normalize Arabic text for matching"""
        if not text:
            return ""
        # Remove tashkeel/diacritics
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        # Normalize Arabic letters
        text = text.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
        text = text.replace('ة', 'ه').replace('ى', 'ي')
        text = text.replace('ﻷ', 'لا').replace('ﻹ', 'لي').replace('ﻻ', 'لا')
        # Remove non-alphanumeric except spaces
        text = re.sub(r'[^\w\s]', '', text)
        return cls.WHITESPACE_PATTERN.sub(' ', text).strip().lower()
    
    @classmethod
    def has_url(cls, text: str) -> bool:
        """Check if text contains URLs"""
        return bool(cls.URL_PATTERN.search(text))
    
    @classmethod
    def has_mention(cls, text: str) -> bool:
        """Check if text has long mentions"""
        return bool(cls.MENTION_PATTERN.search(text))
    
    @classmethod
    def word_count(cls, text: str) -> int:
        """Count words in text"""
        return len(cls.WORD_COUNT_PATTERN.findall(text))
    
    @classmethod
    def has_digits(cls, text: str) -> bool:
        """Check if text contains digits"""
        return bool(cls.DIGIT_PATTERN.search(text))
    
    @classmethod
    def should_skip(cls, text: str) -> bool:
        """Quick check if message should be skipped"""
        # Skip if too long (> 17 words)
        if cls.word_count(text) > 17:
            return True
        # Skip if has URLs
        if cls.has_url(text):
            return True
        # Skip if has mentions
        if cls.has_mention(text):
            return True
        # Skip if has digits
        if cls.has_digits(text):
            return True
        return False
    
    @classmethod
    def fuzzy_match(cls, text: str, patterns: List[str], threshold: int = 75) -> bool:
        """Fast fuzzy matching against list of patterns"""
        normalized = cls.normalize(text)
        if not normalized:
            return False
        for pattern in patterns:
            if fuzz.partial_ratio(normalized, cls.normalize(pattern)) >= threshold:
                return True
        return False
    
    @classmethod
    def keyword_match(cls, text: str, regex_pattern: re.Pattern) -> bool:
        """Fast keyword matching using compiled regex"""
        if not regex_pattern:
            return False
        return bool(regex_pattern.search(text))
    
    @classmethod
    def generate_id(cls, *args) -> str:
        """Generate unique ID from arguments"""
        content = "|".join(str(a) for a in args)
        return hashlib.md5(content.encode()).hexdigest()


class Cache:
    """Simple TTL cache for fast lookups"""
    
    def __init__(self, ttl: int = 300):
        self._data: Dict[Any, Tuple[Any, float]] = {}
        self._ttl = ttl
    
    def get(self, key: Any) -> Optional[Any]:
        """Get cached value if not expired"""
        item = self._data.get(key)
        if item is None:
            return None
        value, expiry = item
        if time.time() > expiry:
            del self._data[key]
            return None
        return value
    
    def set(self, key: Any, value: Any):
        """Cache value with TTL"""
        self._data[key] = (value, time.time() + self._ttl)
    
    def delete(self, key: Any):
        """Delete cached value"""
        self._data.pop(key, None)
    
    def clear(self):
        """Clear all cached values"""
        self._data.clear()
    
    def cleanup(self):
        """Remove expired entries"""
        now = time.time()
        expired = [k for k, (_, exp) in self._data.items() if now > exp]
        for k in expired:
            del self._data[k]


class RateLimiter:
    """Rate limiter for messages"""
    
    def __init__(self, max_requests: int = 30, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self._requests: Dict[int, List[float]] = {}
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is within rate limit"""
        now = time.time()
        requests = self._requests.get(user_id, [])
        # Remove old requests
        requests = [r for r in requests if now - r < self.window]
        self._requests[user_id] = requests
        return len(requests) < self.max_requests
    
    def add_request(self, user_id: int):
        """Record a request"""
        now = time.time()
        if user_id not in self._requests:
            self._requests[user_id] = []
        self._requests[user_id].append(now)


class MetricsCollector:
    """Collect and track performance metrics"""
    
    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, List[float]] = {}
        self._start_times: Dict[str, float] = {}
    
    def increment(self, metric: str, value: int = 1):
        """Increment counter"""
        self._counters[metric] = self._counters.get(metric, 0) + value
    
    def start_timer(self, metric: str):
        """Start timing"""
        self._start_times[metric] = time.time()
    
    def end_timer(self, metric: str):
        """End timing and record"""
        if metric in self._start_times:
            elapsed = time.time() - self._start_times[metric]
            if metric not in self._timers:
                self._timers[metric] = []
            self._timers[metric].append(elapsed)
            del self._start_times[metric]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get collected metrics"""
        stats = {}
        stats['counters'] = self._counters.copy()
        
        for metric, times in self._timers.items():
            if times:
                stats[f'{metric}_avg'] = sum(times) / len(times)
                stats[f'{metric}_min'] = min(times)
                stats[f'{metric}_max'] = max(times)
                stats[f'{metric}_count'] = len(times)
        
        return stats
    
    def reset(self):
        """Reset all metrics"""
        self._counters.clear()
        self._timers.clear()
        self._start_times.clear()


def async_retry(max_retries: int = 3, delay: float = 1.0):
    """Decorator for async retry logic"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay * (2 ** attempt))
            raise last_exception
        return wrapper
    return decorator


def format_phone(phone: str) -> str:
    """Normalize phone number"""
    phone = re.sub(r'\D', '', phone)
    if not phone.startswith('+'):
        phone = '+' + phone
    return phone


def truncate_text(text: str, max_length: int = 4000) -> List[str]:
    """Split long text into chunks"""
    if len(text) <= max_length:
        return [text]
    chunks = []
    for i in range(0, len(text), max_length):
        chunks.append(text[i:i + max_length])
    return chunks


def escape_markdown(text: str) -> str:
    """Escape markdown special characters"""
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, f'\\{char}')
    return text
