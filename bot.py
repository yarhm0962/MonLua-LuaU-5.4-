import os
import re
import random
import string
import io
import discord
from discord import app_commands

class UltimateLuaObfuscator:
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

    def _gen_name(self, length=10):
        chars = string.ascii_letters + '_'
        return random.choice(string.ascii_letters + '_') + ''.join(random.choices(chars, k=length-1))

    def remove_comments(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        return code

    def encrypt_strings(self, code):
        key = random.randint(50, 200)
        def replace_str(match):
            s = match.group(1) or match.group(2) or ''
            if not s:
                return '""'
            encrypted_bytes = [(ord(c) ^ key) for c in s]
            return f'(function() local x="" for _,v in ipairs({encrypted_bytes}) do x=x..string.char(v ~ {key}) end return x end)()'
        pattern = r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\''
        return re.sub(pattern, replace_str, code)

    def flatten_control_flow(self, code):
        func_pattern = r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)(.*?)(?=\bend\b)'
        def flatten_func(match):
            fname = match.group(1)
            fargs = match.group(2)
            fbody = match.group(3)
            state_var = self._gen_name()
            states = [random.randint(1000, 9999) for _ in range(4)]
            random.shuffle(states)
            flattened = f'local function {fname}({fargs})\n'
            flattened += f'local {state_var} = {states[0]}\n'
            flattened += f'while true do\n'
            flattened += f'if {state_var} == {states[0]} then\n'
            flattened += f'{fbody}\n'
            flattened += f'{state_var} = {states[1]}\n'
            flattened += f'elseif {state_var} == {states[1]} then\n'
            flattened += f'break\n'
            flattened += f'end\n'
            flattened += f'end\n'
            flattened += f'end\n'
            return flattened
        code = re.sub(func_pattern, flatten_func, code, flags=re.DOTALL)
        return code

    def mask_globals(self, code):
        env_name = self._gen_name()
        def replace_builtin(match):
            name = match.group(0)
            if name in self.builtins and name not in self.keywords:
                return f'{env_name}["{name}"]'
            return name
        pattern = r'\b(' + '|'.join(self.builtins) + r')\b'
        code = re.sub(pattern, replace_builtin, code)
        wrapper = f'local {env_name} = (getfenv and getfenv()) or _ENV or _G;\n'
        return wrapper + code

    def wrap_in_vm(self, code):
        vm_key = random.randint(11, 99)
        encoded_bytes = [(ord(c) ^ vm_key) for c in code]
        vm_runner = f"""
(function()
    local d = {encoded_bytes}
    local k = {vm_key}
    local b = ""
    for i = 1, #d do
        b = b .. string.char(d[i] ~ k)
    end
    local env = (getfenv and getfenv()) or _ENV or _G
    local f, e = loadstring(b)
    if f then
        if setfenv then
            setfenv(f, env)
        end
        return f()
    else
        error(e)
    end
end)()
"""
        return vm_runner.strip()

    def obfuscate(self, code):
        code = self.remove_comments(code)
        code = self.flatten_control_flow(code)
        code = self.encrypt_strings(code)
        code = self.mask_globals(code)
        code = self.wrap_in_vm(code)
        return code

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = UltimateLuaObfuscator()

@client.event
async def on_ready():
    await tree.sync()

@tree.command(name="obfuscate", description="Obfuscates Lua code securely")
@app_commands.describe(code="The raw Lua code to obfuscate")
async def obfuscate_command(interaction: discord.Interaction, code: str):
    await interaction.response.defer(thinking=True)
    try:
        obfuscated_code = obfuscator.obfuscate(code)
        file_bytes = io.BytesIO(obfuscated_code.encode('utf-8'))
        file = discord.File(file_bytes, filename="obfuscated.lua")
        await interaction.followup.send("Here is your obfuscated script:", file=file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred during obfuscation: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
