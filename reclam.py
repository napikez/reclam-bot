import asyncio
import random
import os
import sys
import json
import time
import warnings
from collections import Counter

# ===== ФИКС ДЛЯ PYTHON =====
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, errors, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant
from pyrogram.enums import ChatAction, ChatMemberStatus, ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiohttp import web

# ===== ВЕБ-СЕРВЕР ДЛЯ UPTIMEROBOT =====
async def health_check(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")
    while True:
        await asyncio.sleep(3600)

warnings.filterwarnings("ignore")
original_stderr = sys.stderr

class FilteredStderr:
    def write(self, text):
        if text and not any(x in text for x in ["Peer id invalid", "ID not found", "Task exception was never retrieved"]):
            original_stderr.write(text)
    def flush(self):
        original_stderr.flush()

sys.stderr = FilteredStderr()

API_ID = int(os.getenv("API_ID", 37635168))
API_HASH = os.getenv("API_HASH", "47e36b7f99b31f55be222b4200ea94ca")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8361641777").split(",")]

SESSIONS_FILE = "sessions.json"
USERS_DB = "users_db.json"
ALLOWED_USERS_FILE = "allowed_users.json"
ERROR_LOG = "error.log"

START_GIF_URL = os.getenv("START_GIF_URL", "https://i.postimg.cc/Y0z1tvpv/pinnsaver-c4f2378bff1a8783e55571f6099484da.gif")

bot_logs = []

def add_bot_log(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {message}"
    bot_logs.append(log_entry)
    if len(bot_logs) > 150:
        bot_logs.pop(0)
    print(f"[ЛОГ] {log_entry}")

def log_error(error_text):
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_text}\n")

def load_json(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_sessions():
    data = load_json(SESSIONS_FILE, {})
    converted = {}
    for key, value in data.items():
        if isinstance(value, str):
            converted[key] = {"phone": value, "type": "парсинг"}
        else:
            converted[key] = value
    return converted

def get_first_session():
    sessions = load_sessions()
    if sessions:
        return list(sessions.keys())[0]
    return None

def load_allowed_users():
    default_users = [8361641777, 1843278717, 8442524073, 472576710, 8512512297, 8289868542]
    users = load_json(ALLOWED_USERS_FILE, default_users)
    return [int(u) for u in users]

def save_allowed_users(users):
    save_json(ALLOWED_USERS_FILE, list(set(users)))

async def join_with_retry(app, link, session_name):
    for attempt in range(3):
        try:
            chat = await app.join_chat(link)
            return chat.id, True, False
        except FloodWait as e:
            wait_time = e.value if hasattr(e, 'value') else 60
            await asyncio.sleep(wait_time + 2)
            continue
        except UserAlreadyParticipant:
            try:
                chat = await app.get_chat(link)
                return chat.id, True, False
            except:
                return None, False, False
        except Exception as e:
            error_str = str(e).lower()
            if any(x in error_str for x in ["inviterequestsent", "privacy", "request to join"]):
                return None, False, True
            if "t.me/" in link:
                clean_link = link.replace("https://t.me/", "").replace("http://t.me/", "").split("?")[0]
                if not clean_link.startswith("+"):
                    try:
                        chat = await app.join_chat(clean_link)
                        return chat.id, True, False
                    except:
                        pass
            return None, False, False
    return None, False, False

def calculate_chance(user_data, total_messages):
    msg_count = user_data.get("message_count", 0)
    has_username = 1 if user_data.get("username") else 0
    is_admin = 1 if user_data.get("is_admin", False) else 0
    
    if msg_count == 0: msg_score = 0
    elif msg_count <= 3: msg_score = 30
    elif msg_count <= 10: msg_score = 60
    elif msg_count <= 30: msg_score = 80
    else: msg_score = 50
    
    username_score = 20 if has_username else 0
    admin_penalty = -30 if is_admin else 0
    chance = msg_score + username_score + admin_penalty + random.randint(-5, 5)
    return max(0, min(100, chance))

async def send_results_to_chat(app, result, output_chat_id):
    try:
        result.sort(key=lambda x: x[1].get("chance", 0), reverse=True)
        
        blocks = []
        for uid, data in result:
            chance = data.get("chance", 0)
            username = data.get("username", "unknown")
            msg_count = data.get("message_count", 0)
            is_premium = data.get("is_premium", False)

            status_text = "Premium" if is_premium else "Обычный"
            
            if username and username != "unknown":
                user_display = f"@{username}"
                user_link = f"https://t.me/{username}"
            else:
                user_display = data.get("first_name", "Без имени")
                user_link = f"tg://openmessage?user_id={uid}"

            block = (
                f"<b>{user_display}</b>\n"
                f"Сообщения: {msg_count} | Шанс: {chance}%\n"
                f"Статус: {status_text}\n"
                f"Ссылка: {user_link}\n"
                + "-"*30
            )
            blocks.append(block)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
        MAX_MSG_LEN = 4000
        current_text = f"Результаты парсинга:\nВсего найдено: <b>{len(result)}</b>\n\n"
        
        for idx, block in enumerate(blocks):
            if len(current_text) + len(block) + 2 > MAX_MSG_LEN:
                await app.send_message(output_chat_id, current_text, parse_mode=ParseMode.HTML)
                current_text = ""
            
            current_text += block + "\n"
            
        if current_text:
            await app.send_message(output_chat_id, current_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            
    except Exception as e:
        log_error(f"Ошибка отправки результатов: {e}")

async def parse_chat_for_active_users(target_link, limit=500, max_users=30, status_msg=None, bot_app=None, output_chat_id=None):
    session_name = get_first_session()
    if not session_name:
        if status_msg: await status_msg.edit_text("Ошибка: Нет доступных сессий для парсинга.")
        return []

    userbot = Client(session_name, api_id=API_ID, api_hash=API_HASH)
    try:
        await userbot.start()
    except Exception as e:
        if status_msg: await status_msg.edit_text(f"Ошибка запуска сессии: {e}")
        return []

    if status_msg: await status_msg.edit_text("Подключаюсь к чату...")
    chat_id, success, pending = await join_with_retry(userbot, target_link, session_name)
    
    if pending:
        if status_msg: await status_msg.edit_text("Заявка отправлена. Ожидание одобрения...")
        for _ in range(12):
            await asyncio.sleep(10)
            try:
                chat = await userbot.get_chat(target_link)
                chat_id = chat.id
                success = True
                break
            except:
                pass
        if not success:
            if status_msg: await status_msg.edit_text("Ошибка: Чат недоступен или заявка не принята.")
            await userbot.stop()
            return []

    admin_ids = set()
    if success:
        try:
            async for member in userbot.get_chat_members(chat_id, filter="administrators"):
                if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    admin_ids.add(member.user.id)
        except:
            pass

    if status_msg: await status_msg.edit_text("Анализирую сообщения...")
    users_db = load_json(USERS_DB, {})
    user_message_count = Counter()
    collected_users = {}

    try:
        message_count = 0
        async for message in userbot.get_chat_history(chat_id, limit=limit):
            message_count += 1
            if message_count % 200 == 0 and status_msg:
                await status_msg.edit_text(f"Парсинг... Обработано {message_count} сообщений.")

            if not message.from_user or message.from_user.is_bot:
                continue
            
            user = message.from_user
            if user.id in admin_ids:
                continue
            
            user_message_count[user.id] += 1
            if user.id not in collected_users and len(collected_users) < max_users * 3:
                try:
                    member = await userbot.get_chat_member(chat_id, user.id)
                    is_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
                except:
                    is_admin = False

                if is_admin or not user.username:
                    continue

                collected_users[str(user.id)] = {
                    "id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "is_premium": getattr(user, 'is_premium', False)
                }

        if not collected_users:
            if status_msg: await status_msg.edit_text("Активных пользователей не найдено.")
            await userbot.stop()
            return []

        sorted_users = sorted([(uid, data) for uid, data in collected_users.items()],
                              key=lambda x: user_message_count.get(int(x[0]), 0), reverse=True)[:max_users]

        total_messages = sum(user_message_count.get(int(uid), 0) for uid, _ in sorted_users) or 1
        result = []
        for uid, data in sorted_users:
            data["message_count"] = user_message_count.get(int(uid), 0)
            data["chance"] = calculate_chance(data, total_messages)
            users_db[uid] = data
            result.append((uid, data))

        save_json(USERS_DB, users_db)
        if status_msg: await status_msg.edit_text("Поиск завершен. Формирую результаты...")
        
        await send_results_to_chat(bot_app, result, output_chat_id)
        if status_msg: await status_msg.delete()
    except Exception as e:
        log_error(f"Ошибка парсинга: {e}")
        if status_msg: await status_msg.edit_text("Ошибка парсинга. Возможно, чат закрыт.")

    await userbot.stop()
    return result

# ============================================================
# ===== ЗАПУСК TELEGRAM БОТА =====
# ============================================================

def run_telegram_bot():
    if not BOT_TOKEN:
        print("Ошибка: Не задан BOT_TOKEN!")
        return

    bot_app = Client("my_bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    def get_start_text(first_name):
        return (
            f"Hello my friend {first_name}\n\n"
            "Команды (нажми, чтобы скопировать):\n"
            "<code>/pars</code> [ссылка] [сообщ] [юзеров] — парсинг чата.\n"
            "<code>/help</code> — шаблоны команд.\n\n"
            "Admin: @xurder / @lurder"
        )
        
    def get_start_keyboard(user_id):
        buttons = [[InlineKeyboardButton("Шаблоны", callback_data="help")]]
        if user_id in ADMIN_IDS:
            buttons.append([InlineKeyboardButton("Логи", callback_data="logs"), InlineKeyboardButton("Юзеры", callback_data="users")])
        return InlineKeyboardMarkup(buttons)

    @bot_app.on_message(filters.command(["start"]))
    async def start_cmd(client, message):
        user = message.from_user
        add_bot_log(f"ID {user.id} вызвал /start")
        
        allowed = load_allowed_users()
        if user.id not in allowed and user.id not in ADMIN_IDS:
            await message.reply(f"У вас нет доступа к боту.\nВаш ID: <code>{user.id}</code>\n\nПередайте его администратору.", parse_mode=ParseMode.HTML)
            return

        text = get_start_text(user.first_name)
        kb = get_start_keyboard(user.id)
        
        try:
            await message.reply_animation(animation=START_GIF_URL, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except:
            await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    @bot_app.on_message(filters.command(["pars"]))
    async def pars_cmd(client, message):
        user = message.from_user
        allowed = load_allowed_users()
        if user.id not in allowed and user.id not in ADMIN_IDS:
            return

        args = message.text.split()
        if len(args) < 2:
            await message.reply("Пример: <code>/pars https://t.me/durov 20000 100</code>", parse_mode=ParseMode.HTML)
            return

        target_link = args[1]
        limit = int(args[2]) if len(args) >= 3 else 20000
        max_users = int(args[3]) if len(args) >= 4 else 100

        status_msg = await message.reply("Поиск...")
        add_bot_log(f"ID {user.id} запустил парсинг: {target_link}")

        asyncio.create_task(parse_chat_for_active_users(
            target_link=target_link,
            limit=limit,
            max_users=max_users,
            status_msg=status_msg,
            bot_app=client,
            output_chat_id=user.id
        ))

    @bot_app.on_message(filters.command(["help"]))
    async def help_cmd(client, message):
        user = message.from_user
        allowed = load_allowed_users()
        if user.id not in allowed and user.id not in ADMIN_IDS:
            return
            
        help_text = (
            "Команды (нажми, чтобы скопировать):\n"
            "<code>/pars https://t.me/название_канала</code> — обычный парсинг.\n"
            "<code>/pars https://t.me/название_канала 300 20</code> — с настройками.\n\n"
            "Пример: <code>/pars https://t.me/durov 20000 100</code>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
        await message.reply(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)

    # ===== ИНЛАЙН КНОПКИ =====
    @bot_app.on_callback_query()
    async def callback_handler(client, query: CallbackQuery):
        user_id = query.from_user.id
        allowed = load_allowed_users()
        
        if user_id not in allowed and user_id not in ADMIN_IDS:
            await query.answer("Нет доступа!", show_alert=True)
            return

        if query.data == "menu":
            text = get_start_text(query.from_user.first_name)
            kb = get_start_keyboard(user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            
        elif query.data == "help":
            help_text = (
                "Команды (нажми, чтобы скопировать):\n"
                "<code>/pars https://t.me/название_канала</code> — обычный парсинг.\n"
                "<code>/pars https://t.me/название_канала 300 20</code> — с настройками."
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
            await query.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            
        elif query.data == "logs":
            if user_id not in ADMIN_IDS:
                await query.answer("Нет доступа", show_alert=True)
                return
            logs_text = "\n".join(bot_logs[-15:]) if bot_logs else "Логов пока нет"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
            await query.message.edit_text(f"<b>Последние логи:</b>\n\n<code>{logs_text}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
            
        elif query.data == "users":
            if user_id not in ADMIN_IDS:
                await query.answer("Нет доступа", show_alert=True)
                return
            users_list = "\n".join([f"• <code>{u}</code>" for u in allowed])
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
            await query.message.edit_text(f"<b>Пользователи с доступом:</b>\n\n{users_list}", reply_markup=kb, parse_mode=ParseMode.HTML)

    # ===== АДМИНСКИЕ КОМАНДЫ =====
    @bot_app.on_message(filters.command(["allow"]))
    async def allow_user(client, message):
        if message.from_user.id not in ADMIN_IDS:
            return
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.reply("Формат выдачи доступа: <code>/allow 12345678</code>", parse_mode=ParseMode.HTML)
            return
        uid = int(args[1])
        allowed = load_allowed_users()
        if uid not in allowed:
            allowed.append(uid)
            save_allowed_users(allowed)
        await message.reply(f"Пользователь <code>{uid}</code> получил доступ.", parse_mode=ParseMode.HTML)
        add_bot_log(f"ID {uid} получил доступ")

    @bot_app.on_message(filters.command(["unallow"]))
    async def unallow_user(client, message):
        if message.from_user.id not in ADMIN_IDS:
            return
        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.reply("Формат удаления доступа: <code>/unallow 12345678</code>", parse_mode=ParseMode.HTML)
            return
        uid = int(args[1])
        allowed = load_allowed_users()
        if uid in allowed:
            allowed.remove(uid)
            save_allowed_users(allowed)
        await message.reply(f"Пользователь <code>{uid}</code> лишен доступа.", parse_mode=ParseMode.HTML)
        add_bot_log(f"У ID {uid} забрали доступ")

    print("Запуск бота через токен BotFather...")
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    bot_app.run()

if __name__ == "__main__":
    run_telegram_bot()
