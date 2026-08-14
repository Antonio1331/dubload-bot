import os
import asyncio
import logging
import threading
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import yt_dlp
from dotenv import load_dotenv
import db

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')

# Чтение ADMIN_IDS из .env
raw_admin_ids = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(',') if x.strip().isdigit()]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_PATH = './downloads'
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

def prepare_cookies():
    secret_path = '/etc/secrets/cookies.txt'
    tmp_path = '/tmp/cookies.txt'
    local_path = 'cookies.txt'

    target_path = None
    if os.path.exists(secret_path):
        target_path = secret_path
    elif os.path.exists(local_path):
        target_path = local_path

    if target_path:
        try:
            # Читаем файл и принудительно нормализуем переносы строк на Unix-формат (\n)
            with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Заменяем Windows CRLF (\r\n) на Linux LF (\n)
            content = content.replace('\r\n', '\n')

            # Перезаписываем во временный файл в /tmp/
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logging.info(f"✅ Файл cookies.txt обработан и записан в /tmp/ (Размер: {len(content)} символов)")
            return tmp_path
        except Exception as e:
            logging.error(f"❌ Ошибка обработки cookies: {e}")
            return None

    logging.warning("⚠️ Файл cookies.txt не найден!")
    return None

COOKIES_FILE = prepare_cookies()


# --- HTTP Server для обхода таймаутов Render (Health Check) ---

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dubload Bot is running!")

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


# --- Вспомогательные функции подписки ---

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


# --- Старт и проверка подписки ---

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
        "• 🎬 **YouTube:** Скачивание с выбором дубляжа (RU/EN/ES...) и качества.\n"
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


