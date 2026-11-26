import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "/app/data/bot.db")

app = FastAPI(title="Podslushano Izmaylovo Mini App API")


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_api_tables():
    connection = get_db()

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT DEFAULT '',
            is_anonymous INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            suggestions_count INTEGER DEFAULT 0,
            published_count INTEGER DEFAULT 0,
            likes_count INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            media_type TEXT,
            media_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'На модерации',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_suggestions_user
        ON suggestions(user_id);

        CREATE INDEX IF NOT EXISTS idx_suggestions_status
        ON suggestions(status);
        """
    )

    connection.commit()
    connection.close()


init_api_tables()


def validate_init_data(init_data: str) -> dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Нет данных Telegram")

    try:
        parsed = dict(
            item.split("=", 1)
            for item in init_data.split("&")
        )

        received_hash = parsed.pop("hash")

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
    except Exception:
        raise HTTPException(status_code=401, detail="Неверные данные Telegram")

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Подпись Telegram не совпадает")

    try:
        user = json.loads(parsed["user"])
    except Exception:
        raise HTTPException(status_code=401, detail="Не найден пользователь Telegram")

    return user


def get_user_id(request: Request) -> int:
    init_data = request.headers.get("X-Telegram-Init-Data", "")

    if not init_data:
        init_data = request.query_params.get("initData", "")

    user = validate_init_data(init_data)
    return int(user["id"])


def get_profile(connection: sqlite3.Connection, user_id: int):
    now = int(datetime.now(timezone.utc).timestamp())

    connection.execute(
        """
        INSERT OR IGNORE INTO user_profiles
        (user_id, display_name, is_anonymous, created_at, updated_at)
        VALUES (?, '', 1, ?, ?)
        """,
        (user_id, now, now),
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO user_stats
        (user_id, suggestions_count, published_count, likes_count, updated_at)
        VALUES (?, 0, 0, 0, ?)
        """,
        (user_id, now),
    )

    connection.commit()

    profile = connection.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    stats = connection.execute(
        "SELECT * FROM user_stats WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    return profile, stats


@app.get("/api/my_suggestions")
async def my_suggestions(request: Request):
    user_id = get_user_id(request)
    connection = get_db()

    rows = connection.execute(
