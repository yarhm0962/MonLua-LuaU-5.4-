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


def unescape(s):
    if "\\" not in s:
        return s
    out = []
    i, n = 0, len(s)
    simple = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
              "f": "\f", "v": "\v", "\\": "\\", "\"": "\"", "'": "'"}
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            out.append("\\")
            break
        e = s[i + 1]
        if e in simple:
            out.append(simple[e])
            i += 2
        elif e == "\n":
            i += 2
        elif e == "z":
            i += 2
            while i < n and s[i] in " \t\r\n":
                i += 1
        elif e == "x" and i + 3 < n:
            try:
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
            except ValueError:
                out.append("\\x")
                i += 2
        elif e.isdigit():
            j = i + 1
            while j < n and j < i + 4 and s[j].isdigit():
                j += 1
            try:
                out.append(chr(int(s[i + 1:j]) % 256))
                i = j
            except ValueError:
                out.append("\\")
                i += 1
        elif e == "u" and i + 2 < n and s[i + 2] == "{":
            j = s.find("}", i + 3)
            if j == -1:
                out.append("\\u")
                i += 2
            else:
                try:
                    out.append(chr(int(s[i + 3:j], 16)))
                    i = j + 1
                except ValueError:
                    out.append("\\u")
                    i += 2
        else:
            out.append("\\" + e)
            i += 2
    return "".join(out)


def lex(src):
    if src.startswith("#!"):
        j = src.find("\n")
        src = "" if j == -1 else src[j:]
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
            toks.append(Tok("string", unescape("".join(buf))))
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
        three = src[i:i + 3]
        if three == "...":
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


class Unsupported(Exception):
    pass


BIN_PREC = {"or": 1, "and": 2, "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3,
            "..": 4, "+": 5, "-": 5, "*": 6, "/": 6, "//": 6, "%": 6, "^": 8}
