import os
import subprocess
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask

# ========================= CONFIG =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")

DOWNLOAD_DIR = "downloads"
OUTPUT_DIR = "outputs"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ======================= FLASK APP =======================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Telegram WebM Bot is running!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ======================= TELEGRAM BOT =======================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video = None
    
    # Support video, GIF (animation), and video/GIF documents
    if message.video:
        video = message.video
    elif message.animation:  # GIF support
        video = message.animation
    elif message.document and message.document.mime_type:
        if (message.document.mime_type.startswith("video/") or 
            message.document.mime_type == "image/gif"):
            video = message.document

    if not video:
        return

    try:
        await message.reply_text("🔄 Downloading...")
        file = await context.bot.get_file(video.file_id)
       
        input_path = os.path.join(DOWNLOAD_DIR, f"{video.file_id}.mp4")
        output_path = os.path.join(OUTPUT_DIR, f"{video.file_id}.webm")
       
        await file.download_to_drive(input_path)
        
        await message.reply_text("⚙️ Converting to WebM (first 3s)...")
        
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", input_path, "-t", "3",
            "-vf", "scale='min(512,iw)':min'(512,ih)':force_original_aspect_ratio=decrease,fps=30",
            "-an", "-c:v", "libvpx-vp9", "-b:v", "150K", "-maxrate", "150K",
            "-bufsize", "300K", "-crf", "35", "-deadline", "realtime", "-cpu-used", "6",
            output_path
        ]
       
        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
       
        if os.path.exists(output_path) and os.path.getsize(output_path) < 256 * 1024:
            await message.reply_document(
                document=open(output_path, "rb"),
                filename="sticker.webm",
                caption="✅ Here is your WebM sticker!"
            )
        else:
            await message.reply_text("❌ Failed or file too big (>256KB)")
           
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

def create_app():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO | filters.Document.ANIMATION, handle_video))
    return app

# ======================= MAIN =======================
if __name__ == "__main__":
    import asyncio
    bot_app = create_app()
    print("🤖 Starting bot with polling (local mode)")
    bot_app.run_polling()
else:
    pass
