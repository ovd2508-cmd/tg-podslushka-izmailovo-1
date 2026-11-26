import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import parse_qsl

from aiogram.exceptions import TelegramAPIError
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from bot import bot
from config import BASE_DIR, BOT_TOKEN, MINI_APP_AUTH_MAX_AGE
from db import (
    get_media_owner,
    get_public_top,
    get_submission_count,
    get_suggestion_count,
    get_user_profile,
    get_user_submissions,
    init_db,
    save_user_profile,
)

logger = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    application.state.database_ready = True

    try:
        yield
    finally:
        application.state.database_ready = False


app = FastAPI(
    title="Подслушано Измайлово",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.state.database_ready = False

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR), check_dir=False),
    name="static",
)


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"

    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    return response


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def validate_init_data(raw_init_data: str) -> dict:
    if not raw_init_data:
        raise unauthorized("Открой приложение через кнопку в Telegram-боте.")

    if len(raw_init_data) > 16384:
        raise unauthorized("Слишком большой объём данных авторизации.")

    try:
        pairs = parse_qsl(
            raw_init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=50,
            encoding="utf-8",
            errors="strict",
        )
    except (ValueError, UnicodeError):
        raise unauthorized("Некорректный формат данных авторизации.")

    keys = [key for key, _ in pairs]

    if len(keys) != len(set(keys)):
        raise unauthorized("Дублирующиеся параметры авторизации.")

    data = dict(pairs)
    received_hash = data.pop("hash", None)

    if (
        not received_hash
        or len(received_hash) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in received_hash
        )
    ):
        raise unauthorized("Отсутствует или повреждена подпись Telegram.")

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=BOT_TOKEN.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        raise unauthorized("Подпись Telegram не прошла проверку.")

    try:
        auth_date = int(data["auth_date"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized("Отсутствует корректное время авторизации.")

    now = time.time()

    if auth_date > now + 60:
        raise unauthorized("Некорректное время авторизации.")

    if now - auth_date > MINI_APP_AUTH_MAX_AGE:
        raise unauthorized("Сессия истекла. Открой приложение заново.")

    try:
        user = json.loads(data["user"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized("В авторизации отсутствует пользователь Telegram.")

    if not isinstance(user, dict):
        raise unauthorized("Некорректные данные пользователя.")

    user_id = user.get("id")

    if type(user_id) is not int or user_id <= 0:
        raise unauthorized("Некорректный ID пользователя Telegram.")

    return user


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization:
        raise unauthorized("Открой приложение из Telegram-бота.")

    scheme, separator, credentials = authorization.partition(" ")

    if not separator or scheme.lower() not in {"tma", "telegram"} or not credentials:
        raise unauthorized("Неверный формат заголовка авторизации.")

    return validate_init_data(credentials)


TelegramUser = Annotated[dict, Depends(current_user)]


def get_telegram_name(user: dict) -> str:
    username = user.get("username")

    if isinstance(username, str) and username.strip():
        return f"@{username.strip()}"[:64]

    name_parts = []

    for field in ("first_name", "last_name"):
        value = user.get(field)

        if isinstance(value, str):
            name_parts.extend(value.split())

    return " ".join(name_parts)[:64] or "Пользователь"


async def build_my_profile(user: dict) -> dict:
    user_id = user["id"]
    profile = await get_user_profile(user_id)
    suggestions_count = await get_suggestion_count(user_id)
    total_submissions = await get_submission_count(user_id)

    rating = await get_public_top(100, current_user_id=user_id)
    position = next(
        (
            item["position"]
            for item in rating
            if item["is_me"]
        ),
        None,
    )

    is_anonymous = (
        profile["is_anonymous"] if profile is not None else True
    )
    telegram_name = get_telegram_name(user)

    display_name = (
        "Аноним"
        if is_anonymous
        else profile["display_name"] or telegram_name
        if profile
        else telegram_name
    )

    return {
        "user_id": user_id,
        "telegram_name": telegram_name,
        "display_name": display_name,
        "is_anonymous": is_anonymous,
        "suggestions_count": suggestions_count,
        "total_submissions": total_submissions,
        "position": position,
        "has_profile": profile is not None,
    }


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    is_anonymous: bool


@app.get("/health")
async def health(response: Response):
    ready = app.state.database_ready
    response.status_code = 200 if ready else 503

    return {
        "status": "ok" if ready else "starting",
        "database_ready": ready,
    }


@app.get("/", include_in_schema=False)
async def index():
    if not INDEX_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail="Фронтенд ещё не установлен. Нужен файл static/index.html.",
        )

    return FileResponse(
        path=INDEX_FILE,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/me")
async def api_me(user: TelegramUser):
    return await build_my_profile(user)


@app.get("/api/rating")
async def api_rating(user: TelegramUser):
    return await get_public_top(10, current_user_id=user["id"])


@app.get("/api/top")
async def api_top(user: TelegramUser):
    return {
        "items": await get_public_top(10, current_user_id=user["id"])
    }


@app.get("/api/submissions")
async def api_submissions(user: TelegramUser):
    submissions = await get_user_submissions(user["id"], limit=50)

    return [
        {
            "id": item["id"],
            "kind": item["kind"],
            "status": item["status"],
            "body": item["body"],
            "created_at": item["created_at"],
            "media": item["media"],
        }
        for item in submissions
    ]


@app.get("/api/media/{file_id}")
async def api_media(file_id: str, user: TelegramUser):
    media = await get_media_owner(file_id)

    if not media or media["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Файл не найден.")

    try:
        file = await bot.get_file(file_id)
        downloaded = await bot.download_file(file.file_path)
        content = downloaded.read()
    except TelegramAPIError:
        logger.exception("Media download failed")
        raise HTTPException(status_code=502, detail="Файл недоступен.")

    content_types = {
        "photo": "image/jpeg",
        "video": "video/mp4",
        "animation": "video/mp4",
        "document": "application/octet-stream",
        "voice": "audio/ogg",
        "audio": "audio/mpeg",
    }

    return Response(
        content=content,
        media_type=content_types.get(
            media["media_type"],
            "application/octet-stream",
        ),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/profile")
async def api_profile(
    payload: ProfileUpdate,
    user: TelegramUser,
):
    await save_user_profile(
        user_id=user["id"],
        display_name=get_telegram_name(user),
        is_anonymous=payload.is_anonymous,
    )

    return {
        "ok": True,
        "profile": await build_my_profile(user),
    }
