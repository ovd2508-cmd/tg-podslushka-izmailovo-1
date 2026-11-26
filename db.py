import json
import time
from contextlib import asynccontextmanager
import aiosqlite
from config import DB_PATH

@asynccontextmanager
async def connection():
    db = await aiosqlite.connect(str(DB_PATH), timeout=30)
    try:
        await db.execute("PRAGMA busy_timeout=30000")
        yield db
    finally:
        await db.close()

async def init_db():
    async with connection() as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS admin_messages (
            admin_message_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_cooldowns (
            user_id INTEGER PRIMARY KEY,
            last_sent_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            suggestions_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT,
            is_anonymous INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            admin_message_id INTEGER NOT NULL UNIQUE,
            body TEXT NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            media_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS
