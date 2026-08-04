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
from pyrogram.errors import FloodWait, UserAlreadyParticipant, SessionPasswordNeeded
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

# Настройки
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
active_sessions = set()  
auth_steps = {}  

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

def load_allowed_users():
    default_users = [8361641777, 1843278717, 8442524073, 472576710, 8512512297, 8289868542]
    users = load_json(ALLOWED_USERS_FILE, default_users)
    return [int(u) for u in users]

def save_allowed_users(users):
    save_json(ALLOWED_USERS_FILE, list(set(users)))

# ============================================================
# ===== ЛОГИКА ПАРСИНГА =====
# ============================================================

async def join_with_retry(app, link):
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
            username = data.get("username", "")
            msg_count = data.get("message_count", 0)
            is_premium = data.get("is_premium", False)
            status_text = "Premium" if is_premium else "Обычный"
            
            if username:
                user_display = f"@{username}"
                user_link = f"https://t.me/{username}"
            else:
                first_name = data.get("first_name")
                if not first_name: first_name = "Без имени"
                safe_name = str(first_name).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                user_display = f"{safe_name}"
                user_link = f"tg://user?id={uid}"

            block = (
                f"👤 <b>{user_display}</b>\n"
                f"💬 Сообщения: {msg_count} | 🎯 Шанс: {chance}%\n"
                f"💎 Статус: {status_text}\n"
                f"🔗 <a href=\"{user_link}\">Перейти к пользователю</a>\n"
                + "▬"*15
            )
            blocks.append(block)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("В меню", callback_data="menu_edit")],
            [InlineKeyboardButton("В меню (оставить список)", callback_data="menu_keep")]
        ])
        
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

