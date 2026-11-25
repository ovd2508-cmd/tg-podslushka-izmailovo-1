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
from aiogram.types import (Message, CallbackQuery, ErrorEvent, BotCommand,
    BotCommandScopeAllPrivateChats, MenuButtonCommands, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo)

from config import BOT_TOKEN, ADMIN_CHAT_ID, CHANNEL_URL, FLOOD_SECONDS, MINI_APP_URL
from db import (init_db, get_last_sent_at, record_submission, get_suggestion_count,
    history_summary, list_submissions, get_submission, withdraw_submission, get_reply_target)

logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
locks = {}
SUGGEST = '📝 Отправить предложение'
CONTACT = '💬 Связаться с админами'
ADULT = '🔞 Отправить материалы 18+'
MY = '📂 Мои предложения'
CHANNEL = '📣 Канал'
CANCEL = '↩️ Отменить ввод'
NAMES = {'suggestion':'📝 Предложение','adult':'🔞 18+ — отдельное рассмотрение','contact':'💬 Сообщение админу'}

class States(StatesGroup):
    waiting_for_suggestion = State()
    waiting_for_contact = State()
    waiting_for_adult = State()

DESCRIPTION = (
    '👋 Привет! Это предложка «Подслушано Измайлово».\n\n'
    '🕶 Предложения публикуются анонимно.\n'
    '🔞 Материалы 18+ — через отдельную кнопку, на рассмотрение админов.\n'
    '🤝 Мы НЕ сливаем людей.\n'
    '📎 Есть информация — скидывай пруфы.\n\n'
    'ℹ️ Админы видят ID и ник отправителя для обратной связи. '
    'Анонимность в рейтинге не скрывает эти данные от админов.'
)

def main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=SUGGEST),KeyboardButton(text=CONTACT)],
        [KeyboardButton(text=ADULT)],
        [KeyboardButton(text=MY),KeyboardButton(text=CHANNEL)],
        [KeyboardButton(text=CANCEL)]],resize_keyboard=True,is_persistent=True,
        one_time_keyboard=False,input_field_placeholder='Выбери действие 👇')

