import asyncio
import io
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from engine import ALLOWED_SUFFIXES, BuildError, build_artifact

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(512 * 1024)))
BUILD_TIMEOUT_SECONDS = int(os.getenv("BUILD_TIMEOUT_SECONDS", "300"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("monlua")


class MonLuaBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()
        logger.info("slash_commands_synced")


intents = discord.Intents.default()
bot = MonLuaBot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    logger.info("bot_ready user_id=%s", bot.user.id if bot.user else "unknown")


@bot.tree.command(name="obfuscate", description="Obfuscate a Lua/Luau script with the MonLua VM engine.")
@app_commands.describe(file="Attach a .lua, .luau or .txt file")
async def obfuscate(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True, thinking=True)
    started = time.monotonic()

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        await interaction.followup.send("Upload a `.lua`, `.luau` or `.txt` file.", ephemeral=True)
        return
    if file.size > MAX_UPLOAD_BYTES:
        await interaction.followup.send(
            f"File exceeds the configured limit of {MAX_UPLOAD_BYTES // 1024} KB.",
            ephemeral=True
        )
        return

    try:
        data = await asyncio.wait_for(file.read(use_cached=True), timeout=10)
        if len(data) > MAX_UPLOAD_BYTES:
            raise BuildError("The downloaded file exceeds the configured size limit.")
        result = await asyncio.wait_for(
            asyncio.to_thread(
                build_artifact,
                data,
                file.filename,
                "luau",
                BUILD_TIMEOUT_SECONDS
            ),
            timeout=BUILD_TIMEOUT_SECONDS + 5
        )
    except asyncio.TimeoutError:
        logger.warning("build_failed reason=timeout")
        await interaction.followup.send("Build timed out. Try a smaller file.", ephemeral=True)
        return
    except BuildError as exc:
        logger.warning("build_failed reason=%s", exc.code)
        await interaction.followup.send(f"Build stopped: {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("build_failed reason=internal_error")
        await interaction.followup.send(
            "The build failed unexpectedly. No source content was written to the logs.",
            ephemeral=True
        )
        return

    elapsed = round(time.monotonic() - started, 2)
    logger.info(
        "build_complete mode=%s in_bytes=%s out_bytes=%s elapsed_seconds=%s",
        result.mode,
        len(data),
        len(result.output_bytes),
        elapsed
    )

    report = (
        "**MonLua 5.4+ obfuscation report**\n"
        f"Input: `{Path(file.filename).name}` ({len(data):,} bytes)\n"
        f"Output: `{result.output_name}` ({len(result.output_bytes):,} bytes)\n"
        f"Engine: `{result.mode}`\n"
        f"Input SHA-256: `{result.input_sha256}`\n"
        f"Output SHA-256: `{result.output_sha256}`\n"
        f"Validation: `{result.validation}`\n"
        f"Status: `{result.status}`\n"
        f"Elapsed: `{elapsed}s`\n\n"
        f"**Limitations:** {result.notice}"
    )
    attachments = [
        discord.File(io.BytesIO(result.output_bytes), filename=result.output_name),
        discord.File(io.BytesIO(result.manifest_bytes), filename=result.output_name + ".manifest.json")
    ]
    await interaction.followup.send(content=report, files=attachments, ephemeral=True)


@bot.tree.command(name="protectinfo", description="Show what this bot does and its limitations.")
async def protectinfo(interaction: discord.Interaction):
    embed = discord.Embed(
        title="MonLua 5.4+",
        description="VM-based Lua/Luau obfuscation. Protection, not encryption.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Implemented",
        value=(
            "VM compilation to a custom bytecode with shuffled opcodes, encoded strings "
            "and numbers, junk-code insertion, randomized identifiers, private replies, "
            "SHA-256 manifest, upload limits, optional compiler validation."
        ),
        inline=False
    )
    embed.add_field(
        name="Limitations",
        value=(
            "Obfuscation is protection, not encryption. Dedicated tooling may still be able "
            "to reverse it. A hash manifest detects changes only; it hides nothing."
        ),
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is required.")

bot.run(TOKEN)
