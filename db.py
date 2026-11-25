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
            admin_message_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_cooldowns (
            user_id INTEGER PRIMARY KEY, last_sent_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY, suggestions_count INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY, display_name TEXT,
            is_anonymous INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, kind TEXT NOT NULL,
            admin_message_id INTEGER NOT NULL UNIQUE,
            body TEXT NOT NULL, created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent');
        CREATE INDEX IF NOT EXISTS submissions_owner ON submissions(user_id, id);
        """)
        await db.commit()

async def get_last_sent_at(user_id):
    async with connection() as db:
        cur = await db.execute("SELECT last_sent_at FROM user_cooldowns WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None

async def record_submission(user_id, kind, admin_message_id, body):
    now = time.time()
    async with connection() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "INSERT INTO submissions(user_id,kind,admin_message_id,body,created_at) VALUES(?,?,?,?,?)",
                (user_id, kind, admin_message_id, body, now))
            sid = cur.lastrowid
            await db.execute("INSERT INTO admin_messages VALUES(?,?)", (admin_message_id, user_id))
            await db.execute("""INSERT INTO user_cooldowns VALUES(?,?) ON CONFLICT(user_id)
                DO UPDATE SET last_sent_at=excluded.last_sent_at""", (user_id, now))
            if kind != 'contact':
                await db.execute("""INSERT INTO user_stats VALUES(?,1) ON CONFLICT(user_id)
                    DO UPDATE SET suggestions_count=suggestions_count+1""", (user_id,))
            await db.commit()
            return sid
        except BaseException:
            await db.rollback()
            raise

async def get_suggestion_count(user_id):
    async with connection() as db:
        cur = await db.execute("SELECT suggestions_count FROM user_stats WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

async def history_summary(user_id):
    async with connection() as db:
        cur = await db.execute("""SELECT kind,COUNT(*),SUM(status='withdrawn')
            FROM submissions WHERE user_id=? GROUP BY kind""", (user_id,))
        return {r[0]: (r[1], r[2]) for r in await cur.fetchall()}

async def list_submissions(user_id, offset=0, limit=5):
    async with connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""SELECT * FROM submissions WHERE user_id=?
            ORDER BY id DESC LIMIT ? OFFSET ?""", (user_id, limit, offset))
        return [dict(r) for r in await cur.fetchall()]

async def get_submission(sid, user_id):
    async with connection() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM submissions WHERE id=? AND user_id=?", (sid, user_id))
        row = await cur.fetchone()
        return dict(row) if row else None

async def withdraw_submission(sid, user_id):
    async with connection() as db:
        cur = await db.execute("""UPDATE submissions SET status='withdrawn'
            WHERE id=? AND user_id=? AND status='sent' AND kind!='contact'""", (sid,user_id))
        await db.commit()
        return cur.rowcount == 1

async def get_reply_target(admin_message_id):
    async with connection() as db:
        cur = await db.execute("""SELECT a.user_id, s.status FROM admin_messages a
            LEFT JOIN submissions s ON s.admin_message_id=a.admin_message_id
            WHERE a.admin_message_id=?""", (admin_message_id,))
        return await cur.fetchone()

async def get_user_id_by_admin_message(admin_message_id):
    row = await get_reply_target(admin_message_id)
    return row[0] if row else None

async def get_top_users(limit=10):
    async with connection() as db:
        cur = await db.execute("""SELECT s.user_id,s.suggestions_count,p.display_name,p.is_anonymous
            FROM user_stats s LEFT JOIN user_profiles p ON p.user_id=s.user_id
            WHERE s.suggestions_count>0 ORDER BY s.suggestions_count DESC,s.user_id LIMIT ?""",
            (max(1,min(100,int(limit))),))
        return [dict(user_id=r[0],suggestions_count=r[1],display_name=r[2],
            is_anonymous=(r[3] is None or bool(r[3]) or not r[2])) for r in await cur.fetchall()]

async def get_public_top(limit=10):
    rows = await get_top_users(limit)
    return [dict(position=i,display_name='Аноним' if r['is_anonymous'] else r['display_name'],
        suggestions_count=r['suggestions_count'],is_anonymous=r['is_anonymous'])
        for i,r in enumerate(rows,1)]

async def get_user_profile(user_id):
    async with connection() as db:
        cur = await db.execute("SELECT display_name,is_anonymous FROM user_profiles WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(user_id=user_id,display_name=row[0],is_anonymous=bool(row[1]) or not row[0]) if row else None

async def save_user_profile(user_id, display_name, is_anonymous):
    name = (display_name or '').strip()[:64] or None
    async with connection() as db:
        await db.execute("""INSERT INTO user_profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            display_name=excluded.display_name,is_anonymous=excluded.is_anonymous,updated_at=excluded.updated_at""",
            (user_id,name,int(bool(is_anonymous) or not name),time.time()))
        await db.commit()

async def get_stats(user_id):
    async with connection() as db:
        cur = await db.execute("""SELECT COUNT(CASE WHEN suggestions_count>0 THEN 1 END),
            COALESCE(SUM(suggestions_count),0),COALESCE(MAX(CASE WHEN user_id=? THEN suggestions_count END),0)
            FROM user_stats""", (user_id,))
        return tuple(await cur.fetchone())
