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
OBF_ENGINE_VERSION="v6.1 main"
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
    pieces=[]
    previous=None
    word_kinds={"ident","number"}
    for kind,value in tokens:
        if kind=="directive":
            if pieces:
                pieces.append("\n")
            pieces.append(value)
            pieces.append("\n")
            previous=None
            continue
        if previous is not None:
            prev_kind,prev_value=previous
            need_space=False
            if prev_kind in word_kinds and kind in word_kinds:
                need_space=True
            if prev_value in {"+","-"} and value in {"+","-"}:
                need_space=True
            if prev_value=="/" and value=="/":
                need_space=True
            if prev_value=="." and kind=="number":
                need_space=True
            if prev_kind=="number" and value.startswith("."):
                need_space=True
            if need_space:
                pieces.append(" ")
        pieces.append(value)
        previous=(kind,value)
    return "".join(pieces).strip()+"\n"

def transform_lua_numbers(tokens):
    output=[]
    for kind,value in tokens:
        if kind!="number" or not re.fullmatch(r"\d+",value):
            output.append((kind,value))
            continue
        number=int(value)
        if number in {0,1,2} or number>1000000:
            output.append((kind,value))
            continue
        if OBF_OPT_LEVEL<=1:
            output.append((kind,value))
            continue
        left=OBF_RNG.randint(3,97)
        right=OBF_RNG.randint(2,41)
        base=number//left
        remainder=number-(base*left)
        if base==0:
            divisor=OBF_RNG.randint(2,11)
            left_value=number*divisor
            expression=f"({left_value}/{divisor})"
        elif OBF_OPT_LEVEL>=3:
            gate=OBF_RNG.randint(3,31)
            expression=f"(((((({base}*{left})+{remainder})*{gate})-{number}*{gate})+{number}))"
        else:
            expression=f"(({base}*{left})+{remainder})"
        subtokens=lex_lua_source(expression)
        output.extend(subtokens)
    return output

def format_lua_numeric_array(values,width=18,indent="    "):
    if not values:
        return "{}"
    rows=[]
    for start in range(0,len(values),width):
        chunk=values[start:start+width]
        rows.append(indent+",".join(str(number) for number in chunk)+",")
    return "{\n"+"\n".join(rows)+"\n}"

def build_lua_string_pool(string_values,used):
    if not string_values:
        return {},""
    decoder=lua_identifier_name(used,"_d")
    pool=lua_identifier_name(used,"_p")
    step=OBF_RNG.randint(5,23)
    lines=[
        f"local {decoder}=function(a,k)local b={{}} for i=1,#a do b[i]=string.char((a[i]-k-((i*{step})%251))%256) end return table.concat(b) end",
        f"local {pool}={{}}",
    ]
    replacements={}
    for index,value in enumerate(string_values,1):
        raw=decode_lua_literal_bytes(value)
        if raw is None:
            replacements[value]=value
            continue
        key=OBF_RNG.randint(17,239)
        encoded=[(byte+key+((position+1)*step)%251)%256 for position,byte in enumerate(raw)]
        if not encoded:
            encoded=[0]
        encoded_block=format_lua_numeric_array(encoded)
        lines.append(f"{pool}[{index}]={decoder}({encoded_block},{key})")
        replacements[value]=f"{pool}[{index}]"
    return replacements,"\n".join(lines)+"\n"

