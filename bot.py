import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import re
import hashlib
import io
import random
import secrets
import json
import urllib.request
import urllib.error

import discord
from discord import app_commands
from discord.ext import commands

TOKEN=os.getenv("DISCORD_TOKEN")
LUA_PROCESS_MAX_BYTES=2*1024*1024
OBFUSCATE_EXTENSIONS={".lua",".txt"}
OBF_MAX_OUTPUT_BYTES=5*1024*1024
OBF_ENGINE_VERSION="v6.2 hardened static"
try:
    OBF_OPT_LEVEL=max(1,min(3,int(os.getenv("OBF_OPT_LEVEL","3"))))
except ValueError:
    OBF_OPT_LEVEL=3
OBF_BUILD_ID=secrets.token_hex(6)
OBF_RNG=random.SystemRandom()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing")

intents=discord.Intents.default()
intents.guilds=True

bot=commands.Bot(command_prefix=commands.when_mentioned_or(),intents=intents,help_command=None)

def make_container(*items,accent_color=None):
    container=discord.ui.Container(*items)
    if accent_color is not None:
        container.accent_color=accent_color
    return container

def make_text(content):
    return discord.ui.TextDisplay(content)

def make_separator():
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.small,visible=True)

def lua_long_bracket_end(source,start):
    if start>=len(source) or source[start]!="[":
        return None
    index=start+1
    while index<len(source) and source[index]=="=":
        index+=1
    if index>=len(source) or source[index]!="[":
        return None
    close="]"+"="*(index-start-1)+"]"
    end=source.find(close,index+1)
    if end<0:
        return None
    return end+len(close),source[start:end+len(close)]

def lex_lua_source(source):
    tokens=[]
    operators=("//=","...","::","//","<<",">>","==","~=","<=",">=","..","+=","-=","*=","/=","%=","^=","&=","|=")
    i=0
    n=len(source)
    while i<n:
        ch=source[i]
        if ch.isspace():
            i+=1
            continue
        if i==0 and source.startswith("#!",i):
            end=source.find("\n",i)
            if end<0:
                end=n
            tokens.append(("directive",source[i:end]))
            i=end
            continue
        if source.startswith("--",i):
            if source.startswith("--!",i):
                end=source.find("\n",i)
                if end<0:
                    end=n
                tokens.append(("directive",source[i:end]))
                i=end
                continue
            long_result=lua_long_bracket_end(source,i+2)
            if long_result is not None and source[i+2:i+3]=="[":
                i=long_result[0]
                continue
            end=source.find("\n",i)
            i=n if end<0 else end
            continue
        if ch in {"'","\""}:
            quote=ch
            j=i+1
            while j<n:
                if source[j]=="\\":
                    j+=2
                    continue
                if source[j]==quote:
                    j+=1
                    break
                j+=1
            if j>n or j==i+1 or source[j-1]!=quote:
                raise ValueError("Unterminated Lua string literal.")
            tokens.append(("string",source[i:j]))
            i=j
            continue
        long_result=lua_long_bracket_end(source,i)
        if long_result is not None:
            end,value=long_result
            tokens.append(("string",value))
            i=end
            continue
        if ch.isalpha() or ch=="_":
            j=i+1
            while j<n and (source[j].isalnum() or source[j]=="_"):
                j+=1
            tokens.append(("ident",source[i:j]))
            i=j
            continue
        if ch.isdigit() or (ch=="." and i+1<n and source[i+1].isdigit()):
            match=re.match(r"(?:0[xX][0-9A-Fa-f]+(?:\.[0-9A-Fa-f]*)?(?:[pP][+-]?\d+)?|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)",source[i:])
            if match:
                value=match.group(0)
                tokens.append(("number",value))
                i+=len(value)
                continue
        matched=None
        for operator in operators:
            if source.startswith(operator,i):
                matched=operator
                break
        if matched is not None:
            tokens.append(("op",matched))
            i+=len(matched)
            continue
        tokens.append(("op",ch))
        i+=1
    return tokens

