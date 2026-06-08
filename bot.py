import os
import subprocess
import logging
import threading
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========================= CONFIG =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")

DOWNLOAD_DIR = "downloads"
OUTPUT_DIR = "outputs"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_SIZE_BYTES = 256 * 1024
MAX_DURATION_SEC = 3
TARGET_RESOLUTION = 512

# ======================= FLASK APP =======================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ Telegram WebM Sticker Bot is running!"

@flask_app.route("/health")
def health():
    return "OK", 200

# ======================= HELPERS =======================
def get_video_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return float(result.stdout.decode().strip())
    except:
        return 0.0

def get_video_dimensions(path: str) -> tuple[int, int]:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        w, h = result.stdout.decode().strip().split(",")
        return int(w), int(h)
    except:
        return 0, 0

def build_scale_filter(width: int, height: int) -> str:
    """
    Правильное масштабирование для Telegram Video Stickers:
    - Длинная сторона = ровно 512 px
    - Короткая сторона ≤ 512 px
    - Пропорции сохраняются на 100% (без обрезки в квадрат!)
    - Без растяжения и чёрных полос
    """
    return (
        "scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,"
        "setsar=1,"
        "fps=30"
    )

def convert_to_webm(input_path: str, output_path: str) -> bool:
    duration = min(get_video_duration(input_path), MAX_DURATION_SEC)
    w, h = get_video_dimensions(input_path)
    vf = build_scale_filter(w, h)

    # Логируем, какой фильтр реально применяется
    logger.info("VIDEO FILTER USED: %s", vf)

    # === Pass 1 ===
    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",           # поддержка прозрачности
        "-crf", "33", "-b:v", "0",
        "-deadline", "good", "-cpu-used", "3",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if os.path.exists(output_path) and os.path.getsize(output_path) <= MAX_SIZE_BYTES:
        return True

    # === Pass 2 (если нужно ужать до 256 КБ) ===
    target_kbps = max(int((MAX_SIZE_BYTES * 8 * 0.9) / (duration * 1000)), 50)
    cmd2 = [
        "ffmpeg", "-y", "-i", input_path, "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",           # поддержка прозрачности
        "-b:v", f"{target_kbps}k",
        "-maxrate", f"{target_kbps}k",
        "-bufsize", f"{target_kbps * 2}k",
        "-deadline", "good", "-cpu-used", "4",
        output_path
    ]
    subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return os.path.exists(output_path) and os.path.getsize(output_path) <= MAX_SIZE_BYTES

# ======================= TELEGRAM HANDLERS =======================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video = message.video or message.animation or (
        message.document if message.document and message.document.mime_type and
        (message.document.mime_type.startswith("video/") or message.document.mime_type == "image/gif")
        else None
    )
    if not video:
        return

    file_id = video.file_id
    input_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{file_id}.webm")

    try:
        await message.reply_text("⬇️ Скачиваю видео...")
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(input_path)

        await message.reply_text("⚙️ Конвертирую в WebM (длинная сторона 512 px, пропорции сохранены)...")

        success = convert_to_webm(input_path, output_path)

        if success:
            size_kb = os.path.getsize(output_path) / 1024
            with open(output_path, "rb") as f:
                await message.reply_document(
                    document=f,
                    filename="sticker.webm",
                    caption=f"✅ Готово! {size_kb:.1f} КБ\n"
                            "WebM VP9 • длинная сторона 512 px (короткая ≤512) • пропорции сохранены • ≤3с • без звука"
                )
        else:
            await message.reply_text("❌ Не удалось уложиться в 256 КБ. Попробуй короче видео или более простую анимацию.")
    except Exception as e:
        logger.exception(e)
        await message.reply_text(f"❌ Ошибка: {e}")
    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Отправь видео или GIF\n\n"
        "Я сделаю стикер:\n"
        "• Длинная сторона = 512 px\n"
        "• Короткая сторона ≤ 512 px\n"
        "• Пропорции сохраняются (без обрезки в квадрат!)\n"
        "• до 3 сек • без звука • до 256 КБ"
    )

def create_app():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    bot_app = create_app()
    threading.Thread(target=run_flask, daemon=True).start()
    print("🤖 Bot started (polling + Flask)")
    bot_app.run_polling()