def build_lua_anti_tamper(used):
    rawget_name=lua_identifier_name(used,"_r")
    type_name=lua_identifier_name(used,"_t")
    pcall_name=lua_identifier_name(used,"_c")
    error_name=lua_identifier_name(used,"_e")
    tonumber_name=lua_identifier_name(used,"_u")
    tostring_name=lua_identifier_name(used,"_s")
    concat_name=lua_identifier_name(used,"_tc")
    byte_name=lua_identifier_name(used,"_tb")
    math_name=lua_identifier_name(used,"_m")
    check_name=lua_identifier_name(used,"_q")
    safe_name=lua_identifier_name(used,"_ok")
    reason_name=lua_identifier_name(used,"_why")
    fingerprint_name=lua_identifier_name(used,"_fp")
    anchor_name=lua_identifier_name(used,"_an")
    build_tag=OBF_BUILD_ID+secrets.token_hex(6)
    sentinel_value=secrets.token_hex(28)
    checksum=sum((index+1)*byte for index,byte in enumerate(sentinel_value.encode("utf-8")))%1000003
    anchor_a=OBF_RNG.randint(10007,90011)
    anchor_b=OBF_RNG.randint(101,997)
    anchor_c=OBF_RNG.randint(17,83)
    anchor_value=((anchor_a*anchor_b)+anchor_c)%1000003
    lines=[
        f'local {rawget_name}=rawget',
        f'local {type_name}=type',
        f'local {pcall_name}=pcall',
        f'local {error_name}=error',
        f'local {tonumber_name}=tonumber',
        f'local {tostring_name}=tostring',
        f'local {concat_name}=table.concat',
        f'local {byte_name}=string.byte',
        f'local {math_name}={rawget_name}(_G,"math")',
        f'local {safe_name}=true',
        f'local {reason_name}=""',
        f'local {fingerprint_name}="{sentinel_value}"',
        f'local _build="{build_tag}"',
        f'local {check_name}=function()',
        f'if {rawget_name}(_G,"rawget")~={rawget_name} then {safe_name}=false {reason_name}="global rawget changed" return false end',
        f'if {rawget_name}(_G,"type")~={type_name} then {safe_name}=false {reason_name}="global type changed" return false end',
        f'if {rawget_name}(_G,"pcall")~={pcall_name} then {safe_name}=false {reason_name}="global pcall changed" return false end',
        f'if {rawget_name}(_G,"error")~={error_name} then {safe_name}=false {reason_name}="global error changed" return false end',
        f'if {rawget_name}(_G,"tonumber")~={tonumber_name} then {safe_name}=false {reason_name}="global tonumber changed" return false end',
        f'local _sum=0',
        f'for _i=1,#{fingerprint_name} do _sum=(_sum+(_i*{byte_name}({fingerprint_name},_i)))%1000003 end',
        f'if _sum~={checksum} then {safe_name}=false {reason_name}="embedded fingerprint changed" return false end',
        f'if {type_name}({concat_name})~="function" or {type_name}({byte_name})~="function" then {safe_name}=false {reason_name}="string primitives unavailable" return false end',
        f'if {math_name}==nil or {type_name}({math_name})~="table" then {safe_name}=false {reason_name}="math library unavailable" return false end',
        f'local _floor={rawget_name}({math_name},"floor")',
        f'if {type_name}(_floor)~="function" then {safe_name}=false {reason_name}="math floor unavailable" return false end',
        f'local _anchor=(({anchor_a}*{anchor_b})+{anchor_c})%1000003',
        f'if _floor(_anchor)~={anchor_value} then {safe_name}=false {reason_name}="runtime anchor mismatch" return false end',
        f'local _ok,_mt={pcall_name}(getmetatable,_G)',
        f'if not _ok and _mt==nil then {safe_name}=false {reason_name}="environment probe failed" return false end',
        f'local _probe={tostring_name}(_build)',
        f'if {type_name}(_probe)~="string" or #_build==0 then {safe_name}=false {reason_name}="build fingerprint invalid" return false end',
        f'return {safe_name}',
        'end',
        f'if not {check_name}() then {error_name}("Protected build rejected: "..{reason_name},0) end',
        f'local {anchor_name}={anchor_value}',
    ]
    return "\n".join(lines)+"\n"

