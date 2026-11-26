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
        """
        SELECT
            id,
            text,
            media_type,
            media_file_id,
            status,
            created_at
        FROM suggestions
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()

    connection.close()

    return {
        "items": [
            {
                "id": row["id"],
                "text": row["text"],
                "media_type": row["media_type"],
                "media_file_id": row["media_file_id"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }


@app.post("/api/suggestions/{suggestion_id}/cancel")
async def cancel_suggestion(suggestion_id: int, request: Request):
    user_id = get_user_id(request)
    connection = get_db()

    row = connection.execute(
        """
        SELECT id, status
        FROM suggestions
        WHERE id = ? AND user_id = ?
        """,
        (suggestion_id, user_id),
    ).fetchone()

    if not row:
        connection.close()
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    if row["status"] != "На модерации":
        connection.close()
        raise HTTPException(status_code=400, detail="Эту заявку уже нельзя отменить")

    connection.execute(
        """
        UPDATE suggestions
        SET status = 'Отклонено', updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            int(datetime.now(timezone.utc).timestamp()),
            suggestion_id,
            user_id,
        ),
    )

    connection.commit()
    connection.close()

    return {
        "ok": True,
        "status": "Отклонено",
    }


@app.get("/api/leaderboard")
async def leaderboard(request: Request):
    get_user_id(request)
    connection = get_db()

    rows = connection.execute(
        """
        SELECT
            user_stats.user_id,
            user_stats.suggestions_count,
            user_stats.published_count,
            user_stats.likes_count,
            COALESCE(user_profiles.display_name, '') AS display_name,
            COALESCE(user_profiles.is_anonymous, 1) AS is_anonymous
        FROM user_stats
        LEFT JOIN user_profiles
            ON user_profiles.user_id = user_stats.user_id
        ORDER BY
            user_stats.published_count DESC,
            user_stats.likes_count DESC,
            user_stats.suggestions_count DESC,
            user_stats.user_id ASC
        LIMIT 10
        """
    ).fetchall()

    connection.close()

    return {
        "items": [
            {
                "place": index + 1,
                "user_id": row["user_id"],
                "name": (
                    "Аноним"
                    if row["is_anonymous"] or not row["display_name"]
                    else row["display_name"]
                ),
                "suggestions_count": row["suggestions_count"],
                "published_count": row["published_count"],
                "likes_count": row["likes_count"],
            }
            for index, row in enumerate(rows)
        ]
    }


@app.get("/api/profile/me")
async def profile_me(request: Request):
    user_id = get_user_id(request)
    connection = get_db()

    profile, stats = get_profile(connection, user_id)
    connection.close()

    return {
        "user_id": user_id,
        "display_name": profile["display_name"] or "",
        "is_anonymous": bool(profile["is_anonymous"]),
        "suggestions_count": stats["suggestions_count"],
        "published_count": stats["published_count"],
        "likes_count": stats["likes_count"],
    }


class ProfileSave(BaseModel):
    display_name: str = ""
    is_anonymous: bool = True


@app.post("/api/profile/save")
async def profile_save(data: ProfileSave, request: Request):
    user_id = get_user_id(request)
    display_name = data.display_name.strip()[:32]
    now = int(datetime.now(timezone.utc).timestamp())

    connection = get_db()
    get_profile(connection, user_id)

    connection.execute(
        """
        UPDATE user_profiles
        SET display_name = ?, is_anonymous = ?, updated_at = ?
        WHERE user_id = ?
        """,
        (display_name, int(data.is_anonymous), now, user_id),
    )

    connection.commit()
    connection.close()

    return {
        "ok": True,
        "display_name": display_name,
        "is_anonymous": data.is_anonymous,
    }


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
