"""
Configuration Module - All settings centralized and controllable via bot
"""
import os
import re
from typing import Set, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration with runtime update capability"""
    
    # Bot settings
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
    
    # Performance settings
    WORKERS: int = int(os.getenv("WORKERS", "50"))
    SEND_CONCURRENCY: int = int(os.getenv("SEND_CONCURRENCY", "50"))
    QUEUE_SIZE: int = int(os.getenv("QUEUE_SIZE", "10000"))
    
    # Forward/Reply settings
    MAX_GROUPS_PER_ACCOUNT: int = int(os.getenv("MAX_GROUPS_PER_ACCOUNT", "200"))
    FORWARD_DELAY: float = float(os.getenv("FORWARD_DELAY", "0.1"))
    REPLY_DELAY: float = float(os.getenv("REPLY_DELAY", "0.1"))
    
    # Database
    DB_PATH: str = os.getenv("DB_PATH", "bot_data.db")
    
    # Valid modes for accounts
    VALID_MODES = {"forward", "reply", "both", "self"}
    
    # Default keywords (matching original logic)
    DEFAULT_KEYWORDS = [
        "ابي مساعده", "يسوي", "يحل", "خصوصي", "شاطر", "تحل", "تسوي", "يعرف", "تعرف", 
        "واجب", "بروجكت", "فاهم", "سكليف", "بحث", "مشروع", "يساعد", "اسايمنت",
        "ابغى مساعده", "ابغا مساعده", "محتاج مساعده", "حد يساعدني", "احد يساعدني",
        "ابي حد يحضر عني", "ابغا حد يحضر عني", "يحضر عني", "يحظر", "يحضر",
        "عندي اختبار", "احد عنده خصوصي", "احد يعرف مختص",
        "case study", "كيس ستدي", "بوربوينت", "بووربوينت", 
        "عذر طبي", "اجازة مرضية", "شرح", "مساعدة", "مساعده",
        "assignments", "homework", "exam", "quiz", "test",
        "midterm", "final", "project", "presentation", "essay",
        "paper", "research", "thesis", "dissertation", "حل",
        "سؤال", "اسئلة", "امتحان", "فاينل", "مدتيرم",
        "تكوين", "وظائف", "تكاليف", "دراسة", "تحليل",
        "برزنتيشن", "سلايد", "تقرير", "بحث علمي",
        "مساعدة طلاب", "مساعده طلاب", "خدمات طلابية",
        "private tutoring", "tutor", "معلم خاص", "مدرس خاص",
    ]
    
    # Compiled regex for keywords (updated at runtime)
    KEYWORDS_REGEX = None
    
    # Default trigger phrases for auto-reply
    DEFAULT_TRIGGERS = [
        "ابي مساعده", "ابغى مساعده", "ابغا مساعده", "محتاج مساعده",
        "حد يساعدني", "احد يساعدني", "يساعد", "مساعده", "مساعدة",
        "ابي حد يحل", "ابغى حد يحل", "يحل واجب", "يحل تكاليف",
        "خصوصي", "معلم خاص", "مدرس خاص", "private",
        "عندي اختبار", "عندي فاينل", "عندي مدتيرم",
        "ابي حد يحضر عني", "يحضر", "يحضر عني",
        "اسايمنت", "بروجكت", "مشروع", "case study",
        "بحث", "تقرير", "برزنتيشن", "بوربوينت",
        "واجب", "تكاليف", "حل", "اسئلة",
    ]
    
    # Default auto-reply messages (rotated)
    DEFAULT_AUTO_REPLIES = [
        "مرحباً! 👋\n\nأنا هنا لمساعدتك في واجباتك واختباراتك 📚✨\n\nارسل التفاصيل و راح أساعدك إن شاء الله 😊",
        "أهلاً وسهلاً! 🌟\n\nأقدر خدمات مساعدة في الواجبات والمشاريع والاختبارات 📝\n\nتواصل معي وأنا جاهز أساعدك 💪",
        "مرحباً! 🎓\n\nمحتاج مساعدة في دراستك؟ لا تشيل هم! \n\nأنا متواجد وأساعدك في كل شي تحتاجه 📖✨",
        "أهلاً بك! 👋\n\nأقدر مساعدة في:\n• الواجبات والتكاليف 📄\n• المشاريع والبحوث 🔬\n• الاختبارات والكويزات 📝\n\nأرسل اللي تحتاجه وأنا جاهز! 💯",
    ]
    
    # Threshold for auto-blocking (number of auto-replies before blocking)
    AUTO_BLOCK_THRESHOLD: int = 4
    AUTO_BLOCK_HOURS: int = 24
    
    # Excluded groups (won't process messages from these)
    DEFAULT_EXCLUDED_GROUPS: Set[int] = set()
    
    # Fallback group for failed forwards
    FALLBACK_GROUP_ID: int = 0
    
    # Command group (for command bot)
    COMMAND_GROUP_ID: int = 0
    
    @classmethod
    def compile_keywords(cls, keywords: List[str]) -> None:
        """Compile keywords into regex for fast matching"""
        if not keywords:
            cls.KEYWORDS_REGEX = None
            return
        # Sort by length (longest first) for better matching
        sorted_keywords = sorted(keywords, key=len, reverse=True)
        escaped = [re.escape(kw) for kw in sorted_keywords if kw.strip()]
        if escaped:
            cls.KEYWORDS_REGEX = re.compile("|".join(escaped), re.IGNORECASE)
        else:
            cls.KEYWORDS_REGEX = None
    
    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Export config as dictionary for display"""
        return {
            "bot_token": "***" if cls.BOT_TOKEN else "Not set",
            "api_id": cls.API_ID,
            "api_hash": "***" if cls.API_HASH else "Not set",
            "owner_id": cls.OWNER_ID,
            "workers": cls.WORKERS,
            "send_concurrency": cls.SEND_CONCURRENCY,
            "queue_size": cls.QUEUE_SIZE,
            "max_groups": cls.MAX_GROUPS_PER_ACCOUNT,
            "forward_delay": cls.FORWARD_DELAY,
            "reply_delay": cls.REPLY_DELAY,
            "auto_block_threshold": cls.AUTO_BLOCK_THRESHOLD,
            "auto_block_hours": cls.AUTO_BLOCK_HOURS,
            "fallback_group": cls.FALLBACK_GROUP_ID,
            "command_group": cls.COMMAND_GROUP_ID,
            "excluded_groups_count": len(cls.DEFAULT_EXCLUDED_GROUPS),
        }