def decode_lua_string_literal(value):
    if len(value)<2 or value[0] not in {'"',"'"} or value[-1]!=value[0]:
        return None
    body=value[1:-1]
    out=bytearray()
    i=0
    simple={"a":7,"b":8,"f":12,"n":10,"r":13,"t":9,"v":11,"\\":92,'"':34,"'":39}
    while i<len(body):
        ch=body[i]
        if ch!="\\":
            out.extend(ch.encode("utf-8"))
            i+=1
            continue
        i+=1
        if i>=len(body):
            return None
        esc=body[i]
        if esc in simple:
            out.append(simple[esc])
            i+=1
            continue
        if esc in {"x","X"} and i+2<len(body):
            piece=body[i+1:i+3]
            if re.fullmatch(r"[0-9A-Fa-f]{2}",piece):
                out.append(int(piece,16))
                i+=3
                continue
        if esc.isdigit():
            j=i
            while j<len(body) and j<i+3 and body[j].isdigit():
                j+=1
            number=int(body[i:j])
            if number>255:
                return None
            out.append(number)
            i=j
            continue
        if esc=="z":
            i+=1
            while i<len(body) and body[i].isspace():
                i+=1
            continue
        if esc=="\n":
            i+=1
            continue
        out.extend(esc.encode("utf-8"))
        i+=1
    return bytes(out)


def decode_lua_long_literal(value):
    if not value.startswith("["):
        return None
    index=1
    while index<len(value) and value[index]=="=":
        index+=1
    if index>=len(value) or value[index]!="[":
        return None
    closing="]"+"="*(index-1)+"]"
    if not value.endswith(closing):
        return None
    body=value[index+1:-len(closing)]
    if body.startswith("\n"):
        body=body[1:]
    return body.encode("utf-8")

def decode_lua_literal_bytes(value):
    if value.startswith(("'","\"")):
        return decode_lua_string_literal(value)
    return decode_lua_long_literal(value)

def lua_identifier_name(used,prefix="_x"):
    while True:
        name=f"{prefix}{secrets.token_hex(7)}"
        if name not in used:
            used.add(name)
            return name

def lua_render_tokens(tokens):
    parts=[]
    for kind,value in tokens:
        if kind=="directive":
            continue
        parts.append(value)
    return " ".join(parts).strip()+"\n"

def transform_lua_numbers(tokens):
    output=[]
    for kind,value in tokens:
        if kind!="number" or not re.fullmatch(r"\d+",value):
            output.append((kind,value))
            continue
        number=int(value)
        if OBF_OPT_LEVEL<=1 or number>1000000:
            output.append((kind,value))
            continue
        if number==0:
            seed=OBF_RNG.randint(17,97)
            expression=f"(({seed}-{seed}))"
        elif number==1:
            seed=OBF_RNG.randint(17,97)
            expression=f"(({seed}/{seed}))"
        elif number==2:
            seed=OBF_RNG.randint(17,97)
            expression=f"(({seed}+{seed})/{seed})"
        else:
            left=OBF_RNG.randint(3,97)
            base=number//left
            remainder=number-(base*left)
            if base==0:
                divisor=OBF_RNG.randint(3,19)
                expression=f"(({number}*{divisor})/{divisor})"
            elif OBF_OPT_LEVEL>=3:
                gate=OBF_RNG.randint(3,31)
                salt=OBF_RNG.randint(5,53)
                expression=f"(((((({base}*{left})+{remainder}+{salt})*{gate})-{salt}*{gate})-{number}*{gate})+{number})"
            else:
                expression=f"(({base}*{left})+{remainder})"
        output.extend(lex_lua_source(expression))
    return output

def format_lua_numeric_array(values,width=18,indent="    "):
    if not values:
        return "{}"
    rows=[]
    for start in range(0,len(values),width):
        chunk=values[start:start+width]
        rows.append(indent+",".join(str(number) for number in chunk)+",")
    return "{\n"+"\n".join(rows)+"\n}"

def mod_inverse_256(value):
    value%=256
    for candidate in range(1,256,2):
        if (value*candidate)%256==1:
            return candidate
    raise ValueError("Could not generate a valid string encoder.")

def build_lua_string_pool(string_values,used):
    if not string_values:
        return {},""
    char_alias=lua_identifier_name(used,"_c")
    concat_alias=lua_identifier_name(used,"_t")
    decode_a=lua_identifier_name(used,"_a")
    decode_b=lua_identifier_name(used,"_b")
    pool=lua_identifier_name(used,"_p")
    lines=[
        f"local {char_alias}=string.char",
        f"local {concat_alias}=table.concat",
        f"local {decode_a}=function(a,k,m,i,s)local b={{}} for j=1,#a do b[j]={char_alias}((((a[j]-k-((j*s)%251))*i)%256)) end return {concat_alias}(b) end",
        f"local {decode_b}=function(a,k,s)local b={{}} for j=1,#a do b[j]={char_alias}((a[j]-k-((j*s)%251)-((j*j)%251))%256) end return {concat_alias}(b) end",
        f"local {pool}={{}}",
    ]
    replacements={}
    for index,value in enumerate(string_values,1):
        raw=decode_lua_literal_bytes(value)
        if raw is None:
            replacements[value]=value
            continue
        if not raw:
            replacements[value]='\"\"'
            continue
        parts=[]
        cursor=0
        target_chunks=2 if len(raw)>6 else 1
        if len(raw)>48:
            target_chunks=3
        cut_positions=[]
        remaining=len(raw)
        for part_index in range(target_chunks-1):
            minimum=1
            maximum=remaining-(target_chunks-part_index-1)
            chunk_len=OBF_RNG.randint(minimum,maximum)
            cut_positions.append(chunk_len)
            remaining-=chunk_len
        chunk_lengths=cut_positions+[remaining]
        for chunk_len in chunk_lengths:
            chunk=raw[cursor:cursor+chunk_len]
            cursor+=chunk_len
            key=OBF_RNG.randint(17,239)
            step=OBF_RNG.randint(5,23)
            if OBF_RNG.choice((True,False)):
                multiplier=OBF_RNG.choice(tuple(range(3,256,2)))
                inverse=mod_inverse_256(multiplier)
                encoded=[((byte*multiplier)+key+(((position+1)*step)%251))%256 for position,byte in enumerate(chunk)]
                parts.append(f"{decode_a}({format_lua_numeric_array(encoded)},{key},{multiplier},{inverse},{step})")
            else:
                encoded=[(byte+key+(((position+1)*step)%251)+(((position+1)*(position+1))%251))%256 for position,byte in enumerate(chunk)]
                parts.append(f"{decode_b}({format_lua_numeric_array(encoded)},{key},{step})")
        lines.append(f"{pool}[{index}]={'+'.join(parts)}")
        replacements[value]=f"{pool}[{index}]"
    return replacements,"\n".join(lines)+"\n"

def validate_luau_source(source):
    if re.search(r"<\s*close\s*>",source):
        raise ValueError('`<close>` declarations are not supported by this compiler profile.')

def strip_lua_comments(source):
    output=[]
    i=0
    n=len(source)
    quote=None
    while i<n:
        if quote is not None:
            ch=source[i]
            output.append(ch)
            if ch=="\\" and i+1<n:
                output.append(source[i+1])
                i+=2
                continue
            if ch==quote:
                quote=None
            i+=1
            continue
        if source.startswith("#!",i):
            end=source.find("\n",i)
            if end<0:
                break
            i=end
            continue
        if source.startswith("--",i):
            long_result=lua_long_bracket_end(source,i+2)
            if long_result is not None and source[i+2:i+3]=="[":
                end=long_result[0]
                block=source[i:end]
                output.extend("\n" for _ in range(block.count("\n")))
                i=end
                continue
            end=source.find("\n",i)
            if end<0:
                break
            i=end
            continue
        if source[i] in {"'",'"'}:
            quote=source[i]
            output.append(source[i])
            i+=1
            continue
        long_result=lua_long_bracket_end(source,i)
        if long_result is not None:
            end,value=long_result
            output.append(value)
            i=end
            continue
        output.append(source[i])
        i+=1
    return "".join(output)

