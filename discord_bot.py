import os
import aiohttp
import discord
from discord.ext import commands

# Environment variables are intentionally read when the bot starts.
# The bot is imported by app.py, so it can run inside the same Render Web Service.
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
INVENTORY_URL = os.environ.get("INVENTORY_URL", "").rstrip("/")
API_KEY = os.environ.get("DISCORD_API_KEY", "").strip()

try:
    CHANNEL_ID = int(os.environ.get("IMPORT_CHANNEL_ID", "0"))
except ValueError:
    CHANNEL_ID = 0

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user} | watching channel {CHANNEL_ID}")


@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return

    text = message.content.strip()
    if not text:
        return

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{INVENTORY_URL}/api/discord/import",
                json={"text": text},
                headers={"X-Discord-API-Key": API_KEY},
            ) as response:
                data = await response.json(content_type=None)

                if response.status == 200 and data.get("ok"):
                    await message.add_reaction("✅")
                    print(f"[Discord] Imported {data.get('count', 0)} account(s).")
                else:
                    await message.add_reaction("❌")
                    errors = data.get("errors", [data.get("error", "Import failed.")])
                    error_text = "\n".join(str(x) for x in errors)[:1800]
                    await message.reply(
                        f"Import failed:\n```text\n{error_text}\n```",
                        mention_author=False,
                    )

    except Exception as exc:
        await message.add_reaction("❌")
        await message.reply(
            f"Bot/API error: `{str(exc)[:500]}`",
            mention_author=False,
        )

    await bot.process_commands(message)


def start_bot():
    """Start the Discord bot when called by app.py."""
    missing = []
    if not TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not INVENTORY_URL:
        missing.append("INVENTORY_URL")
    if not API_KEY:
        missing.append("DISCORD_API_KEY")
    if not CHANNEL_ID:
        missing.append("IMPORT_CHANNEL_ID")

    if missing:
        print("[Discord] Bot disabled. Missing: " + ", ".join(missing))
        return

    print("[Discord] Starting bot...")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    start_bot()