def build_lua_vm(body,used):
    rawget_name=lua_identifier_name(used,"_rg")
    load_name=lua_identifier_name(used,"_ld")
    type_name=lua_identifier_name(used,"_ty")
    error_name=lua_identifier_name(used,"_er")
    byte_name=lua_identifier_name(used,"_by")
    payload_name=lua_identifier_name(used,"_pl")
    decode_name=lua_identifier_name(used,"_dc")
    vm_name=lua_identifier_name(used,"_vm")
    stack_name=lua_identifier_name(used,"_st")
    pc_name=lua_identifier_name(used,"_pc")
    instr_name=lua_identifier_name(used,"_op")
    key_name=lua_identifier_name(used,"_ky")
    loader_name=lua_identifier_name(used,"_fn")
    result_name=lua_identifier_name(used,"_rs")
    register_name=lua_identifier_name(used,"_rgx")
    checksum_name=lua_identifier_name(used,"_ck")
    build_key=OBF_RNG.randint(19,231)
    stride=OBF_RNG.randint(5,29)
    opcode_values=[]
    while len(opcode_values)<6:
        value=OBF_RNG.randint(31,251)
        if value not in opcode_values:
            opcode_values.append(value)
    op_nop,op_decode,op_mix,op_load,op_call,op_halt=opcode_values
    raw=body.encode("utf-8")
    encoded=[(byte+build_key+((index+1)*stride)%251)%256 for index,byte in enumerate(raw)]
    payload_checksum=sum((index+1)*byte for index,byte in enumerate(raw))%1000003
    salt=OBF_RNG.randint(7,97)
    lines=[
        f'local {rawget_name}=rawget',
        f'local {load_name}={rawget_name}(_G,"loadstring") or {rawget_name}(_G,"load")',
        f'local {type_name}=type',
        f'local {error_name}=error',
        f'local {byte_name}=string.byte',
        f'local {payload_name}='+format_lua_numeric_array(encoded),
        f'local {key_name}={build_key}',
        f'local {checksum_name}={payload_checksum}',
        f'local {decode_name}=function(a,k)local b={{}} for i=1,#a do b[i]=string.char((a[i]-k-((i*{stride})%251))%256) end return table.concat(b) end',
        f'if {type_name}({load_name})~="function" then {error_name}("Protected loader unavailable",0) end',
        f'local {loader_name}={load_name}',
        f'local {vm_name}=function()',
        f'local {stack_name}={{}}',
        f'local {register_name}=0',
        f'local {pc_name}=1',
        f'local {result_name}=nil',
        f'local _buf=nil',
        f'local _ops={{{op_nop},{op_decode},{op_mix},{op_load},{op_call},{op_halt}}}',
        'while true do',
        f'local {instr_name}=_ops[{pc_name}]',
        f'if {instr_name}=={op_nop} then {register_name}=({register_name}+{salt})%65521 {pc_name}=2',
        f'elseif {instr_name}=={op_decode} then {stack_name}[1]={decode_name}({payload_name},{key_name}) {pc_name}=3',
        f'elseif {instr_name}=={op_mix} then {register_name}=({register_name}+#{stack_name}[1]+{salt})%65521 {pc_name}=4',
        f'elseif {instr_name}=={op_load} then local _sum=0 for _i=1,#{stack_name}[1] do _sum=(_sum+(_i*{byte_name}({stack_name}[1],_i)))%1000003 end if _sum~={checksum_name} then {error_name}("Protected payload integrity failure",0) end {stack_name}[2]={loader_name}({stack_name}[1]) {pc_name}=5',
        f'elseif {instr_name}=={op_call} then if {type_name}({stack_name}[2])~="function" then {error_name}("Protected chunk rejected",0) end {result_name}={stack_name}[2](...) {pc_name}=6',
        f'elseif {instr_name}=={op_halt} then _buf={register_name} return {result_name}',
        f'else {error_name}("VM integrity failure",0) end',
        'end',
        'end',
        f'return {vm_name}()',
    ]
    return "\n".join(lines)+"\n",{
        "VM execution":f"randomized 6-op dispatcher ({op_nop},{op_decode},{op_mix},{op_load},{op_call},{op_halt})",
        "Runtime payload":f"byte-packed with build key {build_key}",
        "Flow protection":f"state-linked dispatcher stride {stride}",
        "Payload integrity":f"runtime weighted checksum {payload_checksum}",
    }