RIGHT_ASSOC = ("..", "^")
BLOCK_END = ("end", "else", "elseif", "until", "<eof>")


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, off=0):
        j = self.i + off
        return self.t[j] if j < len(self.t) else Tok("sym", "<eof>")

    def nxt(self):
        tk = self.peek()
        self.i += 1
        return tk

    def accept(self, val):
        if self.peek().val == val:
            self.i += 1
            return True
        return False

    def expect(self, val):
        tk = self.nxt()
        if tk.val != val:
            raise Unsupported("expected '%s', got '%s'" % (val, tk.val))
        return tk

    def ident(self):
        tk = self.nxt()
        if tk.kind != "ident":
            raise Unsupported("expected identifier, got '%s'" % tk.val)
        return tk.val

    def block(self):
        stmts = []
        while self.peek().val not in BLOCK_END:
            if self.peek().val == "<eof>":
                raise Unsupported("unexpected eof")
            s = self.statement()
            if s is not None:
                stmts.append(s)
            self.accept(";")
            if stmts and stmts[-1][0] == "return":
                break
        return stmts

    def statement(self):
        tk = self.peek()
        v = tk.val
        if tk.kind == "sym" and v == ";":
            self.nxt()
            return None
        if tk.kind != "kw":
            return self.exprstat()
        if v == "if":
            self.nxt()
            branches = []
            cond = self.exp()
            self.expect("then")
            branches.append((cond, self.block()))
            while self.accept("elseif"):
                c = self.exp()
                self.expect("then")
                branches.append((c, self.block()))
            els = None
            if self.accept("else"):
                els = self.block()
            self.expect("end")
            return ("if", branches, els)
        if v == "while":
            self.nxt()
            c = self.exp()
            self.expect("do")
            b = self.block()
            self.expect("end")
            return ("while", c, b)
        if v == "do":
            self.nxt()
            b = self.block()
            self.expect("end")
            return ("do", b)
        if v == "for":
            self.nxt()
            first = self.ident()
            if self.accept("="):
                e1 = self.exp()
                self.expect(",")
                e2 = self.exp()
                e3 = ("const", 1)
                if self.accept(","):
                    e3 = self.exp()
                self.expect("do")
                b = self.block()
                self.expect("end")
                return ("fornum", first, e1, e2, e3, b)
            names = [first]
            while self.accept(","):
                names.append(self.ident())
            self.expect("in")
            exprs = self.explist()
            self.expect("do")
            b = self.block()
            self.expect("end")
            return ("forin", names, exprs, b)
        if v == "repeat":
            self.nxt()
            b = self.block()
            self.expect("until")
            c = self.exp()
            return ("repeat", b, c)
        if v == "return":
            self.nxt()
            if self.peek().val in BLOCK_END or self.peek().val == ";":
                exprs = []
            else:
                exprs = self.explist()
            return ("return", exprs)
        if v == "break":
            self.nxt()
            return ("break",)
        if v == "local":
            self.nxt()
            if self.accept("function"):
                name = self.ident()
                params, vararg, body = self.funcbody()
                return ("localfunc", name, params, vararg, body)
            names = [self.ident()]
            while self.accept(","):
                names.append(self.ident())
            if self.peek().val == "<":
                raise Unsupported("attributes not supported")
            exprs = []
            if self.accept("="):
                exprs = self.explist()
            return ("local", names, exprs)
        if v == "function":
            self.nxt()
            base = ("var", self.ident())
            while self.accept("."):
                base = ("indexs", base, self.ident())
            if self.accept(":"):
                mname = self.ident()
                params, vararg, body = self.funcbody()
                return ("setf", base, mname, params, vararg, body, True)
            params, vararg, body = self.funcbody()
            return ("setf", base, None, params, vararg, body, False)
        if v == "goto":
            raise Unsupported("goto not supported")
        raise Unsupported("unsupported statement '%s'" % v)

    def exprstat(self):
        e = self.suffixed()
        if self.peek().val in ("=", ","):
            targets = [e]
            while self.accept(","):
                targets.append(self.suffixed())
            self.expect("=")
            rhs = self.explist()
            return ("assign", targets, rhs)
        if e[0] in ("call", "methodcall"):
            return ("callstmt", e)
        raise Unsupported("bad statement")

    def explist(self):
        exprs = [self.exp()]
        while self.accept(","):
            exprs.append(self.exp())
        return exprs

    def exp(self, minp=1):
        tk = self.peek()
        if tk.val in ("not", "-", "#") and tk.kind in ("kw", "sym"):
            self.nxt()
            node = ("un", tk.val, self.exp(7))
        else:
            node = self.primary()
        while True:
            p = BIN_PREC.get(self.peek().val)
            if not p or p < minp:
                break
            op = self.nxt().val
            rhs = self.exp(p if op in RIGHT_ASSOC else p + 1)
            if op == "and":
                node = ("and", node, rhs)
            elif op == "or":
                node = ("or", node, rhs)
            else:
                node = ("bin", op, node, rhs)
        return node

    def primary(self):
        tk = self.peek()
        v = tk.val
        if tk.kind == "number":
            self.nxt()
            try:
                if v.lower().startswith("0x"):
                    num = int(v, 16)
                elif "." in v or "e" in v.lower():
                    num = float(v)
                    if num.is_integer() and "e" not in v.lower():
                        num = int(num)
                else:
                    num = int(v)
            except ValueError:
                raise Unsupported("bad number '%s'" % v)
            return ("const", num)
        if tk.kind == "string":
            self.nxt()
            return ("const", tk.val)
        if v == "nil":
            self.nxt()
            return ("const", None)
        if v == "true":
            self.nxt()
            return ("const", True)
        if v == "false":
            self.nxt()
            return ("const", False)
        if v == "function":
            self.nxt()
            params, vararg, body = self.funcbody()
            return ("func", params, vararg, body)
        if v == "{":
            return self.tablector()
        if v == "...":
            self.nxt()
            return ("vararg",)
        return self.suffixed()

    def suffixed(self):
        if self.accept("("):
            e = self.exp()
            self.expect(")")
            base = ("paren", e)
        elif self.peek().kind == "ident":
            base = ("var", self.nxt().val)
        else:
            raise Unsupported("unexpected '%s'" % self.peek().val)
        while True:
            if self.accept("."):
                base = ("indexs", base, self.ident())
            elif self.accept("["):
                k = self.exp()
                self.expect("]")
                base = ("index", base, k)
            elif self.accept(":"):
                m = self.ident()
                base = ("methodcall", base, m, self.args())
            elif self.peek().val in ("(", "{") or self.peek().kind == "string":
                base = ("call", base, self.args())
            else:
                break
        return base

    def args(self):
        if self.accept("("):
            if self.accept(")"):
                return []
            exprs = self.explist()
            self.expect(")")
            return exprs
        if self.peek().val == "{":
            return [self.tablector()]
        if self.peek().kind == "string":
            return [("const", self.nxt().val)]
        raise Unsupported("bad call args")

    def tablector(self):
        self.expect("{")
        items = []
        while not self.accept("}"):
            if self.accept("["):
                k = self.exp()
                self.expect("]")
                self.expect("=")
                items.append(("map", k, self.exp()))
            elif self.peek().kind == "ident" and self.peek(1).val == "=":
                k = ("const", self.nxt().val)
                self.nxt()
                items.append(("map", k, self.exp()))
            else:
                items.append(("arr", self.exp()))
            if not (self.accept(",") or self.accept(";")):
                self.expect("}")
                break
        return ("table", items)

    def funcbody(self):
        self.expect("(")
        params = []
        vararg = False
        if not self.accept(")"):
            while True:
                if self.accept("..."):
                    vararg = True
                    break
                params.append(self.ident())
                if not self.accept(","):
                    break
            self.expect(")")
        body = self.block()
        self.expect("end")
        return params, vararg, body

