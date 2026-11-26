import asyncio
import logging
import math
import time
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ErrorEvent, BotCommand,
    BotCommandScopeAllPrivateChats, MenuButtonCommands, MenuButtonWebApp,
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, WebAppInfo,
)

from config import BOT_TOKEN, ADMIN_CHAT_ID, CHANNEL_URL, FLOOD_SECONDS, MINI_APP_URL
from db import (
    init_db, get_last_sent_at, record_submission, get_suggestion_count,
    history_summary, list_submissions, get_submission, withdraw_submission,
    get_reply_target,
)

logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
locks = {}

SUGGEST = "📝 Предложить новость"
CONTACT = "💬 Написать админам"
ADULT = "🔞 Материалы 18+"
MY = "📂 Мои отправки"
CHANNEL = "📣 Канал"
CANCEL = "↩️ Отменить"

NAMES = {
    "suggestion": "📝 Предложение для канала",
    "adult": "🔞 Материал 18+",
    "contact": "💬 Сообщение админам",
}

class States(StatesGroup):
    waiting_for_suggestion = State()
    waiting_for_contact = State()
    waiting_for_adult = State()

DESCRIPTION = (
    "Привет! Это предложка «Подслушано Измайлово».\n\n"
    "Здесь можно анонимно рассказать о событиях, проблемах, историях "
    "или интересных находках в районе.\n\n"
    "Если есть фото, видео или другие подтверждения — прикрепляй их к сообщению.\n\n"
    "Для материалов 18+ есть отдельная кнопка. Такие отправки сначала рассматривают админы.\n\n"
    "Админы видят ID и ник отправителя, чтобы можно было ответить или уточнить детали. "
    "В публичных публикациях эти данные не раскрываются."
)

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SUGGEST), KeyboardButton(text=CONTACT)],
            [KeyboardButton(text=ADULT)],
            [KeyboardButton(text=MY), KeyboardButton(text=CHANNEL)],
            [KeyboardButton(text=CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери, что нужно сделать",
    )

def inline(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cb(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)

async def setup_menu():
    await init_db()

    commands = [
        BotCommand(command="start", description="Открыть меню"),
        BotCommand(command="suggest", description="Предложить новость"),
        BotCommand(command="contact", description="Написать админам"),
        BotCommand(command="adult", description="Материалы 18+"),
        BotCommand(command="my", description="Мои отправки"),
        BotCommand(command="cancel", description="Отменить ввод"),
        BotCommand(command="app", description="Открыть Mini App"),
    ]

    await bot.set_my_commands(commands)
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())

    if MINI_APP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Open App",
                web_app=WebAppInfo(url=MINI_APP_URL),
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    await bot.set_my_description(description=DESCRIPTION)
    await bot.set_my_description(description=DESCRIPTION, language_code="ru")
    await bot.set_my_short_description(
        short_description="Анонимная предложка Измайлово: новости, истории и связь с админами."
    )

async def activate(message, state, kind):
    states = {
        "suggestion": States.waiting_for_suggestion,
        "contact": States.waiting_for_contact,
        "adult": States.waiting_for_adult,
    }

    texts = {
        "suggestion": "Расскажи, что произошло или что хочешь предложить для канала.\n\nМожно отправить текст, фото, видео, голосовое или файл.\nЕсли есть подтверждение — приложи его отдельным сообщением.",
        "contact": "Напиши админам: вопрос, жалобу, идею или уточнение по отправке.\n\nЭто не заявка на публикацию. Ответ придёт в этот чат.",
        "adult": "Отправь материал 18+ для проверки админами.\n\nПубликация не гарантирована: админы сначала посмотрят отправку.",
    }

    await state.set_state(states[kind])
    await message.answer(
        texts[kind] + "\n\nОтправляй медиа по одному сообщению — альбомы пока не поддерживаются.\nПередумал — нажми «↩️ Отменить».",
        reply_markup=main_keyboard(),
    )

@dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start(message, state):
    await state.clear()
    await message.answer("Привет! Что хочешь сделать?", reply_markup=main_keyboard())

@dp.message(Command("suggest"), F.chat.type == ChatType.PRIVATE)
@dp.message(F.text.in_({SUGGEST, "Отправить предложение"}), F.chat.type == ChatType.PRIVATE)
async def suggest(message, state):
    await activate(message, state, "suggestion")

@dp.message(Command("contact"), F.chat.type == ChatType.PRIVATE)
@dp.message(F.text.in_({CONTACT, "Связаться с админами"}), F.chat.type == ChatType.PRIVATE)
async def contact(message, state):
    await activate(message, state, "contact")

@dp.message(Command("adult"), F.chat.type == ChatType.PRIVATE)
@dp.message(F.text.in_({ADULT, "Отправить материалы 18+"}), F.chat.type == ChatType.PRIVATE)
async def adult(message, state):
    await activate(message, state, "adult")

@dp.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
@dp.message(F.text == CANCEL, F.chat.type == ChatType.PRIVATE)
async def cancel(message, state):
    await state.clear()
    await message.answer("Отменил. Уже отправленные материалы это не затрагивает.", reply_markup=main_keyboard())

@dp.message(F.text.in_({CHANNEL, "Канал"}), F.chat.type == ChatType.PRIVATE)
async def channel(message, state):
    await state.clear()
    await message.answer(f"Наш канал:\n{CHANNEL_URL}", reply_markup=main_keyboard())

async def show_history(message, user_id, offset=0):
    summary = await history_summary(user_id)
    total = await get_suggestion_count(user_id)

    regular = summary.get("suggestion", (0, 0))
    adult = summary.get("adult", (0, 0))
    contacts = summary.get("contact", (0, 0))
    rows = await list_submissions(user_id, offset, 6)

    text = (
        "📂 Мои отправки\n\n"
        f"📝 Предложений: {regular[0]}\n"
        f"🔞 Материалов 18+: {adult[0]}\n"
        f"💬 Сообщений админам: {contacts[0]}\n"
        f"↩️ Отозвано заявок: {regular[1] + adult[1]}\n"
        f"📊 Всего предложений: {total}\n\n"
        "Здесь видны отправки после обновления бота. Старые счётчики сохранены, но их тексты восстановить нельзя.\n\n"
        "Выбери отправку:"
    )

    buttons = []

    for row in rows[:5]:
        preview = " ".join(row["body"].split())[:32] or "📎 Медиа"
        mark = "↩️" if row["status"] == "withdrawn" else "📨"
        buttons.append([cb(f"{mark} #{row['id']} · {preview}", f"view:{row['id']}")])

    navigation = []

    if offset:
        navigation.append(cb("⬅️ Назад", f"history:{max(0, offset - 5)}"))

    if len(rows) > 5:
        navigation.append(cb("Дальше ➡️", f"history:{offset + 5}"))

    if navigation:
        buttons.append(navigation)

    if not rows:
        text += "\nПока ничего не отправлено."

    await message.answer(text, reply_markup=inline(buttons) if buttons else None)

@dp.message(Command("my", "stats"), F.chat.type == ChatType.PRIVATE)
@dp.message(F.text.in_({MY, "Мои предложения"}), F.chat.type == ChatType.PRIVATE)
async def mine(message, state):
    await state.clear()
    await show_history(message, message.from_user.id)

@dp.message(Command("app"), F.chat.type == ChatType.PRIVATE)
async def miniapp(message, state):
    await state.clear()

    if not MINI_APP_URL:
        await message.answer("Mini App пока не подключён.", reply_markup=main_keyboard())
        return

    await message.answer(
        "Здесь можно посмотреть рейтинг и профиль:",
        reply_markup=inline([
            [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url=MINI_APP_URL))]
        ]),
    )

