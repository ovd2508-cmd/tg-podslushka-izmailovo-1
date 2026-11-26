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

        CREATE INDEX IF NOT EXISTS submissions_owner
        ON submissions(user_id, id);

        CREATE TABLE IF NOT EXISTS submission_media (
            file_id TEXT PRIMARY KEY,
            submission_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            media_type TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS submission_media_owner
        ON submission_media(user_id, submission_id);
        """)

        cursor = await db.execute(
            "PRAGMA table_info(submissions)"
        )
        columns = [row[1] for row in await cursor.fetchall()]

        if "media_json" not in columns:
            await db.execute(
                "ALTER TABLE submissions ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'"
            )

        await db.commit()


async def get_last_sent_at(user_id):
    async with connection() as db:
        cursor = await db.execute(
            "SELECT last_sent_at FROM user_cooldowns WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def record_submission(
    user_id,
    kind,
    admin_message_id,
    body,
    media=None,
):
    now = time.time()
    media = media or []

    async with connection() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")

            cursor = await db.execute(
                """
                INSERT INTO submissions
                (user_id, kind, admin_message_id, body, created_at, media_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    admin_message_id,
                    body,
                    now,
                    json.dumps(media, ensure_ascii=False),
                ),
            )

            submission_id = cursor.lastrowid

            await db.execute(
                "INSERT INTO admin_messages VALUES (?, ?)",
                (admin_message_id, user_id),
            )

            await db.execute(
                """
                INSERT INTO user_cooldowns VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET last_sent_at=excluded.last_sent_at
                """,
                (user_id, now),
            )

            if kind != "contact":
                await db.execute(
                    """
                    INSERT INTO user_stats VALUES (?, 1)
                    ON CONFLICT(user_id)
                    DO UPDATE SET suggestions_count=suggestions_count+1
                    """,
                    (user_id,),
                )

            for item in media:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO submission_media
                    (file_id, submission_id, user_id, media_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        item["file_id"],
                        submission_id,
                        user_id,
                        item["type"],
                    ),
                )

            await db.commit()
            return submission_id

        except BaseException:
            await db.rollback()
            raise


async def get_suggestion_count(user_id):
    async with connection() as db:
        cursor = await db.execute(
            "SELECT suggestions_count FROM user_stats WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_submission_count(user_id):
    async with connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM submissions WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def history_summary(user_id):
    async with connection() as db:
        cursor = await db.execute(
            """
            SELECT kind, COUNT(*), SUM(status='withdrawn')
            FROM submissions
            WHERE user_id=?
            GROUP BY kind
            """,
            (user_id,),
        )

        rows = await cursor.fetchall()
        return {
            row[0]: (row[1], row[2])
            for row in rows
        }


async def list_submissions(user_id, offset=0, limit=5):
    async with connection() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT * FROM submissions
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )

        return [dict(row) for row in await cursor.fetchall()]


async def get_user_submissions(user_id, limit=50):
    async with connection() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT * FROM submissions
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )

        rows = [dict(row) for row in await cursor.fetchall()]

        for row in rows:
            media_cursor = await db.execute(
                """
                SELECT file_id, media_type
                FROM submission_media
                WHERE submission_id=?
                ORDER BY rowid
                """,
                (row["id"],),
            )

            media_rows = await media_cursor.fetchall()

            row["media"] = [
                {
                    "type": media_row[1],
                    "file_id": media_row[0],
                    "url": f"/api/media/{media_row[0]}",
                }
                for media_row in media_rows
            ]

            try:
                row["media_json"] = json.loads(row.get("media_json") or "[]")
            except (TypeError, ValueError):
                row["media_json"] = []

        return rows


async def get_submission(sid, user_id):
    async with connection() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM submissions WHERE id=? AND user_id=?",
            (sid, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_media_owner(file_id):
    async with connection() as db:
        cursor = await db.execute(
            """
            SELECT user_id, submission_id, media_type
            FROM submission_media
            WHERE file_id=?
            """,
            (file_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            "user_id": row[0],
            "submission_id": row[1],
            "media_type": row[2],
        }


async def withdraw_submission(sid, user_id):
    async with connection() as db:
        cursor = await db.execute(
            """
            UPDATE submissions
            SET status='withdrawn'
            WHERE id=? AND user_id=? AND status='sent' AND kind!='contact'
            """,
            (sid, user_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def get_reply_target(admin_message_id):
    async with connection() as db:
        cursor = await db.execute(
            """
            SELECT a.user_id, s.status
            FROM admin_messages a
            LEFT JOIN submissions s
            ON s.admin_message_id=a.admin_message_id
            WHERE a.admin_message_id=?
            """,
