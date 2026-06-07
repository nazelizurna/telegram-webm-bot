# Telegram WebM Sticker Bot

A Telegram bot that converts videos and GIFs into WebM stickers compatible with Telegram's sticker requirements.

## What it does

Send the bot any video or GIF and it returns a `.webm` file ready to use as an animated Telegram sticker:

- Format: WebM (VP9 codec)
- Resolution: 512 x 512 pixels
- Duration: up to 3 seconds
- Audio: stripped
- File size: under 256 KB

## Requirements

- Python 3.10+
- ffmpeg installed and available in PATH
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Installation

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
pip install -r requirements.txt
```

## Configuration

Set your bot token as an environment variable:

```bash
export BOT_TOKEN=your_token_here
```

## Running locally

```bash
python bot.py
```

The bot will start in polling mode.

## Deploying to Render

1. Create a new Web Service on [Render](https://render.com).
2. Set the `BOT_TOKEN` environment variable in the Render dashboard.
3. Use `gunicorn` as the start command with your webhook setup.
4. The `/health` endpoint returns `200 OK` and can be used as a health check.

## Accepted input

- Video files
- Animations (GIF)
- Documents with a video or GIF MIME type

## Dependencies

```
python-telegram-bot
flask
gunicorn
```

## License

MIT
