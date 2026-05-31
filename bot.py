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
    video = update.message.video or (update.message.document if update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/") else None)
    if not video:
        return

    try:
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

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if os.path.exists(output_path) and os.path.getsize(output_path) < 256 * 1024:
            await update.message.reply_document(document=open(output_path, "rb"), filename="sticker.webm")
        else:
            await update.message.reply_text("Conversion failed or file exceeds 256 KB.")

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")
    finally:
        # Cleanup
        for path in (input_path, output_path):
            if os.path.exists(path):
                os.remove(path)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.run_polling()


if __name__ == "__main__":
    main()