def clean_lua_output(source):
    body=strip_lua_comments(source)
    lines=[]
    blank=False
    for raw_line in body.splitlines():
        line=raw_line.rstrip()
        if not line.strip():
            if lines and not blank:
                lines.append("")
            blank=True
            continue
        if line.startswith("#!"):
            continue
        if line.lstrip().startswith("--"):
            continue
        lines.append(line)
        blank=False
    cleaned="\n".join(lines).strip()
    return "-- [[ Forguar Protected v1 ]]\n\n"+cleaned+"\n"

def build_lua_output_banner(profile="MAIN"):
    return "-- [[ Forguar Protected v1 ]]\n\n"

def obfuscate_lua_source(source):
    if not source.strip():
        raise ValueError("The Lua source is empty.")
    validate_luau_source(source)
    tokens=lex_lua_source(source.lstrip("\ufeff"))
    tokens=[token for token in tokens if token[0]!="directive"]
    used={value for kind,value in tokens if kind=="ident"}
    string_values=[]
    seen=set()
    for kind,value in tokens:
        if kind=="string" and value not in seen:
            seen.add(value)
            string_values.append(value)
    replacements,pool_block=build_lua_string_pool(string_values,used)
    transformed=[]
    for kind,value in tokens:
        if kind=="string" and value in replacements:
            transformed.extend(lex_lua_source(replacements[value]))
        else:
            transformed.append((kind,value))
    transformed=transform_lua_numbers(transformed)
    body=lua_render_tokens(transformed)
    if not body.strip():
        raise ValueError("The Lua source did not contain executable code.")
    output=build_lua_output_banner()+pool_block+body
    output=clean_lua_output(output)
    if len(output.encode("utf-8"))>OBF_MAX_OUTPUT_BYTES:
        raise ValueError("The obfuscated result is larger than the 5 MB output limit.")
    features=[
        ("Engine",f"Built-in {OBF_ENGINE_VERSION} portable profile"),
        ("String protection","multi-stage byte encoding with shuffled per-string keys" if string_values else "no string literals detected"),
        ("String sharding","long strings are split into independently encoded runtime chunks" if string_values else "not required"),
        ("Numeric hardening",f"randomized arithmetic transforms at level {OBF_OPT_LEVEL}"),
        ("Luau compatibility","static runtime with no load/loadstring dependency"),
        ("Runtime compatibility","no executor fingerprinting or environment-specific hooks"),
        ("Parser","lexical transformation with normalized safe spacing"),
        ("Build rotation","fresh identifiers, keys, multipliers, and chunk layouts per build"),
        ("Output layout","single Forguar marker with comment-free generated code"),
    ]
    return output,features

