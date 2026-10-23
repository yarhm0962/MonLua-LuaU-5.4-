import logging
import os
import io
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from obfuscator import LuaStrongObfuscator, luau_preprocess

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("obf-bot")

MAX_INPUT = 5 * 1024 * 1024
MAX_OUTPUT = 45 * 1024 * 1024
ALLOWED_EXT = (".lua", ".luau", ".txt")

_settings = {}


def settings_for(uid):
    if uid not in _settings:
        _settings[uid] = {"luau": False, "novm": False, "seed": None}
    return _settings[uid]


def settings_summary(s):
    mode = "Luau" if s["luau"] else "Lua"
    vm = "off" if s["novm"] else "on"
    seed = str(s["seed"]) if s["seed"] is not None else "random"
    return f"Mode: {mode} | VM: {vm} | Seed: {seed}"


def validate_file(filename, size):
    if not filename:
        return False, "File has no name."
    if not filename.lower().endswith(ALLOWED_EXT):
        return False, "Unsupported file type. Only .lua, .luau, and .txt are allowed."
    if size and size > MAX_INPUT:
        return False, f"File too large ({size} bytes, max {MAX_INPUT})."
    return True, ""


def run_obfuscator(source, s):
    if len(source.encode("utf-8")) > MAX_INPUT:
        raise ValueError(f"Input exceeds {MAX_INPUT} bytes.")
    if s["luau"]:
        source = luau_preprocess(source)
    obf = LuaStrongObfuscator(seed=s["seed"], use_vm=not s["novm"])
    return obf.obfuscate(source)


class SettingsView(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=300)
        self.uid = uid
        self.luau_btn = discord.ui.Button(style=discord.ButtonStyle.secondary)
        self.vm_btn = discord.ui.Button(style=discord.ButtonStyle.secondary)
        self.luau_btn.callback = self._toggle_luau
        self.vm_btn.callback = self._toggle_vm
        self.add_item(self.luau_btn)
        self.add_item(self.vm_btn)
        self._refresh(settings_for(uid))

    def _refresh(self, s):
        self.luau_btn.label = f"Luau mode: {'ON' if s['luau'] else 'OFF'}"
        self.vm_btn.label = f"VM mode: {'OFF' if s['novm'] else 'ON'}"

    async def _toggle_luau(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not your settings.", ephemeral=True)
            return
        s = settings_for(self.uid)
        s["luau"] = not s["luau"]
        self._refresh(s)
        await interaction.response.edit_message(content=settings_summary(s), view=self)

    async def _toggle_vm(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not your settings.", ephemeral=True)
            return
        s = settings_for(self.uid)
        s["novm"] = not s["novm"]
        self._refresh(s)
        await interaction.response.edit_message(content=settings_summary(s), view=self)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    guild_id = os.environ.get("GUILD_ID", "").strip()
    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info(f"Synced commands to guild {guild_id}")
        else:
            await bot.tree.sync()
            logger.info("Synced commands globally")
    except Exception as e:
        logger.exception(f"Sync failed: {e}")
    logger.info(f"Logged in as {bot.user}")


@bot.tree.command(name="obfuscate", description="Obfuscate a Lua/Luau file")
@app_commands.describe(file="A .lua, .luau, or .txt file to obfuscate")
async def cmd_obfuscate(interaction: discord.Interaction, file: discord.Attachment):
    ok, err = validate_file(file.filename, file.size)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    s = settings_for(interaction.user.id)
    if file.filename and file.filename.lower().endswith(".luau"):
        s = dict(s)
        s["luau"] = True

    try:
        raw = await file.read()
    except Exception as e:
        logger.exception("attachment read failed")
        await interaction.followup.send(f"Failed to read file: {e}")
        return

    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        await interaction.followup.send(f"File is not valid UTF-8: {e}")
        return

    try:
        result = await asyncio.to_thread(run_obfuscator, src, s)
    except NotImplementedError as e:
        await interaction.followup.send(f"Unsupported construct: {e}")
        return
    except Exception as e:
        logger.exception("obfuscation failed")
        await interaction.followup.send(
            f"Obfuscation failed: {type(e).__name__}: {e}"
        )
        return

    data = result.encode("utf-8")
    if len(data) > MAX_OUTPUT:
        await interaction.followup.send(
            f"Output too large ({len(data)} bytes, max {MAX_OUTPUT})."
        )
        return

    base = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename
    out_name = base + "_obf.lua"
    buf = io.BytesIO(data)
    try:
        await interaction.followup.send(
            content=settings_summary(s),
            file=discord.File(buf, filename=out_name),
        )
    except Exception as e:
        logger.exception("send failed")
        await interaction.followup.send(f"Failed to send output: {e}")


@bot.tree.command(name="settings", description="View or toggle obfuscator settings")
async def cmd_settings(interaction: discord.Interaction):
    s = settings_for(interaction.user.id)
    view = SettingsView(interaction.user.id)
    await interaction.response.send_message(
        settings_summary(s), view=view, ephemeral=True
    )


@bot.tree.command(name="seed", description="Set a fixed obfuscation seed")
@app_commands.describe(value="An integer seed, or 'clear' to reset to random")
async def cmd_seed(interaction: discord.Interaction, value: str):
    s = settings_for(interaction.user.id)
    if value.lower() == "clear":
        s["seed"] = None
        await interaction.response.send_message(
            "Seed cleared (random per run).", ephemeral=True
        )
        return
    try:
        s["seed"] = int(value)
        await interaction.response.send_message(
            f"Seed set to {s['seed']}.", ephemeral=True
        )
    except ValueError:
        await interaction.response.send_message(
            "Seed must be an integer or 'clear'.", ephemeral=True
        )


@bot.tree.command(name="help", description="Show help for the obfuscator bot")
async def cmd_help(interaction: discord.Interaction):
    text = (
        "**Lua Strong Obfuscator**\n\n"
        "`/obfuscate <file>` — obfuscate a `.lua`, `.luau`, or `.txt` file (required)\n"
        "`/settings` — toggle Luau mode and VM mode\n"
        "`/seed <n>` — set a fixed seed\n"
        "`/seed clear` — reset to random\n"
        "`/help` — show this message\n\n"
        f"Current: {settings_summary(settings_for(interaction.user.id))}"
    )
    await interaction.response.send_message(text, ephemeral=True)


def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        logger.error("DISCORD_TOKEN is not set")
        raise SystemExit(1)
    try:
        bot.run(token)
    except Exception as e:
        logger.exception(f"Fatal: {e}")
        raise


if __name__ == "__main__":
    main()
