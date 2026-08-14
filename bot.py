import os
import asyncio
import logging
import threading
import aiohttp
from http.server import BaseHTTPRequestHandler, HTTPServer
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
import db

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')

# Чтение ADMIN_IDS из .env
raw_admin_ids = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(',') if x.strip().isdigit()]

COBALT_API_URL = "https://api.cobalt.tools"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- HTTP Server для Health Check (Render) ---

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dubload Bot is running via Cobalt!")

    def log_message(self, format, *args):
        return


def start_health_check_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()


# --- Состояния FSM ---

class DownloadState(StatesGroup):
    waiting_for_format = State()
    waiting_for_quality = State()
    waiting_for_audio_choice = State()


class AdminState(StatesGroup):
    waiting_for_broadcast_msg = State()
    waiting_for_channel_info = State()
    waiting_for_vip_id = State()
    waiting_for_limit_data = State()


# --- Взаимодействие с Cobalt API ---

async def request_cobalt(url: str, is_audio_only: bool = False, quality: str = "720", audio_lang: str = "ru"):
    """
    Отправляет запрос к Cobalt API и возвращает структуру ответа.
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "url": url,
        "videoQuality": quality if not is_audio_only else "720",
        "downloadMode": "audio" if is_audio_only else "auto",
        "youtubeAudioLanguage": audio_lang,  # Выбор языковой аудиодорожки
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(COBALT_API_URL, json=payload, headers=headers, timeout=35) as resp:
                data = await resp.json()
                return data
        except Exception as e:
            logging.error(f"Ошибка обращения к Cobalt API: {e}")
            return None


# --- Проверка подписок и Клавиатуры ---

async def check_user_subscriptions(user_id: int) -> list:
    channels = await db.get_channels()
    not_subscribed = []
    for ch in channels:
        try:
            raw_cid = ch['channel_id']
            if isinstance(raw_cid, str) and (raw_cid.startswith('-') and raw_cid[1:].isdigit() or raw_cid.isdigit()):
                chat_id = int(raw_cid)
            else:
                chat_id = raw_cid

            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ['creator', 'administrator', 'member']:
                not_subscribed.append(ch)
        except Exception as e:
            logging.error(f"Ошибка проверки подписки для канала {ch.get('channel_id')}: {e}")
            not_subscribed.append(ch)

    return not_subscribed


def get_sub_keyboard(not_subscribed_channels: list) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in not_subscribed_channels:
        builder.button(text=f"📢 {ch['title']}", url=ch['url'])
    builder.button(text="✅ Я подписался", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_main_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Статистика")
    builder.button(text="📢 Рассылка")
    builder.button(text="➕ Добавить канал")
    builder.button(text="📋 Список каналов")
    builder.button(text="💎 Выдать VIP")
    builder.button(text="⚙️ Изменить лимит")
    builder.button(text="❌ Выйти")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


# --- Старт и Проверка подписки ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    unsubbed = await check_user_subscriptions(message.from_user.id)
    if unsubbed:
        await message.answer(
            "⚠️ **Для использования бота необходимо подписаться на наши каналы:**",
            reply_markup=get_sub_keyboard(unsubbed),
            parse_mode="Markdown"
        )
        return

    await message.answer(
        "🎧 **Добро пожаловать в Dubload!**\n"
        "Первый бот для скачивания видео с выбором **языковой аудиодорожки**.\n\n"
        "📌 **Что я умею:**\n"
        "• 🎬 **YouTube:** Скачивание с выбором дубляжа и качества.\n"
        "• 🎵 **MP3:** Извлечение аудиофайла из любого видео.\n"
        "• 📱 **TikTok:** Мгновенная загрузка без водяных знаков.\n\n"
        "🎁 У тебя есть **5 бесплатных загрузок** каждые 24 часа.\n"
        "👇 *Просто отправь мне ссылку на видео!*",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "check_subscription")
async def process_check_sub(callback: types.CallbackQuery):
    unsubbed = await check_user_subscriptions(callback.from_user.id)
    if unsubbed:
        await callback.answer("❌ Вы подписались не на все каналы!", show_alert=True)
    else:
        await callback.answer("🎉 Доступ открыт!")
        await callback.message.delete()
        await callback.message.answer("✨ Отправь ссылку на **YouTube** или **TikTok**!")


# --- Админ-Панель ---

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🛠 **Панель Администратора**", reply_markup=get_admin_main_kb())


@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    total, today = await db.get_stats()
    await message.answer(f"📈 **Всего юзеров:** `{total}`\n🆕 **Новых за сегодня:** `{today}`", parse_mode="Markdown")


@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("📩 Пришлите сообщение для рассылки (или `/cancel` для отмены):")
    await state.set_state(AdminState.waiting_for_broadcast_msg)


@dp.message(AdminState.waiting_for_broadcast_msg)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_admin_main_kb())
        return
    users = await db.get_all_users()
    await message.answer(f"🚀 Запуск рассылки на `{len(users)}` юзеров...", parse_mode="Markdown")
    success = 0
    for u in users:
        try:
            await message.copy_to(chat_id=u)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Готово! Успешно доставлено: `{success}`", reply_markup=get_admin_main_kb())
    await state.clear()


@dp.message(F.text == "➕ Добавить канал")
async def admin_add_ch(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(
        "Пришлите данные в формате:\n`ID_канала|Название|Ссылка`\nПример:\n`-100123456|Мой Канал|https://t.me/channel`",
        parse_mode="Markdown")
    await state.set_state(AdminState.waiting_for_channel_info)


@dp.message(AdminState.waiting_for_channel_info)
async def process_add_ch(message: types.Message, state: FSMContext):
    try:
        cid, title, url = [x.strip() for x in message.text.split("|")]
        await db.add_channel(cid, title, url)
        await message.answer(f"✅ Канал **{title}** добавлен!", reply_markup=get_admin_main_kb())
    except Exception as e:
        await message.answer(f"❌ Ошибка: `{e}`", reply_markup=get_admin_main_kb())
    await state.clear()


@dp.message(F.text == "📋 Список каналов")
async def admin_list_ch(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    channels = await db.get_channels()
    if not channels:
        await message.answer("Список пуст.")
        return
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(text=f"🗑 Удалить {ch['title']}", callback_data=f"del_ch:{ch['channel_id']}")
    builder.adjust(1)
    await message.answer("📋 **Каналы в ОБП:**", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("del_ch:"))
async def del_ch_call(callback: types.CallbackQuery):
    cid = callback.data.split(":")[1]
    await db.delete_channel(cid)
    await callback.answer("Удалено!")
    await callback.message.edit_text("✅ Канал удален.")


@dp.message(F.text == "💎 Выдать VIP")
async def admin_vip_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Введите `user_id` пользователя для выдачи безлимита:")
    await state.set_state(AdminState.waiting_for_vip_id)


@dp.message(AdminState.waiting_for_vip_id)
async def admin_vip_process(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        await db.set_user_vip(uid, 1)
        await message.answer(f"✅ Пользователю `{uid}` выдан VIP / Безлимит!", reply_markup=get_admin_main_kb(),
                             parse_mode="Markdown")
    except Exception:
        await message.answer("❌ Неверный ID.", reply_markup=get_admin_main_kb())
    await state.clear()


@dp.message(F.text == "⚙️ Изменить лимит")
async def admin_limit_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Введите данные в формате: `USER_ID|НОВЫЙ_ЛИМИТ`\nПример: `12345678|20`")
    await state.set_state(AdminState.waiting_for_limit_data)


@dp.message(AdminState.waiting_for_limit_data)
async def admin_limit_process(message: types.Message, state: FSMContext):
    try:
        uid, lim = [int(x.strip()) for x in message.text.split("|")]
        await db.set_user_limit(uid, lim)
        await message.answer(f"✅ Для пользователя `{uid}` установлен лимит: {lim} скачиваний/день.",
                             reply_markup=get_admin_main_kb())
    except Exception:
        await message.answer("❌ Ошибка формата.", reply_markup=get_admin_main_kb())
    await state.clear()


# --- Основная обработка медиа ссылок ---

@dp.message(F.text.contains("tiktok.com") | F.text.contains("youtube.com") | F.text.contains("youtu.be"))
async def handle_media_request(message: types.Message, state: FSMContext):
    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    unsubbed = await check_user_subscriptions(message.from_user.id)
    if unsubbed:
        await message.answer("⚠️ **Сначала подпишитесь на каналы:**", reply_markup=get_sub_keyboard(unsubbed),
                             parse_mode="Markdown")
        return

    allowed, left, total = await db.check_and_update_limit(message.from_user.id)
    if not allowed:
        await message.answer(
            f"🚫 **Дневной лимит исчерпан ({total}/{total})!**\n\n"
            "Лимит обновится через 24 часа. Обратитесь к администратору для увеличения лимита.",
            parse_mode="Markdown"
        )
        return

    url = message.text.strip()

    # TikTok
    if "tiktok.com" in url:
        status_msg = await message.answer("⏳ Скачиваю TikTok...")
        res = await request_cobalt(url)
        if res and res.get("status") in ["tunnel", "redirect"]:
            try:
                await message.answer_video(video=res.get("url"))
                await db.increment_user_downloads(message.from_user.id)
                await status_msg.delete()
            except Exception:
                await status_msg.edit_text(f"🔗 **Ссылка для скачивания TikTok:**\n{res.get('url')}")
        else:
            await status_msg.edit_text("❌ Не удалось загрузить видео из TikTok.")
        return

    # YouTube
    await state.update_data(video_url=url)

    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Видео", callback_data="type:video")
    builder.button(text="🎵 Только MP3", callback_data="type:mp3")
    builder.adjust(2)

    await message.answer(
        f"🎥 **YouTube Video**\n\n"
        f"📊 Осталось скачиваний на сегодня: **{left}** из **{total}**\n"
        "Что вы хотите скачать?",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await state.set_state(DownloadState.waiting_for_format)


@dp.callback_query(DownloadState.waiting_for_format, F.data.startswith("type:"))
async def process_type_choice(callback: types.CallbackQuery, state: FSMContext):
    download_type = callback.data.split(":")[1]
    data = await state.get_data()
    url = data['video_url']

    if download_type == "mp3":
        await callback.message.edit_text("⏳ Получаю аудио через Cobalt...")
        res = await request_cobalt(url, is_audio_only=True)

        if res and res.get("status") in ["tunnel", "redirect"]:
            await callback.message.edit_text("⬆️ Отправляю аудио...")
            try:
                await callback.message.answer_audio(audio=res.get("url"))
                await db.increment_user_downloads(callback.from_user.id)
                await callback.message.delete()
            except Exception:
                await callback.message.edit_text(f"🔗 **Ссылка на MP3:**\n{res.get('url')}")
        else:
            await callback.message.edit_text("❌ Ошибка при получении MP3.")

        await state.clear()

    elif download_type == "video":
        builder = InlineKeyboardBuilder()
        builder.button(text="1080p", callback_data="qual:1080")
        builder.button(text="720p", callback_data="qual:720")
        builder.button(text="480p", callback_data="qual:480")
        builder.button(text="360p", callback_data="qual:360")
        builder.adjust(2)

        await callback.message.edit_text("📐 **Выберите желаемое качество:**", reply_markup=builder.as_markup(),
                                         parse_mode="Markdown")
        await state.set_state(DownloadState.waiting_for_quality)


@dp.callback_query(DownloadState.waiting_for_quality, F.data.startswith("qual:"))
async def process_quality_choice(callback: types.CallbackQuery, state: FSMContext):
    quality = callback.data.split(":")[1]
    await state.update_data(chosen_quality=quality)

    builder = InlineKeyboardBuilder()
    builder.button(text="🇷🇺 Русский", callback_data="audio:ru")
    builder.button(text="🇬🇧 Английский", callback_data="audio:en")
    builder.button(text="🇪🇸 Испанский", callback_data="audio:es")
    builder.button(text="⚙️ По умолчанию", callback_data="audio:default")
    builder.adjust(2)

    await callback.message.edit_text("🎙 **Выберите язык дубляжа:**", reply_markup=builder.as_markup())
    await state.set_state(DownloadState.waiting_for_audio_choice)


@dp.callback_query(DownloadState.waiting_for_audio_choice, F.data.startswith("audio:"))
async def process_audio_choice(callback: types.CallbackQuery, state: FSMContext):
    audio_lang = callback.data.split(":")[1]
    data = await state.get_data()
    url = data['video_url']
    quality = data.get('chosen_quality', '720')

    await callback.message.edit_text(f"⏳ Скачиваю видео ({quality}p)... Это займёт пару секунд.")

    res = await request_cobalt(url, is_audio_only=False, quality=quality, audio_lang=audio_lang)

    if res and res.get("status") in ["tunnel", "redirect"]:
        download_url = res.get("url")
        await callback.message.edit_text("⬆️ Отправляю файл в чат...")
        try:
            await callback.message.answer_video(video=download_url)
            await db.increment_user_downloads(callback.from_user.id)
            await callback.message.delete()
        except Exception as e:
            # Если файл превышает лимит отправки Telegram по URL, отдаем прямую ссылку
            await callback.message.edit_text(
                f"⚠️ **Видео готово, но файл слишком велик для прямого бота.**\n\n"
                f"🔗 [Нажмите сюда, чтобы скачать видео]({download_url})",
                parse_mode="Markdown"
            )
    else:
        err_msg = res.get("error", {}).get("code", "Неизвестная ошибка") if res else "Сервер не ответил"
        await callback.message.edit_text(f"❌ Ошибка загрузки: `{err_msg}`", parse_mode="Markdown")

    await state.clear()


# --- Точка входа ---

async def main():
    threading.Thread(target=start_health_check_server, daemon=True).start()
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())