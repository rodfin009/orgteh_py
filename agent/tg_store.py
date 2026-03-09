"""
agent/tg_store.py
─────────────────────────────────────────────────────────────────────────────
واجهة بسيطة — كل المنطق الحقيقي في telegram_bot.py
هذا الملف موجود فقط حتى يستورد منه routes.py بشكل نظيف
"""

from telegram_bot import (
    save_agent_session      as save_session_to_telegram,
    load_agent_session      as load_session_from_telegram,
    notify_agent_complete   as notify_session_complete,
    check_agent_bot_status,
)

__all__ = [
    "save_session_to_telegram",
    "load_session_from_telegram",
    "notify_session_complete",
    "check_agent_bot_status",
]
