import os
from pyrogram import Client

# Твои API_ID и API_HASH (можно оставить эти же из конфига)
API_ID = 37635168
API_HASH = "47e36b7f99b31f55be222b4200ea94ca"

# Имя сессии (например, session_parcer)
SESSION_NAME = "session_parcer"

app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

async def main():
    await app.start()
    me = await app.get_me()
    print(f"\n[+] Успешная авторизация!")
    print(f"[+] Аккаунт: {me.first_name} (ID: {me.id})")
    print(f"[+] Файл сессии '{SESSION_NAME}.session' успешно создан.")
    await app.stop()

if __name__ == "__main__":
    app.run(main())
