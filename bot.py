import logging
import os
import io
import asyncio
import random
import string
import re

import discord
from discord import app_commands
from discord.ext import commands
from luaparser import ast, astnodes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("obf-bot")

MAX_INPUT = 5 * 1024 * 1024
MAX_OUTPUT = 45 * 1024 * 1024
ALLOWED_EXT = (".lua", ".luau", ".txt")


def lua_escape(s):
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == '\\':
            out.append('\\\\')
        elif c == '\n':
            out.append('\\n')
        elif c == '\r':
            out.append('\\r')
        elif c == '\t':
            out.append('\\t')
        elif ord(c) < 32 or ord(c) > 126:
            out.append('\\%03d' % (ord(c) & 0xFF))
        else:
            out.append(c)
    out.append('"')
    return ''.join(out)


def minify_lua(src):
    result = []
    depth = 0
    paren = 0
    bracket = 0
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            quote = c
            result.append(c)
            i += 1
            while i < n:
                ch = src[i]
                if ch == '\\' and i + 1 < n:
                    result.append(ch)
                    result.append(src[i + 1])
                    i += 2
                    continue
                result.append(ch)
                i += 1
                if ch == quote:
                    break
            continue
        if c == '{':
            depth += 1
            result.append(c)
        elif c == '}':
            depth -= 1
            result.append(c)
        elif c == '(':
            paren += 1
            result.append(c)
        elif c == ')':
            paren -= 1
            result.append(c)
        elif c == '[':
            bracket += 1
            result.append(c)
        elif c == ']':
            bracket -= 1
            result.append(c)
        elif c == '\n' or c == '\r':
            if depth == 0 and paren == 0 and bracket == 0:
                result.append(';')
        elif c == ' ' or c == '\t':
            if depth == 0 and paren == 0 and bracket == 0:
                if result and result[-1] not in (' ', ';', '\t'):
                    result.append(' ')
        else:
            result.append(c)
        i += 1
    out = ''.join(result)
    while ';;' in out:
        out = out.replace(';;', ';')
    out = out.strip(';')
    while '\n' in out or '\r' in out:
        out = out.replace('\n', ';').replace('\r', '')
    while ';;' in out:
        out = out.replace(';;', ';')
    return out


class LuaStrongObfuscator:
    def __init__(self, seed=None, use_vm=True):
        if seed is not None:
            random.seed(seed)
        self.use_vm = use_vm
        self.names = set()
        self.str_key = bytes(random.randint(1, 255) for _ in range(32))
        self.bc_key = random.randint(1, 255)
        self.str_dec = self.fresh('_S')
        self.vm_run = self.fresh('_R')
        self.antidbg = self.fresh('_A')
        self.antidet = self.fresh('_T')
        self.chk = self.fresh('_K')
        self.chk_cache = self.fresh('_Kc')
        self.chk_time = self.fresh('_Kt')
        self.bxor = self.fresh('_X')
        self.unp = self.fresh('_U')
        self.gv = self.fresh('_G')
        self.mprefix = self.fresh('_M')
        self.string_to_marker = {}
        self.marker_to_string = {}
        self.renamed_locals = set()
        ops = ['PUSH', 'LOAD', 'STORE', 'GSTORE', 'ADD', 'SUB', 'MUL',
               'DIV', 'MOD', 'POW', 'EQ', 'NE', 'LT', 'LE', 'GT', 'GE',
               'CONCAT', 'LEN', 'NOT', 'NEG', 'DUP', 'POP', 'JMP', 'JZ',
               'JNZ', 'CALL', 'METHCALL', 'TAILCALL', 'RET', 'HALT',
               'NEWT', 'GETT', 'SETT']
        random.shuffle(ops)
        self.opmap = {}
        v = random.randint(200, 500)
        for op in ops:
            self.opmap[op] = v
            v += random.randint(3, 17)
        self.lc = [0]

    def fresh(self, prefix):
        while True:
            n = prefix + ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10))
            if n not in self.names:
                self.names.add(n)
                return n

    def encrypt(self, data):
        out = []
        for i, b in enumerate(data):
            b = b & 0xFF
            x = (b ^ self.str_key[i % len(self.str_key)]) & 0xFF
            x = (x + i * 13 + 7) & 0xFF
            x = (x ^ self.str_key[(i * 5) % len(self.str_key)]) & 0xFF
            out.append(x)
        return out

    def marker_for(self, s):
        if s in self.string_to_marker:
            return self.string_to_marker[s]
        enc = self.encrypt(s.encode('utf-8'))
        marker = self.mprefix + '_' + '_'.join(str(b) for b in enc)
        self.string_to_marker[s] = marker
        self.marker_to_string[marker] = s
        return marker

    def arr(self, b):
        return '{' + ','.join(str(x) for x in b) + '}'

    def build_prelude(self):
        sk1 = self.str_key[:16]
        sk2 = bytes(self.str_key[i] ^ sk1[i % 16] for i in range(len(self.str_key)))
        return (
            f'local _FP_pc=pcall\n'
            f'local _FP_er=error\n'
            f'local _FP_ty=type\n'
            f'local _FP_rg=rawget\n'
            f'local _FP_sm=setmetatable\n'
            f'local _FP_gm=getmetatable\n'
            f'local _FP_cc=string.char\n'
            f'local _FP_cb=string.byte\n'
            f'local _FP_dp=nil\n'
            f'if type(string)=="table" and type(string.dump)=="function" then _FP_dp=string.dump end\n'
            f'local {self.gv}=_ENV or _G or {{}}\n'
            f'local {self.bxor}\n'
            f'do\n'
            f'  if type(bit32)=="table" and bit32.bxor then {self.bxor}=bit32.bxor\n'
            f'  elseif type(bit)=="table" and bit.bxor then {self.bxor}=bit.bxor\n'
            f'  else\n'
            f'    {self.bxor}=function(a,b)\n'
            f'      local r=0 local p=1\n'
            f'      for i=1,32 do\n'
            f'        local x=a%2 local y=b%2\n'
            f'        if x~=y then r=r+p end\n'
            f'        a=(a-x)/2 b=(b-y)/2 p=p*2\n'
            f'      end\n'
            f'      return r\n'
            f'    end\n'
            f'  end\n'
            f'end\n'
            f'local {self.unp}=unpack or (type(table)=="table" and table.unpack) or function(t)\n'
            f'  local n=#t if n==0 then return end if n==1 then return t[1] end\n'
            f'  return t[1],t[2],t[3],t[4],t[5],t[6],t[7],t[8]\n'
            f'end\n'
            f'local _sc={{}}\n'
            f'local function {self.str_dec}(t)\n'
            f'  local c=_sc[t] if c then return c end\n'
            f'  local a={self.arr(sk1)}\n'
            f'  local b={self.arr(sk2)}\n'
            f'  local k={{}}\n'
            f'  for i=1,#a do k[i]={self.bxor}(a[i],b[i]) end\n'
            f'  local r={{}}\n'
            f'  for i=1,#t do\n'
            f'    local v=t[i]\n'
            f'    v={self.bxor}(v,k[((i-1)*5%#k)+1])\n'
            f'    v=(v-(i-1)*13-7)%256\n'
            f'    v={self.bxor}(v,k[((i-1)%#k)+1])\n'
            f'    r[i]=string.char(v)\n'
            f'  end\n'
            f'  local res=table.concat(r)\n'
            f'  _sc[t]=res\n'
            f'  return res\n'
            f'end\n'
        )

    def build_antidebug(self):
        return (
            f'local {self.antidbg}=function()\n'
            f'  local bad=0\n'
            f'  if type(debug)~="table" then return false end\n'
            f'  if type(debug.gethook)=="function" then\n'
            f'    local ok,h=pcall(debug.gethook)\n'
            f'    if ok and h~=nil then bad=bad+2 end\n'
            f'  end\n'
            f'  if type(debug.sethook)=="function" then\n'
            f'    local fired=false\n'
            f'    local sok=pcall(function()\n'
            f'      debug.sethook(function() fired=true end,"l")\n'
            f'      local x=0\n'
            f'      for i=1,20 do x=x+i end\n'
            f'      debug.sethook()\n'
            f'    end)\n'
            f'    if not sok then bad=bad+1 end\n'
            f'    if not fired then bad=bad+1 end\n'
            f'  end\n'
            f'  if type(debug.getinfo)=="function" then\n'
            f'    local ok,i=pcall(debug.getinfo,1,"Sl")\n'
            f'    if not ok or type(i)~="table" then bad=bad+1\n'
            f'    elseif not i.currentline and not i.linedefined then bad=bad+1 end\n'
            f'  end\n'
            f'  if type(debug.getlocal)=="function" then\n'
            f'    local ok,l=pcall(debug.getlocal,1,1)\n'
            f'    if ok and l==nil then bad=bad+0 end\n'
            f'  end\n'
            f'  if type(string.dump)=="function" then\n'
            f'    local ok,d=pcall(string.dump,function() return 1 end)\n'
            f'    if ok then\n'
            f'      if type(d)~="string" or #d<5 then bad=bad+1\n'
            f'      elseif string.byte(d,1)~=27 or string.byte(d,2)~=76\n'
            f'         or string.byte(d,3)~=117 or string.byte(d,4)~=97 then bad=bad+1 end\n'
            f'    end\n'
            f'  end\n'
            f'  if type(pcall)=="function" then\n'
            f'    local ok,e=pcall(error,"probe")\n'
            f'    if ok or e~="probe" then bad=bad+1 end\n'
            f'  end\n'
            f'  if type(os)=="table" and type(os.clock)=="function" then\n'
            f'    local t=os.clock()\n'
            f'    local x=0\n'
            f'    for i=1,500 do x=(x+i)%1000 end\n'
            f'    if os.clock()-t>0.15 then bad=bad+1 end\n'
            f'  end\n'
            f'  return bad>=3\n'
            f'end\n'
        )

    def build_antidetect(self):
        return (
            f'local {self.antidet}=function()\n'
            f'  if type({self.gv})~="table" then return false end\n'
            f'  local fap={{"writefile","readfile","appendfile","isfile","delfile","listfiles","makefolder","getcustomasset","delfolder","writecustomasset"}}\n'
            f'  for _,n in ipairs(fap) do\n'
            f'    if rawget({self.gv},n) then return true end\n'
            f'  end\n'
            f'  local sig={{"getgenv","getrawmetatable","hookfunction","hookmetamethod",\n'
            f'             "setreadonly","checkcaller","islclosure","getconnections",\n'
            f'             "identifyexecutor","getexecutorname","firesignal","firetouchinterest",\n'
            f'             "getloadedmodules","getnilinstances","setclipboard","queue_on_teleport",\n'
            f'             "getreg","getgc","getinstances","getconstants","getupvalues",\n'
            f'             "getprotos","getstack","setstack","getnamecalling",\n'
            f'             "sethiddenproperty","gethiddenproperty","setsimulationradius",\n'
            f'             "getcallbackvalue","getscriptclosure","getscripthash","getsenv",\n'
            f'             "dumpstring","fireclickdetector","firetouchtransmitter",\n'
            f'             "getthreadidentity","setthreadidentity","getrawmetatable"}}\n'
            f'  local c=0\n'
            f'  for _,n in ipairs(sig) do\n'
            f'    if rawget({self.gv},n) then c=c+1 end\n'
            f'  end\n'
            f'  return c>=3\n'
            f'end\n'
        )

    def build_check(self):
        return (
            f'local {self.chk_cache}=nil\n'
            f'local {self.chk_time}=0\n'
            f'local {self.chk}=function()\n'
            f'  local now=0\n'
            f'  if type(os)=="table" and type(os.clock)=="function" then now=os.clock() end\n'
            f'  if {self.chk_cache}==nil or now-{self.chk_time}>1.0 then\n'
            f'    {self.chk_time}=now\n'
            f'    local score=0\n'
            f'    if pcall~=_FP_pc then score=score+2 end\n'
            f'    if type~=_FP_ty then score=score+2 end\n'
            f'    if error~=_FP_er then score=score+2 end\n'
            f'    if rawget~=_FP_rg then score=score+2 end\n'
            f'    if setmetatable~=_FP_sm then score=score+2 end\n'
            f'    if getmetatable~=_FP_gm then score=score+2 end\n'
            f'    if string.char~=_FP_cc then score=score+2 end\n'
            f'    if string.byte~=_FP_cb then score=score+2 end\n'
            f'    if _FP_dp and string.dump~=_FP_dp then score=score+2 end\n'
            f'    local ok1,r1=pcall({self.antidbg})\n'
            f'    if not ok1 or r1 then score=score+5 end\n'
            f'    local ok2,r2=pcall({self.antidet})\n'
            f'    if not ok2 or r2 then score=score+5 end\n'
            f'    {self.chk_cache}=(score<3)\n'
            f'  end\n'
            f'  return {self.chk_cache}\n'
            f'end\n'
        )

    def build_vm(self):
        o = self.opmap
        return (
            f'local function {self.vm_run}(c,e)\n'
            f'  local s={{}}\n'
            f'  local sp=0\n'
            f'  local pc=1\n'
            f'  while true do\n'
            f'    local i=c[pc]\n'
            f'    if not i then break end\n'
            f'    local op=i[1]\n'
            f'    if op=={o["PUSH"]} then\n'
            f'      sp=sp+1 s[sp]=i[2]\n'
            f'    elseif op=={o["LOAD"]} then\n'
            f'      sp=sp+1\n'
            f'      local v=e[i[2]]\n'
            f'      if v==nil then v={self.gv}[i[2]] end\n'
            f'      s[sp]=v\n'
            f'    elseif op=={o["STORE"]} then\n'
            f'      e[i[2]]=s[sp] sp=sp-1\n'
            f'    elseif op=={o["GSTORE"]} then\n'
            f'      {self.gv}[i[2]]=s[sp] sp=sp-1\n'
            f'    elseif op=={o["ADD"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]+b\n'
            f'    elseif op=={o["SUB"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]-b\n'
            f'    elseif op=={o["MUL"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]*b\n'
            f'    elseif op=={o["DIV"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]/b\n'
            f'    elseif op=={o["MOD"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]%b\n'
            f'    elseif op=={o["POW"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=s[sp]^b\n'
            f'    elseif op=={o["EQ"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]==b) and 1 or 0\n'
            f'    elseif op=={o["NE"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]~=b) and 1 or 0\n'
            f'    elseif op=={o["LT"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]<b) and 1 or 0\n'
            f'    elseif op=={o["LE"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]<=b) and 1 or 0\n'
            f'    elseif op=={o["GT"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]>b) and 1 or 0\n'
            f'    elseif op=={o["GE"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=(s[sp]>=b) and 1 or 0\n'
            f'    elseif op=={o["CONCAT"]} then\n'
            f'      local b=s[sp] sp=sp-1 s[sp]=tostring(s[sp])..tostring(b)\n'
            f'    elseif op=={o["LEN"]} then\n'
            f'      s[sp]=#s[sp]\n'
            f'    elseif op=={o["NOT"]} then\n'
            f'      s[sp]=(not s[sp]) and 1 or 0\n'
            f'    elseif op=={o["NEG"]} then\n'
            f'      s[sp]=-s[sp]\n'
            f'    elseif op=={o["DUP"]} then\n'
            f'      sp=sp+1 s[sp]=s[sp-1]\n'
            f'    elseif op=={o["POP"]} then\n'
            f'      sp=sp-1\n'
            f'    elseif op=={o["JMP"]} then\n'
            f'      pc=i[2]\n'
            f'    elseif op=={o["JZ"]} then\n'
            f'      local v=s[sp] sp=sp-1\n'
            f'      if v==0 or v==false or v==nil then pc=i[2] end\n'
            f'    elseif op=={o["JNZ"]} then\n'
            f'      local v=s[sp] sp=sp-1\n'
            f'      if v~=0 and v~=false and v~=nil then pc=i[2] end\n'
            f'    elseif op=={o["NEWT"]} then\n'
            f'      sp=sp+1 s[sp]={{}}\n'
            f'    elseif op=={o["GETT"]} then\n'
            f'      local k=s[sp] sp=sp-1 s[sp]=s[sp][k]\n'
            f'    elseif op=={o["SETT"]} then\n'
            f'      local v=s[sp] sp=sp-1 local k=s[sp] sp=sp-1 s[sp][k]=v sp=sp-1\n'
            f'    elseif op=={o["CALL"]} then\n'
            f'      local n=i[2] local a={{}}\n'
            f'      for x=n,1,-1 do a[x]=s[sp] sp=sp-1 end\n'
            f'      local f=s[sp] sp=sp-1 sp=sp+1 s[sp]=f({self.unp}(a))\n'
            f'    elseif op=={o["METHCALL"]} then\n'
            f'      local n=i[2] local nm=s[sp] sp=sp-1 local a={{}}\n'
            f'      for x=n,1,-1 do a[x]=s[sp] sp=sp-1 end\n'
            f'      local obj=s[sp] sp=sp-1 local f=obj[nm]\n'
            f'      table.insert(a,1,obj) sp=sp+1 s[sp]=f({self.unp}(a))\n'
            f'    elseif op=={o["TAILCALL"]} then\n'
            f'      local n=i[2] local a={{}}\n'
            f'      for x=n,1,-1 do a[x]=s[sp] sp=sp-1 end\n'
            f'      local f=s[sp] sp=sp-1 return f({self.unp}(a))\n'
            f'    elseif op=={o["RET"]} then\n'
            f'      return s[sp]\n'
            f'    elseif op=={o["HALT"]} then\n'
            f'      return\n'
            f'    end\n'
            f'    pc=pc+1\n'
            f'  end\n'
            f'end\n'
        )

    def emit_bytecode_value(self, x, row_idx):
        K = (self.bc_key + (row_idx + 1) * 13) % 256
        if x is None:
            return 'nil'
        if x is True:
            return 'true'
        if x is False:
            return 'false'
        if isinstance(x, str):
            enc = self.encrypt(x.encode('utf-8'))
            return f'{self.str_dec}({{{",".join(str(b) for b in enc)}}})'
        if isinstance(x, int):
            if 0 <= x < (1 << 30):
                return str(x ^ K)
            return '{' + str(x) + '}'
        if isinstance(x, float):
            if x == int(x) and 0 <= int(x) < (1 << 30):
                return str(int(x) ^ K)
            return '{' + repr(x) + '}'
        if isinstance(x, tuple):
            raise ValueError('unresolved label: ' + str(x))
        raise ValueError('cannot emit ' + type(x).__name__)

    def _inject_dead(self, n):
        out = []
        opcodes = list(self.opmap.values())
        for _ in range(n):
            out.append([random.choice(opcodes), random.randint(0, 80)])
        return out

    def emit_vm_function(self, name, params, bytecode, is_local):
        bc_name = self.fresh('_b')
        rows = []
        for idx, ins in enumerate(bytecode):
            parts = [self.emit_bytecode_value(x, idx) for x in ins]
            rows.append('{' + ','.join(parts) + '}')
        bc_table = '{\n' + ',\n'.join(rows) + '\n}'
        K = self.bc_key
        decode = (
            f'for _i=1,#{bc_name} do\n'
            f'  local _KK=({K}+_i*13)%256\n'
            f'  local _ins={bc_name}[_i]\n'
            f'  for _j=1,#_ins do\n'
            f'    local _v=_ins[_j]\n'
            f'    if type(_v)=="table" then _ins[_j]=_v[1]\n'
            f'    elseif type(_v)=="number" and _v==math.floor(_v) and _v>=0 then _ins[_j]={self.bxor}(_v,_KK) end\n'
            f'  end\n'
            f'end'
        )
        env_parts = ','.join(f'{p}={p}' for p in params)
        prefix = 'local function' if is_local else 'function'
        return (
            f'local {bc_name}={bc_table}\n'
            f'{decode}\n'
            f'{prefix} {name}({",".join(params)})\n'
            f'  if not {self.chk}() then return end\n'
            f'  local _e={{{env_parts}}}\n'
            f'  if type(setmetatable)=="function" then pcall(setmetatable,_e,{{__index={self.gv}}}) end\n'
            f'  return {self.vm_run}({bc_name},_e)\n'
            f'end\n'
        )

    def compile_function(self, func, params):
        code = []
        labels = {}
        break_stack = []
        param_set = set(params)

        def new_label():
            self.lc[0] += 1
            return ('__L__', self.lc[0])

        def emit(op, *args):
            code.append([self.opmap[op]] + list(args))

        def visit(node):
            tn = type(node).__name__

            if tn == 'Block':
                for s in node.body:
                    visit(s)

            elif tn == 'LocalAssign':
                if len(node.targets) != 1 or len(node.values) != 1:
                    raise NotImplementedError('multi local')
                if not isinstance(node.targets[0], astnodes.Name):
                    raise NotImplementedError('non-name local')
                visit(node.values[0])
                emit('STORE', node.targets[0].id)

            elif tn == 'Assign':
                if len(node.targets) != 1 or len(node.values) != 1:
                    raise NotImplementedError('multi assign')
                tgt = node.targets[0]
                if isinstance(tgt, astnodes.Name):
                    visit(node.values[0])
                    is_local = tgt.id in self.renamed_locals or tgt.id in param_set
                    emit('STORE' if is_local else 'GSTORE', tgt.id)
                elif isinstance(tgt, astnodes.Index):
                    visit(tgt.value)
                    if isinstance(tgt.idx, astnodes.String):
                        emit('PUSH', tgt.idx.s)
                    else:
                        visit(tgt.idx)
                    visit(node.values[0])
                    emit('SETT')
                else:
                    raise NotImplementedError('assign target')

            elif tn == 'String':
                emit('PUSH', node.s)

            elif tn == 'Number':
                n = node.n
                if isinstance(n, str):
                    low = n.lower()
                    try:
                        if low.startswith('0x') or low.startswith('-0x'):
                            val = int(n, 16)
                        elif '.' in n or 'e' in low:
                            val = float(n)
                        else:
                            val = int(n)
                    except Exception:
                        val = n
                else:
                    val = n
                emit('PUSH', val)

            elif tn in ('TrueExpr', 'True'):
                emit('PUSH', True)
            elif tn in ('FalseExpr', 'False'):
                emit('PUSH', False)
            elif tn in ('Nil', 'NilExpr'):
                emit('PUSH', None)

            elif tn == 'Name':
                emit('LOAD', node.id)

            elif tn == 'BinOp':
                if node.op == 'and':
                    visit(node.left)
                    emit('DUP')
                    lbl = new_label()
                    emit('JZ', lbl)
                    emit('POP')
                    visit(node.right)
                    labels[lbl] = len(code)
                elif node.op == 'or':
                    visit(node.left)
                    emit('DUP')
                    lbl = new_label()
                    emit('JNZ', lbl)
                    emit('POP')
                    visit(node.right)
                    labels[lbl] = len(code)
                else:
                    visit(node.left)
                    visit(node.right)
                    m = {'+': 'ADD', '-': 'SUB', '*': 'MUL', '/': 'DIV',
                         '%': 'MOD', '^': 'POW', '==': 'EQ', '~=': 'NE',
                         '<': 'LT', '<=': 'LE', '>': 'GT', '>=': 'GE',
                         '..': 'CONCAT'}
                    if node.op not in m:
                        raise NotImplementedError('binop ' + str(node.op))
                    emit(m[node.op])

            elif tn == 'UnaryOp':
                visit(node.operand)
                if node.op == '-':
                    emit('NEG')
                elif node.op == 'not':
                    emit('NOT')
                elif node.op == '#':
                    emit('LEN')
                else:
                    raise NotImplementedError('unop ' + str(node.op))

            elif tn == 'Call':
                visit(node.func)
                for a in node.args:
                    visit(a)
                emit('CALL', len(node.args))

            elif tn == 'Method':
                visit(node.source)
                for a in node.args:
                    visit(a)
                emit('PUSH', node.name)
                emit('METHCALL', len(node.args))

            elif tn == 'Index':
                visit(node.value)
                if isinstance(node.idx, astnodes.String):
                    emit('PUSH', node.idx.s)
                else:
                    visit(node.idx)
                emit('GETT')

            elif tn == 'If':
                end_l = new_label()
                current = node
                while current is not None:
                    visit(current.test)
                    nxt = new_label()
                    emit('JZ', nxt)
                    for s in current.body.body:
                        visit(s)
                    emit('JMP', end_l)
                    labels[nxt] = len(code)
                    orelse = getattr(current, 'orelse', None)
                    if orelse is None:
                        break
                    if isinstance(orelse, astnodes.Block):
                        for s in orelse.body:
                            visit(s)
                        break
                    elif isinstance(orelse, astnodes.If):
                        current = orelse
                    else:
                        break
                labels[end_l] = len(code)

            elif tn == 'While':
                start = new_label()
                end_l = new_label()
                labels[start] = len(code)
                visit(node.test)
                emit('JZ', end_l)
                break_stack.append(end_l)
                for s in node.body.body:
                    visit(s)
                break_stack.pop()
                emit('JMP', start)
                labels[end_l] = len(code)

            elif tn == 'Repeat':
                start = new_label()
                end_l = new_label()
                labels[start] = len(code)
                break_stack.append(end_l)
                for s in node.body.body:
                    visit(s)
                break_stack.pop()
                visit(node.test)
                emit('JZ', start)
                labels[end_l] = len(code)

            elif tn == 'Fornum':
                tgt = node.target
                if not isinstance(tgt, astnodes.Name):
                    raise NotImplementedError('fornum non-name target')
                var = tgt.id
                start_v = self.fresh('_fs')
                limit_v = self.fresh('_fl')
                step_v = self.fresh('_ft')
                visit(node.start)
                emit('STORE', start_v)
                visit(node.stop)
                emit('STORE', limit_v)
                if getattr(node, 'step', None) is not None:
                    visit(node.step)
                else:
                    emit('PUSH', 1)
                emit('STORE', step_v)
                emit('LOAD', start_v)
                emit('STORE', var)
                top = new_label()
                body_l = new_label()
                end_l = new_label()
                neg_l = new_label()
                labels[top] = len(code)
                emit('LOAD', step_v)
                emit('PUSH', 0)
                emit('GT')
                emit('JZ', neg_l)
                emit('LOAD', var)
                emit('LOAD', limit_v)
                emit('GT')
                emit('JNZ', end_l)
                emit('JMP', body_l)
                labels[neg_l] = len(code)
                emit('LOAD', step_v)
                emit('PUSH', 0)
                emit('LT')
                emit('JZ', end_l)
                emit('LOAD', var)
                emit('LOAD', limit_v)
                emit('LT')
                emit('JNZ', end_l)
                labels[body_l] = len(code)
                break_stack.append(end_l)
                for s in node.body.body:
                    visit(s)
                break_stack.pop()
                emit('LOAD', var)
                emit('LOAD', step_v)
                emit('ADD')
                emit('STORE', var)
                emit('JMP', top)
                labels[end_l] = len(code)

            elif tn == 'Break':
                if not break_stack:
                    raise NotImplementedError('break outside loop')
                emit('JMP', break_stack[-1])

            elif tn == 'Table':
                emit('NEWT')
                pos = 0
                for f in node.fields:
                    ft = type(f).__name__
                    if ft == 'Field':
                        if getattr(f, 'key', None) is None:
                            pos += 1
                            emit('DUP')
                            emit('PUSH', pos)
                            visit(f.value)
                            emit('SETT')
                        elif isinstance(f.key, astnodes.String):
                            emit('DUP')
                            emit('PUSH', f.key.s)
                            visit(f.value)
                            emit('SETT')
                        else:
                            raise NotImplementedError('keyed field')
                    else:
                        raise NotImplementedError('field ' + ft)

            elif tn == 'Return':
                if len(node.values) == 0:
                    emit('PUSH', None)
                    emit('RET')
                elif len(node.values) == 1:
                    v = node.values[0]
                    if type(v).__name__ == 'Call':
                        visit(v.func)
                        for a in v.args:
                            visit(a)
                        emit('TAILCALL', len(v.args))
                    else:
                        visit(v)
                        emit('RET')
                else:
                    raise NotImplementedError('multi return')

            elif tn == 'Semicolon':
                pass
            else:
                raise NotImplementedError(tn)

        for s in func.body.body:
            visit(s)
        emit('HALT')
        code.extend(self._inject_dead(random.randint(3, 8)))

        for ins in code:
            for i in range(1, len(ins)):
                if isinstance(ins[i], tuple) and len(ins[i]) == 2 and ins[i][0] == '__L__':
                    if ins[i] not in labels:
                        raise ValueError('unresolved label')
                    ins[i] = labels[ins[i]]

        return code

    def rename_locals(self, tree):
        outer = self

        class R(ast.ASTVisitor):
            def __init__(self):
                self.scopes = [{}]

            def visit_LocalFunction(self, node):
                if hasattr(node, 'name') and isinstance(node.name, astnodes.Name):
                    new = outer.fresh('f')
                    self.scopes[-1][node.name.id] = new
                    outer.renamed_locals.add(new)
                    node.name.id = new
                self.scopes.append({})
                for a in getattr(node, 'args', []):
                    if isinstance(a, astnodes.Name):
                        new = outer.fresh('a')
                        self.scopes[-1][a.id] = new
                        outer.renamed_locals.add(new)
                        a.id = new
                for s in getattr(node.body, 'body', []):
                    self.visit(s)
                self.scopes.pop()

            def visit_Function(self, node):
                self.scopes.append({})
                for a in node.args:
                    if isinstance(a, astnodes.Name):
                        new = outer.fresh('a')
                        self.scopes[-1][a.id] = new
                        outer.renamed_locals.add(new)
                        a.id = new
                for s in node.body.body:
                    self.visit(s)
                self.scopes.pop()

            def visit_LocalAssign(self, node):
                for t in node.targets:
                    if isinstance(t, astnodes.Name):
                        new = outer.fresh('v')
                        self.scopes[-1][t.id] = new
                        outer.renamed_locals.add(new)
                        t.id = new
                for v in node.values:
                    self.visit(v)

            def visit_Fornum(self, node):
                saved = None
                orig = None
                if isinstance(node.target, astnodes.Name):
                    orig = node.target.id
                    saved = self.scopes[-1].get(orig)
                    new = outer.fresh('v')
                    self.scopes[-1][orig] = new
                    outer.renamed_locals.add(new)
                    node.target.id = new
                self.visit(node.start)
                self.visit(node.stop)
                if getattr(node, 'step', None) is not None:
                    self.visit(node.step)
                self.scopes.append({})
                for s in node.body.body:
                    self.visit(s)
                self.scopes.pop()
                if orig is not None:
                    if saved is None:
                        self.scopes[-1].pop(orig, None)
                    else:
                        self.scopes[-1][orig] = saved

            def visit_Forin(self, node):
                saved = {}
                for t in node.targets:
                    if isinstance(t, astnodes.Name):
                        orig = t.id
                        saved[orig] = self.scopes[-1].get(orig)
                        new = outer.fresh('v')
                        self.scopes[-1][orig] = new
                        outer.renamed_locals.add(new)
                        t.id = new
                for e in node.iter:
                    self.visit(e)
                self.scopes.append({})
                for s in node.body.body:
                    self.visit(s)
                self.scopes.pop()
                for orig, old_val in saved.items():
                    if old_val is None:
                        self.scopes[-1].pop(orig, None)
                    else:
                        self.scopes[-1][orig] = old_val

            def visit_Name(self, node):
                for sc in reversed(self.scopes):
                    if node.id in sc:
                        node.id = sc[node.id]
                        return

        R().visit(tree)

    def mark_strings(self, tree):
        outer = self

        class M(ast.ASTVisitor):
            def visit_String(self, node):
                if node.s:
                    node.s = outer.marker_for(node.s)

        M().visit(tree)

    def replace_markers(self, source):
        pat = re.compile('"' + re.escape(self.mprefix) + r'_([0-9]+(?:_[0-9]+)*)"')

        def sub(m):
            nums = m.group(1).replace('_', ',')
            return f'{self.str_dec}({{{nums}}})'

        return pat.sub(sub, source)

    def extract_function(self, node):
        tn = type(node).__name__
        if tn == 'Function' and getattr(node, 'name', None) is not None:
            params = []
            for a in node.args:
                if isinstance(a, astnodes.Name):
                    params.append(a.id)
                else:
                    return None
            return (node.name.id, params, node, False)
        if tn == 'LocalFunction' and getattr(node, 'name', None) is not None:
            params = []
            for a in node.args:
                if isinstance(a, astnodes.Name):
                    params.append(a.id)
                else:
                    return None
            return (node.name.id, params, node, True)
        if tn == 'LocalAssign' and len(node.targets) == 1 and len(node.values) == 1:
            if isinstance(node.targets[0], astnodes.Name) and isinstance(node.values[0], astnodes.Function):
                func = node.values[0]
                params = []
                for a in func.args:
                    if isinstance(a, astnodes.Name):
                        params.append(a.id)
                    else:
                        return None
                return (node.targets[0].id, params, func, True)
        return None

    def obfuscate(self, source):
        tree = ast.parse(source)
        self.rename_locals(tree)

        new_body = []
        for node in tree.body:
            info = self.extract_function(node)
            if info is not None and self.use_vm:
                name, params, func, is_local = info
                try:
                    bc = self.compile_function(func, params)
                    emitted = self.emit_vm_function(name, params, bc, is_local)
                    ast.parse(emitted)
                    new_body.extend(ast.parse(emitted).body)
                    continue
                except Exception:
                    pass
            new_body.append(node)

        tree.body = new_body
        self.mark_strings(tree)
        body_src = ast.to_lua_source(tree)
        body_src = self.replace_markers(body_src)

        result = (
            self.build_prelude()
            + self.build_antidebug()
            + self.build_antidetect()
            + self.build_check()
            + self.build_vm()
            + body_src
        )

        minified = minify_lua(result)
        try:
            ast.parse(minified)
            return minified
        except Exception:
            return result


def luau_preprocess(src):
    src = re.sub(r'(?<!:):\s*[A-Za-z_][\w.<>\[\]|&?]*(?=\s*[,)=;\n])', '', src)
    return src


_settings = {}


def settings_for(uid):
    if uid not in _settings:
        _settings[uid] = {"luau": False, "novm": False, "seed": None}
    return _settings[uid]


def settings_summary(s):
    mode = "Luau" if s["luau"] else "Lua"
    vm = "off" if s["novm"] else "on"
    seed = str(s["seed"]) if s["seed"] is not None else "random"
    return f"Mode: {mode} | VM: {vm} | Seed: {seed}"


def validate_file(filename, size):
    if not filename:
        return False, "File has no name."
    if not filename.lower().endswith(ALLOWED_EXT):
        return False, "Unsupported file type. Only .lua, .luau, and .txt are allowed."
    if size and size > MAX_INPUT:
        return False, f"File too large ({size} bytes, max {MAX_INPUT})."
    return True, ""


def run_obfuscator(source, s):
    if len(source.encode("utf-8")) > MAX_INPUT:
        raise ValueError(f"Input exceeds {MAX_INPUT} bytes.")
    if s["luau"]:
        source = luau_preprocess(source)
    obf = LuaStrongObfuscator(seed=s["seed"], use_vm=not s["novm"])
    return obf.obfuscate(source)