# --- Обработка TikTok и YouTube ---

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
        out_tmpl = os.path.join(DOWNLOAD_PATH, f"%(id)s.%(ext)s")
        ydl_opts = {'outtmpl': out_tmpl, 'format': 'b', 'quiet': True}
        file_path = None
        try:
            loop = asyncio.get_running_loop()
            file_path = await loop.run_in_executor(None, download_media, url, ydl_opts)
            await status_msg.edit_text("⬆️ Отправляю...")
            await message.answer_video(video=types.FSInputFile(file_path))
            await db.increment_user_downloads(message.from_user.id)
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка TikTok: {e}")
        finally:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        return

    # YouTube
    status_msg = await message.answer("🔍 Анализирую видео...")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, extract_info, url, {
            'quiet': True,
            'skip_download': True,
            'extract_flat': False,
            'no_warnings': True,
        })

        await state.update_data(video_url=url, video_info=info)

        builder = InlineKeyboardBuilder()
        builder.button(text="🎬 Видео", callback_data="type:video")
        builder.button(text="🎵 Только MP3", callback_data="type:mp3")
        builder.adjust(2)

        await status_msg.edit_text(
            f"🎥 **{info.get('title', 'YouTube Video')}**\n\n"
            f"📊 Осталось скачиваний на сегодня: **{left}** из **{total}**\n"
            "Что вы хотите скачать?",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        await state.set_state(DownloadState.waiting_for_format)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка анализа YouTube: {e}")


@dp.callback_query(DownloadState.waiting_for_format, F.data.startswith("type:"))
async def process_type_choice(callback: types.CallbackQuery, state: FSMContext):
    download_type = callback.data.split(":")[1]
    data = await state.get_data()
    url = data['video_url']
    info = data['video_info']

    if download_type == "mp3":
        await callback.message.edit_text("⏳ Скачиваю и конвертирую в MP3...")
        opts = {
            'outtmpl': os.path.join(DOWNLOAD_PATH, f"%(id)s.%(ext)s"),
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
        file_path = None
        try:
            loop = asyncio.get_running_loop()
            file_path = await loop.run_in_executor(None, download_media, url, opts)
            if not file_path.endswith('.mp3'):
                file_path = file_path.rsplit('.', 1)[0] + '.mp3'

            await callback.message.edit_text("⬆️ Отправляю аудио...")
            await callback.message.answer_audio(audio=types.FSInputFile(file_path), title=info.get('title'))
            await db.increment_user_downloads(callback.from_user.id)
            await callback.message.delete()
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка скачивания MP3: {e}")
        finally:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
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
    data = await state.get_data()
    info = data['video_info']

    audio_tracks = get_audio_tracks(info.get('formats', []))

    if len(audio_tracks) > 1:
        builder = InlineKeyboardBuilder()
        for track in audio_tracks:
            lang_label = track['language']
            if track['is_original']:
                lang_label += " (Оригинал)"
            builder.button(text=lang_label, callback_data=f"audio:{track['format_id']}")
        builder.adjust(2)

        await callback.message.edit_text("🎙 **Найдено несколько дубляжей!**\nВыберите язык:",
                                         reply_markup=builder.as_markup())
        await state.set_state(DownloadState.waiting_for_audio_choice)
    else:
        await download_and_send_video(callback.message, state, audio_format_id=None)


@dp.callback_query(DownloadState.waiting_for_audio_choice, F.data.startswith("audio:"))
async def process_audio_choice(callback: types.CallbackQuery, state: FSMContext):
    audio_format_id = callback.data.split(":")[1]
    await download_and_send_video(callback.message, state, audio_format_id=audio_format_id)


async def download_and_send_video(message: types.Message, state: FSMContext, audio_format_id: str = None):
    data = await state.get_data()
    url = data['video_url']
    quality = data.get('chosen_quality', '720')

    await message.edit_text(f"⏳ Скачиваю видео ({quality}p)... Это может занять пару минут.")

    video_fmt = f"bestvideo[height<={quality}]"
    audio_fmt = f"+{audio_format_id}" if audio_format_id else "+bestaudio"

    # Гибкий селектор форматов: пробует собрать лучшее видео+аудио,
    # а если не выходит — берет готовое видео с аудио или любое доступное
    format_selector = (
        f"{video_fmt}{audio_fmt}/"
        f"bestvideo[height<={quality}]+bestaudio/"
        f"best[height<={quality}]/"
        f"b/best"
    )

    opts = {
        'outtmpl': os.path.join(DOWNLOAD_PATH, f"%(id)s.%(ext)s"),
        'format': format_selector,
        'merge_output_format': 'mp4',
        'quiet': True,
    }
    # ... оставшаяся часть функции без изменений

    file_path = None
    try:
        loop = asyncio.get_running_loop()
        file_path = await loop.run_in_executor(None, download_media, url, opts)

        base_path = os.path.splitext(file_path)[0]
        if os.path.exists(f"{base_path}.mp4"):
            file_path = f"{base_path}.mp4"

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден по пути: {file_path}")

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if file_size_mb > 49.5:
            await message.edit_text(
                f"⚠️ **Файл слишком большой ({file_size_mb:.1f} МБ)!**\n\n"
                "Telegram Bot API на бесплатном сервере принимает файлы только **до 50 МБ**.\n"
                "Попробуйте выбрать меньшее качество (480p или 360p).",
                parse_mode="Markdown"
            )
            return

        await message.edit_text("⬆️ Отправляю файл в чат...")
        await message.answer_video(video=types.FSInputFile(file_path))

        await db.increment_user_downloads(message.chat.id if hasattr(message, 'chat') else message.from_user.id)
        await message.delete()
    except Exception as e:
        logging.error(f"Ошибка download_and_send_video: {e}")
        await message.answer(f"❌ Ошибка загрузки: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logging.error(f"Не удалось удалить файл {file_path}: {e}")
        await state.clear()


# Задаем User-Agent современного браузера Chrome
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'

YOUTUBE_EXTRACTOR_ARGS = {
    'youtube': {
        'player_client': ['android', 'ios', 'mweb', 'tv', 'web'],
    }
}


def extract_info(url, opts):
    y_opts = opts.copy() if opts else {}

    # Не запрашиваем конкретный формат при анализе метаданных
    y_opts['format'] = 'best'
    y_opts['extractor_args'] = YOUTUBE_EXTRACTOR_ARGS
    y_opts['user_agent'] = USER_AGENT

    if COOKIES_FILE:
        y_opts['cookiefile'] = COOKIES_FILE

    with yt_dlp.YoutubeDL(y_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_media(url, opts):
    opts_copy = opts.copy()
    opts_copy['extractor_args'] = YOUTUBE_EXTRACTOR_ARGS
    opts_copy['user_agent'] = USER_AGENT

    if COOKIES_FILE:
        opts_copy['cookiefile'] = COOKIES_FILE

    with yt_dlp.YoutubeDL(opts_copy) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if opts_copy.get('merge_output_format'):
            ext = opts_copy['merge_output_format']
            base = os.path.splitext(filename)[0]
            filename = f"{base}.{ext}"
        return filename

def get_audio_tracks(formats):
    tracks = []
    seen_languages = set()
    for fmt in formats:
        vcodec = fmt.get('vcodec')
        acodec = fmt.get('acodec')
        if (vcodec == 'none' or not vcodec) and acodec and acodec != 'none':
            lang_code = fmt.get('language') or fmt.get('language_preference')

            if lang_code and str(lang_code).lower() != 'none':
                lang_str = str(lang_code).upper()
                if lang_str not in seen_languages:
                    seen_languages.add(lang_str)
                    note = str(fmt.get('format_note', '')).lower()
                    tracks.append({
                        'format_id': fmt.get('format_id'),
                        'language': lang_str,
                        'is_original': 'original' in note or 'main' in note
                    })
    return tracks


async def main():
    threading.Thread(target=start_health_check_server, daemon=True).start()

    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())