OPCODES = ["PUSHK", "PUSHVAR", "STOREVAR", "SETGLOBAL", "POP", "DUP", "GETTABLE",
           "SETTABLE", "SELF", "NEWTABLE", "SETLIST", "SETMAP", "CALL", "CALLM",
           "VARARG", "ADJUST", "RET", "RETM", "JMP", "JF", "JT", "ADD", "SUB",
           "MUL", "DIV", "IDIV", "MOD", "POW", "CONCAT", "EQ", "NE", "LT", "LE",
           "GT", "GE", "NOT", "NEG", "LEN", "CLOSURE", "ENV_PUSH", "ENV_POP",
           "FORPREP", "FORLOOP", "FORIN_PREP", "FORIN_LOOP", "POPCTL", "MARK"]


class Fn:
    def __init__(self, params=()):
        self.code = []
        self.konst = []
        self.protos = []
        self.decls = set()
        self.env_depth = 0
        self.loops = []
        self.npar = len(params)
        for p in params:
            self.decls.add(p)
            self.konst.append(p)

    def k(self, v):
        for i, x in enumerate(self.konst):
            if type(x) is type(v) and x == v:
                return i
        self.konst.append(v)
        return len(self.konst) - 1

    def emit(self, op, a=0, b=0):
        self.code.extend([op, a, b])
        return len(self.code) - 3

    def patch(self, idx, target):
        self.code[idx + 1] = target


def c_block(fn, stmts, push_env):
    if push_env:
        fn.emit("ENV_PUSH")
        fn.env_depth += 1
    for s in stmts:
        c_stmt(fn, s)
    if push_env:
        fn.emit("ENV_POP")
        fn.env_depth -= 1


def c_stmt(fn, s):
    tag = s[0]
    if tag == "local":
        _, names, exprs = s
        for nm in names:
            fn.decls.add(nm)
        c_explist(fn, exprs, len(names))
        for nm in reversed(names):
            fn.emit("STOREVAR", fn.k(nm))
    elif tag == "localfunc":
        _, name, params, vararg, body = s
        fn.decls.add(name)
        fn.emit("CLOSURE", c_closure(fn, params, vararg, body))
        fn.emit("STOREVAR", fn.k(name))
    elif tag == "assign":
        _, targets, rhs = s
        for t in targets:
            if t[0] == "index":
                c_exp_adjust(fn, t[1])
                c_exp_adjust(fn, t[2])
            elif t[0] == "indexs":
                c_exp_adjust(fn, t[1])
                fn.emit("PUSHK", fn.k(t[2]))
            elif t[0] != "var":
                raise Unsupported("bad assign target")
        c_explist(fn, rhs, len(targets))
        for t in reversed(targets):
            if t[0] == "var":
                if t[1] in fn.decls:
                    fn.emit("STOREVAR", fn.k(t[1]))
                else:
                    fn.emit("SETGLOBAL", fn.k(t[1]))
            else:
                fn.emit("SETTABLE")
    elif tag == "callstmt":
        c_call(fn, s[1])
        fn.emit("ADJUST", 0)
    elif tag == "if":
        jends = []
        for cond, body in s[1]:
            c_exp_adjust(fn, cond)
            fn.emit("DUP")
            jf = fn.emit("JF", 0)
            fn.emit("POP")
            c_block(fn, body, True)
            jends.append(fn.emit("JMP", 0))
            fn.patch(jf, len(fn.code))
            fn.emit("POP")
        if s[2] is not None:
            c_block(fn, s[2], True)
        for j in jends:
            fn.patch(j, len(fn.code))
    elif tag == "while":
        base = fn.env_depth
        start = len(fn.code)
        c_exp_adjust(fn, s[1])
        fn.emit("DUP")
        jt = fn.emit("JT", 0)
        fn.emit("POP")
        jend = fn.emit("JMP", 0)
        fn.patch(jt, len(fn.code))
        fn.emit("POP")
        fn.loops.append([[], base, False])
        c_block(fn, s[2], True)
        fn.emit("JMP", start)
        fn.patch(jend, len(fn.code))
        for j in fn.loops[-1][0]:
            fn.patch(j, len(fn.code))
        fn.loops.pop()
    elif tag == "repeat":
        base = fn.env_depth
        fn.emit("ENV_PUSH")
        fn.env_depth += 1
        start = len(fn.code)
        fn.loops.append([[], base, False])
        for st in s[1]:
            c_stmt(fn, st)
        c_exp_adjust(fn, s[2])
        fn.emit("DUP")
        jt = fn.emit("JT", 0)
        fn.emit("POP")
        fn.emit("JMP", start)
        fn.patch(jt, len(fn.code))
        fn.emit("POP")
        fn.emit("ENV_POP")
        fn.env_depth -= 1
        for j in fn.loops[-1][0]:
            fn.patch(j, len(fn.code))
        fn.loops.pop()
    elif tag == "fornum":
        _, var, e1, e2, e3, body = s
        base = fn.env_depth
        fn.decls.add(var)
        c_exp_adjust(fn, e1)
        c_exp_adjust(fn, e2)
        c_exp_adjust(fn, e3)
        jprep = fn.emit("FORPREP", 0, fn.k(var))
        body_addr = len(fn.code)
        fn.loops.append([[], base, True])
        for st in body:
            c_stmt(fn, st)
        fn.emit("FORLOOP", body_addr, fn.k(var))
        fn.patch(jprep, len(fn.code))
        for j in fn.loops[-1][0]:
            fn.patch(j, len(fn.code))
        fn.loops.pop()
    elif tag == "forin":
        _, names, exprs, body = s
        base = fn.env_depth
        for nm in names:
            fn.decls.add(nm)
        c_explist(fn, exprs, 3)
        jprep = fn.emit("FORIN_PREP", 0)
        body_addr = len(fn.code)
        fn.loops.append([[], base, True])
        for st in body:
            c_stmt(fn, st)
        fn.emit("FORIN_LOOP", body_addr, fn.k(list(names)))
        fn.patch(jprep, len(fn.code))
        for j in fn.loops[-1][0]:
            fn.patch(j, len(fn.code))
        fn.loops.pop()
    elif tag == "break":
        if not fn.loops:
            raise Unsupported("break outside loop")
        patches, base, has_ctl = fn.loops[-1]
        for _ in range(fn.env_depth - base):
            fn.emit("ENV_POP")
        if has_ctl:
            fn.emit("POPCTL")
        patches.append(fn.emit("JMP", 0))
    elif tag == "return":
        exprs = s[1]
        if not exprs:
            fn.emit("RET", 0)
        else:
            fn.emit("MARK")
            for e in exprs[:-1]:
                c_exp_adjust(fn, e)
            if c_exp(fn, exprs[-1]):
                fn.emit("RETM")
            else:
                fn.emit("ADJUST", len(exprs))
                fn.emit("RET", len(exprs))
    elif tag == "do":
        c_block(fn, s[1], True)
    elif tag == "setf":
        _, base, mname, params, vararg, body, is_method = s
        if is_method:
            params = ["self"] + list(params)
        fexpr = ("func", params, vararg, body)
        if base[0] == "var" and mname is None:
            c_exp(fn, fexpr)
            if base[1] in fn.decls:
                fn.emit("STOREVAR", fn.k(base[1]))
            else:
                fn.emit("SETGLOBAL", fn.k(base[1]))
            return
        c_exp_adjust(fn, base[1])
        if base[0] == "indexs":
            fn.emit("PUSHK", fn.k(base[2]))
        else:
            c_exp_adjust(fn, base[2])
        if mname is not None:
            fn.emit("PUSHK", fn.k(mname))
        c_exp(fn, fexpr)
        fn.emit("SETTABLE")
    else:
        raise Unsupported("cannot compile '%s'" % tag)


