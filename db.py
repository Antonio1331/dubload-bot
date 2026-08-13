import aiosqlite
import datetime

DB_PATH = "database.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                daily_downloads INTEGER DEFAULT 0,
                last_download_date TEXT,
                custom_limit INTEGER DEFAULT 5,
                is_vip INTEGER DEFAULT 0
            )
        """)
        # Таблица каналов для обязательной подписки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT UNIQUE,
                title TEXT,
                url TEXT
            )
        """)
        await db.commit()

async def add_user(user_id: int, username: str, first_name: str):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, first_name, last_download_date) 
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=?, first_name=?""",
            (user_id, username, first_name, today, username, first_name)
        )
        await db.commit()

async def check_and_update_limit(user_id: int) -> tuple[bool, int, int]:
    """
    Проверяет и обновляет дневной лимит.
    Возвращает (разрешено_ли_скачивание, осталось_скачиваний, всего_лимит)
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT daily_downloads, last_download_date, custom_limit, is_vip FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return True, 5, 5

            downloads, last_date, custom_limit, is_vip = row

            # Если пользователь VIP - лимит бесконечный
            if is_vip == 1:
                return True, 999, 999

            # Если наступил новый день - сбрасываем счетчик
            if last_date != today:
                downloads = 0
                await db.execute(
                    "UPDATE users SET daily_downloads = 0, last_download_date = ? WHERE user_id = ?",
                    (today, user_id)
                )
                await db.commit()

            if downloads < custom_limit:
                return True, (custom_limit - downloads), custom_limit
            else:
                return False, 0, custom_limit

async def increment_user_downloads(user_id: int):
    """Увеличивает счетчик скачиваний на 1"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET daily_downloads = daily_downloads + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def set_user_vip(user_id: int, is_vip: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (is_vip, user_id))
        await db.commit()

async def set_user_limit(user_id: int, new_limit: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET custom_limit = ? WHERE user_id = ?", (new_limit, user_id))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) FROM users WHERE DATE(joined_at) = ?", (today,)) as cursor:
            today_count = (await cursor.fetchone())[0]
        return total, today_count

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

# --- Управление каналами ---

async def add_channel(channel_id: str, title: str, url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO channels (channel_id, title, url) VALUES (?, ?, ?)",
            (channel_id, title, url)
        )
        await db.commit()

async def delete_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        await db.commit()

async def get_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_id, title, url FROM channels") as cursor:
            rows = await cursor.fetchall()
            return [{'channel_id': r[0], 'title': r[1], 'url': r[2]} for r in rows]