def build_lua_decoys(used):
    decoy_name=lua_identifier_name(used,"_dc")
    alt_name=lua_identifier_name(used,"_da")
    state=random.SystemRandom().randint(1000,9000)
    token=random.SystemRandom().randint(10000,99999)
    lines=[
        f'local {decoy_name}={token}',
        f'local {alt_name}=function(a)return ((a*17)%{state+31})=={state+7} end',
        f'if {alt_name}(0) then local _={decoy_name} end',
    ]
    return "\n".join(lines)+"\n"

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

def build_lua_section(title,body):
    return body.rstrip()+"\n\n"

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
    anti=build_lua_anti_tamper(used)
    decoys=build_lua_decoys(used)
    vm,vm_features=build_lua_vm(pool_block+body,used)
    junk=[]
    for _ in range(4 if OBF_OPT_LEVEL<3 else 7):
        a=OBF_RNG.randint(41,997)
        b=OBF_RNG.randint(17,89)
        c=OBF_RNG.randint(7,61)
        modulus=OBF_RNG.randint(257,65521)
        junk.append(f"local {lua_identifier_name(used,'_j')}=(({a}*{b})+{c})%{modulus}")
    junk_block="\n".join(junk)+"\n"
    output=(
        build_lua_output_banner()
        +build_lua_section("Runtime Integrity",anti)
        +build_lua_section("Compatibility Layer",decoys)
        +build_lua_section("Build Material",junk_block)
        +build_lua_section("Protected Runtime",vm)
    )
    output=clean_lua_output(output)
    if len(output.encode("utf-8"))>OBF_MAX_OUTPUT_BYTES:
        raise ValueError("The obfuscated result is larger than the 5 MB output limit.")
    features=[
        ("Engine",f"Built-in {OBF_ENGINE_VERSION} hardened profile"),
        ("Constant encryption","runtime byte-packed string pool with per-build keys"),
        ("Numeric folding","level-aware arithmetic transforms"),
        ("Control-flow protection",vm_features["Flow protection"]),
        ("VM execution",vm_features["VM execution"]),
        ("Runtime payload",vm_features["Runtime payload"]),
        ("Payload integrity",vm_features["Payload integrity"]),
        ("Anti-tamper","runtime primitive, fingerprint, environment, and integrity checks"),
        ("Typed Luau support","token-preserving parser path for annotations and nested closures"),
        ("Non-virtualized support","optimized token transforms with closure-safe rendering"),
        ("Optimization",f"level {OBF_OPT_LEVEL} folding with compact rendering"),
        ("Output layout","single Forguar marker with comment-free generated code"),
        ("Build rotation","fresh keys, opcodes, anchors, identifiers, and build material per run"),
        ("Compile guard","rejects unsupported `<close>` declarations early"),
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
        "type":"PASTE",
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
            "`ON` String protection",
            f"`ON` Numeric transforms · {discord.utils.escape_markdown(feature_map.get('Numeric folding','enabled'))}",
            f"`ON` Flow protection · {discord.utils.escape_markdown(feature_map.get('Control-flow protection','enabled'))}",
            f"`ON` Runtime integrity · {discord.utils.escape_markdown(feature_map.get('Anti-tamper','enabled'))}",
            f"`ON` Payload integrity · {discord.utils.escape_markdown(feature_map.get('Payload integrity','enabled'))}",
        ))
        self.add_item(
            make_container(
                make_text("## ✦ Forguar Protected v1"),
                make_text(f"`{safe_filename}`\n**Obfuscation complete.** Your protected output has been generated successfully."),
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