def c_exp_adjust(fn, e):
    if c_exp(fn, e):
        fn.emit("ADJUST", 1)


def c_explist(fn, exprs, n):
    if not exprs:
        for _ in range(n):
            fn.emit("PUSHK", fn.k(None))
        return
    fn.emit("MARK")
    for e in exprs[:-1]:
        c_exp_adjust(fn, e)
    c_exp(fn, exprs[-1])
    fn.emit("ADJUST", n)


def c_call(fn, e):
    c_exp_adjust(fn, e[1])
    if e[0] == "call":
        args = e[2]
    else:
        fn.emit("SELF", fn.k(e[2]))
        args = e[3]
    fn.emit("MARK")
    multi = False
    if args:
        for a in args[:-1]:
            c_exp_adjust(fn, a)
        multi = c_exp(fn, args[-1])
        if not multi:
            fn.emit("ADJUST", 1)
    fn.emit("CALL" if e[0] == "call" else "CALLM", len(args), 1 if multi else 0)


def c_table(fn, items):
    fn.emit("NEWTABLE")
    run = 0
    for idx, it in enumerate(items):
        if it[0] == "arr":
            multi = c_exp(fn, it[1])
            if multi and idx == len(items) - 1:
                fn.emit("SETLIST", run, 1)
                run = 0
            else:
                if multi:
                    fn.emit("ADJUST", 1)
                run += 1
        else:
            if run:
                fn.emit("SETLIST", run, 0)
                run = 0
            c_exp_adjust(fn, it[1])
            c_exp_adjust(fn, it[2])
            fn.emit("SETMAP")
    if run:
        fn.emit("SETLIST", run, 0)


def c_closure(fn, params, vararg, body):
    sub = Fn(params)
    for st in body:
        c_stmt(sub, st)
    sub.emit("RET", 0)
    fn.protos.append(sub)
    return len(fn.protos) - 1


