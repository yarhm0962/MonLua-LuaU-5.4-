import discord
from discord import app_commands
import os
import io
import random
import string
import base64
import re

ALLOWED_EXTENSIONS = {'.lua', '.txt', '.luau'}
MAX_FILE_SIZE = 2 * 1024 * 1024

class AdvancedLuaObfuscator:
    def __init__(self):
        self.used_names = set()
        self.name_map = {}
        self.seed = random.randint(100000000, 999999999)
        random.seed(self.seed)

    def _gen_name(self, n=12):
        while True:
            c = '_' + ''.join(random.choices(string.ascii_letters, k=n))
            if c not in self.used_names:
                self.used_names.add(c)
                return c

    def _b64_encode(self, s):
        return base64.b64encode(s.encode('utf-8')).decode('ascii')

    def _encrypt_string(self, s):
        b64 = self._b64_encode(s)
        v = self._gen_name(8)
        return "(function()local " + v + "='" + b64 + "';local function _b(s)local t,r='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/',{}for i=1,#s,4 do local a,b,c,d=s:byte(i,i+3)b,c,d=b or 64,c or 64,d or 64 a=(a<58 and a-45 or a-65)*64+(b<58 and b-45 or b-65)r[#r+1]=math.floor(a/64);r[#r+1]=a%64 a=(c<58 and c-45 or c-65)*64+(d<58 and d-45 or d-65)r[#r+1]=math.floor(a/64);r[#r+1]=a%64 end local o=''for i=1,#r,3 do local a,b,c=r[i],r[i+1],r[i+2]o=o..string.char(a*262144+b*4096+c)end return o:gsub('%z','') end return _b(" + v + ") end)()"

    def _encode_number(self, m):
        orig = m.group(0)
        if '.' in orig:
            n = float(orig)
            off = round(random.uniform(0.001, 0.999), 3)
            return "(" + str(n + off) + "-" + str(off) + ")"
        n = int(orig)
        off = random.randint(100, 9999)
        return "(" + str(n + off) + "-" + str(off) + ")"

    def _rename_identifiers(self, code):
        keywords = {'and','break','do','else','elseif','end','false','for','function','if','in','local','nil','not','or','repeat','return','then','true','until','while','self','goto'}
        def replacer(m):
            name = m.group(0)
            if name in keywords or name.startswith('__'):
                return name
            if name not in self.name_map:
                self.name_map[name] = self._gen_name()
            return self.name_map[name]
        return re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', replacer, code)

    def _encrypt_strings(self, code):
        def double_quote(m):
            content = m.group(1)
            return self._encrypt_string(content)
        def single_quote(m):
            content = m.group(1)
            return self._encrypt_string(content)
        code = re.sub(r'"([^"\\]*(\\.[^"\\]*)*)"', double_quote, code)
        code = re.sub(r"'([^'\\]*(\\.[^'\\]*)*)'", single_quote, code)
        return code

    def _encode_numbers(self, code):
        return re.sub(r'\b\d+\.?\d*\b', self._encode_number, code)

    def obfuscate(self, source):
        if not source or not source.strip():
            raise ValueError("Source file is empty")
        obf = source
        obf = self._encrypt_strings(obf)
        obf = self._encode_numbers(obf)
        obf = self._rename_identifiers(obf)
        final = "(function()" + obf + "end)()"
        final = re.sub(r'\s+', ' ', final).strip()
        return final

obfuscator = AdvancedLuaObfuscator()

def validate_file(filename):
    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        return False, ext
    return True, ext

intents = discord.Intents.default()
intents.message_content = True

class ObfuscatorBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = ObfuscatorBot()

@client.tree.command(name="obfuscate", description="Upload a .lua, .txt, or .luau file to obfuscate it")
@app_commands.describe(file="Your script file (.lua, .txt, .luau)")
async def obfuscate_command(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(thinking=True)
    is_valid, ext = validate_file(file.filename)
    if not is_valid:
        allowed = ', '.join(ALLOWED_EXTENSIONS)
        await interaction.followup.send("❌ Error: Unsupported file type `%s`. Only these are allowed: %s" % (ext, allowed), ephemeral=True)
        return
    if file.size > MAX_FILE_SIZE:
        await interaction.followup.send("❌ Error: File too large. Maximum size is 2MB.", ephemeral=True)
        return
    try:
        file_bytes = await file.read()
        source_code = file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        await interaction.followup.send("❌ Error: Could not read file. Ensure it is valid text/UTF-8.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send("❌ Error reading file: %s" % str(e), ephemeral=True)
        return
    try:
        obfuscated_code = obfuscator.obfuscate(source_code)
        output_filename = os.path.splitext(file.filename)[0] + "_obfuscated.lua"
        output_file = io.BytesIO(obfuscated_code.encode('utf-8'))
        await interaction.followup.send(
            content="✅ Obfuscation complete! ⚠️ Always test your script.",
            file=discord.File(output_file, filename=output_filename)
        )
    except Exception as e:
        await interaction.followup.send("❌ Error during obfuscation: %s" % str(e), ephemeral=True)
        return

@client.event
async def on_ready():
    print('Logged in as %s' % client.user)

if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set")
        exit(1)
    client.run(TOKEN)