async def combined(chat_id, source, prefix):
    is_text = source.text is not None

    if not is_text and not (
        source.photo or source.video or source.animation
        or source.document or source.voice or source.audio
    ):
        raise ValueError("Отправь текст, фото, видео, GIF, файл, аудио или голосовое.")

    body = source.text if is_text else source.caption or ""
    text = prefix + ("\n\n" + body if body else "")
    limit = 4096 if is_text else 1024

    if len(text.encode("utf-16-le")) // 2 > limit:
        raise ValueError("Сообщение слишком длинное. Сократи текст или подпись и отправь снова.")

    if is_text:
        result = await bot.send_message(chat_id, text)
    else:
        result = await bot.copy_message(
            chat_id, source.chat.id, source.message_id, caption=text, parse_mode=None
        )

    return result.message_id

def collect_media(message):
    media = []

    if message.photo:
        media.append({"type": "photo", "file_id": message.photo[-1].file_id})

    if message.video:
        media.append({"type": "video", "file_id": message.video.file_id})

    if message.animation:
        media.append({"type": "animation", "file_id": message.animation.file_id})

    if message.document:
        media.append({"type": "document", "file_id": message.document.file_id})

    if message.voice:
        media.append({"type": "voice", "file_id": message.voice.file_id})

    if message.audio:
        media.append({"type": "audio", "file_id": message.audio.file_id})

    return media