def inline(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cb(text,data):
    return InlineKeyboardButton(text=text,callback_data=data)

async def setup_menu():
    await init_db()
    commands = [BotCommand(command=c,description=d) for c,d in [
        ('start','👋 Главное меню'),('suggest','📝 Отправить предложение'),
        ('contact','💬 Связаться с админами'),('adult','🔞 Материалы 18+'),
        ('my','📂 Мои предложения'),('cancel','↩️ Отменить ввод'),('app','📱 Mini App')]]
    await bot.set_my_commands(commands)
    await bot.set_my_commands(commands,scope=BotCommandScopeAllPrivateChats())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await bot.set_my_description(description=DESCRIPTION)
    await bot.set_my_description(description=DESCRIPTION,language_code='ru')
    await bot.set_my_short_description(short_description='📝 Анонимная предложка Измайлово: предложения и связь с админами.')

async def activate(message,state,kind):
    await state.set_state({'suggestion':States.waiting_for_suggestion,
        'contact':States.waiting_for_contact,'adult':States.waiting_for_adult}[kind])
    texts = {
        'suggestion':'📝 Пришли предложение для канала: текст, фото или видео.\n📎 Приложи пруфы, если они есть.',
        'contact':'💬 Напиши вопрос, жалобу или обращение админу.\n📨 Это не заявка на публикацию. Ответ придёт сюда.',
        'adult':'🔞 Пришли материал 18+ для отдельного рассмотрения.\nℹ️ Публикация не гарантируется.'}
    await message.answer(texts[kind]+'\n\n📎 Медиа отправляй по одному, не альбомом.\n↩️ Передумал? Нажми «Отменить ввод».',reply_markup=main_keyboard())

@dp.message(CommandStart(),F.chat.type==ChatType.PRIVATE)
async def start(message:Message,state:FSMContext):
    await state.clear()
    await message.answer('✅ Бот готов!\n👇 Выбери действие на панели внизу.\n📂 В «Моих предложениях» можно посмотреть новые отправки и отозвать заявку.',reply_markup=main_keyboard())

@dp.message(Command('suggest'),F.chat.type==ChatType.PRIVATE)
@dp.message(F.text.in_({SUGGEST,'Отправить предложение'}),F.chat.type==ChatType.PRIVATE)
async def suggest(message:Message,state:FSMContext):
    await activate(message,state,'suggestion')

@dp.message(Command('contact'),F.chat.type==ChatType.PRIVATE)
@dp.message(F.text.in_({CONTACT,'Связаться с админами'}),F.chat.type==ChatType.PRIVATE)
async def contact(message:Message,state:FSMContext):
    await activate(message,state,'contact')

@dp.message(Command('adult'),F.chat.type==ChatType.PRIVATE)
@dp.message(F.text.in_({ADULT,'Отправить материалы 18+'}),F.chat.type==ChatType.PRIVATE)
async def adult(message:Message,state:FSMContext):
    await activate(message,state,'adult')

@dp.message(Command('cancel'),F.chat.type==ChatType.PRIVATE)
@dp.message(F.text==CANCEL,F.chat.type==ChatType.PRIVATE)
async def cancel(message:Message,state:FSMContext):
    await state.clear()
    await message.answer('↩️ Ввод отменён. Уже отправленные заявки это не отзывает.\n👇 Выбери действие.',reply_markup=main_keyboard())

@dp.message(F.text.in_({CHANNEL,'Канал'}),F.chat.type==ChatType.PRIVATE)
async def channel(message:Message,state:FSMContext):
    await state.clear()
    await message.answer('📣 Наш канал:\n'+CHANNEL_URL,reply_markup=main_keyboard())

async def show_history(message,user_id,offset=0):
    summary = await history_summary(user_id)
    total = await get_suggestion_count(user_id)
    regular = summary.get('suggestion',(0,0))
    adult = summary.get('adult',(0,0))
    contacts = summary.get('contact',(0,0))
    rows = await list_submissions(user_id,offset,6)
    text = (f'📂 Мои предложения\n\n📝 Отправлено предложений: {regular[0]}\n'
        f'🔞 Отправлено 18+: {adult[0]}\n💬 Обращений админу: {contacts[0]}\n'
        f'↩️ Отозвано заявок: {regular[1]+adult[1]}\n'
        f'📊 Общий счётчик предложений, включая прежние: {total}\n\n'
        'ℹ️ Категории и история доступны для отправок после этого обновления. '
        'Прежние счётчики сохранены; тексты старых отправок не восстанавливаются.\n'
        '👇 Выбери отправку:')
    buttons = []
    for r in rows[:5]:
        preview = ' '.join(r['body'].split())[:32] or '📎 Медиа'
        mark = '↩️' if r['status']=='withdrawn' else '📨'
        buttons.append([cb(f"{mark} #{r['id']} {preview}",f"view:{r['id']}")])
    nav=[]
    if offset: nav.append(cb('⬅️ Назад',f'history:{max(0,offset-5)}'))
    if len(rows)>5: nav.append(cb('Далее ➡️',f'history:{offset+5}'))
    if nav: buttons.append(nav)
    if not rows: text+='\n📭 Здесь пока пусто.'
    await message.answer(text,reply_markup=inline(buttons) if buttons else None)

@dp.message(Command('my','stats'),F.chat.type==ChatType.PRIVATE)
@dp.message(F.text.in_({MY,'Мои предложения'}),F.chat.type==ChatType.PRIVATE)
async def mine(message:Message,state:FSMContext):
    await state.clear()
    await message.answer('📂 Открываю твои отправки…',reply_markup=main_keyboard())
    await show_history(message,message.from_user.id)

@dp.message(Command('app'),F.chat.type==ChatType.PRIVATE)
async def miniapp(message:Message,state:FSMContext):
    await state.clear()
    if not MINI_APP_URL:
        await message.answer('📱 Mini App пока не подключён.',reply_markup=main_keyboard())
        return
    await message.answer('📱 Рейтинг и профиль:',reply_markup=inline([
        [InlineKeyboardButton(text='📱 Открыть Mini App',web_app=WebAppInfo(url=MINI_APP_URL))]]))

async def combined(chat_id,source,prefix):
    is_text = source.text is not None
    if not is_text and not (source.photo or source.video or source.animation or source.document or source.voice or source.audio):
        raise ValueError('📎 Отправь текст, фото, видео, GIF, файл, аудио или голосовое.')
    body = source.text if is_text else source.caption or ''
    text = prefix + ('\n\n'+body if body else '')
    limit=4096 if is_text else 1024
    if len(text.encode('utf-16-le'))//2>limit:
        raise ValueError('✂️ Слишком длинный текст или подпись. Сократи и отправь снова.')
    if is_text:
        result=await bot.send_message(chat_id,text)
    else:
        result=await bot.copy_message(chat_id,source.chat.id,source.message_id,caption=text,parse_mode=None)
    return result.message_id

async def deliver(message,state,kind):
    if message.media_group_id:
        await message.answer('📎 Альбом не отправлен. Отправляй медиа по одному.'); return
    if message.text and message.text.startswith('/'):
        await message.answer('❓ Неизвестная команда. Выбери действие на панели.'); return
    uid=message.from_user.id
    async with locks.setdefault(uid,asyncio.Lock()):
        last=await get_last_sent_at(uid)
        left=FLOOD_SECONDS-max(0,time.time()-last) if last is not None else 0
        if left>0:
            await message.answer(f'⏳ Подожди {math.ceil(left)} сек.'); return
        user=message.from_user
        name='@'+user.username if user.username else ' '.join(user.full_name.split())
        stamp=datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
        prefix=f'{NAMES[kind]}\nОт: {name} (ID: {uid})\nВремя: {stamp}'
        try:
            mid=await combined(ADMIN_CHAT_ID,message,prefix)
        except ValueError as e:
            await message.answer(str(e)); return
        except TelegramAPIError:
            logger.exception('Delivery failed')
            await message.answer('⚠️ Не удалось подтвердить доставку. Попробуй позже.'); return
        try:
            sid=await record_submission(uid,kind,mid,message.text or message.caption or '📎 Медиа')
        except Exception:
            logger.exception('Delivered but DB failed; user=%s message=%s',uid,mid)
            await state.clear()
            await message.answer('⚠️ Доставлено, но сохранить историю не удалось. Повторно не отправляй.',reply_markup=main_keyboard()); return
        await state.clear()
        buttons=[[cb('📂 Посмотреть отправку',f'view:{sid}')]]
        if kind!='contact': buttons.append([cb('↩️ Отозвать заявку',f'ask:{sid}')])
        await message.answer(f'✅ Отправлено! Номер отправки: #{sid}',reply_markup=main_keyboard())
        await message.answer('📂 Управление отправкой:',reply_markup=inline(buttons))

@dp.message(States.waiting_for_suggestion,F.chat.type==ChatType.PRIVATE)
async def receive_suggestion(message:Message,state:FSMContext):
    await deliver(message,state,'suggestion')

@dp.message(States.waiting_for_contact,F.chat.type==ChatType.PRIVATE)
async def receive_contact(message:Message,state:FSMContext):
    await deliver(message,state,'contact')

@dp.message(States.waiting_for_adult,F.chat.type==ChatType.PRIVATE)
async def receive_adult(message:Message,state:FSMContext):
    await deliver(message,state,'adult')

@dp.callback_query(F.data.startswith('history:'))
async def page(call:CallbackQuery):
    await call.answer()
    if not isinstance(call.message,Message) or call.message.chat.type!=ChatType.PRIVATE: return
    try: offset=max(0,int(call.data.split(':')[1]))
    except ValueError: return
    await show_history(call.message,call.from_user.id,offset)

@dp.callback_query(F.data.startswith('view:'))
async def view(call:CallbackQuery):
    if not isinstance(call.message,Message) or call.message.chat.type!=ChatType.PRIVATE:
        await call.answer(); return
    r=await get_submission(int(call.data.split(':')[1]),call.from_user.id)
    if not r:
        await call.answer('Отправка не найдена',show_alert=True); return
    await call.answer()
    stamp=datetime.fromtimestamp(r['created_at'],timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
    status='↩️ Отозвана' if r['status']=='withdrawn' else '📨 Доставлена админам'
    await call.message.answer(f"📂 Отправка #{r['id']}\n{NAMES[r['kind']]}\n🕒 {stamp}\n{status}")
    try:
        await bot.copy_message(call.from_user.id,ADMIN_CHAT_ID,r['admin_message_id'])
    except TelegramAPIError:
        await call.message.answer('⚠️ Оригинал недоступен. Сохранённый текст:\n'+r['body'][:3500])
    buttons=[]
    if r['status']=='sent' and r['kind']!='contact':
        buttons.append([cb('↩️ Отозвать заявку',f"ask:{r['id']}")])
    buttons.append([cb('📂 К списку','history:0')])
    await call.message.answer('👇 Действия:',reply_markup=inline(buttons))

@dp.callback_query(F.data.startswith('ask:'))
async def ask(call:CallbackQuery):
    if not isinstance(call.message,Message) or call.message.chat.type!=ChatType.PRIVATE:
        await call.answer(); return
    r=await get_submission(int(call.data.split(':')[1]),call.from_user.id)
    if not r or r['kind']=='contact' or r['status']!='sent':
        await call.answer('Заявка недоступна для отзыва',show_alert=True); return
    await call.answer()
    await call.message.answer('↩️ Отозвать заявку?\nАдмины получат уведомление. '
        'Уже опубликованное это не удалит. Счётчик отправленных заявок не уменьшается.',
        reply_markup=inline([[cb('✅ Да, отозвать',f"withdraw:{r['id']}")],
            [cb('Оставить заявку',f"view:{r['id']}")]]))

@dp.callback_query(F.data.startswith('withdraw:'))
async def withdraw(call:CallbackQuery):
    if not isinstance(call.message,Message) or call.message.chat.type!=ChatType.PRIVATE:
        await call.answer(); return
    sid=int(call.data.split(':')[1])
    r=await get_submission(sid,call.from_user.id)
    if not r or not await withdraw_submission(sid,call.from_user.id):
        await call.answer('Заявка уже отозвана или недоступна',show_alert=True); return
    await call.answer('Заявка отозвана')
    notice=f"↩️ Отправитель отозвал заявку #{sid}.\nID исходного сообщения: {r['admin_message_id']}.\nНе публикуйте её без повторного согласования."
    notified=False
    try:
        await bot.send_message(ADMIN_CHAT_ID,notice,reply_to_message_id=r['admin_message_id'],allow_sending_without_reply=True)
        notified=True
    except TelegramAPIError:
        logger.exception('Withdrawal notification failed')
    await call.message.answer('↩️ Заявка помечена отозванной. '+('Админы уведомлены.' if notified else
        '⚠️ Уведомить админов не удалось. Напиши им через «Связаться с админами».'),reply_markup=main_keyboard())
    await call.message.edit_reply_markup(reply_markup=inline([[cb('📂 К списку','history:0')]]))

@dp.message(F.chat.id==ADMIN_CHAT_ID,F.reply_to_message)
async def reply_admin(message:Message):
    target=await get_reply_target(message.reply_to_message.message_id)
    if not target: return
    if message.sender_chat or not message.from_user:
        await message.reply('👤 Ответь от своего аккаунта администратора.'); return
    member=await bot.get_chat_member(ADMIN_CHAT_ID,message.from_user.id)
    if member.status not in {'creator','administrator'}: return
    if target[1]=='withdrawn':
        await message.reply('↩️ Заявка отозвана. Для переписки дождитесь нового обращения пользователя.'); return
    if message.media_group_id:
        await message.reply('📎 Ответ отправляй не альбомом.'); return
    try:
        await combined(target[0],message,'💬 Ответ админа')
    except ValueError as e:
        await message.reply(str(e)); return
    except TelegramAPIError:
        logger.exception('Reply failed')
        await message.reply('⚠️ Не удалось подтвердить доставку ответа.'); return
    await message.reply('✅ Ответ отправлен.')

@dp.message(F.chat.type==ChatType.PRIVATE)
async def fallback(message:Message):
    await message.answer('👇 Выбери действие на панели внизу.',reply_markup=main_keyboard())

@dp.errors()
async def errors(event:ErrorEvent):
    e=event.exception
    logger.error('Update error',exc_info=(type(e),e,e.__traceback__))
    return True
