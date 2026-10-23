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
        self.xor_key = random.randbytes(16)
        self.seed = random.randint(100000000, 999999999)
        random.seed(self.seed)

    def _gen_name(self, n=16):
        while True:
            c = ''.join(random.choices(string.ascii_letters + '_', k=n))
            if c not in self.used_names:
                self.used_names.add(c)
                return c

    def _encrypt_str(self, s):
        data = s.encode('utf-8')
        enc = bytes(a ^ b for a, b in zip(data, self.xor_key * (len(data) // len(self.xor_key) + 1)))
        b64 = base64.b85encode(enc).decode('ascii')
        kn = self._gen_name(8)
        dn = self._gen_name(8)
        key_str = ','.join(str(x) for x in self.xor_key)
        return f"(function()local {kn}={{{key_str}}};local {dn}=base64.b85decode('{b64}');local r='';for i=1,#{dn}do r=r..string.char(string.byte({dn},i)~{kn}[(i-1)%#{kn}+1])end;return r end)()"

    def _encrypt_num(self, m):
        orig = m.group(0)
        if '.' in orig:
            n = float(orig)
            a = random.randint(10000, 99999) / 1000
            b = n - a
            return f"({a}+({b}))"
        n = int(orig)
        off = random.randint(5000, 50000)
        return f"({n+off}-{off})"

    def _tokenize(self, code):
        tokens = []
        i = 0
        n = len(code)
        patterns = [
            ('COMMENT', r'--\[\[.*?\]\]|--.*?$', re.MULTILINE),
            ('STRING', r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\[\[.*?\]\]', re.DOTALL),
            ('NUMBER', r'\b\d+\.\d*([eE][+-]?\d+)?|\b\d+[eE][+-]?\d+|\b\d+\b'),
            ('NAME', r'[a-zA-Z_]\w*'),
            ('SYMBOL', r'==|~=|<=|>=|\.\.\.|\.\.|::|[+\-*/%^#<>=\(\){}\[\];:,\\.]'),
            ('WS', r'\s+'),
        ]
        while i < n:
            matched = False
            for kind, pat, *flags in patterns:
                f = flags[0] if flags else 0
                m = re.match(pat, code[i:], f)
                if m:
                    tokens.append((kind, m.group(0)))
                    i += len(m.group(0))
                    matched = True
                    break
            if not matched:
                tokens.append(('SYMBOL', code[i]))
                i += 1
        return tokens

    def _rename_tokens(self, tokens):
        result = []
        keywords = {'and','break','do','else','elseif','end','false','for','function','if','in','local','nil','not','or','repeat','return','then','true','until','while','self','goto'}
        for kind, val in tokens:
            if kind == 'NAME' and val not in keywords:
                if val not in self.name_map:
                    self.name_map[val] = self._gen_name()
                result.append(('NAME', self.name_map[val]))
            else:
                result.append((kind, val))
        return result

    def _flatten_flow(self, code):
        lines = [l for l in code.split(';') if l.strip()]
        if len(lines) < 6:
            return code
        st = self._gen_name(10)
        cases = []
        for idx, ln in enumerate(lines):
            cases.append(f"if {st}=={idx}then {ln} {st}={st}+1 end")
        return f"local {st}=0;while true do;{';'.join(cases)};if {st}>={len(lines)}then break end;end"

    def _inject_junk(self, code):
        junk = [
            f"local {self._gen_name()}={{[false]=true,[nil]=0}}",
            f"pcall(function()local {self._gen_name()}={{}} end)",
            f"select(2,pcall(function()end))",
        ]
        parts = code.split(';')
        out = []
        for p in parts:
            out.append(p)
            if p.strip() and random.random() < 0.2:
                out.append(random.choice(junk))
        return ';'.join(out)

    def _anti_debug(self):
        c1 = self._gen_name(12)
        c2 = self._gen_name(12)
        c3 = self._gen_name(12)
        return f"local {c1}=(debug and debug.getinfo and function()return end or function()end);local {c2}=pcall({c1});local {c3}=(os and os.clock or function()return 0 end);local _{c3}={c3}();if {c3}()-_{c3}>0.1 then return end;"

    def obfuscate(self, source):
        tokens = self._tokenize(source)
        tokens = self._rename_tokens(tokens)
        code_parts = []
        for kind, val in tokens:
            if kind == 'STRING' and not val.startswith('[['):
                inner = val[1:-1]
                code_parts.append(self._encrypt_str(inner))
            elif kind == 'NUMBER':
                code_parts.append(self._encrypt_num(re.match(r'\d+\.?\d*', val)))
            else:
                code_parts.append(val)
        out = ''.join(code_parts)
        out = self._inject_junk(out)
        out = out.replace('\n', ';').replace('\r', '')
        out = self._flatten_flow(out)
        ad = self._anti_debug()
        wrapper_name = self._gen_name(8)
        key_arr = ','.join(str(x) for x in self.xor_key)
        final = f"(function()local {wrapper_name}={{xor={{{key_arr}}}}};{ad}{out}end)()"
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
        await interaction.followup.send(f"❌ Error: Unsupported file type `{ext}`. Only these are allowed: {allowed}", ephemeral=True)
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
        await interaction.followup.send(f"❌ Error reading file: {str(e)}", ephemeral=True)
        return
    try:
        obfuscated_code = obfuscator.obfuscate(source_code)
        output_filename = os.path.splitext(file.filename)[0] + "_obfuscated.lua"
        output_file = io.BytesIO(obfuscated_code.encode('utf-8'))
        await interaction.followup.send(
            content="✅ Obfuscation complete!",
            file=discord.File(output_file, filename=output_filename)
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error during obfuscation: {str(e)}", ephemeral=True)
        return

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set")
        exit(1)
    client.run(TOKEN)