def create_pastefy_paste(title,content):
    token=os.getenv("PASTEFY_API_TOKEN")
    if not token:
        raise RuntimeError("PASTEFY_API_TOKEN environment variable is missing")
    endpoint="https://pastefy.app/api/v2/paste"
    headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"Forguar-Obfuscator/1.0",
    }

    def send(payload):
        request=urllib.request.Request(endpoint,data=json.dumps(payload).encode("utf-8"),method="POST",headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=25) as response:
                body=response.read().decode("utf-8",errors="replace")
                status=response.status
        except urllib.error.HTTPError as error:
            detail=error.read().decode("utf-8",errors="replace")[:1000]
            return None,error.code,detail
        except urllib.error.URLError as error:
            raise RuntimeError(f"Pastefy connection failed: {error.reason}")
        return body,status,None

    payload={
        "title":title[:255],
        "content":content,
        "visibility":"UNLISTED",
    }
    body,status,error=send(payload)
    if body is None and status in {400,422,500}:
        fallback={
            "title":title[:255],
            "content":content,
            "visibility":"UNLISTED",
        }
        body,status,error=send(fallback)
    if body is None:
        raise RuntimeError(f"Pastefy returned HTTP {status}: {error}")
    try:
        data=json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("Pastefy returned an invalid response")
    if data.get("success") is False:
        detail=data.get("exception") or data.get("message") or data.get("error") or "Paste creation failed"
        raise RuntimeError(f"Pastefy rejected the paste: {detail}")
    paste=data.get("paste",data)
    if not isinstance(paste,dict):
        raise RuntimeError("Pastefy returned an invalid paste object")
    raw_url=paste.get("raw_url") or paste.get("rawUrl")
    paste_id=paste.get("id")
    web_url=f"https://pastefy.app/{paste_id}" if paste_id else None
    if raw_url and raw_url.startswith("/"):
        raw_url="https://pastefy.app"+raw_url
    if not raw_url and not web_url:
        raise RuntimeError("Pastefy did not return a paste URL")
    return web_url or raw_url,raw_url or web_url


class ObfuscationResultView(discord.ui.LayoutView):
    def __init__(self,filename,result,features,paste_url=None,raw_url=None,paste_error=None):
        super().__init__(timeout=None)
        safe_filename=discord.utils.escape_markdown(filename)
        output_name=discord.utils.escape_markdown(os.path.splitext(filename)[0]+".obfuscated.lua")
        digest=hashlib.sha256(result.encode("utf-8")).hexdigest()
        output_size=len(result.encode("utf-8"))
        feature_map=dict(features)
        paste_section=(
            f"**Pastefy**\n<{paste_url}>\n"
            + (f"**Raw**\n<{raw_url}>" if raw_url and raw_url != paste_url else "")
            if paste_url else
            f"**Pastefy**\n`Unavailable` · `{discord.utils.escape_markdown(paste_error or 'Upload failed')}`"
        )
        protection_text="\n".join((
            f"`ON` String protection · {discord.utils.escape_markdown(feature_map.get('String protection','enabled'))}",
            f"`ON` Numeric folding · {discord.utils.escape_markdown(feature_map.get('Numeric folding','enabled'))}",
            f"`ON` Luau compatibility · {discord.utils.escape_markdown(feature_map.get('Luau compatibility','static output'))}",
            f"`ON` Parser safety · {discord.utils.escape_markdown(feature_map.get('Parser','lexical transformation'))}",
        ))
        self.add_item(
            make_container(
                make_text("## ✦ Forguar Protected v1"),
                make_text(f"`{safe_filename}`\nYour protected Lua output is ready to download or open on Pastefy."),
                make_separator(),
                make_text(
                    "### Build\n"
                    f"`{OBF_ENGINE_VERSION}`  ·  `L{OBF_OPT_LEVEL}`  ·  `{OBF_BUILD_ID}`\n"
                    f"**Size** `{output_size:,} bytes`  ·  **SHA-256** `{digest[:16]}…`"
                ),
                make_separator(),
                make_text("### Protection\n"+protection_text),
                make_separator(),
                make_text("### Results\n"+paste_section),
                make_separator(),
                make_text(
                    "### Download\n"
                    f"`{output_name}`\n"
                    "The protected `.lua` file is attached to this response."
                ),
                make_separator(),
                make_text("-# Output contains only the Forguar Protected v1 marker comment."),
                accent_color=0x7C3AED,
            )
        )
        if paste_url:
            row=discord.ui.ActionRow(
                discord.ui.Button(label="Open Pastefy",style=discord.ButtonStyle.link,url=paste_url),
            )
            if raw_url and raw_url != paste_url:
                row.add_item(discord.ui.Button(label="Open Raw",style=discord.ButtonStyle.link,url=raw_url))
            self.add_item(row)


