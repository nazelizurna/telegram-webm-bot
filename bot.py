import os
import subprocess
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")

DOWNLOAD_DIR = "downloads"
OUTPUT_DIR = "outputs"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_SIZE_BYTES = 256 * 1024      # 256 KB
MAX_DURATION_SEC = 3             # 3 seconds max
TARGET_RESOLUTION = 512          # 512x512

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ Telegram WebM Sticker Bot is running!"

@flask_app.route("/health")
def health():
    return "OK", 200
def get_video_duration(path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return float(result.stdout.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


def get_video_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of the video using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        w, h = result.stdout.decode().strip().split(",")
        return int(w), int(h)
    except Exception:
        return 0, 0


def build_scale_filter(width: int, height: int) -> str:
    return (
        "crop=min(iw\\,ih):min(iw\\,ih),"   # square central crop
        f"scale={TARGET_RESOLUTION}:{TARGET_RESOLUTION}:flags=lanczos,"  # 512×512
        f"fps=30"                              # cap at 30 fps
    )


def convert_to_webm(input_path: str, output_path: str) -> bool:
    duration = min(get_video_duration(input_path), MAX_DURATION_SEC)
    width, height = get_video_dimensions(input_path)
    vf = build_scale_filter(width, height)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-an",                          # no audio
        "-c:v", "libvpx-vp9",
        "-crf", "33",                   # quality-based (lower = better quality)
        "-b:v", "0",                    # pure CRF mode (b:v=0 required for vp9 CRF)
        "-deadline", "good",
        "-cpu-used", "3",
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("ffmpeg pass-1 stderr: %s", result.stderr.decode()[-800:])

    if os.path.exists(output_path):
        size = os.path.getsize(output_path)
        logger.info("Pass-1 output size: %d bytes", size)
        if size <= MAX_SIZE_BYTES:
            return True
    logger.info("File too large, switching to constrained bitrate encode")
    target_kbps = int((MAX_SIZE_BYTES * 8 * 0.9) / (duration * 1000))
    target_kbps = max(target_kbps, 50)   # floor at 50 Kbps

    cmd2 = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libvpx-vp9",
        "-b:v", f"{target_kbps}k",
        "-maxrate", f"{target_kbps}k",
        "-bufsize", f"{target_kbps * 2}k",
        "-deadline", "good",
        "-cpu-used", "4",
        output_path
    ]
    result2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("ffmpeg pass-2 stderr: %s", result2.stderr.decode()[-800:])

    if os.path.exists(output_path):
        size2 = os.path.getsize(output_path)
        logger.info("Pass-2 output size: %d bytes", size2)
        return size2 <= MAX_SIZE_BYTES

    return False

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video = None
    if message.video:
        video = message.video
    elif message.animation:
        video = message.animation
    elif message.document and message.document.mime_type:
        mime = message.document.mime_type
        if mime.startswith("video/") or mime == "image/gif":
            video = message.document

    if not video:
        return

    file_id = video.file_id
    input_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{file_id}.webm")

    try:
        await message.reply_text("⬇️ Скачиваю видео...")
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(input_path)

        await message.reply_text(
            "⚙️ Конвертирую в WebM 512×512, до 3 с, без звука..."
        )

        success = convert_to_webm(input_path, output_path)

        if success:
            size_kb = os.path.getsize(output_path) / 1024
            with open(output_path, "rb") as f:
                await message.reply_document(
                    document=f,
                    filename="sticker.webm",
                    caption=(
                        f"✅ Готово! Размер: {size_kb:.1f} КБ\n"
                        "Формат: WebM VP9 • 512×512 • ≤3 с • без звука"
                    )
                )
        else:
            await message.reply_text(
                "❌ Не удалось уложиться в 256 КБ.\n"
                "Попробуй видео покороче или с меньшим движением."
            )

    except Exception as e:
        logger.exception("Error processing video")
        await message.reply_text(f"❌ Ошибка: {e}")

    finally:
        for path in (input_path, output_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне видео или GIF, и я конвертирую его "
        "в WebM-стикер для Telegram:\n\n"
        "• 512×512 пикселей\n"
        "• до 3 секунд\n"
        "• без звука\n"
        "• до 256 КБ\n"
        "• кодек VP9"
    )

def create_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(
            filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO,
            handle_video
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    return app

if __name__ == "__main__":
    bot_app = create_app()
    print("🤖 Starting bot (polling mode)")
    bot_app.run_polling()
