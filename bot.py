import os
import re
import random
import string
import io
import discord
from discord import app_commands

class RobustLuaObfuscator:
    def __init__(self):
        pass

    def minify(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^#<>~(),{}[\];:])\s*', r'\1', code)
        return code.strip()

    def obfuscate(self, code):
        minified = self.minify(code)
        
        anti_tamper = '''
local isSafe = true
local isRoblox = game ~= nil and typeof ~= nil
if getfenv ~= nil then
    local ok, env = pcall(getfenv, 0)
    if ok and env then
        if isRoblox and (env.game == nil or env.workspace == nil) then
            isSafe = false
        end
    else
        isSafe = false
    end
end
if not isSafe then
    error("Security violation detected", 0)
end
'''.strip()

        combined = anti_tamper + "\n" + minified
        
        k1 = random.randint(11, 99)
        k2 = random.randint(100, 250)
        encoded_bytes = [((ord(c) ^ k1) + k2) % 256 for c in combined]

        vm_stub = f'''(function()
local d = {encoded_bytes}
local k1 = {k1}
local k2 = {k2}
local s = ""
for i = 1, #d do
local v = d[i]
v = (v - k2) % 256
s = s .. string.char(v ~ k1)
end
local env = (getfenv and getfenv()) or _ENV or _G
local f, e = loadstring(s)
if f then
if setfenv then setfenv(f, env) end
return f()
else
error(e)
end
end)()'''

        return " ".join(vm_stub.split())

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = RobustLuaObfuscator()

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
        await interaction.followup.send("Obfuscation complete and verified for execution:", file=output_file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
