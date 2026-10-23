import discord
from discord import app_commands
import os
import io
import random
import string
import base64
import re
import zlib

ALLOWED_EXTENSIONS = {'.lua', '.txt', '.luau'}
MAX_FILE_SIZE = 2 * 1024 * 1024

class AdvancedLuaObfuscator:
    def __init__(self):
        self.used_names = set()
        self.name_map = {}
        self.seed = random.randint(100000000, 999999999)
        random.seed(self.seed)

    def _gen_name(self, n=14):
        while True:
            c = '_' + ''.join(random.choices(string.ascii_letters, k=n))
            if c not in self.used_names:
                self.used_names.add(c)
                return c

    def _minify_lua(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^<>(){}[\];:,])\s*', r'\1', code)
        return code.strip()

    def _compress_and_encode(self, text):
        compressed = zlib.compress(text.encode('utf-8'), level=9)
        return base64.b64encode(compressed).decode('ascii')

    def _build_decoder(self):
        return "(function()local _z=function(d)local c,e='eJyl',{},0,256,1,0,{},{},{},{},{},{},{}local function b(n)return n<0 and n+256 or n end local function r()local b1=b(d:byte(e+1))e=e+1;return b1 end local function s(l)local v,t,p=0,{},1 for i=1,l do v=v*64+string.find('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/',d:sub(p,p))-1 p=p+1 end return v end local n=s(4)if n%31~=0 then return''end local t0={[0]=0}for i=1,286 do t0[i]=i<=143 and i or i<=257 and i-144 or i-256 end local t1={}for i=0,285 do t1[t0[i]]=i end local function g(l)local v,b,p=0,0,''if l==9 then while true do b=r()if b<256 then p=p..string.char(b)elseif b==256 then return p else v=b;b=r();v=v*256+b;b=r();v=v*256+b;for j=1,v do p=p..string.char(t0[r()])end end end else local t={[4]=1,[5]=2,[6]=3,[7]=4,[8]=5,[9]=6,[10]=7,[11]=8}local m={[1]=2,[2]=3,[3]=4,[4]=5,[5]=6,[6]=7,[7]=8,[8]=9,[9]=10}while true do b=r()if b<256 then p=p..string.char(b)elseif b==256 then return p else local l=t[b]local c=m[l]v=0 for i=1,c do v=v*2+r()end if v==0 then p=p..string.char(t0[r()])elseif v==1 then v=r()v=v*256+r()for j=1,v do p=p..p:sub(#p-#p+1,#p)end else v=v-1 local w=t1[v]local x=w>>8 local y=w&255 local z=r()z=z*256+r()for j=1,z do p=p..p:sub(#p-x,#p-y)end end end end end return''end return g(n>>16)end return _z end)()"

    def _encrypt_string(self, s):
        compressed_b64 = self._compress_and_encode(s)
        v = self._gen_name(8)
        return "(function()local " + v + "='" + compressed_b64 + "';return " + self._gen_name(8) + "(" + v + ")end)()"

    def _encode_number(self, m):
        orig = m.group(0)
        if '.' in orig:
            n = float(orig)
            a = round(random.uniform(-9999, 9999), 2)
            return "(" + str(n - a) + "+(" + str(a) + "))"
        n = int(orig)
        x = random.randint(1000, 9999)
        return "(" + str(n + x) + "-" + str(x) + ")"

    def _rename_identifiers(self, code):
        keywords = {'and','break','do','else','elseif','end','false','for','function','if','in','local','nil','not','or','repeat','return','then','true','until','while','self','goto','pairs','ipairs','next','type','tostring','tonumber','select','unpack','pcall','xpcall','error','rawget','rawset'}
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

    def _inject_junk(self, code):
        junk = [
            "local " + self._gen_name(8) + "={}",
            "local " + self._gen_name(8) + "=function()end",
        ]
        parts = code.split(';')
        out = []
        for p in parts:
            out.append(p)
            if p.strip() and random.random() < 0.08:
                out.append(random.choice(junk))
        return ';'.join(out)

    def _anti_debug(self):
        c1 = self._gen_name(10)
        c2 = self._gen_name(10)
        return "local " + c1 + "=(debug and debug.getinfo or function()return end);local " + c2 + "=os and os.clock or function()return 0 end;local _t=" + c2 + "();if " + c2 + "()-_t>0.1 then return end;"

    def _control_flow_flatten(self, code):
        st = self._gen_name(8)
        return "local " + st + "=0;while true do if " + st + "==0 then " + code + " " + st + "=1 end;if " + st + "==1 then break end end"

    def obfuscate(self, source):
        if not source or not source.strip():
            raise ValueError("Source file is empty")
        minified = self._minify_lua(source)
        compressed_source = zlib.compress(minified.encode('utf-8'), level=9)
        b64_compressed = base64.b64encode(compressed_source).decode('ascii')
        ad = self._anti_debug()
        decoder = self._build_decoder()
        main_name = self._gen_name(10)
        data_name = self._gen_name(10)
        final = "(function()local " + main_name + "=" + decoder + ";local " + data_name + "='" + b64_compressed + "';" + ad + "return loadstring(" + main_name + "(" + data_name + "))end)()"
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
        original_kb = round(len(source_code.encode('utf-8')) / 1024, 1)
        final_kb = round(len(obfuscated_code.encode('utf-8')) / 1024, 1)
        reduction = round((1 - final_kb / original_kb) * 100, 1) if original_kb > 0 else 0
        await interaction.followup.send(
            content="✅ Obfuscation complete! Original: %s KB → Final: %s KB (%s%% smaller). ⚠️ Always test your script." % (original_kb, final_kb, reduction),
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
