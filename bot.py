import os
import re
import random
import string
import io
import discord
from discord import app_commands

class AdvancedVMObfuscator:
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

    def build_vm(self, code):
        vm_key_1 = random.randint(11, 99)
        vm_key_2 = random.randint(100, 250)
        encoded_bytes = [((ord(c) ^ vm_key_1) + vm_key_2) % 256 for c in code]
        
        vm_code = f"""
(function()
    local bytecode = {encoded_bytes}
    local k1 = {vm_key_1}
    local k2 = {vm_key_2}
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
    local env = (getfenv and getfenv()) or _ENV or _G
    local fn, err = loadstring(decoded)
    if fn then
        if setfenv then
            setfenv(fn, env)
        end
        return fn()
    else
        error(err)
    end
end)()
"""
        return vm_code.strip()

    def obfuscate(self, code):
        code = self.remove_comments(code)
        code = self.flatten_control_flow(code)
        code = self.encrypt_strings(code)
        code = self.mask_globals(code)
        code = self.build_vm(code)
        return code

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
obfuscator = AdvancedVMObfuscator()

@client.event
async def on_ready():
    await tree.sync()

@tree.command(name="obfuscate", description="Obfuscates Lua code via file attachment")
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
        await interaction.followup.send("Obfuscation complete with real VM protection:", file=output_file)
    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}")

token = os.environ.get("DISCORD_TOKEN")
if token:
    client.run(token)
