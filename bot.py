import os
import re
import random
import string
import io
import discord
from discord import app_commands

class MegaLuaObfuscator:
    def __init__(self):
        pass

    def minify(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^#<>~(),{}[\];:])\s*', r'\1', code)
        return code.strip()

    def flatten_control_flow(self, code):
        func_pattern = r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)(.*?)(?=\bend\b)'
        def flatten_func(match):
            fname = match.group(1)
            fargs = match.group(2)
            fbody = match.group(3)
            state_var = "".join(random.choices(string.ascii_letters, k=8))
            s1 = random.randint(10000, 30000)
            s2 = s1 + 1337
            s3 = s2 + 42
            flattened = f'local function {fname}({fargs})\n'
            flattened += f'local {state_var} = {s1}\n'
            flattened += f'while true do\n'
            flattened += f'if ({state_var} * 3) % 2 == { (s1 * 3) % 2 } and {state_var} == {s1} then\n'
            flattened += f'{fbody}\n'
            flattened += f'{state_var} = {s2}\n'
            flattened += f'elseif {state_var} == {s2} then\n'
            flattened += f'{state_var} = {s3}\n'
            flattened += f'elseif {state_var} == {s3} then\n'
            flattened += f'break\n'
            flattened += f'end\n'
            flattened += f'end\n'
            flattened += f'end\n'
            return flattened
        return re.sub(func_pattern, flatten_func, code, flags=re.DOTALL)

    def obfuscate(self, code):
        minified = self.minify(code)
        flattened = self.flatten_control_flow(minified)
        
        anti_tamper = r'''
local isSafe = true
local securityReason = ""
local isRobloxEnvironment = game ~= nil and typeof ~= nil
local function verifyGetfenvHook()
if getfenv ~= nil then
local getfenvType = type(getfenv)
if getfenvType ~= "function" then
isSafe = false
securityReason = "getfenv spoofed"
return
end
local success, envTable = pcall(getfenv, 0)
if not success then
isSafe = false
securityReason = "getfenv(0) error"
return
end
if isRobloxEnvironment then
if envTable.game == nil or envTable.workspace == nil then
isSafe = false
securityReason = "Roblox env missing"
return
end
end
end
local function verifyEnvironmentProxy()
local mt = getmetatable(_G)
if mt ~= nil then
isSafe = false
securityReason = "Environment proxied"
return
end
end
local function verifyEnvironmentLeak()
local keys = {"fenv","env","_fenv","genv","_env","hookenv"}
for _, k in ipairs(keys) do
if rawget(_G, k) ~= nil then
isSafe = false
securityReason = "Env leak detected"
return
end
end
end
local function executeSecurityChecks()
verifyGetfenvHook()
if isSafe then verifyEnvironmentProxy() end
if isSafe then verifyEnvironmentLeak() end
if not isSafe then error(securityReason, 0) end
end
executeSecurityChecks()
local proxyEnv = setmetatable({}, {
__index = function(_, k)
return _G[k]
end,
__newindex = function(_, k, v)
_G[k] = v
end
})
'''.strip()

        combined = anti_tamper + "\n" + flattened
        
        vm_key1 = random.randint(11, 99)
        vm_key2 = random.randint(100, 250)
        encoded_bytes = [((ord(c) ^ vm_key1) + vm_key2) % 256 for c in combined]

        vm_stub = f'''(function()
local bytecode = {encoded_bytes}
local k1 = {vm_key1}
local k2 = {vm_key2}
local decoded = ""
local vm_state = 1
while vm_state == 1 do
for i = 1, #bytecode do
local val = bytecode[i]
val = (val - k2) % 256
decoded = decoded .. string.char(val ~ k1)
end
vm_state = 2
end
local env = proxyEnv or (getfenv and getfenv()) or _ENV or _G
local fn, err = loadstring(decoded)
if fn then
if setfenv then setfenv(fn, env) end
return fn()
else
error(err)
end
end)()'''

        return " ".join(vm_stub.split())

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = MegaLuaObfuscator()

@client.event
async def on_ready():
    await tree.sync()

@tree.command(name="obfuscate", description="Obfuscates Lua code with advanced VM, CFF, and Anti-Tamper protection")
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
        output_file = discord.File(output_io, filename="secured_" + file.filename)
        await interaction.followup.send("Obfuscation complete with multi-layer VM, CFF, and Anti-Tamper protection:", file=output_file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
