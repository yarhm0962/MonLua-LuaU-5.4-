import os
import io
import re
import random
import discord
from discord import app_commands
from discord.ext import commands

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
}

PROTECTED = {
    "_G", "_ENV", "self", "arg", "print", "pairs", "ipairs", "next", "type",
    "tostring", "tonumber", "rawget", "rawset", "setmetatable", "getmetatable",
    "pcall", "xpcall", "error", "assert", "select", "unpack", "require",
    "load", "loadstring", "dofile", "collectgarbage", "string", "table",
    "math", "os", "io", "coroutine", "bit32", "debug", "utf8", "jit",
} | KEYWORDS

ALLOWED_EXT = (".lua", ".luau", ".txt")
MAX_SIZE = 512 * 1024


class Tok:
    __slots__ = ("kind", "val")

    def __init__(self, kind, val):
        self.kind = kind
        self.val = val


def lex(src):
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n\f\v":
            i += 1
            continue
        if src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, i + m.end())
                if j == -1:
                    raise ValueError("unterminated long comment")
                i = j + len(close)
                continue
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if c == "[":
            m = re.match(r"\[(=*)\[", src[i:])
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, i + m.end())
                if j == -1:
                    raise ValueError("unterminated long string")
                toks.append(Tok("string", src[i + m.end():j]))
                i = j + len(close)
                continue
        if c in "\"'":
            j, buf = i + 1, []
            while j < n:
                ch = src[j]
                if ch == "\\":
                    if j + 1 >= n:
                        raise ValueError("bad escape")
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if ch == c:
                    j += 1
                    break
                if ch == "\n":
                    raise ValueError("unterminated string")
                buf.append(ch)
                j += 1
            else:
                raise ValueError("unterminated string")
            toks.append(Tok("string", "".join(buf)))
            i = j
            continue
        m = re.match(r"0[xX][0-9a-fA-F.]+|\d+\.?\d*(?:[eE][+-]?\d+)?", src[i:])
        if m:
            toks.append(Tok("number", m.group(0)))
            i += m.end()
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", src[i:])
        if m:
            w = m.group(0)
            toks.append(Tok("kw" if w in KEYWORDS else "ident", w))
            i += m.end()
            continue
        two = src[i:i + 3]
        if two == "...":
            toks.append(Tok("sym", "..."))
            i += 3
            continue
        two = src[i:i + 2]
        if two in ("::", "..", "==", "~=", "<=", ">=", "//", "+=", "-=", "*=", "/=", "%=", "^="):
            toks.append(Tok("sym", two))
            i += 2
            continue
        toks.append(Tok("sym", c))
        i += 1
    return toks


def rnd_name(rng, used):
    glyphs = "Il1j0Oo_"
    while True:
        name = "_" + "".join(
            rng.choice(glyphs) if rng.random() < 0.4 else rng.choice("abcdefghklmnprstuvwxyz")
            for _ in range(rng.randint(6, 14))
        )
        if name not in used and name not in PROTECTED:
            used.add(name)
            return name


def collect_locals(toks):
    decls = set()
    for idx, t in enumerate(toks):
        if t.kind == "kw" and t.val == "local":
            j = idx + 1
            if j < len(toks) and toks[j].kind == "kw" and toks[j].val == "function":
                if j + 1 < len(toks) and toks[j + 1].kind == "ident":
                    decls.add(toks[j + 1].val)
            else:
                while j < len(toks) and toks[j].kind == "ident":
                    decls.add(toks[j].val)
                    j += 1
                    if j < len(toks) and toks[j].val == ",":
                        j += 1
                        continue
                    break
        elif t.kind == "kw" and t.val == "for":
            j = idx + 1
            while j < len(toks) and toks[j].kind == "ident":
                decls.add(toks[j].val)
                j += 1
                if j < len(toks) and toks[j].val == ",":
                    j += 1
                    continue
                break
    return decls - PROTECTED


DECODERS = [
    "local function %s(c,a,b) local x=function(u,v) local r,m=0,1 "
    "while u>0 or v>0 do local p,q=u%%2,v%%2 if p~=q then r=r+m end "
    "u=(u-p)/2 v=(v-q)/2 m=m*2 end return r end local o={} "
    "for i=1,#c do o[i]=string.char(x(x(c[i],a[((i-1)%%#a)+1]),b[((i-1)%%#b)+1])) end "
    "return table.concat(o) end ",

    "local function %s(c,a,b) local x=function(u,v) local r,m=0,1 "
    "repeat local p,q=u%%2,v%%2 if p~=q then r=r+m end u=(u-p)/2 v=(v-q)/2 "
    "m=m*2 until u<=0 and v<=0 return r end local o={} "
    "for i=1,#c do o[i]=string.char(x(x(c[i],a[((i-1)%%#a)+1]),b[((i-1)%%#b)+1])) end "
    "return table.concat(o) end ",
]


def lua_table(vals):
    return "{" + ",".join(str(v) for v in vals) + "}"