async def process_obf_command(interaction,filename,data):
    await interaction.response.defer(thinking=True)
    try:
        source=data.decode("utf-8-sig")
    except UnicodeDecodeError:
        await interaction.edit_original_response(content="❌ **Obfuscation failed**\nThe file must be valid UTF-8 Lua/TXT source.",view=None)
        return
    try:
        result,features=await asyncio.to_thread(obfuscate_lua_source,source)
    except Exception as error:
        message=str(error)[:1500]
        await interaction.edit_original_response(
            content=f"❌ **Obfuscation failed**\n`{discord.utils.escape_markdown(message)}`",
            view=None,
        )
        return
    paste_url=None
    raw_url=None
    paste_error=None
    try:
        paste_title=os.path.splitext(filename)[0]+".obfuscated.lua"
        paste_url,raw_url=await asyncio.to_thread(create_pastefy_paste,paste_title,result)
    except Exception as error:
        paste_error=str(error)[:500]
    view=ObfuscationResultView(filename,result,features,paste_url,raw_url,paste_error)
    await interaction.edit_original_response(content=None,view=view)
    output_filename=os.path.splitext(filename)[0]+".obfuscated.lua"
    try:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(result.encode("utf-8")),filename=output_filename),
            ephemeral=True,
        )
    except discord.HTTPException:
        try:
            await interaction.followup.send(
                content="⚠️ **Protected file created, but Discord could not attach the download. Run `/obfuscate` again.",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


@bot.tree.command(name="obfuscate",description="Obfuscate a Lua or TXT source file.")
@app_commands.describe(file="Required .lua or .txt source file")
async def obfuscate_command(interaction:discord.Interaction,file:discord.Attachment):
    filename=os.path.basename(file.filename or "input.lua")
    extension=os.path.splitext(filename)[1].lower()
    if extension not in OBFUSCATE_EXTENSIONS:
        await interaction.response.send_message("❌ Only `.lua` and `.txt` files are supported.",ephemeral=True)
        return
    if file.size is not None and file.size>LUA_PROCESS_MAX_BYTES:
        await interaction.response.send_message("❌ The maximum file size is 2 MB.",ephemeral=True)
        return
    try:
        data=await file.read()
    except discord.HTTPException:
        await interaction.response.send_message("❌ Could not read the uploaded file.",ephemeral=True)
        return
    if not data:
        await interaction.response.send_message("❌ The uploaded file is empty.",ephemeral=True)
        return
    if len(data)>LUA_PROCESS_MAX_BYTES:
        await interaction.response.send_message("❌ The maximum file size is 2 MB.",ephemeral=True)
        return
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        await interaction.response.send_message("❌ The uploaded file must be valid UTF-8 Lua/TXT text.",ephemeral=True)
        return
    await process_obf_command(interaction,filename,data)


class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Forguar Obfuscator is online")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self,format,*args):
        return


def start_render_health_server():
    port=int(os.getenv("PORT","10000"))
    server=ThreadingHTTPServer(("0.0.0.0",port),RenderHealthHandler)
    server.daemon_threads=True
    server.serve_forever()


@bot.event
async def on_ready():
    synced=await bot.tree.sync()
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Synced {len(synced)} command(s)")


async def start_bot():
    while True:
        try:
            await bot.start(TOKEN)
            break
        except discord.LoginFailure:
            print("Invalid Discord bot token.")
            break
        except discord.HTTPException as error:
            retry_after=getattr(error,"retry_after",30)
            print(f"Discord connection error: {error}")
            print(f"Retrying in {retry_after:.1f} seconds...")
            await asyncio.sleep(retry_after)
        except Exception as error:
            print(f"Bot error: {error}")
            await asyncio.sleep(30)


health_thread=threading.Thread(target=start_render_health_server,daemon=True)
health_thread.start()
asyncio.run(start_bot())
