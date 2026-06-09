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
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(result.stdout.strip())
    except:
        return 0.0

def get_video_dimensions(path: str) -> tuple[int, int]:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except:
        return 0, 0

def get_output_dimensions(path: str) -> tuple[int, int]:
    """Получает реальные размеры готового webm"""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except:
        return 0, 0

def build_scale_filter(width: int, height: int) -> str:
    """
    Самый простой и надёжный способ:
    - Если видео горизонтальное → scale=512:-1 (высота автоматически)
    - Если вертикальное → scale=-1:512 (ширина автоматически)
    """
    if width > height:
        return "scale=512:-1:flags=lanczos,setsar=1,fps=30"
    else:
        return "scale=-1:512:flags=lanczos,setsar=1,fps=30"

def convert_to_webm(input_path: str, output_path: str) -> bool:
    duration = min(get_video_duration(input_path), MAX_DURATION_SEC)
    w, h = get_video_dimensions(input_path)
    vf = build_scale_filter(w, h)

    logger.info(f"Input: {w}x{h} → Filter: {vf}")

    # === Pass 1 ===
    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-t", str(duration),
        "-vf", vf, "-an",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-crf", "33", "-b:v", "0",
        "-deadline", "good", "-cpu-used", "3",
        output_path
    ]
    result1 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if result1.returncode != 0:
        logger.error(f"FFmpeg Pass 1 error: {result1.stderr}")
    
    if os.path.exists(output_path) and os.path.getsize(output_path) <= MAX_SIZE_BYTES:
        return True

    # === Pass 2 ===
    target_kbps = max(int((MAX_SIZE_BYTES * 8 * 0.9) / (duration * 1000)), 50)
    cmd2 = [
        "ffmpeg", "-y", "-i", input_path, "-t", str(duration),
        "-vf", vf, "-an",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-b:v", f"{target_kbps}k", "-maxrate", f"{target_kbps}k",
        "-bufsize", f"{target_kbps * 2}k",
        "-deadline", "good", "-cpu-used", "4",
        output_path
    ]
    result2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result2.returncode != 0:
        logger.error(f"FFmpeg Pass 2 error: {result2.stderr}")

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

        await message.reply_text("⚙️ Конвертирую (сохраняю пропорции)...")

        success = convert_to_webm(input_path, output_path)

        if success:
            out_w, out_h = get_output_dimensions(output_path)
            size_kb = os.path.getsize(output_path) / 1024

            logger.info(f"OUTPUT SIZE: {out_w}x{out_h} | {size_kb:.1f} KB")

            with open(output_path, "rb") as f:
                await message.reply_document(
                    document=f,
                    filename="sticker.webm",
                    caption=f"✅ Готово! {size_kb:.1f} КБ\n"
                            f"Размер: {out_w}×{out_h} px (длинная сторона 512)\n"
                            "Пропорции сохранены • VP9 • ≤3с • без звука"
                )
        else:
            await message.reply_text("❌ Не удалось уложиться в 256 КБ. Попробуй короче видео.")
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
        "Я сделаю стикер с сохранением пропорций:\n"
        "• Длинная сторона = 512 px\n"
        "• Короткая сторона ≤ 512 px\n"
        "• Без обрезки в квадрат!"
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