async def deliver(message, state, kind):
    if message.media_group_id:
        await message.answer("Альбомы пока не поддерживаются. Отправь фото или видео по одному.")
        return

    if message.text and message.text.startswith("/"):
        await message.answer("Такой команды нет. Выбери действие на панели внизу.")
        return

    user_id = message.from_user.id

    async with locks.setdefault(user_id, asyncio.Lock()):
        last = await get_last_sent_at(user_id)
        left = FLOOD_SECONDS - max(0, time.time() - last) if last is not None else 0

        if left > 0:
            await message.answer(f"Подожди ещё {math.ceil(left)} сек. перед следующей отправкой.")
            return

        user = message.from_user
        name = "@" + user.username if user.username else " ".join(user.full_name.split())
        stamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

        prefix = f"{NAMES[kind]}\nОт: {name} (ID: {user_id})\nВремя: {stamp}"

        try:
            admin_message_id = await combined(ADMIN_CHAT_ID, message, prefix)
        except ValueError as error:
            await message.answer(str(error))
            return
        except TelegramAPIError:
            logger.exception("Delivery failed")
            await message.answer("Не получилось отправить сообщение. Попробуй ещё раз чуть позже.")
            return

        media = collect_media(message)

        try:
            submission_id = await record_submission(
                user_id,
                kind,
                admin_message_id,
                message.text or message.caption or "📎 Медиа",
                media,
            )
        except Exception:
            logger.exception("Delivered but DB failed; user=%s message=%s", user_id, admin_message_id)
            await state.clear()
            await message.answer(
                "Сообщение доставлено, но сохранить его в истории не удалось. Не отправляй его повторно.",
                reply_markup=main_keyboard(),
            )
            return

        await state.clear()

        buttons = [[cb("📂 Посмотреть отправку", f"view:{submission_id}")]]

        if kind != "contact":
            buttons.append([cb("↩️ Отозвать заявку", f"ask:{submission_id}")])

        await message.answer(
            f"Отправлено. Номер отправки: #{submission_id}",
            reply_markup=main_keyboard(),
        )
        await message.answer("Что сделать с этой отправкой?", reply_markup=inline(buttons))

@dp.message(States.waiting_for_suggestion, F.chat.type == ChatType.PRIVATE)
async def receive_suggestion(message, state):
    await deliver(message, state, "suggestion")

@dp.message(States.waiting_for_contact, F.chat.type == ChatType.PRIVATE)
async def receive_contact(message, state):
    await deliver(message, state, "contact")

@dp.message(States.waiting_for_adult, F.chat.type == ChatType.PRIVATE)
async def receive_adult(message, state):
    await deliver(message, state, "adult")

@dp.callback_query(F.data.startswith("history:"))
async def page(call):
    await call.answer()

    if not isinstance(call.message, Message) or call.message.chat.type != ChatType.PRIVATE:
        return

    try:
        offset = max(0, int(call.data.split(":")[1]))
    except ValueError:
        return

    await show_history(call.message, call.from_user.id, offset)

