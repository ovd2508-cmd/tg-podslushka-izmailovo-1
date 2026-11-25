import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Response,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from config import (
    BASE_DIR,
    BOT_TOKEN,
    MINI_APP_AUTH_MAX_AGE,
)
from db import (
    get_public_top,
    get_suggestion_count,
    get_user_profile,
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

# Папка может ещё отсутствовать при первом запуске.
# Поэтому проверку её существования откладываем.
app.mount(
    "/static",
    StaticFiles(
        directory=str(STATIC_DIR),
        check_dir=False,
    ),
    name="static",
)


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"

    # Не разрешаем кэшировать персональные ответы API.
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    return response


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
    )


def validate_init_data(raw_init_data: str) -> dict:
    """
    Проверить Telegram WebApp.initData.

    Возвращает только проверенные данные пользователя.
    """
    if not raw_init_data:
        raise unauthorized(
            "Открой приложение через кнопку в Telegram-боте."
        )

    if len(raw_init_data) > 16384:
        raise unauthorized(
            "Слишком большой объём данных авторизации."
        )

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
        raise unauthorized(
            "Некорректный формат данных авторизации."
        )

    # Не принимаем неоднозначные параметры,
    # например два разных поля user или hash.
    keys = [key for key, _ in pairs]

    if len(keys) != len(set(keys)):
        raise unauthorized(
            "Дублирующиеся параметры авторизации."
        )

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
        raise unauthorized(
            "Отсутствует или повреждена подпись Telegram."
        )

    # Для проверки HMAC с токеном исключаем только hash.
    # Если Telegram передал signature, оно остаётся
    # частью проверяемой строки.
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

    if not hmac.compare_digest(
        expected_hash,
        received_hash.lower(),
    ):
        raise unauthorized(
            "Подпись Telegram не прошла проверку."
        )

    try:
        auth_date = int(data["auth_date"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized(
            "Отсутствует корректное время авторизации."
        )

    now = time.time()

    if auth_date > now + 60:
        raise unauthorized(
            "Некорректное время авторизации. "
            "Проверь время на сервере."
        )

    if now - auth_date > MINI_APP_AUTH_MAX_AGE:
        raise unauthorized(
            "Сессия истекла. "
            "Закрой приложение и открой его снова через бота."
        )

    try:
        user = json.loads(data["user"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized(
            "В авторизации отсутствует пользователь Telegram."
        )

    if not isinstance(user, dict):
        raise unauthorized(
            "Некорректные данные пользователя."
        )

    user_id = user.get("id")

    if type(user_id) is not int or user_id <= 0:
        raise unauthorized(
            "Некорректный ID пользователя Telegram."
        )

    return user


async def current_user(
    authorization: Annotated[
        str | None,
        Header(),
    ] = None,
) -> dict:
    """
    Фронтенд должен отправлять заголовок:

    Authorization: tma <Telegram.WebApp.initData>
    """
    if not authorization:
        raise unauthorized(
            "Открой приложение из Telegram-бота."
        )

    scheme, separator, credentials = (
        authorization.partition(" ")
    )

    if (
        not separator
        or scheme.lower() != "tma"
        or not credentials
    ):
        raise unauthorized(
            "Неверный формат заголовка авторизации."
        )

    return validate_init_data(credentials)


TelegramUser = Annotated[
    dict,
    Depends(current_user),
]


def get_telegram_name(user: dict) -> str:
    """
    Берём ник или имя только из проверенных данных Telegram,
    а не из произвольного текста, присланного фронтендом.
    """
    username = user.get("username")

    if isinstance(username, str) and username.strip():
        return f"@{username.strip()}"[:64]

    name_parts = []

    for field in ("first_name", "last_name"):
        value = user.get(field)

        if isinstance(value, str):
            name_parts.extend(value.split())

    return (
        " ".join(name_parts)[:64]
        or "Пользователь"
    )


async def build_my_profile(user: dict) -> dict:
    user_id = user["id"]

    profile = await get_user_profile(user_id)
    count = await get_suggestion_count(user_id)

    # Пользователь без сохранённого профиля
    # отображается в рейтинге анонимно.
    is_anonymous = (
        profile["is_anonymous"]
        if profile is not None
        else True
    )

    telegram_name = get_telegram_name(user)

    if is_anonymous:
        display_name = "Аноним"
    else:
        display_name = (
            profile["display_name"]
            or telegram_name
        )

    return {
        "telegram_name": telegram_name,
        "display_name": display_name,
        "is_anonymous": is_anonymous,
        "suggestions_count": count,
        "has_profile": profile is not None,
    }


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

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
            detail=(
                "Фронтенд ещё не установлен. "
                "Нужен файл static/index.html."
            ),
        )

    return FileResponse(
        path=INDEX_FILE,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/me")
async def api_me(user: TelegramUser):
    return await build_my_profile(user)


@app.get("/api/top")
async def api_top(user: TelegramUser):
    # Авторизация нужна даже для чтения рейтинга.
    # Персональные ID участников в ответ не включаем.
    return {
        "items": await get_public_top(10),
    }


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