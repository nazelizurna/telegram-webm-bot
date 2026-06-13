import os
import signal
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
MAX_INPUT_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB guard

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
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return float(result.stdout.decode().strip())
    except ValueError:
        return 0.0


def get_video_dimensions(path: str) -> tuple[int, int]:
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
    except (ValueError, AttributeError):
        return 0, 0


def calc_scaled_dimensions(width: int, height: int) -> tuple[int, int]:
    """
    Scale so that the long side = 512 px, preserving aspect ratio.
    Both dimensions are rounded to the nearest even number (VP9 requirement).
    """
    if width >= height:
        # Landscape or square
        new_width = TARGET_RESOLUTION
        new_height = round(height * TARGET_RESOLUTION / width)
    else:
        # Portrait
        new_height = TARGET_RESOLUTION
        new_width = round(width * TARGET_RESOLUTION / height)

    # Round to nearest even (VP9 requires even dimensions)
    # Round UP to even so we never go below the calculated size
    if new_width % 2 != 0:
        new_width += 1
    if new_height % 2 != 0:
        new_height += 1

    # Clamp to TARGET_RESOLUTION in case rounding pushed us over
    new_width = min(new_width, TARGET_RESOLUTION)
    new_height = min(new_height, TARGET_RESOLUTION)

    return new_width, new_height


def convert_to_webm(input_path: str, output_path: str) -> tuple[bool, bool]:
    """
    Convert video to WebM VP9 sticker format.

    Returns:
        (success, was_trimmed) — was_trimmed is True when the source was
        longer than MAX_DURATION_SEC and got cut.
    """
    actual_duration = get_video_duration(input_path)
    was_trimmed = actual_duration > MAX_DURATION_SEC
    duration = min(actual_duration, MAX_DURATION_SEC)

    w, h = get_video_dimensions(input_path)
    if w == 0 or h == 0:
        logger.error("Could not read video dimensions from %s", input_path)
        return False, was_trimmed

    new_w, new_h = calc_scaled_dimensions(w, h)
    vf = f"scale={new_w}:{new_h}:flags=lanczos,fps=30"
    logger.info("VIDEO FILTER: scale=%d:%d (source %dx%d)", new_w, new_h, w, h)

    # --- Pass 1: CRF (quality-based) ---
    cmd1 = [
        "ffmpeg", "-y", "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libvpx-vp9",
        "-crf", "33", "-b:v", "0",
        "-deadline", "good", "-cpu-used", "3",
        output_path
    ]
    result1 = subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result1.returncode == 0 and os.path.exists(output_path):
        if os.path.getsize(output_path) <= MAX_SIZE_BYTES:
            logger.info("SUCCESS on pass 1 (CRF)")
            return True, was_trimmed
        logger.info(
            "Pass 1 output too large (%d bytes), falling back to pass 2",
            os.path.getsize(output_path)
        )
    else:
        logger.warning("Pass 1 ffmpeg failed: %s", result1.stderr.decode())

    # --- Pass 2: Fixed bitrate ---
    target_kbps = max(int((MAX_SIZE_BYTES * 8 * 0.9) / (duration * 1000)), 50)
    cmd2 = [
        "ffmpeg", "-y", "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libvpx-vp9",
        "-b:v", f"{target_kbps}k",
        "-maxrate", f"{target_kbps}k",
        "-bufsize", f"{target_kbps * 2}k",
        "-deadline", "good", "-cpu-used", "4",
        output_path
    ]
    result2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result2.returncode != 0:
        logger.warning("Pass 2 ffmpeg failed: %s", result2.stderr.decode())

    success = (
        os.path.exists(output_path)
        and os.path.getsize(output_path) <= MAX_SIZE_BYTES
    )
    if success:
        logger.info("SUCCESS on pass 2 (%dk bitrate)", target_kbps)
    else:
        logger.warning("FAILED both passes")

    return success, was_trimmed


# ======================= TELEGRAM HANDLERS =======================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # Resolve the media object (video, animation, or video/gif document)
    video = (
        message.video
        or message.animation
        or (
            message.document
            if message.document
            and message.document.mime_type
            and (
                message.document.mime_type.startswith("video/")
                or message.document.mime_type == "image/gif"
            )
            else None
        )
    )

    if not video:
        return

    # Guard against excessively large files before downloading
    file_size = getattr(video, "file_size", None)
    if file_size and file_size > MAX_INPUT_SIZE_BYTES:
        await message.reply_text(
            f"❌ Файл слишком большой ({file_size / 1024 / 1024:.1f} МБ). "
            "Максимум — 50 МБ."
        )
        return

    file_id = video.file_id
    input_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{file_id}.webm")

    try:
        await message.reply_text("⬇️ Скачиваю видео...")
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(input_path)

        w, h = get_video_dimensions(input_path)
        if w == 0 or h == 0:
            await message.reply_text("❌ Не удалось определить размеры видео.")
            return

        new_w, new_h = calc_scaled_dimensions(w, h)
        await message.reply_text(f"⚙️ Конвертирую в WebM ({new_w}x{new_h})...")

        success, was_trimmed = convert_to_webm(input_path, output_path)

        if success:
            size_kb = os.path.getsize(output_path) / 1024
            trim_note = "\n✂️ Видео обрезано до 3 сек." if was_trimmed else ""
            with open(output_path, "rb") as f:
                await message.reply_document(
                    document=f,
                    filename="sticker.webm",
                    caption=(
                        f"✅ Готово! {size_kb:.1f} КБ\n"
                        f"WebM VP9 • {new_w}x{new_h} • до 3с • без звука"
                        f"{trim_note}"
                    )
                )
        else:
            await message.reply_text(
                "❌ Не удалось уложиться в 256 КБ. "
                "Попробуй отправить более короткое видео."
            )

    except Exception:
        logger.exception("Unhandled error in handle_video")
        await message.reply_text("❌ Произошла ошибка при обработке видео.")

    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    logger.warning("Could not delete temp file %s: %s", p, e)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Отправь видео или GIF\n\n"
        "Я сделаю стикер:\n"
        "• Длинная сторона = 512 px\n"
        "• Короткая сторона сохраняет пропорции (≤ 512)\n"
        "• до 3 сек • без звука • до 256 КБ"
    )


# ======================= APP FACTORY =======================
def create_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # FIX: added filters.Document.IMAGE so image/gif documents are handled
    app.add_handler(MessageHandler(
        filters.VIDEO
        | filters.ANIMATION
        | filters.Document.VIDEO
        | filters.Document.IMAGE,
        handle_video
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text
    ))
    return app


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ======================= ENTRY POINT =======================
if __name__ == "__main__":
    bot_app = create_app()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        bot_app.stop_running()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("🤖 Bot started (polling + Flask on port %s)", os.environ.get("PORT", 5000))
    bot_app.run_polling()