def c_exp(fn, e):
    tag = e[0]
    if tag == "const":
        fn.emit("PUSHK", fn.k(e[1]))
        return False
    if tag == "var":
        fn.emit("PUSHVAR", fn.k(e[1]))
        return False
    if tag == "vararg":
        fn.emit("VARARG")
        return True
    if tag in ("call", "methodcall"):
        c_call(fn, e)
        return True
    if tag == "func":
        fn.emit("CLOSURE", c_closure(fn, e[1], e[2], e[3]))
        return False
    if tag == "paren":
        c_exp_adjust(fn, e[1])
        return False
    if tag == "index":
        c_exp_adjust(fn, e[1])
        c_exp_adjust(fn, e[2])
        fn.emit("GETTABLE")
        return False
    if tag == "indexs":
        c_exp_adjust(fn, e[1])
        fn.emit("PUSHK", fn.k(e[2]))
        fn.emit("GETTABLE")
        return False
    if tag == "un":
        c_exp_adjust(fn, e[2])
        fn.emit({"not": "NOT", "-": "NEG", "#": "LEN"}[e[1]])
        return False
    if tag == "bin":
        op = e[1]
        c_exp_adjust(fn, e[2])
        c_exp_adjust(fn, e[3])
        fn.emit({"==": "EQ", "~=": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE",
                 "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "//": "IDIV",
                 "%": "MOD", "^": "POW", "..": "CONCAT"}[op])
        return False
    if tag == "and":
        c_exp_adjust(fn, e[1])
        fn.emit("DUP")
        jf = fn.emit("JF", 0)
        fn.emit("POP")
        c_exp_adjust(fn, e[2])
        jend = fn.emit("JMP", 0)
        fn.patch(jf, len(fn.code))
        fn.patch(jend, len(fn.code))
        return False
    if tag == "or":
        c_exp_adjust(fn, e[1])
        fn.emit("DUP")
        jt = fn.emit("JT", 0)
        fn.emit("POP")
        c_exp_adjust(fn, e[2])
        jend = fn.emit("JMP", 0)
        fn.patch(jt, len(fn.code))
        fn.patch(jend, len(fn.code))
        return False
    if tag == "table":
        c_table(fn, e[1])
        return False
    raise Unsupported("cannot compile expr '%s'" % tag)


def finish(fn, opmap):
    code = list(fn.code)
    for i in range(0, len(code), 3):
        code[i] = opmap[code[i]]
    return [code, fn.konst, [finish(p, opmap) for p in fn.protos], fn.npar]


def compile_vm(src, rng):
    parser = Parser(lex(src))
    stmts = parser.block()
    if parser.peek().val != "<eof>":
        raise Unsupported("trailing tokens")
    fn = Fn(())
    for st in stmts:
        c_stmt(fn, st)
    fn.emit("RET", 0)
    shuffled = list(OPCODES)
    rng.shuffle(shuffled)
    opmap = {name: i + 1 for i, name in enumerate(shuffled)}
    return finish(fn, opmap), opmap

def lua_num(v):
    r = repr(v)
    if "inf" in r or "nan" in r:
        raise Unsupported("non-finite number")
    return r


def ser(v, rng):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return lua_num(v)
    if isinstance(v, str):
        data = list(v.encode("utf-8", "surrogateescape"))
        k1 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
        k2 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
        enc = [b ^ k1[i % len(k1)] for i, b in enumerate(data)]
        enc = [b ^ k2[i % len(k2)] for i, b in enumerate(enc)]
        return '{["M"]=1,["b"]=%s,["a"]=%s,["c"]=%s}' % (
            ser(enc, rng), ser(k1, rng), ser(k2, rng))
    if isinstance(v, list):
        return "{" + ",".join(ser(x, rng) for x in v) + "}"
    raise Unsupported("cannot serialize %r" % type(v))


