Telegram WebM Converter Bot
===========================

A Telegram bot that converts videos sent to it into WebM format with the following specifications:

- Format: .webm
- Codec: VP9 (libvpx-vp9)
- Resolution: 512 x 512 pixels
- Duration: up to 3 seconds
- Frame rate: 30 fps
- Audio: removed
- Maximum file size: 256 KB

The bot runs 24/7 in the cloud (e.g., Railway) and processes videos automatically.

Features
--------

- Converts videos to VP9 WebM
- Pads videos to square format (512x512) while preserving aspect ratio
- Trims videos to 3 seconds
- Strips audio
- Automatically replies with the converted file
- Handles videos sent as video messages or documents

Requirements
------------

- Python 3.9+
- FFmpeg installed (Dockerfile includes FFmpeg)
- python-telegram-bot==20.7

Setup
-----

1. Clone the repository:

   git clone https://github.com/nazelizurna/telegram-webm-bot.git
   cd telegram-webm-bot

2. Install dependencies (optional if using Docker):

   pip install -r requirements.txt

3. Set your Telegram bot token:

   Linux/macOS:
       export BOT_TOKEN="YOUR_BOT_TOKEN"
   Windows:
       set BOT_TOKEN="YOUR_BOT_TOKEN"

4. Run the bot locally (optional):

   python bot.py

Deployment (Recommended: Cloud)
------------------------------

1. Push the repository to GitHub.
2. Use Railway (or any cloud with Docker support) to deploy.
3. Railway will detect the Dockerfile and start the bot.
4. Set the BOT_TOKEN environment variable in Railway.
5. The bot runs 24/7.

Docker
------

The included Dockerfile installs Python and FFmpeg automatically:

FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]

Usage
-----

1. Open your Telegram bot.
2. Send a video file.
3. The bot converts it and sends back the WebM file.
4. If the file exceeds 256 KB, the bot may fail or reject it.

Limitations
-----------

- Large or complex videos may not fit under 256 KB.
