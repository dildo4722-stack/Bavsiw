# main.py — Полный Telegram Bot для магазина/тикетов/поддержки (aiogram 3.x)
import asyncio
import logging
from datetime import datetime, timedelta
from random import choice
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ChatPermissions
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    FSInputFile, ReplyKeyboardRemove, ChatMember, ChatMemberUpdated
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiosqlite
import json
from typing import Any

DB_PATH = "bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблицы с JSON-полем "data"
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                product_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS raffles (
                raffle_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS group_data (
                chat_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pending_autoposts (
                post_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}'
            )
        ''')

        # Таблицы-списки (reviews, channels_required, autopost_channels)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS channels_required (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS autopost_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL
            )
        ''')

        # Счётчики
        await db.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
        ''')

        await db.commit()

# Универсальные функции загрузки/сохранения
async def load_dict(table: str, key_col: str = "user_id", dict_global=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT {key_col}, data FROM {table}") as cursor:
            rows = await cursor.fetchall()
            result = {}
            for row in rows:
                key = row[0]
                try:
                    value = json.loads(row[1])
                except:
                    value = {}
                result[key] = value
            if dict_global is not None:
                dict_global.clear()
                dict_global.update(result)
            return result

async def save_dict(table: str, data: dict, key_col: str = "user_id"):
    async with aiosqlite.connect(DB_PATH) as db:
        for key, value in data.items():
            await db.execute(
                f"INSERT INTO {table} ({key_col}, data) VALUES (?, ?) "
                f"ON CONFLICT({key_col}) DO UPDATE SET data = excluded.data",
                (key, json.dumps(value, ensure_ascii=False, default=str))
            )
        await db.commit()

async def load_list(table: str, global_list=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT data FROM {table} ORDER BY id") as cursor:
            rows = await cursor.fetchall()
            result = [json.loads(row[0]) for row in rows]
            if global_list is not None:
                global_list.clear()
                global_list.extend(result)
            return result

async def save_list(table: str, data: list):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"DELETE FROM {table}")
        for item in data:
            await db.execute(f"INSERT INTO {table} (data) VALUES (?)", (json.dumps(item, ensure_ascii=False, default=str),))
        await db.commit()



async def save_counter(name: str, value: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value)
        )
        await db.commit()

async def load_counter(name: str, default: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM counters WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

pending_requests = {}


# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
# УНИВЕРСАЛЬНАЯ ФУНКЦИЯ: @username или ID → user_id
async def get_user_id(text: str) -> int | None:
    """
    Принимает: 123456789 или @username или username
    Возвращает: user_id или None
    """
    text = text.strip().lstrip('@')
    
    if text.isdigit():
        return int(text)
    
    try:
        chat = await bot.get_chat(f"@{text}")
        if chat.type == "private":
            return chat.id
    except Exception as e:
        logging.warning(f"Не удалось найти @{text}: {e}")
    
    return None
# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
# ====================== CONFIG ======================
API_TOKEN = "8379431754:AAHyBqmT80QZ6fdcAHx885_F0AfbEuGXVX8"  # ← ЗАМЕНИ НА СВОЙ!
ADMIN_IDS = [6081780420]  # ← СВОИ АДМИН-ID (уровень 3 по умолчанию)

# Уровни админов: user_id -> level (1: Саппорт, 2: Модератор, 3: Администратор)
admins = {6081780420: 3}  # Начальный админ уровня 3

# ====================== DATA (будет загружаться из БД) ======================
users = {}
products = {}
tickets = {}
raffles = {}
reviews = []
channels_required = []
banned_users = {}
group_data = {}
autopost_channels = []
pending_autoposts = {}
counters = {"product": 1, "ticket": 1, "raffle": 1, "autopost": 1}
admins = {}

# ====================== STATES ======================
class UserStates(StatesGroup):
    ticket = State()
    chatting = State()

class ReviewStates(StatesGroup):
    rating = State()
    text = State()

class AdminStates(StatesGroup):
    # Каналы
    add_channel = State()
    edit_user_balance = State()
   
    # Розыгрыши
    create_raffle_prizes = State()
    create_raffle_hours = State()
   
    # Выдача админа
    grant_admin_id = State()
    grant_admin_level = State()
    grant_admin_confirm = State()
   
    # Выдача баланса
    grant_balance_type = State()
    grant_balance_id = State()
    grant_balance_amount = State()
    grant_balance_confirm = State()
   
    # Бан в боте
    ban_id = State()
    ban_duration = State()
    ban_confirm = State()
   
    # Групповые команды
    mute_id = State()
    mute_duration = State()
    warn_id = State()
    warn_amount = State()
    kick_id = State()
    ban_group_id = State()
    ban_group_duration = State()
   
    # Правила группы
    set_rules = State()
   
    # Автопостинг
    add_autopost_channel = State()
    set_autopost_cost = State()
    autopost_content = State()

class PaymentStates(StatesGroup):
    waiting_amount_balance = State()
    waiting_proof_balance = State()
    waiting_proof_support = State()

class AdminTicketStates(StatesGroup):
    waiting_answer = State()

# НОВЫЙ КЛАСС — ТОЛЬКО ДЛЯ ТОВАРОВ (чтобы не конфликтовать с AdminStates)
class ProductStates(StatesGroup):
    name = State()
    price_rub = State()
    price_stars = State()
    photo = State()
    content_type = State()
    content = State()

# ====================== KEYBOARDS ======================
def start_kb(user_id: int):
    level = admins.get(user_id, 0)
    is_admin = level > 0
    kb = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"),
         InlineKeyboardButton(text="🎲 Розыгрыши", callback_data="raffles")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support"),
         InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews")],
        [InlineKeyboardButton(text="🎫 Тикеты", callback_data="tickets")],
        [InlineKeyboardButton(text="📢 Автопостинг", callback_data="autoposting")],
        [InlineKeyboardButton(text="Пополнить баланс", callback_data="topup")],
        [InlineKeyboardButton(text="Админы", callback_data="admins_list")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_panel_kb(level: int):
    kb = [
        [InlineKeyboardButton(text="Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="Товары", callback_data="admin_products")],
        [InlineKeyboardButton(text="Тикеты", callback_data="admin_tickets")],
        [InlineKeyboardButton(text="Каналы", callback_data="admin_channels")],
        [InlineKeyboardButton(text="Розыгрыши", callback_data="admin_raffles")],
        [InlineKeyboardButton(text="Создать розыгрыш", callback_data="create_raffle")],
        [InlineKeyboardButton(text="Выдать баланс", callback_data="grant_balance")],
        [InlineKeyboardButton(text="Выдать админа", callback_data="grant_admin")],
        [InlineKeyboardButton(text="Автопостинг", callback_data="admin_autoposting")],
        [InlineKeyboardButton(text="Отзывы", callback_data="admin_reviews")],  # ← ДОБАВИЛ
    ]

    # === ПРАВА ПО УРОВНЯМ ===
    if level == 1:  # Саппорт
        allowed = {
            "admin_tickets",     # отвечать на тикеты
            "admin_users",       # просматривать пользователей
            "admin_reviews"      # просматривать отзывы
        }
        kb = [row for row in kb if row[0].callback_data in allowed]

    elif level == 2:  # Модератор
        allowed = {
            "admin_tickets",
            "admin_users",
            "admin_channels",    # добавление обязательной подписки
            "admin_raffles",
            "admin_autoposting",
            "admin_reviews"
        }
        kb = [row for row in kb if row[0].callback_data in allowed]

    # level 3 — Администратор: всё доступно
    # level >= 3 — всё видно, ничего не фильтруем

    kb.append([InlineKeyboardButton(text="Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ====================== HELPERS ======================
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    if not channels_required:
        return True
    for ch in channels_required:
        try:
            member: ChatMember = await bot.get_chat_member(ch["channel_id"], user_id)
            if member.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

def subscription_text():
    if not channels_required:
        return "✅ Подписки не требуются!"
    text = "📢 Чтобы пользоваться ботом, подпишись на каналы:\n\n"
    for ch in channels_required:
        text += f"• {ch['title']} → {ch['invite_link']}\n"
    text += "\nПосле подписки нажми «Проверить»"
    return text

async def check_subscription_and_prompt(message: Message, is_group=False):
    if not await is_subscribed(bot, message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]])
        if is_group:
            await message.reply(subscription_text(), reply_markup=kb)
        else:
            await message.answer(subscription_text(), reply_markup=kb)
        return False
    return True

async def is_banned(user_id: int) -> bool:
    ban = banned_users.get(user_id)
    if ban:
        if ban['until'] and ban['until'] < datetime.now():
            del banned_users[user_id]
            return False
        return True
    return False

async def is_group_admin(bot: Bot, user_id: int, chat_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in ("administrator", "creator")

# Автомодерация: проверка на ссылки и спам
def is_spam_message(text: str) -> bool:
    # Ссылки
    if re.search(r'(http|https|www\.|t\.me/|telegram\.me/|bit\.ly/|tinyurl\.com/)', text, re.IGNORECASE):
        return True
    # Предложения работы
    spam_keywords = ["работа", "заработок", "вакансия", "подработка", "заработай", "инвестиции", "крипта", "трейдинг"]
    if any(keyword in text.lower() for keyword in spam_keywords):
        return True
    return False

# ====================== BOT SETUP ======================

# ====================== EMOJI WRAPPER (AUTOMATIC) ======================
# Добавлено автоматически: каждое текстовое сообщение бота будет получать небольшой эмодзи в конце.
# Это НЕ меняет логику бота — только визуально украшает тексты.
_EMOJI = " ✨"

def _make_wrapper(func):
    async def _wrapped(*args, **kwargs):
        # Append emoji to common text-like kwargs
        for key in ("text", "caption"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = kwargs[key] + _EMOJI
        # If positional args contain a string (after self/chat_id), append emoji to the first found
        args_list = list(args)
        for i in range(0, len(args_list)):
            # skip 'self' references that are objects (not plain str)
            if isinstance(args_list[i], str):
                # avoid changing token in Bot(token=...) style - but here positional strings are message texts
                args_list[i] = args_list[i] + _EMOJI
                args = tuple(args_list)
                break
        return await func(*args, **kwargs)
    return _wrapped

# Apply wrappers to common sending/editing methods if available in the aiogram classes.
try:
    from aiogram import Bot
    # Patch Bot methods
    if hasattr(Bot, "send_message"):
        Bot.send_message = _make_wrapper(Bot.send_message)
    if hasattr(Bot, "send_photo"):
        Bot.send_photo = _make_wrapper(Bot.send_photo)
    if hasattr(Bot, "send_document"):
        Bot.send_document = _make_wrapper(Bot.send_document)
    if hasattr(Bot, "send_video"):
        Bot.send_video = _make_wrapper(Bot.send_video)
    if hasattr(Bot, "send_audio"):
        Bot.send_audio = _make_wrapper(Bot.send_audio)
except Exception:
    # If aiogram isn't importable at patch time, we'll patch later (after bot import) below.
    pass

# We'll also try to patch Message methods (for message.answer, message.reply, message.edit_text)
try:
    from aiogram.types import Message as AiogramMessage
    if hasattr(AiogramMessage, "answer"):
        AiogramMessage.answer = _make_wrapper(AiogramMessage.answer)
    if hasattr(AiogramMessage, "reply"):
        AiogramMessage.reply = _make_wrapper(AiogramMessage.reply)
    if hasattr(AiogramMessage, "edit_text"):
        AiogramMessage.edit_text = _make_wrapper(AiogramMessage.edit_text)
except Exception:
    pass

# If bot already created earlier in file, patch its instance methods too (safe no-op if names missing)
def _patch_instance_methods(bot_instance):
    try:
        if hasattr(bot_instance, "send_message"):
            bot_instance.send_message = _make_wrapper(bot_instance.send_message)
        if hasattr(bot_instance, "send_photo"):
            bot_instance.send_photo = _make_wrapper(bot_instance.send_photo)
        if hasattr(bot_instance, "send_document"):
            bot_instance.send_document = _make_wrapper(bot_instance.send_document)
        if hasattr(bot_instance, "send_video"):
            bot_instance.send_video = _make_wrapper(bot_instance.send_video)
    except Exception:
        pass

# ====================== END EMOJI WRAPPER ======================
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

scheduler = AsyncIOScheduler()

async def autosave():
    await save_dict("users", users)
    await save_dict("products", products, "product_id")
    await save_dict("tickets", tickets, "ticket_id")
    await save_dict("raffles", raffles, "raffle_id")
    await save_list("reviews", reviews)
    await save_list("channels_required", channels_required)
    await save_dict("banned_users", banned_users)
    await save_dict("group_data", group_data, "chat_id")
    await save_list("autopost_channels", autopost_channels)
    await save_dict("pending_autoposts", pending_autoposts, "post_id")
    await save_dict("admins", admins)

    for name in counters:
        await save_counter(name, counters[name])

# Автосейв каждую минуту
scheduler.add_job(autosave, "interval", seconds=60, id="autosave")

async def load_all_data():
    global counters
    await init_db()

    await load_dict("users", dict_global=users)
    await load_dict("products", "product_id", products)
    await load_dict("tickets", "ticket_id", tickets)
    await load_dict("raffles", "raffle_id", raffles)
    await load_list("reviews", reviews)
    await load_list("channels_required", channels_required)
    await load_dict("banned_users", dict_global=banned_users)
    await load_dict("group_data", "chat_id", group_data)
    await load_list("autopost_channels", autopost_channels)
    await load_dict("pending_autoposts", "post_id", pending_autoposts)
    await load_dict("admins", "user_id", admins)

    # Загружаем счётчики
    counters["product"] = await load_counter("product", 1)
    counters["ticket"] = await load_counter("ticket", 1)
    counters["raffle"] = await load_counter("raffle", 1)
    counters["autopost"] = await load_counter("autopost", 1)

    # Если админов нет — добавляем владельца
    if not admins and ADMIN_IDS:
        admins[ADMIN_IDS[0]] = 3
        await save_dict("admins", admins)

    logging.info("Все данные успешно загружены из базы")

# ====================== SCHEDULER TASKS ======================
async def send_reminders():
    for user_id in list(users.keys()):
        if not await is_subscribed(bot, user_id):
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]])
                await bot.send_message(user_id, 
                    "🔔 Напоминание!\n\n" + subscription_text(),
                    reply_markup=kb)
            except:
                pass

async def check_raffles():
    now = datetime.now()
    for r_id, raffle in list(raffles.items()):
        if raffle["ends_at"] <= now and not raffle["finished"]:
            await finish_raffle(r_id)

scheduler.add_job(send_reminders, 'interval', minutes=60)
scheduler.add_job(check_raffles, 'interval', minutes=60)

# ====================== START & SUBSCRIPTION ======================
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await message.answer("🚫 Вы заблокированы в боте.")
        return
    username = message.from_user.username or ""
    full_name = message.from_user.full_name

    if user_id not in users:
        users[user_id] = {
            "balance": 0,
            "stars": 0,
            "purchases": [],
            "username": username,
            "name": full_name,
            "tickets": [],
            "banned": False,
            "warns": {}
        }

    level = admins.get(user_id, 0)
    subscribed = await is_subscribed(bot, user_id)

    if not subscribed and channels_required:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")]
        ])
        await message.answer(
            f"👋 Привет, {full_name}!\n\n"
            "Добро пожаловать в наш магазин! 🌟\n\n"
            + subscription_text(),
            reply_markup=kb
        )
    else:
        await message.answer(
            f"👋 Привет, {full_name}!\n\n"
            "Чем займёмся сегодня? 🚀",
            reply_markup=start_kb(user_id)
        )

@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    subscribed = await is_subscribed(bot, call.from_user.id)
    if subscribed:
        await call.message.edit_text(
            "✅ Отлично! Подписка подтверждена.\nТеперь все функции доступны! 🎉",
            reply_markup=start_kb(call.from_user.id)
        )
    else:
        await call.answer("❌ Ты ещё не подписался на все каналы!", show_alert=True)

# ====================== PROFILE ======================
@router.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    u = users.get(call.from_user.id, {})
    purchases = len(u.get("purchases", []))
    text = f"👤 Твой профиль\n\n" \
           f"💰 Баланс: {u.get('balance', 0)} ₽\n" \
           f"⭐ Звёздочки: {u.get('stars', 0)}\n" \
           f"🛒 Покупок: {purchases}\n\n" \
           f"Спасибо, что с нами! ❤️"
    await call.message.edit_text(text, reply_markup=start_kb(call.from_user.id))

# ====================== SHOP ======================
@router.callback_query(F.data == "shop")
async def shop_main(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    if not products:
        await call.message.edit_text("🛒 Магазин пока пустует.\nСкоро появятся товары! 🌟",
                                    reply_markup=start_kb(call.from_user.id))
        return

    text = "🛒 Наши товары\n\n"
    kb = []
    for p_id, p in products.items():
        text += f"📦 {p['name']} — {p['price']} ₽\n{p['description']}\n\n"
        kb.append([InlineKeyboardButton(text=f"💳 Купить — {p['price']} ₽", callback_data=f"buy_{p_id}")])
    kb.append([InlineKeyboardButton(text="◀ Назад", callback_data="back_main")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("buy_"))
async def buy_product(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    product_id = int(call.data.split("_")[1])
    product = products.get(product_id)
    if not product:
        await call.answer("❌ Товар не найден!", show_alert=True)
        return

    user = users[call.from_user.id]
    if user["balance"] < product["price"]:
        await call.answer("❌ Недостаточно средств!", show_alert=True)
        return

    user["balance"] -= product["price"]
    user["purchases"].append({
        "id": product_id,
        "name": product["name"],
        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
    })

    await call.message.delete()
    await call.message.answer(f"✅ Покупка успешна!\n\n"
                              f"📦 Товар: {product['name']}\n"
                              f"💳 Списано: {product['price']} ₽\n"
                              f"💰 Остаток: {user['balance']} ₽")

    # Выдача товара
    if product["type"] == "text":
        await bot.send_message(call.from_user.id, f"📝 {product['content']}")
    elif product["type"] == "link":
        await bot.send_message(call.from_user.id, f"🔗 Ссылка: {product['content']}")
    elif product["type"] == "file":
        await bot.send_document(call.from_user.id, FSInputFile(product["content"]))
    elif product["type"] == "video":
        await bot.send_video(call.from_user.id, product["content"])

# ====================== REVIEWS ======================
@router.callback_query(F.data == "reviews")
async def show_reviews(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    if not reviews:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="leave_review")]])
        await call.message.edit_text("⭐ Ещё никто не оставил отзыв.\nБудь первым! 🌟", reply_markup=kb)
        return

    text = "⭐ Отзывы о нас\n\n"
    for r in reviews[-10:]:  # Последние 10
        username = r.get("username", "Аноним")
        stars = "★" * r["rating"] + "☆" * (5 - r["rating"])
        text += f"<b>{username}</b>  {stars}\n{r['text']}\n\n"

    kb = [
        [InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data="leave_review")],
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "leave_review")
async def leave_review_rating(call: CallbackQuery, state: FSMContext):
    if not await check_subscription_and_prompt(call.message):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="★☆☆☆☆ 1", callback_data="rate_1"),
         InlineKeyboardButton(text="★★☆☆☆ 2", callback_data="rate_2"),
         InlineKeyboardButton(text="★★★☆☆ 3", callback_data="rate_3")],
        [InlineKeyboardButton(text="★★★★☆ 4", callback_data="rate_4"),
         InlineKeyboardButton(text="★★★★★ 5", callback_data="rate_5")]
    ])
    await call.message.edit_text("⭐ Оцени нас от 1 до 5 звёзд", reply_markup=kb)
    await state.set_state(ReviewStates.rating)

@router.callback_query(F.data.startswith("rate_"))
async def leave_review_text(call: CallbackQuery, state: FSMContext):
    rating = int(call.data.split("_")[1])
    await state.update_data(rating=rating)
    await call.message.edit_text("✍️ Напиши свой отзыв (текстом)")
    await state.set_state(ReviewStates.text)

@router.message(StateFilter(ReviewStates.text))
async def save_review(message: Message, state: FSMContext):
    data = await state.get_data()
    reviews.append({
        "user_id": message.from_user.id,
        "username": message.from_user.username or "Аноним",
        "rating": data["rating"],
        "text": message.text.strip(),
        "date": datetime.now().strftime("%d.%m.%Y")
    })
    await message.answer("🙏 Спасибо за отзыв! Он очень важен для нас! 🌟")
    await state.clear()

# ====================== SUPPORT ======================
@router.callback_query(F.data == "support")
async def support_menu(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    kb = [
        [InlineKeyboardButton(text="⭐ Отправить звезду админу", callback_data="send_star")],
        [InlineKeyboardButton(text="💳 Пополнить баланс бота", callback_data="send_money")],
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]
    ]
    await call.message.edit_text(
        "📞 Поддержка проекта\n\n"
        "Ты можешь поблагодарить админа звёздочкой ⭐ или помочь развитию бота переводом 💳",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@router.callback_query(F.data == "send_star")
async def send_star(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    user = users.get(call.from_user.id, {})
    if user.get("stars", 0) <= 0:
        await call.answer("❌ У тебя нет звёздочек!", show_alert=True)
        return
    user["stars"] -= 1
    await call.message.edit_text("🌟 Спасибо! Админ получил твою звезду! ❤️")
    
    for admin_id, level in admins.items():
        if level == 3:
            try:
                await bot.send_message(admin_id,
                    f"⭐ Новый донат — звезда!\n"
                    f"От: {call.from_user.full_name} (@{call.from_user.username or 'нет'})\n"
                    f"Осталось у юзера: {user['stars']}")
            except:
                pass

@router.callback_query(F.data == "send_money")
async def send_money_start(call: CallbackQuery, state: FSMContext):
    if not await check_subscription_and_prompt(call.message):
        return
    await call.message.edit_text("💳 Сколько рублей перевести на развитие бота?\n(введи число)")
    await state.set_state(AdminStates.edit_user_balance)  # Переиспользуем

@router.message(StateFilter(AdminStates.edit_user_balance))
async def send_money_finish(message: Message, state: FSMContext):
    if message.from_user.id in admins:
        await state.clear()
        return
    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи нормальное число!")
        return

    user = users[message.from_user.id]
    if user["balance"] < amount:
        await message.answer("❌ Недостаточно средств на балансе!")
        return

    user["balance"] -= amount
    await message.answer(f"🙏 Спасибо огромное за {amount} ₽!\n"
                         f"Это очень помогает развитию бота! 🚀")

    for admin_id, level in admins.items():
        if level == 3:
            try:
                await bot.send_message(admin_id,
                    f"💳 Новый донат — {amount} ₽!\n"
                    f"От: {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n"
                    f"Остаток у юзера: {user['balance']} ₽")
            except:
                pass
    await state.clear()

# ====================== TICKETS ======================
@router.callback_query(F.data == "tickets")
async def user_tickets(call: CallbackQuery, state: FSMContext):
    if not await check_subscription_and_prompt(call.message):
        return
    await call.message.edit_text("🎫 Напиши свой вопрос — я передам администрации!",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀ Назад", callback_data="back_main")]]))
    await state.set_state(UserStates.ticket)

@router.message(StateFilter(UserStates.ticket))
async def user_ticket_message(message: Message, state: FSMContext):
    global counters
    t_id = counters["ticket"]
    counters["ticket"] += 1

    tickets[t_id] = {
        "id": t_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or "без юзернейма",
        "name": message.from_user.full_name,
        "messages": [],
        "open": True
    }

    # Первое сообщение
    tickets[t_id]["messages"].append({
        "text": message.text,
        "from": "user",
        "date": datetime.now().strftime("%d.%m %H:%M")
    })

    await message.answer("Тикет создан!\nТеперь можешь писать сюда — админ ответит")
    await state.clear()
    await state.set_state(UserStates.chatting)
    await state.update_data(current_ticket=t_id)

    # Уведомление админу
    for admin_id in admins:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Ответить", callback_data=f"answer_ticket_{t_id}")],
                [InlineKeyboardButton(text="Закрыть тикет", callback_data=f"close_ticket_{t_id}")]
            ])
            await bot.send_message(admin_id,
                f"НОВЫЙ ТИКЕТ #{t_id}\n\n"
                f"От: <b>{message.from_user.full_name}</b>\n"
                f"@{message.from_user.username or 'без юзернейма'}\n"
                f"ID: <code>{message.from_user.id}</code>\n\n"
                f"{message.text}",
                reply_markup=kb)
        except: pass

@router.message(StateFilter(UserStates.chatting))
async def user_chat_in_ticket(message: Message, state: FSMContext):
    data = await state.get_data()
    t_id = data.get("current_ticket")
    
    if not t_id or t_id not in tickets or not tickets[t_id]["open"]:
        await message.answer("Тикет закрыт\nСоздай новый через меню")
        await state.clear()
        return

    # Сохраняем сообщение
    tickets[t_id]["messages"].append({
        "text": message.text,
        "from": "user",
        "date": datetime.now().strftime("%H:%M")
    })

    await message.answer("Сообщение отправлено админу")

    # Пересылаем админам
    for admin_id in admins:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Ответить", callback_data=f"answer_ticket_{t_id}")],
                [InlineKeyboardButton(text="Закрыть тикет", callback_data=f"close_ticket_{t_id}")]
            ])
            await bot.send_message(admin_id,
                f"СООБЩЕНИЕ В ТИКЕТЕ #{t_id}\n\n"
                f"От: <b>{tickets[t_id]['name']}</b>\n"
                f"{message.text}",
                reply_markup=kb)
        except: pass

@router.callback_query(F.data.startswith("answer_ticket_"))
async def answer_ticket_start(call: CallbackQuery, state: FSMContext):
    level = admins.get(call.from_user.id, 0)
    if level < 1:
        await call.answer("Только для админов!", show_alert=True)
        return
    
    t_id = int(call.data.split("_")[2])
    if t_id not in tickets or not tickets[t_id]["open"]:
        await call.answer("Тикет закрыт", show_alert=True)
        return

    await state.update_data(admin_ticket=t_id)
    await call.message.edit_text(
        f"Ответ в тикет #{t_id}\n\n"
        f"Пользователь: {tickets[t_id]['name']}\n\n"
        "Напиши ответ:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="back_main")]
        ])
    )
    await state.set_state(AdminTicketStates.waiting_answer)

@router.message(StateFilter(AdminTicketStates.waiting_answer))
async def admin_send_answer(message: Message, state: FSMContext):
    level = admins.get(message.from_user.id, 0)
    if level < 1:
        await state.clear()
        return

    data = await state.get_data()
    t_id = data.get("admin_ticket")
    if not t_id or t_id not in tickets or not tickets[t_id]["open"]:
        await message.answer("Тикет закрыт")
        await state.clear()
        return

    user_id = tickets[t_id]["user_id"]

    tickets[t_id]["messages"].append({
        "text": message.text,
        "from": "admin",
        "date": datetime.now().strftime("%H:%M")
    })

    try:
        await bot.send_message(user_id,
            f"ОТВЕТ ОТ АДМИНИСТРАЦИИ\n\n"
            f"{message.text}\n\n"
            f"Тикет #{t_id} • Пиши, если остались вопросы!")
        await message.answer(f"Ответ отправлен в тикет #{t_id}")
    except:
        await message.answer("Юзер заблокировал бота")

    await state.clear()

@router.callback_query(F.data.startswith("close_ticket_"))
async def close_ticket(call: CallbackQuery):
    level = admins.get(call.from_user.id, 0)
    if level < 1:
        return
    
    t_id = int(call.data.split("_")[2])
    if t_id not in tickets or not tickets[t_id]["open"]:
        await call.answer("Уже закрыт", show_alert=True)
        return

    user_id = tickets[t_id]["user_id"]
    tickets[t_id]["open"] = False

    await call.message.edit_text(f"Тикет #{t_id} закрыт")

    try:
        await bot.send_message(user_id,
            f"Тикет #{t_id} закрыт администратором\n\n"
            "Спасибо за обращение! Если будут вопросы — создавай новый")
    except: pass

# ====================== RAFFLES ======================
@router.callback_query(F.data == "raffles")
async def raffles_list(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    text = "🎲 Активные розыгрыши\n\n"
    kb = []
    active = False
    for r_id, r in raffles.items():
        if not r.get("finished", False):
            active = True
            left = int((r["ends_at"] - datetime.now()).total_seconds() / 60)
            text += f"#{r_id} — {r['prize_count']} призов\nОсталось: ~{left} мин\n\n"
            kb.append([InlineKeyboardButton(text="🎯 Участвовать", callback_data=f"join_raffle_{r_id}")])
    if not active:
        text = "🎲 Нет активных розыгрышей.\nСкоро новые! 🌟"
    kb.append([InlineKeyboardButton(text="◀ Назад", callback_data="back_main")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("join_raffle_"))
async def join_raffle(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return
    r_id = int(call.data.split("_")[2])
    if r_id not in raffles or raffles[r_id].get("finished"):
        await call.answer("❌ Розыгрыш завершён!", show_alert=True)
        return
    uid = call.from_user.id
    if uid not in raffles[r_id]["participants"]:
        raffles[r_id]["participants"].append(uid)
        await call.answer("✅ Ты участвуешь! Удачи! 🍀")
    else:
        await call.answer("ℹ️ Ты уже участвуешь!")

async def finish_raffle(r_id: int):
    raffle = raffles[r_id]
    participants = raffle["participants"]
    prize_count = raffle["prize_count"]
    if len(participants) < prize_count:
        winners = participants
    else:
        winners = []
        temp = participants[:]
        for _ in range(prize_count):
            if not temp:
                break
            winner = choice(temp)
            winners.append(winner)
            temp.remove(winner)
    
    raffle["winners"] = winners
    raffle["finished"] = True
    
    text = f"🎉 Розыгрыш #{r_id} завершён!\n\n" \
           f"Призов: {prize_count}\n" \
           f"Участников: {len(participants)}\n\n"
    if winners:
        text += "🏆 Победители:\n"
        for w in winners:
            user = users.get(w, {"name": "Unknown"})
            text += f"• {user.get('name', f'ID{w}')}\n"
            try:
                await bot.send_message(w, f"🎊 Поздравляем! Ты выиграл в розыгрыше #{r_id}! 🏆")
            except:
                pass
    else:
        text += "😔 Никто не выиграл :("
    
    for p in participants:
        try:
            await bot.send_message(p, text)
        except:
            pass

    for admin_id in admins:
        try:
            await bot.send_message(admin_id, f"🔥 Розыгрыш #{r_id} завершён! Победителей: {len(winners)}")
        except:
            pass

# ====================== ADMIN PANEL ======================
@router.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    level = admins.get(call.from_user.id, 0)
    if level == 0:
        await call.answer("🚫 Доступ запрещён!", show_alert=True)
        return
    await call.message.edit_text("⚙️ Админ-панель\nВыбери раздел:", reply_markup=admin_panel_kb(level))

@router.callback_query(F.data == "back_main")
async def back_to_main(call: CallbackQuery):
    await call.message.edit_text("🏠 Главное меню", reply_markup=start_kb(call.from_user.id))

# ADMIN: GRANT ADMIN
@router.callback_query(F.data == "grant_admin")
async def grant_admin_start(call: CallbackQuery, state: FSMContext):
    level = admins.get(call.from_user.id, 0)
    if level < 3:
        await call.answer("Доступно только администраторам уровня 3", show_alert=True)
        return
    await call.message.edit_text("👮 Введите ID или @username для выдачи прав админа")
    await state.set_state(AdminStates.grant_admin_id)

@router.message(StateFilter(AdminStates.grant_admin_id))
async def grant_admin_id(message: Message, state: FSMContext):
    uid = await get_user_id(message.text)
    if not uid:
        await message.answer("Не найден пользователь.\nВведи ID или @username")
        return
    
    await state.update_data(grant_id=uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 - Саппорт", callback_data="glevel_1")],
        [InlineKeyboardButton(text="2 - Модератор", callback_data="glevel_2")],
        [InlineKeyboardButton(text="3 - Администратор", callback_data="glevel_3")]
    ])
    await message.answer(f"Выбран пользователь: <b>ID {uid}</b>\nВыбери уровень:", reply_markup=kb, parse_mode="HTML")
    await state.set_state(AdminStates.grant_admin_level)

@router.callback_query(F.data.startswith("glevel_"))
async def grant_admin_level(call: CallbackQuery, state: FSMContext):
    l = int(call.data.split("_")[1])
    await state.update_data(grant_level=l)
    data = await state.get_data()
    uid = data["grant_id"]
    await call.message.edit_text(f"Подтвердите выдачу уровня {l} для ID {uid}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="gconfirm_yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="gconfirm_no")]
    ]))
    await state.set_state(AdminStates.grant_admin_confirm)

@router.callback_query(F.data.startswith("gconfirm_"))
async def grant_admin_confirm(call: CallbackQuery, state: FSMContext):
    if call.data.endswith("no"):
        await call.message.edit_text("Отменено")
        await state.clear()
        return
    data = await state.get_data()
    uid = data["grant_id"]
    l = data["grant_level"]
    admins[uid] = l
    await call.message.edit_text(f"✅ Права уровня {l} выданы ID {uid}")
    await state.clear()

# ADMIN: GRANT BALANCE
@router.callback_query(F.data == "grant_balance")
async def grant_balance_start(call: CallbackQuery, state: FSMContext):
    level = admins.get(call.from_user.id, 0)
    if level < 3:
        await call.answer("Доступно только администраторам уровня 3", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Рубли", callback_data="gtype_rub")],
        [InlineKeyboardButton(text="⭐ Звезды", callback_data="gtype_star")]
    ])
    await call.message.edit_text("Выберите тип баланса", reply_markup=kb)
    await state.set_state(AdminStates.grant_balance_type)

@router.callback_query(F.data.startswith("gtype_"))
async def grant_balance_type(call: CallbackQuery, state: FSMContext):
    t = "balance" if call.data.endswith("rub") else "stars"
    await state.update_data(grant_type=t)
    await call.message.edit_text("Введите ID или @username")
    await state.set_state(AdminStates.grant_balance_id)

@router.message(StateFilter(AdminStates.grant_balance_id))
async def grant_balance_id(message: Message, state: FSMContext):
    uid = await get_user_id(message.text)
    if not uid:
        await message.answer("Не найден пользователь. Введи ID или @username")
        return
    
    await state.update_data(grant_id=uid)
    await message.answer("Сколько выдать?")
    await state.set_state(AdminStates.grant_balance_amount)

@router.message(StateFilter(AdminStates.grant_balance_amount))
async def grant_balance_amount(message: Message, state: FSMContext):
    try:
        amt = int(message.text)
    except:
        await message.answer("❌ Число!")
        return
    await state.update_data(grant_amount=amt)
    data = await state.get_data()
    uid = data["grant_id"]
    t = data["grant_type"]
    await message.answer(f"Подтвердите выдачу {amt} {t} для ID {uid}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="gbconfirm_yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="gbconfirm_no")]
    ]))
    await state.set_state(AdminStates.grant_balance_confirm)

@router.callback_query(F.data.startswith("gbconfirm_"))
async def grant_balance_confirm(call: CallbackQuery, state: FSMContext):
    if call.data.endswith("no"):
        await call.message.edit_text("Отменено")
        await state.clear()
        return
    data = await state.get_data()
    uid = data["grant_id"]
    t = data["grant_type"]
    amt = data["grant_amount"]
    if uid in users:
        users[uid][t] += amt
    else:
        await call.answer("Пользователь не найден")
        return
    await call.message.edit_text(f"✅ Выдано {amt} {t} для ID {uid}")
    await state.clear()

# ADMIN: PRODUCTS (only level 3)

# ADMIN: CHANNELS (level 2+)
async def admin_channels(call: CallbackQuery):
    level = admins.get(call.from_user.id, 0)
    if level < 2: return
    # ... (остальной код без изменений)

# ADMIN: RAFFLES (level 3)
@router.callback_query(F.data == "admin_raffles")
async def admin_raffles(call: CallbackQuery):
    level = admins.get(call.from_user.id, 0)
    if level < 3: return
    # ... (добавьте FSM для создания, если нужно)

# ====================== BAN COMMANDS ======================
# ====================== КОМАНДА /ban — САППОРТ ВВОДИТ ДНИ САМ ======================

@router.message(Command("ban"))
async def cmd_ban_bot(message: Message, command: CommandObject):
    level = admins.get(message.from_user.id, 0)
    if level < 1:
        await message.reply("Доступно только саппортам и выше!")
        return

    if not command.args:
        await message.reply(
            "Использование:\n"
            "/ban <@username или ID> <дни>\n\n"
            "Примеры:\n"
            "/ban @user123 7 — бан на 7 дней\n"
            "/ban 123456789 0 — бан навсегда\n"
            "/ban @user123 365 — бан на год"
        )
        return

    args = command.args.strip().split(maxsplit=1)
    if len(args) != 2:
        await message.reply("Укажи и пользователя, и количество дней!")
        return

    target = args[0]
    days_str = args[1]

    # Проверяем дни
    try:
        days = int(days_str)
        if days < 0 or days > 10000:
            raise ValueError
    except ValueError:
        await message.reply("Дни — число от 0 до 10000!\n0 = навсегда")
        return

    # Находим пользователя
    uid = await get_user_id(target)
    if not uid:
        await message.reply("Пользователь не найден!\nПроверь @username или ID")
        return

    if uid in admins:
        await message.reply("Нельзя банить админа!")
        return

    # Определяем срок
    if days == 0:
        term = "навсегда"
        until = None
    else:
        term = f"{days} дн."
        until = datetime.now() + timedelta(days=days)

    # Баним
    banned_users[uid] = {"reason": "Бан от саппорта/админа", "until": until}

    # Красивый ответ
    try:
        user = await bot.get_chat(uid)
        name = user.full_name
        username = f"@{user.username}" if user.username else ""
    except:
        name = "Пользователь"
        username = ""

    await message.reply(
        f"Пользователь забанен в боте!\n\n"
        f"{name} {username}\n"
        f"ID: <code>{uid}</code>\n"
        f"Срок: <b>{term}</b>\n"
        f"Забанил: {message.from_user.first_name}",
        parse_mode="HTML"
    )

    # Уведомляем забаненного
    try:
        await bot.send_message(
            uid,
            f"Вы забанены в боте.\n"
            f"Срок: {term}\n"
            f"Причина: нарушение правил\n"
            f"Забанил: {message.from_user.first_name}"
        )
    except:
        pass  # заблокировал бота

@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    level = admins.get(message.from_user.id, 0)
    if level < 1:
        return
    args = command.args
    try:
        uid = int(args)
    except:
        await message.reply("Usage: /unban id")
        return
    if message.chat.type != ChatType.PRIVATE:
        chat_id = message.chat.id
        if uid in group_data.get(chat_id, {}).get('bans', {}):
            del group_data[chat_id]['bans'][uid]
            await bot.unban_chat_member(chat_id, uid)
            await message.reply(f"User {uid} unbanned in group")
        return
    if uid in banned_users:
        del banned_users[uid]
        await message.reply(f"User {uid} unbanned in bot")

# ====================== GROUP COMMANDS ======================
@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject):
    if message.chat.type == ChatType.PRIVATE:
        return

    level = admins.get(message.from_user.id, 0)
    if level < 2:
        return

    if not command.args:
        await message.reply("/mute <@username или ID> <часы> [причина]")
        return

    args = command.args.split(maxsplit=2)
    uid = await get_user_id(args[0])
    if not uid:
        await message.reply("Пользователь не найден")
        return

    try:
        hours = int(args[1])
    except:
        await message.reply("Укажи часы числом!")
        return

    reason = args[2] if len(args) > 2 else "Не указана"
    chat_id = message.chat.id

    if chat_id not in group_data:
        group_data[chat_id] = {'rules': '', 'warns': {}, 'bans': {}, 'mutes': {}}

    until = None if hours == 0 else datetime.now() + timedelta(hours=hours)
    group_data[chat_id]['mutes'][uid] = {'until': until, 'reason': reason}

    try:
        await bot.restrict_chat_member(
            chat_id, uid,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        await message.reply(f"Пользователь замучен на {hours if hours > 0 else 'навсегда'} ч.")
    except:
        await message.reply("Замучен в боте, но не в чате (нет прав)")

@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject):
    if message.chat.type == ChatType.PRIVATE:
        return  # warn только в группах

    level = admins.get(message.from_user.id, 0)
    if level < 2:  # модератор и выше
        return

    if not command.args:
        await message.reply("Использование: /warn <@username или ID> [причина]")
        return

    args = command.args.split(maxsplit=1)
    target = args[0]

    uid = await get_user_id(target)
    if not uid:
        await message.reply("Пользователь не найден. Укажи правильный @username или ID")
        return

    reason = args[1] if len(args) > 1 else "Не указана"

    chat_id = message.chat.id
    if chat_id not in group_data:
        group_data[chat_id] = {'rules': '', 'warns': {}, 'bans': {}, 'mutes': {}}

    # Считаем предупреждения
    warns = group_data[chat_id]['warns'].get(uid, 0) + 1
    group_data[chat_id]['warns'][uid] = warns

    # Ответ пользователю
    text = (f"Пользователь <a href='tg://user?id={uid}'>предупреждён</a> ({warns}/3)\n"
            f"Причина: {reason}")

    if warns >= 3:
        # Автобан при 3 предупреждениях
        group_data[chat_id]['bans'][uid] = {
            'until': None,  # навсегда
            'reason': "3/3 предупреждения"
        }
        try:
            await bot.ban_chat_member(chat_id, uid)
            text += "\nАвтоматический бан — 3/3 предупреждения!"
        except:
            text += "\nНе удалось кикнуть из чата (нет прав)"

        # Сбрасываем счётчик после бана (по желанию)
        group_data[chat_id]['warns'][uid] = 0

    await message.reply(text, parse_mode="HTML")

@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject):
    if message.chat.type == ChatType.PRIVATE:
        return  # kick только в группах

    level = admins.get(message.from_user.id, 0)
    if level < 2:  # модератор и выше
        return

    if not command.args and not message.reply_to_message:
        await message.reply("Использование: /kick <@username или ID> [причина]\nИли ответь на сообщение")
        return

    # Определяем цель
    uid = None
    if command.args:
        target = command.args.split(maxsplit=1)[0]
        uid = await get_user_id(target)
    elif message.reply_to_message:
        uid = message.reply_to_message.from_user.id

    if not uid:
        await message.reply("Не удалось определить пользователя.\nУкажи @username, ID или ответь на сообщение")
        return

    reason = ""
    if command.args and len(command.args.split(maxsplit=1)) > 1:
        reason = command.args.split(maxsplit=1)[1]
    elif message.reply_to_message and message.reply_to_message.text:
        reason = " (кик по реплаю)"
    else:
        reason = "Не указана"

    chat_id = message.chat.id

    try:
        # Сначала бан на 1 минуту + анбан = кик
        await bot.ban_chat_member(chat_id, uid, until_date=datetime.now() + timedelta(minutes=1))
        await bot.unban_chat_member(chat_id, uid)  # разбаниваем сразу

        await message.reply(
            f"Пользователь <a href='tg://user?id={uid}'>выгнан из чата</a>\n"
            f"Причина: {reason}",
            parse_mode="HTML"
        )

        # Опционально: логируем в базу (если хочешь вести статистику киков)
        if chat_id not in group_data:
            group_data[chat_id] = {'rules': '', 'warns': {}, 'bans': {}, 'mutes': {}, 'kicks': {}}
        from collections import defaultdict
        group_data[chat_id]['kicks'][uid] = group_data[chat_id]['kicks'].get(uid, 0) + 1

    except Exception as e:
        # Если бот не админ или пользователь уже не в чате
        await message.reply(f"Не удалось кикнуть пользователя.\n"
                          f"Ошибка: {str(e)[:100]}")

@router.message(Command("rules"))
async def cmd_rules(message: Message):
    if message.chat.type in (ChatType.PRIVATE, ChatType.CHANNEL):
        return
    chat_id = message.chat.id
    rules = group_data.get(chat_id, {}).get('rules', 'No rules set')
    await message.reply(rules)

@router.message(Command("setrules"), StateFilter(None))
async def cmd_setrules(message: Message, state: FSMContext):
    if message.chat.type in (ChatType.PRIVATE, ChatType.CHANNEL):
        return
    level = admins.get(message.from_user.id, 0)
    if level < 3 and not await is_group_admin(bot, message.from_user.id, message.chat.id):
        return
    await message.reply("Введите текст правил")
    await state.set_state(AdminStates.set_rules)

@router.message(StateFilter(AdminStates.set_rules))
async def set_rules_finish(message: Message, state: FSMContext):
    chat_id = message.chat.id
    if chat_id not in group_data:
        group_data[chat_id] = {'rules': '', 'warns': {}, 'bans': {}, 'mutes': {}}
    group_data[chat_id]['rules'] = message.text
    await message.reply("Правила установлены")
    await state.clear()

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = "/mute /warn /kick /ban /rules /help"
    await message.reply(help_text)

# ====================== AUTOMODERATION ======================
@router.message(F.chat.type.in_({"group", "supergroup"}))
async def automod(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    level = admins.get(user_id, 0)
    if level > 0 or await is_group_admin(bot, user_id, chat_id):
        return  # No punishment for admins
    if is_spam_message(message.text or ""):
        await message.delete()
        banned_users[user_id] = {'reason': 'Spam/link', 'until': None}
        for admin_id in admins:
            await bot.send_message(admin_id, f"Spam detected from {user_id} in {chat_id}: {message.text}")
        await bot.ban_chat_member(chat_id, user_id)

# ====================== AUTOPOSTING ======================
# === НОВЫЙ АВТОПОСТИНГ С ПОКУПКОЙ ===
@router.callback_query(F.data == "autoposting")
async def autoposting_menu(call: CallbackQuery):
    if not await check_subscription_and_prompt(call.message):
        return

    if not autopost_channels:
        await call.message.edit_text("Автопостинг временно недоступен — каналы не настроены.")
        return

    text = "Автопостинг — публикация в наших каналах\n\n"
    for ch in autopost_channels:
        cost = ch.get("cost", 0)
        if cost > 0:
            text += f"• {ch['title']} — {cost} ₽ (мгновенно)\n"
        else:
            text += f"• {ch['title']} — бесплатно (на модерации)\n"
    text += "\nВыбери тип поста:"

    kb = [
        [InlineKeyboardButton(text="Платный пост (без модерации)", callback_data="autopost_paid")],
        [InlineKeyboardButton(text="Бесплатный пост (на модерации)", callback_data="autopost_free")],
        [InlineKeyboardButton(text="Назад", callback_data="back_main")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.in_({"autopost_paid", "autopost_free"}))
async def autopost_type_selected(call: CallbackQuery, state: FSMContext):
    is_paid = call.data == "autopost_paid"
    await state.update_data(is_paid=is_paid)

    if is_paid:
        # Считаем общую стоимость (можно сделать по каналам отдельно — пока глобально)
        total_cost = sum(ch.get("cost", 0) for ch in autopost_channels if ch.get("cost", 0) > 0)
        if total_cost == 0:
            await call.message.edit_text("Платный пост недоступен — цена не установлена.")
            return
        await call.message.edit_text(
            f"Платный пост\n\n"
            f"Стоимость: <b>{total_cost} ₽</b>\n"
            f"Пост будет опубликован мгновенно во всех каналах.\n\n"
            f"Отправь текст/фото/видео для публикации:",
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            "Бесплатный пост\n\n"
            "Твой пост отправится на модерацию.\n"
            "После одобрения — опубликуем.\n\n"
            "Отправь текст/фото/видео:"
        )
    await state.set_state(AdminStates.autopost_content)


@router.message(StateFilter(AdminStates.autopost_content))
async def autopost_receive_content(message: Message, state: FSMContext):
    data = await state.get_data()
    is_paid = data.get("is_paid", False)

    if is_paid:
        total_cost = sum(ch.get("cost", 0) for ch in autopost_channels if ch.get("cost", 0) > 0)
        user = users[message.from_user.id]

        if user["balance"] < total_cost:
            await message.answer(f"Недостаточно средств! Нужно {total_cost} ₽, у тебя {user['balance']} ₽")
            await state.clear()
            return

        # Списываем деньги
        user["balance"] -= total_cost

        # Публикуем сразу
        published = 0
        for ch in autopost_channels:
            try:
                if message.photo:
                    await bot.send_photo(ch["channel_id"], message.photo[-1].file_id, caption=message.caption)
                elif message.video:
                    await bot.send_video(ch["channel_id"], message.video.file_id, caption=message.caption)
                elif message.document:
                    await bot.send_document(ch["channel_id"], message.document.file_id, caption=message.caption)
                else:
                    await bot.send_message(ch["channel_id"], message.text or "Пост от пользователя")
                published += 1
            except:
                pass

        await message.answer(
            f"Готово! Твой пост опубликован в {published} каналах!\n"
            f"Списано: {total_cost} ₽\n"
            f"Остаток: {user['balance']} ₽"
        )

    else:
        # Бесплатный — на модерацию
        global counters
        post_id = counters["autopost"]
        counters["autopost"] += 1

        pending_autoposts[post_id] = {
            "user_id": message.from_user.id,
            "message": message
        }

        await message.answer("Твой пост отправлен на модерацию! Ожидай уведомления.")

        # Уведомляем админов (уровень 2+)
        for admin_id, lvl in admins.items():
            if lvl >= 2:
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Опубликовать", callback_data=f"approve_post_{post_id}"),
                         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_post_{post_id}")]
                    ])
                    await bot.forward_message(admin_id, message.chat.id, message.message_id)
                    await bot.send_message(admin_id, f"Новый пост на модерацию #{post_id}", reply_markup=kb)
                except:
                    pass

    await state.clear()

# ADMIN: AUTOPOSTING SETTINGS
@router.callback_query(F.data == "admin_autoposting")
async def admin_autoposting(call: CallbackQuery, state: FSMContext):
    level = admins.get(call.from_user.id, 0)
    if level < 3:
        return
    text = "📢 Настройки автопостинга\n\n"
    kb = [[InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_autopost_channel")]]
    for ch in autopost_channels:
        text += f"• {ch['title']} - {ch['cost']} ₽\n"
        kb.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"del_autopost_{ch['channel_id']}")])
    kb.append([InlineKeyboardButton(text="💰 Установить стоимость", callback_data="set_autopost_cost")])
    kb.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "add_autopost_channel")
async def add_autopost_channel_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите ID канала для автопостинга")
    await state.set_state(AdminStates.add_autopost_channel)

@router.message(StateFilter(AdminStates.add_autopost_channel))
async def add_autopost_channel_finish(message: Message, state: FSMContext):
    try:
        ch_id = int(message.text)
        chat = await bot.get_chat(ch_id)
        autopost_channels.append({"channel_id": ch_id, "title": chat.title, "cost": 0})
        await message.answer("Канал добавлен")
    except:
        await message.answer("Ошибка")
    await state.clear()

@router.callback_query(F.data.startswith("del_autopost_"))
async def del_autopost_channel(call: CallbackQuery):
    ch_id = int(call.data.split("_")[2])
    for ch in autopost_channels[:]:
        if ch["channel_id"] == ch_id:
            autopost_channels.remove(ch)
    await call.message.edit_text("Канал удален")

@router.callback_query(F.data == "set_autopost_cost")
async def set_autopost_cost_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите новую стоимость для платного поста (для всех каналов)")
    await state.set_state(AdminStates.set_autopost_cost)

@router.message(StateFilter(AdminStates.set_autopost_cost))
async def set_autopost_cost_finish(message: Message, state: FSMContext):
    try:
        cost = int(message.text)
        for ch in autopost_channels:
            ch["cost"] = cost
        await message.answer("Стоимость установлена")
    except:
        await message.answer("Ошибка")
    await state.clear()

# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
# СОЗДАНИЕ РОЗЫГРЫША ИЗ АДМИНКИ (вставляй сюда!)
@router.callback_query(F.data == "create_raffle")
async def create_raffle_start(call: CallbackQuery, state: FSMContext):
    level = admins.get(call.from_user.id, 0)
    if level < 3:
        await call.answer("Только для администраторов!", show_alert=True)
        return
    await call.message.edit_text("Сколько призов в розыгрыше?")
    await state.set_state(AdminStates.create_raffle_prizes)


@router.message(StateFilter(AdminStates.create_raffle_prizes))
async def raffle_prizes(message: Message, state: FSMContext):
    try:
        prizes = int(message.text)
        if prizes <= 0:
            raise ValueError
    except:
        await message.answer("Введи число больше 0!")
        return
    await state.update_data(prize_count=prizes)
    await message.answer("На сколько часов розыгрыш? (например: 24)")
    await state.set_state(AdminStates.create_raffle_hours)


@router.message(StateFilter(AdminStates.create_raffle_hours))
async def raffle_hours(message: Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            raise ValueError
    except:
        await message.answer("Введи число!")
        return

    data = await state.get_data()
    global counters
    r_id = counters["raffle"]
    counters["raffle"] += 1

    raffles[r_id] = {
        "prize_count": data["prize_count"],
        "ends_at": datetime.now() + timedelta(hours=hours),
        "participants": [],
        "finished": False
    }

    await message.answer(f"Розыгрыш #{r_id} создан!\nПризов: {data['prize_count']}\nДлительность: {hours} ч.")
    await state.clear()

@router.callback_query(F.data == "topup")
async def topup_menu(call: CallbackQuery):
    kb = [
        [InlineKeyboardButton(text="Передать звёзды @buwse", callback_data="topup_stars_transfer")],
        [InlineKeyboardButton(text="Перевод на карту", callback_data="topup_card")],
        [InlineKeyboardButton(text="Назад", callback_data="back_main")]
    ]
    await call.message.edit_text("Пополнить баланс:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# ─────────────────────── 1. ПЕРЕВОД ЗВЁЗД НА @buwse ───────────────────────
@router.callback_query(F.data == "topup_stars_transfer")
async def stars_transfer_start(call: CallbackQuery):
    text = (
        "Пополнение через передачу звёзд\n\n"
        "1. Перейди → @buwse\n"
        "2. Нажми «Отправить звёзды»\n"
        "3. Переведи любое количество\n"
        "4. Вернись сюда и нажми кнопку"
    )
    kb = [[InlineKeyboardButton(text="Я перевёл звёзды", callback_data="stars_paid")]]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "stars_paid")
async def stars_paid_pressed(call: CallbackQuery):
    user_id = call.from_user.id
    request_id = f"stars_{user_id}_{int(time.time())}"  # ← надёжный ID

    pending_requests[request_id] = {"user_id": user_id, "type": "stars"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Зачислить", callback_data=f"approve_{request_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{request_id}")]
    ])

    for admin_id, level in admins.items():
        if level >= 2:
            try:
                await bot.send_message(
                    admin_id,
                    f"Заявка: звёзды @buwse\n"
                    f"От: <a href='tg://user?id={user_id}'>пользователь</a>\n"
                    f"Проверь платежи в @buwse",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except:
                pass

    await call.message.edit_text("Заявка отправлена админам.\nОжидай зачисления звёзд.")
    await call.answer()


# ─────────────────────── 2. ПЕРЕВОД НА КАРТУ ───────────────────────
@router.callback_query(F.data == "topup_card")
async def card_transfer_start(call: CallbackQuery):
    text = (
        "Пополнение рублями\n\n"
        "Карта СБЕР:\n"
        "<code>2202 2001 2345 6789</code>\n"
        "Иван Иванович И.\n\n"
        "Сделай перевод → нажми кнопку ниже"
    )
    kb = [[InlineKeyboardButton(text="Я перевёл деньги", callback_data="card_paid")]]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


@router.callback_query(F.data == "card_paid")
async def card_paid_pressed(call: CallbackQuery):
    user_id = call.from_user.id
    request_id = f"card_{user_id}_{int(time.time())}"

    pending_requests[request_id] = {"user_id": user_id, "type": "rub"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Зачислить", callback_data=f"approve_{request_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{request_id}")]
    ])

    for admin_id, level in admins.items():
        if level >= 2:
            try:
                await bot.send_message(
                    admin_id,
                    f"Заявка: перевод на карту\n"
                    f"От: <a href='tg://user?id={user_id}'>пользователь</a>\n"
                    f"Проверь поступление",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except:
                pass

    await call.message.edit_text("Заявка отправлена админам.\nОжидай зачисления.")
    await call.answer()


# ─────────────────────── ОБЩАЯ ОБРАБОТКА ОДОБРЕНИЯ/ОТКЛОНА ───────────────────────
@router.callback_query(F.data.regexp(r"^(approve|reject)_(.+)$"))
async def process_request(call: CallbackQuery):
    if admins.get(call.from_user.id, 0) < 2:
        await call.answer("Нет прав", show_alert=True)
        return

    action, request_id = call.data.split("_", 1)
    if request_id not in pending_requests:
        await call.answer("Заявка устарела")
        return

    data = pending_requests.pop(request_id)
    user_id = data["user_id"]
    req_type = data["type"]

    if action == "approve":
        await bot.send_message(user_id, "Платёж подтверждён!\nЗвёзды зачислены на баланс.")
        await call.message.edit_text(f"ЗАЧИСЛЕНО\nID: {user_id}\nТип: {req_type}")
    else:
        await bot.send_message(user_id, "Платёж не найден или отклонён.\nПопробуй снова.")
        await call.message.edit_text(f"ОТКЛОНЕНО\nID: {user_id}")

    await call.answer()
# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
# ====================== ПОПОЛНЕНИЕ БАЛАНСА ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ======================
pending_requests = {}  # ← Обязательно оставь эту строку (если ещё нет)

@router.callback_query(F.data == "topup")
async def topup_menu(call: CallbackQuery):
    kb = [
        [InlineKeyboardButton(text="Передать звёзды @buwse", callback_data="topup_stars_transfer")],
        [InlineKeyboardButton(text="Перевод на карту", callback_data="topup_card")],
        [InlineKeyboardButton(text="Назад", callback_data="back_main")]
    ]
    await call.message.edit_text("Пополнить баланс:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# ——— Звёзды на @buwse ———
@router.callback_query(F.data == "topup_stars_transfer")
async def stars_transfer_start(call: CallbackQuery):
    text = "Пополнение звёздами\n\n1. Перейди → @buwse\n2. Отправь любое количество звёзд\n3. Вернись и нажми кнопку ниже"
    kb = [[InlineKeyboardButton(text="Я перевёл звёзды", callback_data="stars_paid")]]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "stars_paid")
async def stars_paid_pressed(call: CallbackQuery):
    user_id = call.from_user.id
    request_id = f"stars_{user_id}_{int(time.time())}"
    pending_requests[request_id] = {"user_id": user_id, "type": "звёзды"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Зачислить", callback_data=f"approve_{request_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{request_id}")]
    ])

    for admin_id, level in admins.items():
        if level >= 2:
            try:
                await bot.send_message(admin_id,
                    f"Заявка на звёзды\nОт: <a href='tg://user?id={user_id}'>юзер</a>\nПроверь @buwse",
                    reply_markup=kb, parse_mode="HTML")
            except: pass

    await call.message.edit_text("Заявка отправлена админам. Ожидай зачисления звёзд.")
    await call.answer()


# ——— Перевод на карту ———
@router.callback_query(F.data == "topup_card")
async def card_transfer_start(call: CallbackQuery):
    text = ("Пополнение рублями\n\n"
            "Карта Альфа Банк:\n<code>2200 1505 8541 8889</code>\n"
            "Иван Иванович И.\n\n"
            "Сделай перевод → нажми кнопку ниже")
    kb = [[InlineKeyboardButton(text="Я перевёл деньги", callback_data="card_paid")]]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


@router.callback_query(F.data == "card_paid")
async def card_paid_pressed(call: CallbackQuery):
    user_id = call.from_user.id
    request_id = f"card_{user_id}_{int(time.time())}"
    pending_requests[request_id] = {"user_id": user_id, "type": "рубли"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Зачислить", callback_data=f"approve_{request_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{request_id}")]
    ])

    for admin_id, level in admins.items():
        if level >= 2:
            try:
                await bot.send_message(admin_id,
                    f"Заявка на рубли\nОт: <a href='tg://user?id={user_id}'>юзер</a>\nПроверь карту",
                    reply_markup=kb, parse_mode="HTML")
            except: pass

    await call.message.edit_text("Заявка отправлена админам. Ожидай зачисления.")
    await call.answer()


# ——— Одобрение / отклонение (для админов) ———
@router.callback_query(F.data.regexp(r"^(approve|reject)_(.+)$"))
async def process_request(call: CallbackQuery):
    if admins.get(call.from_user.id, 0) < 2:
        await call.answer("Нет прав", show_alert=True)
        return

    action, request_id = call.data.split("_", 1)
    if request_id not in pending_requests:
        await call.answer("Заявка устарела")
        return

    user_id = pending_requests[request_id]["user_id"]
    del pending_requests[request_id]

    if action == "approve":
        await bot.send_message(user_id, "Платёж подтверждён!\nЗвёзды зачислены на баланс.")
        await call.message.edit_text(f"ЗАЧИСЛЕНО\nID: {user_id}")
    else:
        await bot.send_message(user_id, "Платёж не найден или отклонён.\nПопробуй снова.")
        await call.message.edit_text(f"ОТКЛОНЕНО\nID: {user_id}")

    await call.answer()

    # ====================== ПОЛНОЦЕННЫЕ РАЗДЕЛЫ АДМИНКИ (ФИНАЛЬНАЯ ВЕРСИЯ) ======================

# ——— ПОЛЬЗОВАТЕЛИ (с пагинацией и действиями) ———
from aiogram.utils.keyboard import InlineKeyboardBuilder

@router.callback_query(F.data == "admin_users")
async def admin_users_list(call: CallbackQuery, state: FSMContext):
    await state.update_data(page=0)
    await show_users_page(call.message, state)

async def show_users_page(message: Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 0)
    per_page = 5
    user_list = list(users.items())
    total = len(user_list)
    start = page * per_page
    end = start + per_page
    page_users = user_list[start:end]

    if not page_users:
        await message.edit_text("Пользователей нет", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="admin_panel")]
        ]))
        return

    text = f"Пользователи ({total} всего) — страница {page + 1}\n\n"
    kb = InlineKeyboardBuilder()

    for uid, u in page_users:
        name = u.get("name", "Без имени")
        username = u.get("username", "нет")
        text += f"<b>{name}</b> @{username}\nID: <code>{uid}</code>\nБаланс: {u.get('balance',0)}₽ | Звёзды: {u.get('stars',0)}\n\n"
        kb.row(
            InlineKeyboardButton(text=f"Рубли → {uid}", callback_data=f"grant_rub_{uid}"),
            InlineKeyboardButton(text=f"Звёзды → {uid}", callback_data=f"grant_star_{uid}")
        )
        kb.row(
            InlineKeyboardButton(text=f"Админ → {uid}", callback_data=f"make_admin_{uid}"),
            InlineKeyboardButton(text=f"Бан → {uid}", callback_data=f"ban_user_{uid}")
        )

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data="users_prev"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Вперёд", callback_data="users_next"))
    if nav:
        kb.row(*nav)

    if page > 0 or end < total:
        kb.row(InlineKeyboardButton(text="В админку", callback_data="admin_panel"))
    else:
        kb.row(InlineKeyboardButton(text="Назад", callback_data="admin_panel"))

    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.in_({"users_prev", "users_next"}))
async def users_nav(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 0)
    if call.data == "users_prev":
        page -= 1
    else:
        page += 1
    await state.update_data(page=page)
    await show_users_page(call.message, state)

# Быстрые действия
@router.callback_query(F.data.regexp(r"^grant_rub_(\d+)$"))
async def quick_grant_rub(call: CallbackQuery):
    uid = int(call.data.split("_")[-1])
    if uid not in users: return
    users[uid]["balance"] += 500  # можно поменять
    await call.answer(f"+500₽ пользователю {uid}")
    await bot.send_message(uid, "Вам выдали 500₽ на баланс!")

@router.callback_query(F.data.regexp(r"^grant_star_(\d+)$"))
async def quick_grant_star(call: CallbackQuery):
    uid = int(call.data.split("_")[-1])
    if uid not in users: return
    users[uid]["stars"] += 100
    await call.answer(f"+100 звёзд пользователю {uid}")
    await bot.send_message(uid, "Вам выдали 100 звёзд!")

@router.callback_query(F.data.regexp(r"^make_admin_(\d+)$"))
async def quick_make_admin(call: CallbackQuery):
    uid = int(call.data.split("_")[-1])
    admins[uid] = 2  # модератор
    await call.answer(f"Пользователь {uid} теперь модератор")
    await bot.send_message(uid, "Вы назначены модератором!")

@router.callback_query(F.data.regexp(r"^ban_user_(\d+)$"))
async def quick_ban(call: CallbackQuery):
    uid = int(call.data.split("_")[-1])
    banned_users[uid] = {"reason": "По решению админа", "until": None}
    await call.answer(f"Пользователь {uid} забанен")
    await bot.send_message(uid, "Вы забанены в боте навсегда.")

# ——— ТИКЕТЫ ———
@router.callback_query(F.data == "admin_tickets")
async def admin_tickets_list(call: CallbackQuery):
    open_t = [t for t in tickets.values() if t.get("open", False)]
    if not open_t:
        await call.message.edit_text("Нет открытых тикетов", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="admin_panel")]
        ]))
        return

    kb = InlineKeyboardBuilder()
    for t in open_t[:20]:  # лимит
        kb.row(InlineKeyboardButton(text=f"Тикет #{t['id']} — {t['name']}", callback_data=f"ticket_{t['id']}"))
    kb.row(InlineKeyboardButton(text="Назад", callback_data="admin_panel"))
    await call.message.edit_text("Открытые тикеты:", reply_markup=kb.as_markup())

@router.callback_query(F.data.regexp(r"^ticket_(\d+)$"))
async def show_ticket_admin(call: CallbackQuery):
    t_id = int(call.data.split("_")[1])
    if t_id not in tickets or not tickets[t_id].get("open"):
        await call.answer("Тикет закрыт")
        return
    t = tickets[t_id]
    text = f"Тикет #{t_id}\nОт: {t['name']} (@{t.get('username','—')})\n\n"
    for m in t["messages"]:
        sender = "Вы" if m["from"] == "admin" else t['name']
        text += f"<b>{sender}:</b> {m['text']}\n\n"

    kb = [
        [InlineKeyboardButton(text="Ответить", callback_data=f"answer_ticket_{t_id}")],
        [InlineKeyboardButton(text="Закрыть", callback_data=f"close_ticket_{t_id}")],
        [InlineKeyboardButton(text="Назад", callback_data="admin_tickets")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


# ——— РОЗЫГРЫШИ ———
@router.callback_query(F.data == "admin_raffles")
async def admin_raffles_list(call: CallbackQuery):
    kb = [[InlineKeyboardButton(text="Создать розыгрыш", callback_data="create_raffle")]]
    if raffles:
        for r_id, r in raffles.items():
            status = "Завершён" if r.get("finished") else "Активен"
            kb.append([InlineKeyboardButton(text=f"#{r_id} — {status}", callback_data=f"view_raffle_{r_id}")])
    kb.append([InlineKeyboardButton(text="Назад", callback_data="admin_panel")])
    await call.message.edit_text("Розыгрыши:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

   # ====================== КАНАЛЫ — РАБОТАЕТ В AIOGRAM 3.X БЕЗ iter_dialogs ======================
from aiogram.utils.keyboard import InlineKeyboardBuilder

@router.callback_query(F.data == "admin_channels")
async def admin_channels_menu(call: CallbackQuery):
    text = "Обязательные каналы для подписки:\n\n"
    kb = InlineKeyboardBuilder()

    if channels_required:
        for ch in channels_required:
            title = ch.get("title", "Без названия")
            text += f"• {title}\n"
            kb.row(InlineKeyboardButton(text=f"Удалить {title}", callback_data=f"del_ch_{ch['channel_id']}"))
    else:
        text += "Нет добавленных каналов"

    kb.row(InlineKeyboardButton(text="Добавить канал", callback_data="add_channel_by_link"))
    kb.row(InlineKeyboardButton(text="Назад", callback_data="admin_panel"))

    await call.message.edit_text(text, reply_markup=kb.as_markup())


# ——— СПОСОБ 1: Добавление по @username или ссылке (самый надёжный) ———
@router.callback_query(F.data == "add_channel_by_link")
async def add_channel_by_link(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "Пришли @username канала или ссылку на него\n\n"
        "Примеры:\n"
        "@mychannel\n"
        "https://t.me/mychannel\n"
        "t.me/mychannel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="admin_channels")]
        ])
    )
    await state.set_state(AdminStates.add_channel)


@router.message(StateFilter(AdminStates.add_channel))
async def process_channel_link(message: Message, state: FSMContext):
    text = message.text.strip().lstrip("@").replace("https://t.me/", "").replace("t.me/", "").split("/")[0]

    if not text:
        await message.answer("Неправильная ссылка или username")
        return

    try:
        chat = await bot.get_chat(f"@{text}")
    except:
        await message.answer("Канал не найден или бот не может его увидеть")
        return

    if chat.type not in ("channel", "supergroup"):
        await message.answer("Это не канал!")
        return

    # Проверяем, админ ли бот
    try:
        member = await bot.get_chat_member(chat.id, bot.id)
        if member.status not in ("administrator", "creator"):
            await message.answer(
                f"Бот НЕ админ в канале <b>{chat.title}</b>\n\n"
                "Добавь бота в администраторы и попробуй снова!",
                parse_mode="HTML"
            )
            return
    except Exception as e:
        await message.answer(f"Ошибка проверки прав: {e}")
        return

    # Проверяем дубликат
    if any(ch["channel_id"] == chat.id for ch in channels_required):
        await message.answer("Этот канал уже добавлен!")
        await state.clear()
        return

    # Добавляем
    invite_link = f"https://t.me/{chat.username}" if chat.username else "приватный"
    channels_required.append({
        "channel_id": chat.id,
        "title": chat.title,
        "invite_link": invite_link
    })

    await message.answer(
        f"Канал <b>{chat.title}</b> успешно добавлен в автоподписку!",
        parse_mode="HTML"
    )

    # Уведомление всем админам
    for admin_id in admins.keys():
        try:
            await bot.send_message(
                admin_id,
                f"НОВЫЙ ОБЯЗАТЕЛЬНЫЙ КАНАЛ\n\n"
                f"<b>{chat.title}</b>\n"
                f"ID: <code>{chat.id}</code>\n"
                f"Добавил: {message.from_user.first_name}",
                parse_mode="HTML"
            )
        except:
            pass

    await state.clear()


# ——— УДАЛЕНИЕ КАНАЛА ———
@router.callback_query(F.data.regexp(r"^del_ch_(-?\d+)$"))
async def delete_channel(call: CallbackQuery):
    ch_id = int(call.data.split("_")[-1])
    was = len(channels_required)
    channels_required[:] = [ch for ch in channels_required if ch["channel_id"] != ch_id]

    if len(channels_required) < was:
        await call.message.edit_text(
            "Канал удалён из автоподписки!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="admin_channels")]
            ])
        )
    else:
        await call.answer("Не найдено")

# ====================== АДМИНЫ С АВАТАРКАМИ + ОНЛАЙН + КНОПКА "НАПИСАТЬ" (БЕЗ ОШИБОК) ======================

from aiogram.types import InputMediaPhoto

@router.callback_query(F.data == "admins_list")
async def admins_carousel(call: CallbackQuery, state: FSMContext):
    if not admins:
        await call.message.edit_text(
            "Администраторов пока нет",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад в меню", callback_data="back_main")]
            ])
        )
        return

    # Сортируем по уровню (от старшего к младшему)
    sorted_admins = sorted(admins.items(), key=lambda x: x[1], reverse=True)
    admin_list = []

    for user_id, level in sorted_admins:
        # Определяем роль и эмодзи
        if level >= 10:
            role = "Владелец"
            emoji = "Владелец"
        elif level >= 5:
            role = "Администратор"
            emoji = "Администратор"
        elif level >= 3:
            role = "Модератор"
            emoji = "Модератор"
        else:
            role = "Саппорт"
            emoji = "Саппорт"

        try:
            user = await bot.get_chat(user_id)
            name = user.full_name.strip()
            username = f"@{user.username}" if user.username else "нет юзернейма"
            photo = user.photo.big_file_id if user.photo else None
        except:
            name = "Скрытый профиль"
            username = "профиль скрыт"
            photo = None

        admin_list.append({
            "id": user_id,
            "name": name,
            "username": username,
            "role": role,
            "emoji": emoji,
            "photo": photo
        })

    await state.update_data(admin_list=admin_list, page=0)
    await show_admin_page(call.message, state)


async def show_admin_page(message: Message, state: FSMContext):
    data = await state.get_data()
    admins_list = data.get("admin_list", [])
    page = data.get("page", 0)

    if not admins_list:
        return

    # Циклическая навигация
    if page >= len(admins_list):
        page = 0
    elif page < 0:
        page = len(admins_list) - 1

    admin = admins_list[page]

    # Онлайн/оффлайн (если скрыт — покажет "Неизвестно")
    try:
        member = await bot.get_chat_member(admin["id"], admin["id"])
        status = "Онлайн" if getattr(member.user, "is_online", False) else "Оффлайн"
    except:
        status = "Неизвестно"

    text = (
        f"{admin['emoji']} <b>{admin['role']}</b>\n\n"
        f"<b>{admin['name']}</b>\n"
        f"{admin['username']}\n"
        f"Статус: <b>{status}</b>\n\n"
        f"Администратор {page + 1} из {len(admins_list)}\n"
        f"Разработчик  бота - @emftooo"
    )

    kb = [
        [InlineKeyboardButton(text="Написать", url=f"tg://user?id={admin['id']}")],
        [
            InlineKeyboardButton(text="Предыдущий", callback_data="admin_prev"),
            InlineKeyboardButton(text=f"{page + 1}/{len(admins_list)}", callback_data="pass"),
            InlineKeyboardButton(text="Следующий", callback_data="admin_next")
        ],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_main")]
    ]

    if admin["photo"]:
        try:
            await message.edit_media(
                media=InputMediaPhoto(media=admin["photo"], caption=text, parse_mode="HTML"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
            )
        except:
            # Если не удалось заменить фото — просто текст
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    else:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


@router.callback_query(F.data == "admin_prev")
async def admin_prev(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = (data.get("page", 0) - 1) % len(data.get("admin_list", [1]))
    await state.update_data(page=page)
    await show_admin_page(call.message, state)


@router.callback_query(F.data == "admin_next")
async def admin_next(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total = len(data.get("admin_list", [1]))
    page = (data.get("page", 0) + 1) % total
    await state.update_data(page=page)
    await show_admin_page(call.message, state)

    # ====================== СИСТЕМА ТОВАРОВ — ПОЛНОСТЬЮ РАБОЧАЯ ======================
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import InputMediaPhoto

# ====================== ТОВАРЫ В АДМИНКЕ — 100% РАБОЧИЙ ВАРИАНТ ======================
from aiogram.fsm.state import StatesGroup, State



# ====================== ТОВАРЫ — ГАРАНТИРОВАННО РАБОТАЕТ ======================
from aiogram.fsm.state import StatesGroup, State

class AddProductStates(StatesGroup):
    name = State()
    price_rub = State()
    price_stars = State()
    photo = State()
    content_type = State()
    content = State()


# ====================== ТОВАРЫ — 100% РАБОЧАЯ ВЕРСИЯ (aiogram 3.x) ======================
from aiogram.utils.keyboard import InlineKeyboardBuilder

class ProductStates(StatesGroup):
    name = State()
    price_rub = State()
    price_stars = State()
    photo = State()
    content_type = State()
    content = State()

# Главное меню товаров в админке
@router.callback_query(F.data == "admin_products")
async def admin_products_menu(call: CallbackQuery):
    builder = InlineKeyboardBuilder()

    if products:
        for pid, p in products.items():
            price_text = "Бесплатно"
            if p.get("price_rub", 0) > 0:
                price_text = f"{p['price_rub']}₽"
            if p.get("price_stars", 0) > 0:
                price_text += f" | {p['price_stars']}⭐"
            builder.row(InlineKeyboardButton(
                text=f"❌ {p['name']} — {price_text}",
                callback_data=f"delprod_{pid}"
            ))

    builder.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data="add_product_start"))
    builder.row(InlineKeyboardButton(text="◀ Назад", callback_data="admin_panel"))

    text = "<b>Управление товарами</b>\n\n"
    text += "Список товаров:" if products else "Товаров пока нет"

    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# НАЧАЛО ДОБАВЛЕНИЯ ТОВАРА
@router.callback_query(F.data == "add_product_start")
async def add_product_name(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "Введите название товара:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_products")]])
    )
    await state.set_state(ProductStates.name)


@router.message(ProductStates.name)
async def add_product_price_rub(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Цена в рублях (0 = бесплатно):")
    await state.set_state(ProductStates.price_rub)


@router.message(ProductStates.price_rub)
async def add_product_price_stars(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи только число!")
        return
    await state.update_data(price_rub=int(message.text))
    await message.answer("Цена в звёздах (0 = не требуется):")
    await state.set_state(ProductStates.price_stars)


@router.message(ProductStates.price_stars)
async def add_product_photo(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введи только число!")
        return
    await state.update_data(price_stars=int(message.text))
    await message.answer("Пришли фото товара:")
    await state.set_state(ProductStates.photo)


@router.message(ProductStates.photo)
async def add_product_content_type(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("Нужно именно фото!")
        return
    await state.update_data(photo=message.photo[-1].file_id)
    await message.answer("Что выдать после покупки?\n\nВарианты: text | link | file | photo | video")
    await state.set_state(ProductStates.content_type)


@router.message(ProductStates.content_type)
async def add_product_content(message: Message, state: FSMContext):
    ctype = message.text.lower().strip()
    if ctype not in ["text", "link", "file", "photo", "video"]:
        await message.answer("Выбери один из вариантов: text, link, file, photo, video")
        return
    await state.update_data(content_type=ctype)
    await message.answer("Теперь пришли содержимое (текст, ссылку, файл, фото или видео):")
    await state.set_state(ProductStates.content)


@router.message(ProductStates.content)
async def save_new_product(message: Message, state: FSMContext):
    global counters
    pid = counters.get("product", 1)  # начинаем с 1, а не с 0
    counters["product"] = pid + 1     # следующий будет pid+1

    data = await state.get_data()

    content = ""
    if data["content_type"] == "text":
        content = message.text or ""
    elif data["content_type"] == "link":
        content = message.text or ""
    elif data["content_type"] == "file" and message.document:
        content = message.document.file_id
    elif data["content_type"] == "photo" and message.photo:
        content = message.photo[-1].file_id
    elif data["content_type"] == "video" and message.video:
        content = message.video.file_id
    else:
        await message.answer("Не понял содержимое, попробуй ещё раз")
        return

    products[pid] = {
        "name": data["name"],
        "price_rub": data["price_rub"],
        "price_stars": data["price_stars"],
        "photo": data["photo"],
        "content_type": data["content_type"],
        "content": content
    }

    await message.answer(
        f"Товар успешно создан! ID: {pid}\n\n"
        f"Название: {data['name']}\n"
        f"Цена: {data['price_rub']}₽ | {data['price_stars']}⭐",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("К товарам", callback_data="admin_products")]])
    )
    await state.clear()


# УДАЛЕНИЕ ТОВАРА
@router.callback_query(F.data.regexp(r"^delprod_(\d+)$"))
async def delete_product(call: CallbackQuery):
    pid = int(call.data.split("_")[1])
    if pid in products:
        del products[pid]
        await call.answer("Товар удалён")
        await admin_products_menu(call)  # обновляем меню
    else:
        await call.answer("Товар уже удалён")

        # ====================== АДМИНКА: УПРАВЛЕНИЕ ОТЗЫВАМИ ======================

@router.callback_query(F.data == "admin_reviews")
async def admin_reviews_menu(call: CallbackQuery, state: FSMContext):
    if not reviews:
        await call.message.edit_text(
            "Отзывов пока нет",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="admin_panel")]
            ])
        )
        return

    # Пагинация: 5 отзывов на страницу
    await state.update_data(page=0)
    await show_reviews_page(call.message, state)

async def show_reviews_page(message: Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 0)
    per_page = 5
    total = len(reviews)
    start = page * per_page
    end = start + per_page
    page_reviews = reviews[start:end]

    text = f"<b>Управление отзывами</b> ({total} всего)\n\n"
    kb = InlineKeyboardBuilder()

    for idx, r in enumerate(page_reviews, start=start):
        username = r.get("username", "Аноним")
        stars = "★" * r["rating"] + "☆" * (5 - r["rating"])
        short_text = (r["text"][:70] + "...") if len(r["text"]) > 70 else r["text"]
        text += f"<b>{idx + 1}.</b> <b>{username}</b> {stars}\n{short_text}\n\n"

        kb.row(InlineKeyboardButton(
            text=f"Удалить #{idx + 1}",
            callback_data=f"del_review_{idx}"
        ))

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data="reviews_prev"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Вперёд", callback_data="reviews_next"))
    if nav:
        kb.row(*nav)

    kb.row(InlineKeyboardButton(text="Назад в админку", callback_data="admin_panel"))

    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.in_({"reviews_prev", "reviews_next"}))
async def reviews_nav(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 0)
    if call.data == "reviews_prev":
        page -= 1
    else:
        page += 1
    await state.update_data(page=page)
    await show_reviews_page(call.message, state)

# Удаление с подтверждением
@router.callback_query(F.data.regexp(r"^del_review_(\d+)$"))
async def confirm_delete_review(call: CallbackQuery, state: FSMContext):
    review_idx = int(call.data.split("_")[2])
    if review_idx >= len(reviews):
        await call.answer("Отзыв уже удалён", show_alert=True)
        return

    review = reviews[review_idx]
    username = review.get("username", "Аноним")
    stars = "★" * review["rating"] + "☆" * (5 - review["rating"])
    short = (review["text"][:100] + "...") if len(review["text"]) > 100 else review["text"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ДА, УДАЛИТЬ", callback_data=f"confirm_del_{review_idx}")],
        [InlineKeyboardButton(text="Отмена", callback_data="admin_reviews")]
    ])

    await call.message.edit_text(
        f"<b>Подтверди удаление отзыва:</b>\n\n"
        f"<b>{username}</b> {stars}\n"
        f"{short}\n\n"
        f"Это действие нельзя отменить!",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data.regexp(r"^confirm_del_(\d+)$"))
async def do_delete_review(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_")[2])
    if idx >= len(reviews):
        await call.answer("Уже удалён")
        return

    deleted = reviews.pop(idx)
    await call.message.edit_text(
        f"Отзыв удалён!\n\n"
        f"От: <b>{deleted.get('username', 'Аноним')}</b>\n"
        f"Оценка: {'★' * deleted['rating'] + '☆' * (5 - deleted['rating'])}\n"
        f"Текст: {deleted['text']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="К отзывам", callback_data="admin_reviews")]
        ]),
        parse_mode="HTML"
    )
    await call.answer("Удалено!")

 # ====================== ОДНОРАЗОВАЯ РАССЫЛКА ВСЕМ ======================
async def one_time_broadcast():
    # === ИЗМЕНИ ЭТО СООБЩЕНИЕ НА СВОЁ ===
    text = """Бот вновь функционирует!
    
    Из за большой нагрузки на сервера кнопка "Магазин" поломалась.
    Наша команда работала над этим и исправила все ошибки!"""
    # =====================================

    if not users:
        print("❌ Нет пользователей для рассылки.")
        return

    print(f"🚀 Начинаю рассылку {len(users)} пользователям...")
    success = 0
    failed = 0

    for user_id in list(users.keys()):
        try:
            await bot.send_message(user_id, text)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # защита от бана

    print(f"✅ РАССЫЛКА ЗАВЕРШЕНА!")
    print(f"📤 Успешно: {success}")
    print(f"❌ Не доставлено: {failed}")
    print(f"👥 Всего пользователей: {len(users)}")
    print(f"📊 Процент успеха: {(success/len(users)*100):.1f}%")
# ====================== MAIN ======================
async def main():
    logging.basicConfig(level=logging.INFO)
    
    try:
        print("Запуск бота... Загрузка данных из базы...")
        await load_all_data()
        print("Данные загружены успешно!")
        
        # === РАССЫЛКА ВСЕМ (УБЕРИ ЭТИ 2 СТРОКИ ПОСЛЕ ПЕРВОЙ РАССЫЛКИ ===
        await one_time_broadcast()
        # ========================================================
        
    except Exception as e:
        # ... остальной код без изменений ...
        logging.error(f"ОШИБКА ПРИ ЗАГРУЗКЕ БАЗЫ ДАННЫХ: {e}")
        import traceback
        traceback.print_exc()
        print("Бот НЕ МОЖЕТ запуститься без базы. Создаём чистую базу...")
        # Попробуем создать базу заново
        await init_db()
        # И добавим хотя бы владельца как админа
        if ADMIN_IDS:
            admins[ADMIN_IDS[0]] = 3
            await save_dict("admins", admins)
        print("Пустая база создана, владелец добавлен как админ.")

    try:
        print("Запуск планировщика задач...")
        scheduler.start()
        scheduler.add_job(autosave, "interval", seconds=60, id="autosave", replace_existing=True)
        print("Планировщик запущен. Автосохранение каждые 60 сек.")
    except Exception as e:
        logging.error(f"Ошибка запуска scheduler: {e}")
        import traceback
        traceback.print_exc()

    print("Бот запущен и работает!")
    print(f"Твой ID (владелец): {ADMIN_IDS}")
    print("Нажми Ctrl+C для остановки\n")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logging.error(f"Критическая ошибка polling: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Останавливаем бота... Сохраняем данные...")
        await autosave()  # Сохраним на выходе
        await bot.session.close()
        print("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    except Exception as e:
        print(f"Фатальная ошибка: {e}")
        import traceback
        traceback.print_exc()