class SettingsView(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=300)
        self.uid = uid
        self.luau_btn = discord.ui.Button(style=discord.ButtonStyle.secondary)
        self.vm_btn = discord.ui.Button(style=discord.ButtonStyle.secondary)
        self.luau_btn.callback = self._toggle_luau
        self.vm_btn.callback = self._toggle_vm
        self.add_item(self.luau_btn)
        self.add_item(self.vm_btn)
        self._refresh(settings_for(uid))

    def _refresh(self, s):
        self.luau_btn.label = f"Luau mode: {'ON' if s['luau'] else 'OFF'}"
        self.vm_btn.label = f"VM mode: {'OFF' if s['novm'] else 'ON'}"

    async def _toggle_luau(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not your settings.", ephemeral=True)
            return
        s = settings_for(self.uid)
        s["luau"] = not s["luau"]
        self._refresh(s)
        await interaction.response.edit_message(content=settings_summary(s), view=self)

    async def _toggle_vm(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not your settings.", ephemeral=True)
            return
        s = settings_for(self.uid)
        s["novm"] = not s["novm"]
        self._refresh(s)
        await interaction.response.edit_message(content=settings_summary(s), view=self)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    guild_id = os.environ.get("GUILD_ID", "").strip()
    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info(f"Synced commands to guild {guild_id}")
        else:
            await bot.tree.sync()
            logger.info("Synced commands globally")
    except Exception as e:
        logger.exception(f"Sync failed: {e}")
    logger.info(f"Logged in as {bot.user}")


@bot.tree.command(name="obfuscate", description="Obfuscate a Lua/Luau file")
@app_commands.describe(file="A .lua, .luau, or .txt file to obfuscate")
async def cmd_obfuscate(interaction: discord.Interaction, file: discord.Attachment):
    ok, err = validate_file(file.filename, file.size)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    s = settings_for(interaction.user.id)
    if file.filename and file.filename.lower().endswith(".luau"):
        s = dict(s)
        s["luau"] = True

    try:
        raw = await file.read()
    except Exception as e:
        logger.exception("attachment read failed")
        await interaction.followup.send(f"Failed to read file: {e}")
        return

    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        await interaction.followup.send(f"File is not valid UTF-8: {e}")
        return

    try:
        result = await asyncio.to_thread(run_obfuscator, src, s)
    except NotImplementedError as e:
        await interaction.followup.send(f"Unsupported construct: {e}")
        return
    except Exception as e:
        logger.exception("obfuscation failed")
        await interaction.followup.send(
            f"Obfuscation failed: {type(e).__name__}: {e}"
        )
        return

    data = result.encode("utf-8")
    if len(data) > MAX_OUTPUT:
        await interaction.followup.send(
            f"Output too large ({len(data)} bytes, max {MAX_OUTPUT})."
        )
        return

    base = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename
    out_name = base + "_obf.lua"
    buf = io.BytesIO(data)
    try:
        await interaction.followup.send(
            content=settings_summary(s),
            file=discord.File(buf, filename=out_name),
        )
    except Exception as e:
        logger.exception("send failed")
        await interaction.followup.send(f"Failed to send output: {e}")


@bot.tree.command(name="settings", description="View or toggle obfuscator settings")
async def cmd_settings(interaction: discord.Interaction):
    s = settings_for(interaction.user.id)
    view = SettingsView(interaction.user.id)
    await interaction.response.send_message(
        settings_summary(s), view=view, ephemeral=True
    )


@bot.tree.command(name="seed", description="Set a fixed obfuscation seed")
@app_commands.describe(value="An integer seed, or 'clear' to reset to random")
async def cmd_seed(interaction: discord.Interaction, value: str):
    s = settings_for(interaction.user.id)
    if value.lower() == "clear":
        s["seed"] = None
        await interaction.response.send_message(
            "Seed cleared (random per run).", ephemeral=True
        )
        return
    try:
        s["seed"] = int(value)
        await interaction.response.send_message(
            f"Seed set to {s['seed']}.", ephemeral=True
        )
    except ValueError:
        await interaction.response.send_message(
            "Seed must be an integer or 'clear'.", ephemeral=True
        )


@bot.tree.command(name="help", description="Show help for the obfuscator bot")
async def cmd_help(interaction: discord.Interaction):
    text = (
        "**Lua Strong Obfuscator**\n\n"
        "`/obfuscate <file>` — obfuscate a `.lua`, `.luau`, or `.txt` file (required)\n"
        "`/settings` — toggle Luau mode and VM mode\n"
        "`/seed <n>` — set a fixed seed\n"
        "`/seed clear` — reset to random\n"
        "`/help` — show this message\n\n"
        f"Current: {settings_summary(settings_for(interaction.user.id))}"
    )
    await interaction.response.send_message(text, ephemeral=True)


def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        logger.error("DISCORD_TOKEN is not set")
        raise SystemExit(1)
    try:
        bot.run(token)
    except Exception as e:
        logger.exception(f"Fatal: {e}")
        raise


if __name__ == "__main__":
    main()
