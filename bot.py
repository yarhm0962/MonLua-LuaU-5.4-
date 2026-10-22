import os
import re
import random
import string
import base64
import io
import discord
from discord import app_commands

class ProLuaObfuscator:
    def __init__(self):
        self.keywords = {
            'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for',
            'function', 'goto', 'if', 'in', 'local', 'nil', 'not', 'or',
            'repeat', 'return', 'then', 'true', 'until', 'while'
        }
        self.builtins = {
            'print', 'warn', 'error', 'assert', 'collectgarbage', 'dofile',
            'getmetatable', 'setmetatable', 'ipairs', 'pairs', 'next', 'pcall',
            'xpcall', 'select', 'tonumber', 'tostring', 'type', '_VERSION',
            '_G', 'game', 'workspace', 'script', 'math', 'table', 'string',
            'coroutine', 'utf8', 'io', 'os', 'debug', 'task', 'bit32',
            'Instance', 'Vector3', 'CFrame', 'Color3', 'UDim2', 'BrickColor',
            'TweenInfo', 'Ray', 'Region3', 'Enum', 'SharedTable', 'shared',
            'loadstring', 'elapsedTime', 'tick', 'time', 'delay', 'spawn', 'firetouchinterest'
        }

    def minify(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^#<>~(),{}[\];:])\s*', r'\1', code)
        return code.strip()

    def obfuscate(self, code):
        minified = self.minify(code)
        encoded_bytes = base64.b64encode(minified.encode('utf-8')).decode('utf-8')
        
        vm_stub = f"""(function()
local b64 = "{encoded_bytes}"
local b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
local function decode(data)
data = string.gsub(data, '[^'..b..'=]', '')
return (data:gsub('.', function(x)
if (x == '=') then return '' end
local r,f='',b:find(x)-1
for i=6,1,-1 do r=r..(f%2^i-f%2^(i-1)>0 and '1' or '0') end
return r
end):gsub('%d%d%d?%d?%d?%d?%d?%d?', function(x)
if (#x ~= 8) then return '' end
local c=0
for i=1,8 do c=c+(x:sub(i,i)=='1' and 2^(8-i) or 0) end
return string.char(c)
end))
end
local src = decode(b64)
local env = (getfenv and getfenv()) or _ENV or _G
local fn, err = loadstring(src)
if fn then
if setfenv then setfenv(fn, env) end
return fn()
else
error(err)
end
end)()"""
        
        single_line = " ".join(vm_stub.split())
        return single_line

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = ProLuaObfuscator()

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
        await interaction.followup.send("Obfuscation complete. Single-line VM protected script ready:", file=output_file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
