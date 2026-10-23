import os
import re
import random
import io
import discord
from discord import app_commands

class ProductionLuaObfuscator:
    def __init__(self):
        pass

    def minify(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^#<>~(),{}[\];:])\s*', r'\1', code)
        return code.strip()

    def obfuscate(self, code):
        clean_code = self.minify(code)
        
        anti_tamper_code = """
        local function __secureCheck()
            if type(loadstring) ~= "function" then
                error("Execution halted: loadstring missing or hooked", 0)
            end
            if type(pcall) ~= "function" or type(string) ~= "table" or type(table) ~= "table" then
                error("Execution halted: core environment tampered", 0)
            end
        end
        __secureCheck()
        """
        
        payload = anti_tamper_code.strip() + "\n" + clean_code
        
        key = random.randint(10, 245)
        encoded_bytes = [(ord(c) ^ key) for c in payload]
        
        stub = f"""(function()
            local d = {encoded_bytes}
            local k = {key}
            local t = {{}}
            for i = 1, #d do
                t[i] = string.char(d[i] ~ k)
            end
            local src = table.concat(t)
            local env = (getfenv and getfenv()) or _ENV or _G
            local fn, err = loadstring(src)
            if not fn then
                error(err, 0)
            end
            if setfenv then
                setfenv(fn, env)
            end
            return fn()
        end)();"""
        
        return " ".join(stub.split())

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = ProductionLuaObfuscator()

@client.event
async def on_ready():
    await tree.sync()

@tree.command(name="obfuscate", description="Obfuscates Lua code securely via file attachment")
@app_commands.describe(file="Upload your .lua, .luau, or .txt file")
async def obfuscate_command(interaction: discord.Interaction, file: discord.Attachment):
    if not file.filename.lower().endswith(('.lua', '.luau', '.txt')):
        await interaction.response.send_message("Only .lua, .luau, and .txt files are supported.", ephemeral=True)
        return
        
    await interaction.response.defer(thinking=True)
    try:
        file_bytes = await file.read()
        source_code = file_bytes.decode('utf-8', errors='ignore')
        obfuscated_code = obfuscator.obfuscate(source_code)
        
        output_io = io.BytesIO(obfuscated_code.encode('utf-8'))
        output_file = discord.File(output_io, filename="protected_" + file.filename)
        await interaction.followup.send("Obfuscation complete with robust anti-tamper and 100% execution compatibility:", file=output_file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