VM_TEMPLATE = r"""
local U = unpack or table.unpack
local GTAB = _G
local function isna(t)
    if type(t) ~= "table" then
        return false
    end
    for _k, v in next, t do
        if type(v) ~= "number" then
            return false
        end
    end
    return true
end
local xx
local function dstr(t)
    local b = t.b
    local k1 = t.a
    local k2 = t.c
    local o = {}
    for i = 1, #b do
        local c = b[i]
        c = xx(c, k1[((i - 1) % #k1) + 1])
        c = xx(c, k2[((i - 1) % #k2) + 1])
        o[i] = string.char(c)
    end
    return table.concat(o)
end
local function D2(t)
    if type(t) == "table" then
        if rawget(t, "M") == 1 and isna(rawget(t, "b")) and isna(rawget(t, "a")) and isna(rawget(t, "c")) then
            return dstr(t)
        end
        for k, v in next, t do
            t[k] = D2(v)
        end
    end
    return t
end
xx = function(a, b)
    local r, m = 0, 1
    while a > 0 or b > 0 do
        local p = a % 2
        local q = b % 2
        if p ~= q then
            r = r + m
        end
        a = (a - p) / 2
        b = (b - q) / 2
        m = m * 2
    end
    return r
end
local function newchild(p)
    return setmetatable({}, { __index = p })
end
local MARKV = {}
local run
local function mkclosure(proto, cenv)
    return function(...)
        local a = { ... }
        local an = select("#", ...)
        local e2 = newchild(cenv)
        local res, rn = run(proto, e2, a, an)
        return U(res, 1, rn)
    end
end
run = function(P, env, va, vn)
    local code = P[1]
    local konst = P[2]
    local protos = P[3]
    local npar = P[4]
    local stack = {}
    local top = 0
    local envs = { env }
    local ctls = {}
    local pc = 1
    for i = 1, npar do
        rawset(env, konst[i], va[i])
    end
    while true do
        local op = code[pc]
        if op == @@PUSHK@@ then
            top = top + 1
            stack[top] = konst[code[pc + 1]]
            pc = pc + 3
        elseif op == @@PUSHVAR@@ then
            top = top + 1
            stack[top] = env[konst[code[pc + 1]]]
            pc = pc + 3
        elseif op == @@STOREVAR@@ then
            rawset(env, konst[code[pc + 1]], stack[top])
            stack[top] = nil
            top = top - 1
            pc = pc + 3
        elseif op == @@SETGLOBAL@@ then
            rawset(GTAB, konst[code[pc + 1]], stack[top])
            stack[top] = nil
            top = top - 1
            pc = pc + 3
        elseif op == @@POP@@ then
            stack[top] = nil
            top = top - 1
            pc = pc + 3
        elseif op == @@DUP@@ then
            stack[top + 1] = stack[top]
            top = top + 1
            pc = pc + 3
        elseif op == @@GETTABLE@@ then
            local kk = stack[top]
            local tt = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = tt[kk]
            pc = pc + 3
        elseif op == @@SETTABLE@@ then
            local vv = stack[top]
            local kk = stack[top - 1]
            local tt = stack[top - 2]
            stack[top] = nil
            stack[top - 1] = nil
            stack[top - 2] = nil
            top = top - 3
            tt[kk] = vv
            pc = pc + 3
        elseif op == @@SELF@@ then
            local oo = stack[top]
            top = top + 1
            stack[top] = oo
            top = top + 1
            stack[top] = oo[konst[code[pc + 1]]]
            pc = pc + 3
        elseif op == @@NEWTABLE@@ then
            top = top + 1
            stack[top] = {}
            pc = pc + 3
        elseif op == @@SETLIST@@ then
            local nn = code[pc + 1]
            local tt
            if code[pc + 2] == 1 then
                local vals = {}
                local mm = 0
                while stack[top] ~= MARKV do
                    mm = mm + 1
                    vals[mm] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                stack[top] = nil
                top = top - 1
                tt = stack[top - nn]
                for i = top - nn + 1, top do
                    tt[#tt + 1] = stack[i]
                    stack[i] = nil
                end
                top = top - nn
                for i = mm, 1, -1 do
                    tt[#tt + 1] = vals[i]
                end
            else
                tt = stack[top - nn]
                for i = top - nn + 1, top do
                    tt[#tt + 1] = stack[i]
                    stack[i] = nil
                end
                top = top - nn
            end
            pc = pc + 3
        elseif op == @@SETMAP@@ then
            local vv = stack[top]
            local kk = stack[top - 1]
            stack[top] = nil
            stack[top - 1] = nil
            top = top - 2
            stack[top][kk] = vv
            pc = pc + 3
        elseif op == @@CALL@@ then
            local nn = code[pc + 1]
            local args = {}
            local cnt
            if code[pc + 2] == 1 then
                local vals = {}
                local mm = 0
                while stack[top] ~= MARKV do
                    mm = mm + 1
                    vals[mm] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                stack[top] = nil
                top = top - 1
                for i = nn - 1, 1, -1 do
                    args[i] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                cnt = nn - 1 + mm
                for i = mm, 1, -1 do
                    args[nn - 1 + (mm - i + 1)] = vals[i]
                end
            else
                for i = nn, 1, -1 do
                    args[i] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                cnt = nn
            end
            stack[top] = nil
            top = top - 1
            local ff = stack[top]
            stack[top] = nil
            top = top - 1
            local rr, rn = ff(U(args, 1, cnt))
            top = top + 1
            stack[top] = MARKV
            if rn then
                for i = 1, rn do
                    top = top + 1
                    stack[top] = rr[i]
                end
            end
            pc = pc + 3
        elseif op == @@CALLM@@ then
            local nn = code[pc + 1]
            local args = {}
            local cnt
            if code[pc + 2] == 1 then
                local vals = {}
                local mm = 0
                while stack[top] ~= MARKV do
                    mm = mm + 1
                    vals[mm] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                stack[top] = nil
                top = top - 1
                for i = nn - 1, 1, -1 do
                    args[i] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                cnt = nn - 1 + mm
                for i = mm, 1, -1 do
                    args[nn - 1 + (mm - i + 1)] = vals[i]
                end
            else
                for i = nn, 1, -1 do
                    args[i] = stack[top]
                    stack[top] = nil
                    top = top - 1
                end
                cnt = nn
            end
            stack[top] = nil
            top = top - 1
            local mm2 = stack[top]
            stack[top] = nil
            top = top - 1
            local oo2 = stack[top]
            stack[top] = nil
            top = top - 1
            local rr, rn = mm2(oo2, U(args, 1, cnt))
            top = top + 1
            stack[top] = MARKV
            if rn then
                for i = 1, rn do
                    top = top + 1
                    stack[top] = rr[i]
                end
            end
            pc = pc + 3
        elseif op == @@VARARG@@ then
            top = top + 1
            stack[top] = MARKV
            for i = 1, vn do
                top = top + 1
                stack[top] = va[i]
            end
            pc = pc + 3
        elseif op == @@ADJUST@@ then
            local nn = code[pc + 1]
            local i = top
            while stack[i] ~= MARKV do
                i = i - 1
            end
            local mm = top - i
            if mm == nn then
                stack[i] = nil
                top = i + nn
            else
                local vals = {}
                for j = 1, mm do
                    vals[j] = stack[i + j]
                end
                for j = i, top do
                    stack[j] = nil
                end
                top = i - 1
                for j = 1, nn do
                    top = top + 1
                    stack[top] = vals[j]
                end
            end
            pc = pc + 3
        elseif op == @@RET@@ then
            local nn = code[pc + 1]
            local res = {}
            for i = nn, 1, -1 do
                res[i] = stack[top]
                stack[top] = nil
                top = top - 1
            end
            return res, nn
        elseif op == @@RETM@@ then
            local i = top
            while stack[i] ~= MARKV do
                i = i - 1
            end
            local mm = top - i
            local res = {}
            for j = 1, mm do
                res[j] = stack[i + j]
            end
            return res, mm
        elseif op == @@JMP@@ then
            pc = code[pc + 1] + 1
        elseif op == @@JF@@ then
            local vv = stack[top]
            stack[top] = nil
            top = top - 1
            if vv == nil or vv == false then
                pc = code[pc + 1] + 1
            else
                pc = pc + 3
            end
        elseif op == @@JT@@ then
            local vv = stack[top]
            stack[top] = nil
            top = top - 1
            if vv ~= nil and vv ~= false then
                pc = code[pc + 1] + 1
            else
                pc = pc + 3
            end
        elseif op == @@ADD@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa + bb
            pc = pc + 3
        elseif op == @@SUB@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa - bb
            pc = pc + 3
        elseif op == @@MUL@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa * bb
            pc = pc + 3
        elseif op == @@DIV@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa / bb
            pc = pc + 3
        elseif op == @@IDIV@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa // bb
            pc = pc + 3
        elseif op == @@MOD@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa % bb
            pc = pc + 3
        elseif op == @@POW@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa ^ bb
            pc = pc + 3
        elseif op == @@CONCAT@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa .. bb
            pc = pc + 3
        elseif op == @@EQ@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa == bb
            pc = pc + 3
        elseif op == @@NE@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa ~= bb
            pc = pc + 3
        elseif op == @@LT@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa < bb
            pc = pc + 3
        elseif op == @@LE@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = aa <= bb
            pc = pc + 3
        elseif op == @@GT@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = bb < aa
            pc = pc + 3
        elseif op == @@GE@@ then
            local bb = stack[top]
            local aa = stack[top - 1]
            stack[top] = nil
            top = top - 1
            stack[top] = bb <= aa
            pc = pc + 3
        elseif op == @@NOT@@ then
            stack[top] = not stack[top]
            pc = pc + 3
        elseif op == @@NEG@@ then
            local vv = stack[top]
            stack[top] = nil
            stack[top] = -vv
            pc = pc + 3
        elseif op == @@LEN@@ then
            local vv = stack[top]
            stack[top] = nil
            stack[top] = #vv
            pc = pc + 3
        elseif op == @@CLOSURE@@ then
            top = top + 1
            stack[top] = mkclosure(protos[code[pc + 1]], env)
            pc = pc + 3
        elseif op == @@ENV_PUSH@@ then
            envs[#envs + 1] = env
            env = newchild(env)
            pc = pc + 3
        elseif op == @@ENV_POP@@ then
            env = envs[#envs]
            envs[#envs] = nil
            pc = pc + 3
        elseif op == @@FORPREP@@ then
            local step = stack[top]
            local lim = stack[top - 1]
            local init = stack[top - 2]
            stack[top] = nil
            stack[top - 1] = nil
            stack[top - 2] = nil
            top = top - 3
            if step == 0 then
                error("'for' step is zero")
            end
            if (step > 0 and init <= lim) or (step < 0 and init >= lim) then
                ctls[#ctls + 1] = { i = init, l = lim, s = step }
                envs[#envs + 1] = env
                env = newchild(env)
                rawset(env, konst[code[pc + 2]], init)
                pc = pc + 3
            else
                pc = code[pc + 1] + 1
            end
        elseif op == @@FORLOOP@@ then
            local ctl = ctls[#ctls]
            local ni = ctl.i + ctl.s
            if (ctl.s > 0 and ni <= ctl.l) or (ctl.s < 0 and ni >= ctl.l) then
                ctl.i = ni
                env = envs[#envs]
                envs[#envs] = nil
                envs[#envs + 1] = env
                env = newchild(env)
                rawset(env, konst[code[pc + 2]], ni)
                pc = code[pc + 1] + 1
            else
                env = envs[#envs]
                envs[#envs] = nil
                ctls[#ctls] = nil
                pc = pc + 3
            end
        elseif op == @@FORIN_PREP@@ then
            local vv = stack[top]
            local ss = stack[top - 1]
            local ff = stack[top - 2]
            stack[top] = nil
            stack[top - 1] = nil
            stack[top - 2] = nil
            top = top - 3
            ctls[#ctls + 1] = { f = ff, s = ss, var = vv }
            pc = pc + 3
        elseif op == @@FORIN_LOOP@@ then
            local ctl = ctls[#ctls]
            local rr = { ctl.f(ctl.s, ctl.var) }
            if rr[1] == nil then
                ctls[#ctls] = nil
                pc = pc + 3
            else
                ctl.var = rr[1]
                envs[#envs + 1] = env
                env = newchild(env)
                local names = konst[code[pc + 2]]
                for i = 1, #names do
                    rawset(env, names[i], rr[i])
                end
                pc = code[pc + 1] + 1
            end
        elseif op == @@POPCTL@@ then
            ctls[#ctls] = nil
            pc = pc + 3
        elseif op == @@MARK@@ then
            top = top + 1
            stack[top] = MARKV
            pc = pc + 3
        else
            error("vm fault")
        end
    end
end
"""