@dp.callback_query(F.data.startswith("view:"))
async def view(call):
    if not isinstance(call.message, Message) or call.message.chat.type != ChatType.PRIVATE:
        await call.answer()
        return

    submission = await get_submission(int(call.data.split(":")[1]), call.from_user.id)

    if not submission:
        await call.answer("Отправка не найдена.", show_alert=True)
        return

    await call.answer()

    stamp = datetime.fromtimestamp(submission["created_at"], timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    status = "↩️ Отозвана" if submission["status"] == "withdrawn" else "📨 Отправлена админам"

    await call.message.answer(
        f"📂 Отправка #{submission['id']}\n{NAMES[submission['kind']]}\n🕒 {stamp}\n{status}"
    )

    try:
        await bot.copy_message(call.from_user.id, ADMIN_CHAT_ID, submission["admin_message_id"])
    except TelegramAPIError:
        await call.message.answer(
            "Оригинал недоступен. Сохранённый текст:\n" + submission["body"][:3500]
        )

    buttons = []

    if submission["status"] == "sent" and submission["kind"] != "contact":
        buttons.append([cb("↩️ Отозвать заявку", f"ask:{submission['id']}")])

    buttons.append([cb("📂 К списку", "history:0")])

    await call.message.answer("Что сделать?", reply_markup=inline(buttons))

@dp.callback_query(F.data.startswith("ask:"))
async def ask(call):
    if not isinstance(call.message, Message) or call.message.chat.type != ChatType.PRIVATE:
        await call.answer()
        return

    submission = await get_submission(int(call.data.split(":")[1]), call.from_user.id)

    if not submission or submission["kind"] == "contact" or submission["status"] != "sent":
        await call.answer("Эту отправку нельзя отозвать.", show_alert=True)
        return

    await call.answer()

    await call.message.answer(
        "Отозвать эту заявку?\n\nАдмины получат уведомление. Если материал уже опубликован, он не удалится автоматически. Счётчик отправленных предложений не уменьшится.",
        reply_markup=inline([
            [cb("✅ Да, отозвать", f"withdraw:{submission['id']}")],
            [cb("Оставить", f"view:{submission['id']}")],
        ]),
    )

@dp.callback_query(F.data.startswith("withdraw:"))
async def withdraw(call):
    if not isinstance(call.message, Message) or call.message.chat.type != ChatType.PRIVATE:
        await call.answer()
        return

    submission_id = int(call.data.split(":")[1])
    submission = await get_submission(submission_id, call.from_user.id)

    if not submission or not await withdraw_submission(submission_id, call.from_user.id):
        await call.answer("Заявка уже отозвана или недоступна.", show_alert=True)
        return

    await call.answer("Заявка отозвана")

    notice = (
        f"↩️ Отправитель отозвал заявку #{submission_id}.\n"
        f"ID исходного сообщения: {submission['admin_message_id']}.\n"
        "Не публикуйте её без повторного согласования."
    )

    notified = False

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            notice,
            reply_to_message_id=submission["admin_message_id"],
            allow_sending_without_reply=True,
        )
        notified = True
    except TelegramAPIError:
        logger.exception("Withdrawal notification failed")

    await call.message.answer(
        "Заявка отозвана. " + (
            "Админы уведомлены." if notified
            else "Не удалось уведомить админов. Напиши им через «💬 Написать админам»."
        ),
        reply_markup=main_keyboard(),
    )

    await call.message.edit_reply_markup(
        reply_markup=inline([[cb("📂 К списку", "history:0")]])
    )

@dp.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def reply_admin(message):
    target = await get_reply_target(message.reply_to_message.message_id)

    if not target:
        return

    if message.sender_chat or not message.from_user:
        await message.reply("Ответь от своего личного аккаунта администратора.")
        return

    member = await bot.get_chat_member(ADMIN_CHAT_ID, message.from_user.id)

    if member.status not in {"creator", "administrator"}:
        return

    if target[1] == "withdrawn":
        await message.reply("Заявка отозвана. Для переписки дождись нового обращения пользователя.")
        return

    if message.media_group_id:
        await message.reply("Ответ нужно отправить одним сообщением, не альбомом.")
        return

    try:
        await combined(target[0], message, "💬 Ответ админа")
    except ValueError as error:
        await message.reply(str(error))
        return
    except TelegramAPIError:
        logger.exception("Reply failed")
        await message.reply("Не получилось отправить ответ.")
        return

    await message.reply("Ответ отправлен.")

@dp.message(F.chat.type == ChatType.PRIVATE)
async def fallback(message):
    await message.answer("Выбери действие на панели внизу.", reply_markup=main_keyboard())

@dp.errors()
async def errors(event):
    exception = event.exception
    logger.error("Update error", exc_info=(type(exception), exception, exception.__traceback__))
    return True