async def parse_chat_for_active_users(session_name, target_link, limit=500, max_users=30, status_msg=None, bot_app=None, output_chat_id=None):
    userbot = Client(session_name, api_id=API_ID, api_hash=API_HASH)
    try:
        await userbot.start()
    except Exception as e:
        if status_msg: await status_msg.edit_text(f"Ошибка запуска сессии <code>{session_name}</code>:\n{e}", parse_mode=ParseMode.HTML)
        return []

    if status_msg: await status_msg.edit_text(f"Подключаюсь к чату... (Сессия: {session_name})")
    chat_id, success, pending = await join_with_retry(userbot, target_link)
    
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
    admin_usernames = set()
    if success:
        try:
            async for member in userbot.get_chat_members(chat_id, filter="administrators"):
                if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    admin_ids.add(member.user.id)
                    if member.user.username:
                        admin_usernames.add(member.user.username.lower())
        except:
            pass

    try:
        if status_msg: await status_msg.edit_text("Проверяю список администраторов через бота...")
        trigger_msg = await userbot.send_message(chat_id, "кто админ")
        await asyncio.sleep(3)
        async for msg in userbot.get_chat_history(chat_id, limit=6):
            if msg.id > trigger_msg.id and msg.text:
                text_lower = msg.text.lower()
                if "админ" in text_lower or "создатель" in text_lower or "старшие" in text_lower or "младший" in text_lower:
                    if msg.entities:
                        for entity in msg.entities:
                            if entity.type == "mention":
                                uname = msg.text[entity.offset : entity.offset + entity.length].replace("@", "")
                                admin_usernames.add(uname.lower())
                            elif entity.type == "text_mention" and entity.user:
                                admin_ids.add(entity.user.id)
                    break
    except Exception as e:
        log_error(f"Ошибка триггера админов: {e}")

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
            
            if user.id in admin_ids or (user.username and user.username.lower() in admin_usernames):
                continue
            
            user_message_count[user.id] += 1
            if str(user.id) not in collected_users and len(collected_users) < max_users * 3:
                try:
                    member = await userbot.get_chat_member(chat_id, user.id)
                    is_admin_check = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
                except:
                    is_admin_check = False

                if is_admin_check or user.id in admin_ids:
                    continue

                collected_users[str(user.id)] = {
                    "id": user.id,
                    "username": user.username if user.username else "",
                    "first_name": user.first_name if user.first_name else "Без имени",
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

    bot_app = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
            buttons.append([InlineKeyboardButton("➕ Добавить сессию", callback_data="add_session")])
            buttons.append([InlineKeyboardButton("➕ Добавить юзера", callback_data="add_user"), InlineKeyboardButton("➖ Убрать юзера", callback_data="remove_user")])
        return InlineKeyboardMarkup(buttons)

    @bot_app.on_message(filters.command(["start"]))
    async def start_cmd(client, message):
        user = message.from_user
        if user.id in auth_steps: del auth_steps[user.id]
        
        allowed = load_allowed_users()
        if user.id not in allowed and user.id not in ADMIN_IDS:
            await message.reply(f"У вас нет доступа к боту.\nВаш ID: <code>{user.id}</code>", parse_mode=ParseMode.HTML)
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
        if user.id in auth_steps: del auth_steps[user.id]
        
        allowed = load_allowed_users()
        if user.id not in allowed and user.id not in ADMIN_IDS:
            return

        args = message.text.split()
        if len(args) < 2:
            await message.reply("Пример: <code>/pars https://t.me/durov 20000 100</code>", parse_mode=ParseMode.HTML)
            return

        sessions_dict = load_sessions()
        available_sessions = [s for s in sessions_dict.keys() if s not in active_sessions]
        
        if not available_sessions:
            await message.reply("⏳ <b>Все сессии сейчас заняты!</b>\nПожалуйста, подождите завершения текущих задач.", parse_mode=ParseMode.HTML)
            return
            
        session_name = available_sessions[0]
        active_sessions.add(session_name)
        
        target_link = args[1]
        limit = int(args[2]) if len(args) >= 3 else 20000
        max_users = int(args[3]) if len(args) >= 4 else 100

        status_msg = await message.reply("Ожидайте, запускаю процесс...")
        add_bot_log(f"ID {user.id} запустил парсинг (Сессия: {session_name})")

        async def run_parsing_task():
            try:
                await parse_chat_for_active_users(session_name, target_link, limit, max_users, status_msg, client, user.id)
            finally:
                active_sessions.remove(session_name)

        asyncio.create_task(run_parsing_task())

    @bot_app.on_callback_query()
    async def callback_handler(client, query: CallbackQuery):
        try:
            user_id = query.from_user.id
            allowed = load_allowed_users()
            
            if user_id not in allowed and user_id not in ADMIN_IDS:
                await query.answer("Нет доступа!", show_alert=True)
                return

            data = query.data

            if data == "menu" or data == "menu_edit":
                if user_id in auth_steps: del auth_steps[user_id]
                text = get_start_text(query.from_user.first_name)
                kb = get_start_keyboard(user_id)
                await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

            elif data == "menu_keep":
                try:
                    await query.message.edit_reply_markup(reply_markup=None)
                except:
                    pass
                text = get_start_text(query.from_user.first_name)
                kb = get_start_keyboard(user_id)
                try:
                    await client.send_animation(query.message.chat.id, animation=START_GIF_URL, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                except:
                    await client.send_message(query.message.chat.id, text, reply_markup=kb, parse_mode=ParseMode.HTML)
                
            elif data == "add_session":
                if user_id not in ADMIN_IDS:
                    await query.answer("Нет доступа", show_alert=True)
                    return
                auth_steps[user_id] = {"step": "phone"}
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="menu")]])
                await query.message.edit_text("<b>Добавление новой сессии</b>\n\nВведите номер телефона (в формате +79991234567):", reply_markup=kb, parse_mode=ParseMode.HTML)
                
            elif data == "add_user":
                if user_id not in ADMIN_IDS:
                    await query.answer("Нет доступа", show_alert=True)
                    return
                auth_steps[user_id] = {"step": "wait_add_user"}
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="menu")]])
                await query.message.edit_text("<b>Добавление пользователя</b>\n\nВведите ID пользователя (только цифры):", reply_markup=kb, parse_mode=ParseMode.HTML)
                
            elif data == "remove_user":
                if user_id not in ADMIN_IDS:
                    await query.answer("Нет доступа", show_alert=True)
                    return
                auth_steps[user_id] = {"step": "wait_remove_user"}
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="menu")]])
                await query.message.edit_text("<b>Удаление пользователя</b>\n\nВведите ID пользователя (только цифры):", reply_markup=kb, parse_mode=ParseMode.HTML)

            elif data == "help":
                help_text = (
                    "Команды (нажми, чтобы скопировать):\n"
                    "<code>/pars https://t.me/название_канала</code> — обычный парсинг.\n"
                    "<code>/pars https://t.me/название_канала 300 20</code> — с настройками."
                )
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
                await query.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                
            elif data == "logs":
                if user_id not in ADMIN_IDS:
                    await query.answer("Нет доступа", show_alert=True)
                    return
                logs_text = "\n".join(bot_logs[-15:]) if bot_logs else "Логов пока нет"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
                await query.message.edit_text(f"<b>Последние логи:</b>\n\n<code>{logs_text}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
                
            elif data == "users":
                if user_id not in ADMIN_IDS:
                    await query.answer("Нет доступа", show_alert=True)
                    return
                users_list = "\n".join([f"• <code>{u}</code>" for u in allowed])
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
                await query.message.edit_text(f"<b>Пользователи с доступом:</b>\n\n{users_list}", reply_markup=kb, parse_mode=ParseMode.HTML)
        finally:
            try:
                await query.answer()
            except:
                pass

    @bot_app.on_message(filters.text & filters.private)
    async def fsm_handler(client, message):
        user_id = message.from_user.id
        if user_id not in auth_steps:
            return 

        state = auth_steps[user_id]
        step = state["step"]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="menu")]])

        if step == "phone":
            phone = message.text.strip()
            session_name = f"session_{int(time.time())}"
            new_client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=False)
            
            await message.reply("Отправляю запрос на код...", reply_markup=kb)
            try:
                await new_client.connect()
                sent_code = await new_client.send_code(phone)
                
                auth_steps[user_id].update({
                    "step": "code",
                    "client": new_client,
                    "phone": phone,
                    "hash": sent_code.phone_code_hash,
                    "name": session_name
                })
                await message.reply("<b>Код отправлен!</b>\nВведите код подтверждения из Telegram (только цифры):", reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                await message.reply(f"Ошибка отправки кода: <code>{e}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
                del auth_steps[user_id]

        elif step == "code":
            code = message.text.replace(" ", "").replace("-", "")
            new_client = state["client"]
            try:
                await new_client.sign_in(state["phone"], state["hash"], code)
                await finalize_session(user_id, state, message)
            except SessionPasswordNeeded:
                auth_steps[user_id]["step"] = "password"
                await message.reply("<b>Обнаружена двухфакторная аутентификация!</b>\nВведите ваш облачный пароль:", reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                await message.reply(f"Ошибка авторизации: <code>{e}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
                await new_client.disconnect()
                del auth_steps[user_id]

        elif step == "password":
            password = message.text.strip()
            new_client = state["client"]
            try:
                await new_client.check_password(password)
                await finalize_session(user_id, state, message)
            except Exception as e:
                await message.reply(f"Неверный пароль: <code>{e}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
                await new_client.disconnect()
                del auth_steps[user_id]
                
        elif step == "wait_add_user":
            try:
                new_user = int(message.text.strip())
                allowed = load_allowed_users()
                if new_user not in allowed:
                    allowed.append(new_user)
                    save_allowed_users(allowed)
                    await message.reply(f"✅ Пользователь <code>{new_user}</code> успешно добавлен в белый список.", reply_markup=kb, parse_mode=ParseMode.HTML)
                    add_bot_log(f"Выдан доступ ID: {new_user}")
                else:
                    await message.reply("У этого пользователя уже есть доступ.", reply_markup=kb)
                del auth_steps[user_id]
            except ValueError:
                await message.reply("ID должен быть числом! Попробуйте еще раз или нажмите Отмена.", reply_markup=kb)
                
        elif step == "wait_remove_user":
            try:
                target_user = int(message.text.strip())
                allowed = load_allowed_users()
                if target_user in allowed:
                    allowed.remove(target_user)
                    save_allowed_users(allowed)
                    await message.reply(f"❌ Доступ для пользователя <code>{target_user}</code> успешно закрыт.", reply_markup=kb, parse_mode=ParseMode.HTML)
                    add_bot_log(f"Забран доступ у ID: {target_user}")
                else:
                    await message.reply("Этого пользователя и так нет в списке.", reply_markup=kb)
                del auth_steps[user_id]
            except ValueError:
                await message.reply("ID должен быть числом! Попробуйте еще раз или нажмите Отмена.", reply_markup=kb)

    async def finalize_session(user_id, state, message):
        new_client = state["client"]
        session_name = state["name"]
        
        sessions = load_sessions()
        sessions[session_name] = {"phone": state["phone"], "type": "парсинг"}
        save_json(SESSIONS_FILE, sessions)
        
        await new_client.disconnect()
        del auth_steps[user_id]
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])
        await message.reply(f"✅ <b>Сессия успешно добавлена!</b>\nИмя: <code>{session_name}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)
        add_bot_log(f"Добавлена новая сессия: {session_name}")

    print("Запуск бота через токен BotFather...")
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    bot_app.run()

if __name__ == "__main__":
    run_telegram_bot()
