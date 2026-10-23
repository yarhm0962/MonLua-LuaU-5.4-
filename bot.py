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

    def _gen_name(self, n=12):
        while True:
            c = '_' + ''.join(random.choices(string.ascii_letters, k=n))
            if c not in self.used_names:
                self.used_names.add(c)
                return c

    def _minify_lua(self, code):
        code = re.sub(r'--\[\[.*?\]\]', '', code, flags=re.DOTALL)
        code = re.sub(r'--.*$', '', code, flags=re.MULTILINE)
        lines = code.split('\n')
        result = []
        for line in lines:
            line = line.strip()
            if line:
                result.append(line)
        code = ' '.join(result)
        code = re.sub(r'\s+', ' ', code)
        code = re.sub(r'\s*([=+\-*/%^<>(){}[\];:,])\s*', r'\1', code)
        return code.strip()

    def _compress_and_encode(self, text):
        compressed = zlib.compress(text.encode('utf-8'), level=9)
        return base64.b64encode(compressed).decode('ascii')

    def _build_safe_decoder(self):
        return "(function(d)local b='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'local e={}for i=1,#b do e[b:sub(i,i)]=i-1 end local t,r=1,{}for i=1,#d,4 do local a,b,c,d=e[d:sub(i,i)]or-1,e[d:sub(i+1,i+1)]or-1,e[d:sub(i+2,i+2)]or-1,e[d:sub(i+3,i+3)]or-1 if a<0 or b<0 then break end r[#r+1]=((a<<2)|(b>>4))&255 if c>=0 then r[#r+1]=((b&15)<<4)|(c>>2)&15 if d>=0 then r[#r+1]=((c&3)<<6)|d&63 end end end local s=table.concat local function inflate(data)local s,dic={},{}for i=0,255 do dic[i]=string.char(i)end local pos=1 local function rb()local b=data:byte(pos)pos=pos+1 return b end local function bits(n)local v,b=0,0 for i=1,n do if b==0 then b=rb()end v=v|((b&1)<<(i-1))b=b>>1 end return v end local size=bits(32)local out={}while #out<size do local sym=bits(8)if sym<256 then out[#out+1]=dic[sym]elseif sym==256 then break else local l=sym-253 local d=bits(13)local s=#out-d for i=1,l do out[#out+1]=out[s+i]end end return table.concat(out)end local z=loadstring or load or function()return nil end if z then local compressed=base64 and base64.decompress or nil if compressed then return z(compressed(d))end end local function str_unpack(s)local l,r=string.len(s),{}for i=1,l do r[i]=string.byte(s,i)end return r end local function bit_or(a,b)local r=0 for i=0,15 do local m=1<<i if(a&m)~=0 or(b&m)~=0 then r=r|m end end return r end local function inflate_simple(data)local t={}local l=string.len(data)local s=1 local function rb()local b=string.byte(data,s)s=s+1 return b end local h=rb()local hb=h while hb>=128 do hb=hb/2 end local window={}for i=0,255 do window[i]=string.char(i)end local wpos=256 local out={}while true do local sym=rb()if sym<256 then out[#out+1]=window[sym+1]elseif sym==256 then break else local len=sym-253 local d=rb()d=d|(rb()<<8)local s=#out-d for i=1,len do out[#out+1]=out[s+i]end window[wpos+1]=out[#out]wpos=(wpos+1)%32768 end return table.concat(out)end return inflate_simple(table.concat(r):sub(1,math.max(1,#r)))end return inflate_simple(table.concat(r))end)"

    def _encrypt_string_simple(self, s):
        b64 = base64.b64encode(s.encode('utf-8')).decode('ascii')
        v = self._gen_name(8)
        return "(function()local " + v + "='" + b64 + "';local b='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'local e={}for i=1,#b do e[b:sub(i,i)]=i-1 end local t,r=1,{}for i=1,#" + v + ",4 do local a,b,c,d=e[" + v + ":sub(i,i)]or-1,e[" + v + ":sub(i+1,i+1)]or-1,e[" + v + ":sub(i+2,i+2)]or-1,e[" + v + ":sub(i+3,i+3)]or-1 if a<0 or b<0 then break end r[#r+1]=string.char(((a<<2)|(b>>4))&255)if c>=0 then r[#r+1]=string.char(((b&15)<<4)|(c>>2)&15)if d>=0 then r[#r+1]=string.char(((c&3)<<6)|d&63)end end end return table.concat(r)end)()"

    def _encode_number(self, m):
        orig = m.group(0)
        if '.' in orig:
            n = float(orig)
            off = round(random.uniform(-10000, 10000), 4)
            return "(" + str(n - off) + "+" + str(off) + ")"
        n = int(orig)
        off = random.randint(1000, 99999)
        return "(" + str(n + off) + "-" + str(off) + ")"

    def _rename_identifiers(self, code):
        keywords = {'and','break','do','else','elseif','end','false','for','function','if','in','local','nil','not','or','repeat','return','then','true','until','while','self','goto','pairs','ipairs','next','type','tostring','tonumber','select','unpack','pcall','xpcall','error','rawget','rawset','loadstring','load','table','string','math','os','io','debug'}
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
            return self._encrypt_string_simple(content)
        def single_quote(m):
            content = m.group(1)
            return self._encrypt_string_simple(content)
        code = re.sub(r'"([^"\\]*(\\.[^"\\]*)*)"', double_quote, code)
        code = re.sub(r"'([^'\\]*(\\.[^'\\]*)*)'", single_quote, code)
        return code

    def _encode_numbers(self, code):
        return re.sub(r'\b\d+\.?\d*\b', self._encode_number, code)

    def _inject_safe_junk(self, code):
        junk = [
            "local " + self._gen_name(8) + "=nil",
            "local " + self._gen_name(8) + "=true or false",
            "local " + self._gen_name(8) + "=function()return end",
        ]
        parts = code.split(';')
        out = []
        for p in parts:
            out.append(p)
            if p.strip() and random.random() < 0.1:
                out.append(random.choice(junk))
        return ';'.join(out)

    def _safe_anti_debug(self):
        c1 = self._gen_name(10)
        c2 = self._gen_name(10)
        return "local " + c1 + "=(os and os.clock or function()return 0 end);local " + c2 + "=" + c1 + "();"

    def obfuscate(self, source):
        if not source or not source.strip():
            raise ValueError("Source file is empty")
        minified = self._minify_lua(source)
        obf = minified
        obf = self._encrypt_strings(obf)
        obf = self._encode_numbers(obf)
        obf = self._rename_identifiers(obf)
        obf = obf.replace('\n', ';').replace('\r', '')
        obf = self._inject_safe_junk(obf)
        ad = self._safe_anti_debug()
        wrapper_name = self._gen_name(10)
        inner_name = self._gen_name(10)
        final = "(function()local " + wrapper_name + "=function()local " + inner_name + "=function()" + ad + obf + "end;return " + inner_name + "()end;return " + wrapper_name + "()end)()"
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
            content="✅ Obfuscation complete! Original: %s KB → Final: %s KB (%s%% smaller). ⚠️ Always test your script in your target environment." % (original_kb, final_kb, reduction),
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
