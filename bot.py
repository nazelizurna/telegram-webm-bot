import os
import subprocess
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")
DOWNLOAD_DIR = "downloads"
OUTPUT_DIR = "outputs"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        return

    file = await context.bot.get_file(video.file_id)
    input_path = os.path.join(DOWNLOAD_DIR, f"{video.file_id}.mp4")
    output_path = os.path.join(OUTPUT_DIR, f"{video.file_id}.webm")

    await file.download_to_drive(input_path)

    ffmpeg_cmd = [
    "ffmpeg", "-y",
    "-i", input_path,
    "-t", "3",
    "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=512:512,fps=30",
    "-an",
    "-c:v", "libvpx-vp9",
    "-b:v", "150K",
    "-maxrate", "150K",
    "-bufsize", "300K",
    "-crf", "40",
    "-deadline", "realtime",
    "-cpu-used", "5",
    output_path
]

    subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(output_path) and os.path.getsize(output_path) < 256 * 1024:
        await update.message.reply_document(document=open(output_path, "rb"))
    else:
        await update.message.reply_text("Conversion failed or file exceeds 256 KB.")

    os.remove(input_path)
    if os.path.exists(output_path):
        os.remove(output_path)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.run_polling()

if __name__ == "__main__":
    main()