def vm_build(src, rng):
    program, opmap = compile_vm(src, rng)
    chunk = VM_TEMPLATE
    for name, oid in opmap.items():
        chunk = chunk.replace("@@%s@@" % name, str(oid))
    chunk = chunk + "\nlocal P = D2(" + ser(program, rng) + ")\n"
    chunk = chunk + "return run(P, newchild(GTAB), { ... }, select('#', ...))\n"
    parser = Parser(lex(chunk))
    parser.block()
    if parser.peek().val != "<eof>":
        raise Unsupported("template parse failed")
    return source_obfuscate(chunk, rng, junk=0, num_obf=False)


def rnd_name(rng, used):
    glyphs = "Il1j0Oo_"
    while True:
        name = "_" + "".join(
            rng.choice(glyphs) if rng.random() < 0.4 else rng.choice("abcdefghklmnprstuvwxyz")
            for _ in range(rng.randint(6, 14)))
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


SRC_DECODERS = [
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


def lua_table_str(vals):
    return "{" + ",".join(str(v) for v in vals) + "}"


def enc_string(s, rng, dec):
    if s == "":
        return "\"\""
    data = list(s.encode("utf-8", "surrogateescape"))
    k1 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    k2 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    enc = [b ^ k1[i % len(k1)] for i, b in enumerate(data)]
    enc = [b ^ k2[i % len(k2)] for i, b in enumerate(enc)]
    return "%s(%s,%s,%s)" % (dec, lua_table_str(enc), lua_table_str(k1), lua_table_str(k2))


def fake_string(rng, dec):
    data = [rng.randrange(1, 256) for _ in range(rng.randint(8, 40))]
    k1 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    k2 = [rng.randrange(1, 256) for _ in range(rng.randint(4, 10))]
    return "%s(%s,%s,%s)" % (dec, lua_table_str(data), lua_table_str(k1), lua_table_str(k2))


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


def junk_stmt(rng, names, dec):
    n = rng.choice(names)
    kind = rng.randrange(4)
    if kind == 0:
        return "do local %s={} if (function()return #%s end)()>%d then return end end " % (
            n, n, rng.randrange(1, 10 ** 6))
    if kind == 1:
        return "if (function() return #(\"x\")==0 end)() then while true do end end "
    if kind == 2:
        return "local %s=%s " % (n, fake_string(rng, dec))
    return "for %s=%d,%d do end " % (n, rng.randrange(500, 2000), rng.randrange(1, 500))


def needs_space(a, b):
    if a is None:
        return False
    if a.endswith("-"):
        if b.startswith("-"):
            return True
    if a.endswith("."):
        return b.startswith(".") or b[0].isdigit()
    if a[-1].isalnum() or a[-1] in "_'\"":
        return b[0].isalnum() or b[0] in "_'\"[{"
    if a[-1] in ")]}":
        return b[0].isalnum() or b[0] in "_'\"[{"
    return False


def source_obfuscate(src, rng, junk=10, num_obf=True):
    toks = lex(src)
    used = set(PROTECTED)
    renames = {old: rnd_name(rng, used) for old in sorted(collect_locals(toks))}
    jnames = [rnd_name(rng, used) for _ in range(4)]
    dec = rnd_name(rng, used)
    main = rnd_name(rng, used)

    parts = [rng.choice(SRC_DECODERS) % dec]

    for t in toks:
        v = t.val
        if t.kind == "string":
            parts.append(enc_string(v, rng, dec))
        elif t.kind == "number":
            parts.append(obf_num(v, rng) if num_obf else v)
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


def obfuscate(src, seed=None):
    rng = random.Random(seed)
    try:
        return vm_build(src, rng)
    except Exception:
        return source_obfuscate(src, rng)


TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable is not set")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print("logged in as %s" % bot.user, flush=True)


@bot.tree.command(name="obfuscate", description="Strongly obfuscate a Lua/Luau script (VM-based)")
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