def enc_string(s, rng, dec):
    data = list(s.encode("utf-8", "surrogateescape")) or [0]
    k1 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    k2 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    enc = [b ^ k1[i % len(k1)] for i, b in enumerate(data)]
    enc = [b ^ k2[i % len(k2)] for i, b in enumerate(enc)]
    return "%s(%s,%s,%s)" % (dec, lua_table(enc), lua_table(k1), lua_table(k2))


def fake_string(rng, dec):
    data = [rng.randrange(1, 256) for _ in range(rng.randint(8, 40))]
    k1 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    k2 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    return "%s(%s,%s,%s)" % (dec, lua_table(data), lua_table(k1), lua_table(k2))


def obf_num(w, rng):
    try:
        v = int(w, 16) if w.lower().startswith("0x") else int(w)
    except ValueError:
        return w
    if rng.random() < 0.3 or v < 3:
        return w
    a = rng.randrange(1, v + 100)
    b = a - v
    if rng.random() < 0.5:
        return "(%d-%d)" % (a, b)
    return "(%d+(%d))" % (a, -b) if b < 0 else "(%d-(%d))" % (a, b)


JUNK = [
    "do local %s={} if #%%s>%d then return end end ",
    "if (function() return #(\"x\")==0 end)() then while true do end end ",
    "local %s=%d ",
    "for %s=%d,%d do end ",
]


def junk_stmt(rng, names, dec):
    n = rng.choice(names)
    kind = rng.randrange(4)
    if kind == 0:
        return "do local %s={} if #%%s>%d then return end end " % (n, rng.randrange(1, 10**6)) % ""
    if kind == 1:
        return "if (function() return #(\"x\")==0 end)() then while true do end end "
    if kind == 2:
        return "local %s=%s " % (n, fake_string(rng, dec))
    return "for %s=%d,%d do end " % (n, rng.randrange(1, 1000), rng.randrange(1, 1000) - 2000)


def needs_space(a, b):
    if a is None:
        return False
    if a.endswith(("-",)):
        if b.startswith("-"):
            return True
    if a.endswith("."):
        return b.startswith(".") or b[0].isdigit()
    if a[-1].isalnum() or a[-1] in "_'\"":
        return b[0].isalnum() or b[0] in "_'\"[{\""
    if a[-1] in ")]}":
        return b[0].isalnum() or b[0] in "_'\"[{"
    return False


def obfuscate(src, seed=None, junk=12):
    rng = random.Random(seed)
    toks = lex(src)
    used = set(PROTECTED)
    renames = {old: rnd_name(rng, used) for old in sorted(collect_locals(toks))}
    jnames = [rnd_name(rng, used) for _ in range(4)]
    dec = rnd_name(rng, used)
    main = rnd_name(rng, used)

    parts = [rng.choice(DECODERS) % dec]

    for t in toks:
        v = t.val
        if t.kind == "string":
            parts.append(enc_string(v, rng, dec))
        elif t.kind == "number":
            parts.append(obf_num(v, rng))
        elif t.kind == "ident":
            parts.append(renames.get(v, v))
        else:
            parts.append(v)
        if junk > 0 and v in ("end", ";") and rng.random() < 0.3:
            parts.append(" " + junk_stmt(rng, jnames, dec) + " ")

    out, last = [], None
    for p in parts:
        if needs_space(last, p):
            out.append(" ")
        out.append(p)
        last = p

    body = "".join(out)
    return "local function %s(...) %s end return %s(...)" % (main, body, main)


TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is not set")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print("logged in as %s" % bot.user, flush=True)


@bot.tree.command(name="obfuscate", description="Strongly obfuscate a Lua/Luau script")
@app_commands.describe(file="Attach a .lua, .luau or .txt file")
async def obfuscate_cmd(interaction: discord.Interaction, file: discord.Attachment):
    fname = file.filename or ""
    low = fname.lower()
    if not low.endswith(ALLOWED_EXT):
        await interaction.response.send_message(
            "Rejected: only `.lua`, `.luau` and `.txt` files are accepted.", ephemeral=True)
        return
    if file.size > MAX_SIZE:
        await interaction.response.send_message(
            "Rejected: file is too large (max 512 KB).", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        raw = await file.read()
        src = raw.decode("utf-8", "surrogateescape")
        out = obfuscate(src)
    except Exception as e:
        await interaction.followup.send("Obfuscation failed: `%s`" % e)
        return
    stem = fname[: fname.rfind(".")]
    ext = fname[fname.rfind("."):]
    buf = io.BytesIO(out.encode("utf-8", "surrogateescape"))
    dfile = discord.File(fp=buf, filename="%s_obfuscated%s" % (stem, ext))
    await interaction.followup.send(
        "Done. `%s` -> `%s_obfuscated%s` (%d -> %d bytes)"
        % (fname, stem, ext, len(src), len(out)), file=dfile)


bot.run(TOKEN)
