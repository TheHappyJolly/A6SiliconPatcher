"""HLE implementations of imported libc / libstdc++ / system functions."""
import struct, sys, math, time, os, random, ctypes
from .ucshim import *


def f32(x): return struct.unpack("<f", struct.pack("<I", x & 0xFFFFFFFF))[0]
def pf32(x): return struct.unpack("<I", struct.pack("<f", x))[0]


def push_st0(uc, val):
    """Return a float/double to the guest in ST0 (the trampoline loads it)."""
    import ctypes
    e = uc.e
    ctypes.c_double.from_address(e._ctx + 0x30).value = float(val)
    ctypes.c_uint32.from_address(e._ctx + 0x38).value |= 1


def install(e):
    h = e.hle
    u = e.uc

    def dbl(n):  # n-th stack arg as double (takes 2 slots at n)
        return struct.unpack("<d", e.rd(u.reg_read(UC_X86_REG_ESP) + 4 + 4 * n, 8))[0]

    def flt(n):
        return f32(e.arg(n))

    # ------------------------------------------------------------------ heap
    heap = {"ptr": 0x20000000, "free": {}}
    PY_LIMIT = 0x30000000                    # the python-side arena ends where the guest malloc arena begins
    MAGIC = 0x50594D41                       # "PYMA": block header = [size u32 @a-8][magic u32 @a-4]; no dict of all blocks

    import threading as _th
    alock = _th.Lock()

    _mstat = {} if __import__('os').environ.get('A6_MALLOCSTATS') else None
    e.malloc_stats = _mstat
    _u32 = ctypes.c_uint32
    _site = [None]; _live = {}
    e.malloc_live = _live
    def malloc(n):
        n = max(16, (n + 15) & ~15)
        if _mstat is not None:
            import sys as _s
            fr = _s._getframe(1); k = (fr.f_code.co_filename.split('/')[-1], fr.f_lineno, fr.f_code.co_name)
            a_ = _mstat.setdefault(k, [0, 0]); a_[0] += 1; a_[1] += n; _site[0] = k
        with alock:
            lst = heap["free"].get(n)
            if lst:
                a = lst.pop()
            else:
                a = heap["ptr"] + 16
                if a + n > PY_LIMIT:
                    print("[!] python-side arena exhausted", file=sys.stderr); return 0
                heap["ptr"] = a + n
            _u32.from_address(a - 8).value = n; _u32.from_address(a - 4).value = MAGIC
            if _mstat is not None: _live[a] = (_site[0], n)
        return a

    def block_size(a):
        if 0x20000010 <= a < PY_LIMIT and _u32.from_address(a - 4).value == MAGIC:
            return _u32.from_address(a - 8).value
        return None

    def free(a):
        if a:
            with alock:
                n = block_size(a)
                if n is not None:
                    _u32.from_address(a - 4).value = 0                # double free / foreign pointer guard
                    _live.pop(a, None)
                    heap["free"].setdefault(n, []).append(a)

    e.malloc, e.free = malloc, free
    e.pyheap = heap
    e.py_block_size = block_size
    h["__py_free"] = lambda e_: free(e_.arg(0))      # guest free() of a python-side block lands here (see guestlib/libc.c)
    h["_malloc"] = lambda e: malloc(e.arg(0))
    h["__Znwm"] = h["__Znam"] = lambda e: malloc(e.arg(0))
    h["__ZnamRKSt9nothrow_t"] = lambda e: malloc(e.arg(0))
    h["_free"] = lambda e: free(e.arg(0))
    h["__ZdlPv"] = h["__ZdaPv"] = lambda e: free(e.arg(0))

    def calloc(e):
        n = e.arg(0) * e.arg(1)
        a = malloc(n); e.wr(a, bytes(n)); return a
    h["_calloc"] = calloc

    def realloc(e):
        a, n = e.arg(0), e.arg(1)
        if not a: return malloc(n)
        old = block_size(a) or 0
        b = malloc(n)
        e.wr(b, e.rd(a, min(old, n)))
        free(a)
        return b
    h["_realloc"] = realloc
    h["__call_return"] = lambda e: None

    # ------------------------------------------------------------ mem/string
    h["_memcpy"] = h["_memmove"] = h["___memcpy_chk"] = lambda e: (e.wr(e.arg(0), e.rd(e.arg(1), e.arg(2))), e.arg(0))[1]
    h["_memset"] = lambda e: (e.wr(e.arg(0), bytes([e.arg(1) & 0xFF]) * e.arg(2)), e.arg(0))[1]
    h["_bzero"] = lambda e: e.wr(e.arg(0), bytes(e.arg(1)))

    def memcmp(e):
        a, b = e.rd(e.arg(0), e.arg(2)), e.rd(e.arg(1), e.arg(2))
        return (a > b) - (a < b)
    h["_memcmp"] = memcmp

    def memchr(e):
        s, c, n = e.arg(0), e.arg(1) & 0xFF, e.arg(2)
        i = e.rd(s, n).find(bytes([c]))
        return 0 if i < 0 else s + i
    h["_memchr"] = memchr
    h["_strlen"] = lambda e: len(e.cstr(e.arg(0)))

    def strcpy(e):
        d, s = e.arg(0), e.arg(1)
        e.wr(d, e.cstr(s) + b"\0"); return d
    h["_strcpy"] = h["___strcpy_chk"] = strcpy

    def strncpy(e):
        d, s, n = e.arg(0), e.arg(1), e.arg(2)
        b = e.cstr(s, n)[:n]
        e.wr(d, b + bytes(n - len(b))); return d
    h["_strncpy"] = strncpy

    def strcat(e):
        d, s = e.arg(0), e.arg(1)
        e.wr(d + len(e.cstr(d)), e.cstr(s) + b"\0"); return d
    h["_strcat"] = strcat

    def strncat(e):
        d, s, n = e.arg(0), e.arg(1), e.arg(2)
        e.wr(d + len(e.cstr(d)), e.cstr(s)[:n] + b"\0"); return d
    h["_strncat"] = strncat

    def cmp(a, b): return (a > b) - (a < b)
    h["_strcmp"] = lambda e: cmp(e.cstr(e.arg(0)), e.cstr(e.arg(1)))
    h["_strncmp"] = lambda e: cmp(e.cstr(e.arg(0))[:e.arg(2)], e.cstr(e.arg(1))[:e.arg(2)])
    h["_strcasecmp"] = lambda e: cmp(e.cstr(e.arg(0)).lower(), e.cstr(e.arg(1)).lower())

    def strchr(e):
        s, c = e.arg(0), e.arg(1) & 0xFF
        b = e.cstr(s) + b"\0"
        i = b.find(bytes([c]))
        return 0 if i < 0 else s + i
    h["_strchr"] = strchr

    def strrchr(e):
        s, c = e.arg(0), e.arg(1) & 0xFF
        b = e.cstr(s) + b"\0"
        i = b.rfind(bytes([c]))
        return 0 if i < 0 else s + i
    h["_strrchr"] = strrchr

    def strstr(e):
        s = e.arg(0); i = e.cstr(s).find(e.cstr(e.arg(1)))
        return 0 if i < 0 else s + i
    h["_strstr"] = strstr

    tokstate = {"p": 0}
    def strtok(e):
        s, delim = e.arg(0), e.cstr(e.arg(1))
        p = s or tokstate["p"]
        if not p: return 0
        while e.rd(p, 1)[0] and e.rd(p, 1) in delim: p += 1
        if not e.rd(p, 1)[0]:
            tokstate["p"] = 0; return 0
        start = p
        while e.rd(p, 1)[0] and e.rd(p, 1) not in delim: p += 1
        if e.rd(p, 1)[0]:
            e.wr(p, b"\0"); tokstate["p"] = p + 1
        else:
            tokstate["p"] = 0
        return start
    h["_strtok"] = strtok
    h["___tolower"] = lambda e: ord(chr(e.arg(0) & 0xFF).lower()) if e.arg(0) < 256 else e.arg(0)
    h["___toupper"] = lambda e: ord(chr(e.arg(0) & 0xFF).upper()) if e.arg(0) < 256 else e.arg(0)

    def parse_num(s, base=10, flt=False):
        import re
        if flt:
            m = re.match(rb"\s*[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?|inf|nan)", s, re.I)
            return (float(m.group(0)) if m else 0.0), (m.end() if m else 0)
        m = re.match(rb"\s*[-+]?(0[xX][0-9a-fA-F]+|\d+)" if base in (0, 10, 16) else rb"\s*[-+]?\w+", s)
        if not m: return 0, 0
        try: return int(m.group(0), base), m.end()
        except ValueError: return 0, 0
    h["_atoi"] = h["_atol"] = lambda e: parse_num(e.cstr(e.arg(0)))[0] & 0xFFFFFFFF
    def strtol(e):
        v, n = parse_num(e.cstr(e.arg(0)), e.arg(2) or 10)
        if e.arg(1): e.w32(e.arg(1), e.arg(0) + n)
        return v & 0xFFFFFFFF
    h["_strtol"] = strtol
    def retd(e, v):
        push_st0(u, v)
    def atof(e):
        retd(e, parse_num(e.cstr(e.arg(0)), flt=True)[0])
    h["_atof"] = atof
    def strtod(e):
        v, n = parse_num(e.cstr(e.arg(0)), flt=True)
        if e.arg(1): e.w32(e.arg(1), e.arg(0) + n)
        retd(e, v)
    h["_strtod$UNIX2003"] = strtod

    # wide chars (4 bytes on darwin)
    def wcs(a):
        out = []
        while True:
            c = e.u32(a + 4 * len(out))
            if c == 0: break
            out.append(c)
        return out
    h["_wcslen"] = lambda e: len(wcs(e.arg(0)))
    h["_wcscpy"] = lambda e: (e.wr(e.arg(0), struct.pack("<%dI" % (len(wcs(e.arg(1))) + 1), *(wcs(e.arg(1)) + [0]))), e.arg(0))[1]
    h["_wcscmp"] = lambda e: cmp(wcs(e.arg(0)), wcs(e.arg(1)))
    h["_wmemcpy"] = h["_wmemmove"] = lambda e: (e.wr(e.arg(0), e.rd(e.arg(1), 4 * e.arg(2))), e.arg(0))[1]
    h["_wmemcmp"] = lambda e: cmp(e.rd(e.arg(0), 4 * e.arg(2)), e.rd(e.arg(1), 4 * e.arg(2)))
    def wmemchr(e):
        s, c, n = e.arg(0), e.arg(1), e.arg(2)
        for i in range(n):
            if e.u32(s + 4 * i) == c: return s + 4 * i
        return 0
    h["_wmemchr"] = wmemchr

    # ------------------------------------------------------------------ math
    def m1f(name, fn):
        def f(e):
            try: v = fn(flt(0))
            except (ValueError, OverflowError): v = float("nan")
            retd(e, v)
        h["_" + name] = f
    def m1d(name, fn):
        def f(e):
            try: v = fn(dbl(0))
            except (ValueError, OverflowError): v = float("nan")
            retd(e, v)
        h["_" + name] = f
    for n, fn in dict(sin=math.sin, cos=math.cos, tan=math.tan, acos=math.acos, asin=math.asin,
                      atan=math.atan, exp=math.exp, log=math.log, log10=math.log10, log2=math.log2,
                      ceil=math.ceil, floor=math.floor).items():
        m1d(n, fn)
        m1f(n + "f", fn)
    def safe_pow(a, b):
        try: return math.pow(a, b)
        except (ValueError, OverflowError, ZeroDivisionError): return float("nan")
    h["_pow"] = lambda e: retd(e, safe_pow(dbl(0), dbl(2)))
    h["_atan2"] = lambda e: retd(e, math.atan2(dbl(0), dbl(2)))
    h["_atan2f"] = lambda e: retd(e, math.atan2(flt(0), flt(1)))
    h["_fmod"] = lambda e: retd(e, math.fmod(dbl(0), dbl(2)) if dbl(2) else float("nan"))
    h["_fmodf"] = lambda e: retd(e, math.fmod(flt(0), flt(1)) if flt(1) else float("nan"))
    h["_fmax"] = lambda e: retd(e, max(dbl(0), dbl(2)))
    h["_fmin"] = lambda e: retd(e, min(dbl(0), dbl(2)))
    h["_ldexp"] = lambda e: retd(e, math.ldexp(dbl(0), struct.unpack("<i", struct.pack("<I", e.arg(2)))[0]))
    h["_lrint"] = lambda e: int(round(dbl(0))) & 0xFFFFFFFF
    h["___udivdi3"] = lambda e: None  # handled below with edx:eax
    def udiv64(e, mod):
        a = e.arg(0) | (e.arg(1) << 32); b = e.arg(2) | (e.arg(3) << 32)
        r = (a % b) if mod else (a // b)
        u.reg_write(UC_X86_REG_EDX, r >> 32)
        return r & 0xFFFFFFFF
    h["___udivdi3"] = lambda e: udiv64(e, False)
    h["___umoddi3"] = lambda e: udiv64(e, True)
    rng = random.Random(1)
    h["_rand"] = lambda e: rng.randrange(0x7FFFFFFF)
    h["_srand"] = lambda e: rng.seed(e.arg(0))

    def div(e):
        a, b = struct.unpack("<ii", struct.pack("<II", e.arg(0), e.arg(1)))
        q = int(a / b); r = a - q * b
        u.reg_write(UC_X86_REG_EDX, r & 0xFFFFFFFF)
        return q
    h["_div"] = div

    # --------------------------------------------------------------- printf
    def fmt(e, f, getarg, getdbl):
        out = bytearray()
        i = 0
        while i < len(f):
            c = f[i]
            if c != 37:
                out.append(c); i += 1; continue
            i += 1
            spec = b"%"
            while i < len(f) and chr(f[i]) in "-+ #0123456789.*hlLqjzt":
                if f[i] == 42:
                    spec += str(struct.unpack("<i", struct.pack("<I", getarg()))[0]).encode()
                else:
                    spec += bytes([f[i]])
                i += 1
            conv = chr(f[i]); i += 1
            mods = spec.replace(b"l", b"").replace(b"h", b"").replace(b"L", b"").replace(b"q", b"").replace(b"j", b"").replace(b"z", b"").replace(b"t", b"")
            longlong = spec.count(b"l") >= 2 or b"q" in spec or b"j" in spec
            s = mods.decode()
            if conv == "%":
                out += b"%"
            elif conv in "di":
                v = getarg()
                if longlong: v |= getarg() << 32; v = struct.unpack("<q", struct.pack("<Q", v))[0]
                else: v = struct.unpack("<i", struct.pack("<I", v))[0]
                if b"hh" in spec: v = ((v + 128) & 0xFF) - 128
                elif b"h" in spec: v = ((v + 32768) & 0xFFFF) - 32768
                out += ((s + "d") % v).encode()
            elif conv in "uxXo":
                v = getarg()
                if longlong: v |= getarg() << 32
                if b"hh" in spec: v &= 0xFF
                elif b"h" in spec: v &= 0xFFFF
                out += ((s + ("d" if conv == "u" else conv)) % v).encode()
            elif conv in "fFeEgGaA":
                v = getdbl()
                out += ((s + conv) % v).encode()
            elif conv == "c":
                out += ((s + "c") % chr(getarg() & 0xFF)).encode()
            elif conv == "s":
                p = getarg()
                b = e.cstr(p) if p else b"(null)"
                out += ((s + "s") % b.decode("latin-1")).encode("latin-1")
            elif conv == "@":
                out += e.rt.describe(getarg()).encode("utf-8")
            elif conv == "p":
                out += b"0x%x" % getarg()
            elif conv == "n":
                getarg()
        return bytes(out)

    e.fmt = fmt

    def stack_args(e, first):
        st = {"i": first}
        def ga():
            v = e.arg(st["i"]); st["i"] += 1; return v
        def gd():
            v = struct.unpack("<d", e.rd(u.reg_read(UC_X86_REG_ESP) + 4 + 4 * st["i"], 8))[0]; st["i"] += 2; return v
        return ga, gd

    def va_args(e, va):
        st = {"p": va}
        def ga():
            v = e.u32(st["p"]); st["p"] += 4; return v
        def gd():
            v = struct.unpack("<d", e.rd(st["p"], 8))[0]; st["p"] += 8; return v
        return ga, gd

    def snprintf(e, buf, n, f, ga, gd):
        s = fmt(e, e.cstr(f), ga, gd)
        if n:
            w = s[:n - 1] + b"\0"
            e.wr(buf, w)
        return len(s)

    h["_sprintf"] = lambda e: snprintf(e, e.arg(0), 1 << 30, e.arg(1), *stack_args(e, 2))
    h["___sprintf_chk"] = lambda e: snprintf(e, e.arg(0), 1 << 30, e.arg(3), *stack_args(e, 4))
    h["_snprintf"] = lambda e: snprintf(e, e.arg(0), e.arg(1), e.arg(2), *stack_args(e, 3))
    h["_vsprintf"] = lambda e: snprintf(e, e.arg(0), 1 << 30, e.arg(1), *va_args(e, e.arg(2)))
    h["_vsnprintf"] = lambda e: snprintf(e, e.arg(0), e.arg(1), e.arg(2), *va_args(e, e.arg(3)))

    stdout_w = lambda b: (sys.stdout.write(b.decode("latin-1")), sys.stdout.flush())
    def printf(e):
        s = fmt(e, e.cstr(e.arg(0)), *stack_args(e, 1)); stdout_w(s); return len(s)
    h["_printf"] = printf
    def vprintf(e):
        s = fmt(e, e.cstr(e.arg(0)), *va_args(e, e.arg(1))); stdout_w(s); return len(s)
    h["_vprintf"] = vprintf
    h["_puts"] = lambda e: (stdout_w(e.cstr(e.arg(0)) + b"\n"), 1)[1]
    h["_putchar"] = lambda e: (stdout_w(bytes([e.arg(0) & 0xFF])), e.arg(0))[1]
    h["_perror"] = lambda e: sys.stderr.write(e.cstr(e.arg(0)).decode("latin-1") + "\n") and None

    def sscanf(e):
        # minimal: %d %f %s %x %lf
        import re
        s = e.cstr(e.arg(0)).decode("latin-1"); f = e.cstr(e.arg(1)).decode("latin-1")
        ga, _ = stack_args(e, 2)
        pos = 0; n = 0; i = 0
        while i < len(f):
            c = f[i]
            if c.isspace():
                while pos < len(s) and s[pos].isspace(): pos += 1
                i += 1; continue
            if c != "%":
                if pos < len(s) and s[pos] == c: pos += 1; i += 1; continue
                break
            i += 1
            while i < len(f) and f[i] in "0123456789": i += 1
            lng = False
            while f[i] in "hl": lng = lng or f[i] == "l"; i += 1
            conv = f[i]; i += 1
            while pos < len(s) and s[pos].isspace() and conv != "c": pos += 1
            if conv in "di":
                m = re.match(r"[-+]?\d+", s[pos:])
                if not m: break
                e.w32(ga(), int(m.group(0))); pos += m.end(); n += 1
            elif conv in "ux":
                m = re.match(r"(0[xX])?[0-9a-fA-F]+" if conv == "x" else r"\d+", s[pos:])
                if not m: break
                e.w32(ga(), int(m.group(0), 16 if conv == "x" else 10)); pos += m.end(); n += 1
            elif conv in "feg":
                m = re.match(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", s[pos:])
                if not m: break
                p = ga(); v = float(m.group(0))
                e.wr(p, struct.pack("<d" if lng else "<f", v)); pos += m.end(); n += 1
            elif conv == "s":
                m = re.match(r"\S+", s[pos:])
                if not m: break
                e.wr(ga(), m.group(0).encode("latin-1") + b"\0"); pos += m.end(); n += 1
            elif conv == "c":
                if pos >= len(s): break
                e.wr(ga(), s[pos].encode("latin-1")); pos += 1; n += 1
            else:
                break
        return n
    h["_sscanf"] = sscanf

    # --------------------------------------------------------------- stdio
    # FILE* handles are small ints in a guest-allocated block so they look like pointers
    files = {}
    stdout_p, stderr_p = e.alloc_static(64), e.alloc_static(64)
    files[stdout_p] = sys.stdout.buffer
    files[stderr_p] = sys.stderr.buffer
    sp = e.alloc_static(4); e.w32(sp, stdout_p)
    se = e.alloc_static(4); e.w32(se, stderr_p)
    e.hle_data["___stdoutp"] = sp
    e.hle_data["___stderrp"] = se
    e.hle_data["___stack_chk_guard"] = e.alloc_static(4)
    e.errno_addr = e.alloc_static(4)
    h["___error"] = lambda e: e.errno_addr
    h["___stack_chk_fail"] = lambda e: (_ for _ in ()).throw(RuntimeError("stack smash"))

    tmp_of = {}
    def _backup_save(path):
        """keep a rolling history of the save files (at most one copy per 5 minutes, newest 30 kept)"""
        try:
            import shutil, time as _t, glob
            if not os.path.exists(path): return
            d = os.path.join(os.path.dirname(path), "Backups"); os.makedirs(d, exist_ok=True)
            base = os.path.basename(path)
            old = sorted(glob.glob(os.path.join(d, base + ".*")))
            if old and _t.time() - os.path.getmtime(old[-1]) < 300: return
            shutil.copy2(path, os.path.join(d, base + _t.strftime(".%Y%m%d-%H%M%S")))
            for f in old[:-29]: os.remove(f)
        except Exception:
            pass
    def fopen(e):
        path = e.cstr(e.arg(0)).decode(); mode = e.cstr(e.arg(1)).decode().replace("b", "")
        path = e.map_path(path)
        _ov = os.environ.get("A6_DATA_OVERRIDE")
        if _ov and mode.startswith("r") and os.path.exists(os.path.join(_ov, os.path.basename(path))): path = os.path.join(_ov, os.path.basename(path))
        if getattr(e, "dbg", False): print("[fopen]", path, mode, file=sys.stderr)
        real = path
        if mode.startswith("w") and path.endswith((".sav", ".bak")):
            _backup_save(path)
            path = path + ".tmp"            # written completely, then renamed over the old file: a crash never leaves a torn save
        try:
            f = open(path, mode if "b" in mode else mode + "b")
        except OSError as ex:
            e.w32(e.errno_addr, ex.errno or 2); return 0
        p = e.alloc_static(64)
        files[p] = f
        if path != real: tmp_of[p] = (path, real)
        return p
    e.map_path = lambda p: p
    h["_fopen$UNIX2003"] = fopen
    def fclose(e):
        p = e.arg(0)
        files.pop(p).close()
        t = tmp_of.pop(p, None)
        if t:
            try: os.replace(t[0], t[1])
            except OSError: pass
        return 0
    h["_fclose"] = fclose
    def fread(e):
        buf, sz, n, fp = e.arg(0), e.arg(1), e.arg(2), e.arg(3)
        _off = files[fp].tell() if getattr(e, "freadlog", None) is not None else 0
        d = files[fp].read(sz * n); e.wr(buf, d)
        if getattr(e, "freadlog", None) is not None and len(d) >= 64:
            e.freadlog.append((files[fp].name, buf, len(d), _off))
            if os.environ.get("A6_FREADLOG") == "full":
                os.makedirs("/tmp/fr", exist_ok=True)
                open(f"/tmp/fr/{os.path.basename(files[fp].name)}_{_off}_{len(d)}.bin", "wb").write(d)
        if getattr(e, "dbg", False): print(f"[fread] fp={fp:#x} want={sz*n} got={len(d)}", file=sys.stderr)
        return len(d) // sz if sz else 0
    h["_fread"] = fread
    def fwrite(e):
        buf, sz, n, fp = e.arg(0), e.arg(1), e.arg(2), e.arg(3)
        files[fp].write(e.rd(buf, sz * n)); return n
    h["_fwrite$UNIX2003"] = fwrite
    h["_fputs$UNIX2003"] = lambda e: (files[e.arg(1)].write(e.cstr(e.arg(0))), 1)[1]
    def fprintf(e):
        s = fmt(e, e.cstr(e.arg(1)), *stack_args(e, 2)); files[e.arg(0)].write(s); return len(s)
    h["_fprintf"] = fprintf
    def fseek(e):
        off = struct.unpack("<i", struct.pack("<I", e.arg(1)))[0]
        files[e.arg(0)].seek(off, e.arg(2))
        if getattr(e, "dbg", False): print(f"[fseek] fp={e.arg(0):#x} off={off} whence={e.arg(2)}", file=sys.stderr)
        return 0
    h["_fseek"] = fseek
    def ftell(e):
        v = files[e.arg(0)].tell()
        if getattr(e, "dbg", False): print(f"[ftell] fp={e.arg(0):#x} -> {v}", file=sys.stderr)
        return v
    h["_ftell"] = ftell
    h["_fflush"] = lambda e: (files[e.arg(0)].flush() if e.arg(0) in files else None, 0)[1]
    def feof(e):
        f = files[e.arg(0)]; p = f.tell(); c = f.read(1)
        if c: f.seek(p); return 0
        return 1
    h["_feof"] = feof
    h["_clearerr"] = lambda e: None
    h["_fileno"] = lambda e: files[e.arg(0)].fileno()

    # ---------------------------------------------------------- process/env
    class Exit(Exception): pass
    def exit_(e):
        raise SystemExit(e.arg(0))
    h["_exit"] = exit_
    def abort(e):
        raise RuntimeError("guest abort()")
    h["_abort"] = abort
    h["___assert_rtn"] = lambda e: (_ for _ in ()).throw(RuntimeError("assert %s %s:%d %s" % (
        e.cstr(e.arg(0)), e.cstr(e.arg(1)), e.arg(2), e.cstr(e.arg(3)))))
    h["___cxa_atexit"] = lambda e: 0
    h["_getenv"] = lambda e: 0
    def getcwd(e):
        p = os.getcwd().encode() + b"\0"; e.wr(e.arg(0), p); return e.arg(0)
    h["_getcwd"] = getcwd
    def chdir(e):
        p = e.cstr(e.arg(0)).decode("utf-8", "replace")
        if getattr(e, "dbg", False): print("[chdir]", p, file=sys.stderr)
        try: os.chdir(p); return 0
        except OSError as ex: e.w32(e.errno_addr, ex.errno or 2); return 0xFFFFFFFF
    h["_chdir"] = chdir
    def nslog(e):
        s = fmt(e, e.rt.py(e.arg(0)).encode("utf-8"), *stack_args(e, 1))
        print("[NSLog]", s.decode("utf-8", "replace"), file=sys.stderr)
    h["_NSLog"] = nslog
    e.stack_args = stack_args
    e.va_args = va_args

    # ----------------------------------------------------------------- time
    # The game steps its physics by the time that passed since the previous frame. After a long stall (disk, a slow GPU frame,
    # a dragged window) a huge step lets cars tunnel through the road, so the clock the game sees never advances by more than
    # CLOCK_CAP seconds between two of its own readings: stalls are hidden, normal time is untouched.
    CLOCK_CAP = float(os.environ.get("A6_CLOCK_CAP", "0.066"))
    clk = {"last": time.monotonic(), "lost": 0.0}
    import threading as _th
    def vclock():
        """(monotonic seconds, seconds hidden so far) as the game's main thread sees them; other threads get the real clock"""
        now = time.monotonic()
        if _th.get_ident() != getattr(e, "main_ident", None) or CLOCK_CAP <= 0: return now, 0.0
        gap = now - clk["last"]; clk["last"] = now
        if gap > CLOCK_CAP: clk["lost"] += gap - CLOCK_CAP
        return now - clk["lost"], clk["lost"]
    e.vclock = vclock
    def gettimeofday(e):
        v, lost = vclock()
        t = time.time() - lost
        if e.arg(0):
            e.w32(e.arg(0), int(t)); e.w32(e.arg(0) + 4, int((t % 1) * 1e6))
        return 0
    h["_gettimeofday"] = gettimeofday
    h["_time"] = lambda e: (e.w32(e.arg(0), int(time.time())) if e.arg(0) else None, int(time.time()))[1]
    h["_clock$UNIX2003"] = lambda e: int(time.process_time() * 1e6)
    h["_mach_absolute_time"] = lambda e: (u.reg_write(UC_X86_REG_EDX, 0), int(time.monotonic() * 1e9) & 0xFFFFFFFF)[1]
    def mach_absolute_time(e):
        t = int(vclock()[0] * 1e9)
        u.reg_write(UC_X86_REG_EDX, t >> 32)
        return t & 0xFFFFFFFF
    h["_mach_absolute_time"] = mach_absolute_time
    def timebase(e):
        e.w32(e.arg(0), 1); e.w32(e.arg(0) + 4, 1); return 0
    h["_mach_timebase_info"] = timebase
    h["_usleep$UNIX2003"] = lambda e: (time.sleep(e.arg(0) / 1e6), 0)[1]
    h["_nanosleep$UNIX2003"] = lambda e: (time.sleep(e.u32(e.arg(0)) + e.u32(e.arg(0) + 4) / 1e9), 0)[1]
    h["_sched_yield"] = lambda e: 0

    # --------------------------------------------------------------- pthread (single-threaded stubs for now)
    for n in ("_pthread_mutex_init", "_pthread_mutex_lock", "_pthread_mutex_unlock", "_pthread_mutex_trylock",
              "_pthread_mutex_destroy", "_pthread_mutexattr_init", "_pthread_mutexattr_settype",
              "_pthread_mutexattr_destroy$UNIX2003", "_pthread_cond_init$UNIX2003", "_pthread_cond_signal",
              "_pthread_cond_broadcast", "_pthread_cond_destroy", "_pthread_attr_init", "_pthread_attr_destroy",
              "_pthread_attr_setdetachstate", "_pthread_setcanceltype$UNIX2003", "_pthread_testcancel$UNIX2003",
              "_pthread_getschedparam", "_pthread_setschedparam", "_pthread_detach"):
        h[n] = lambda e: 0
    h["_pthread_self"] = lambda e: 1
    h["_sched_get_priority_max"] = lambda e: 63
    h["_sched_get_priority_min"] = lambda e: 15

    # --------------------------------------------------------------- CoreServices timing
    def uptime(e):
        t = int(time.monotonic() * 1e9)
        u.reg_write(UC_X86_REG_EDX, t >> 32)
        return t & 0xFFFFFFFF
    h["_UpTime"] = uptime
    h["_AbsoluteToDuration"] = lambda e: min(((e.arg(1) << 32 | e.arg(0)) // 1000000), 0x7FFFFFFF)

    from . import hle_cxx, glhle, hle_sys
    hle_sys.install(e)
    hle_cxx.install(e)
    glhle.install(e)
    from .sched import Sched
    e.sched = Sched(e)

    # ---- _DefaultRuneLocale (isdigit/isalpha etc. are inlined table lookups on darwin)
    A_, C_, D_, G_, L_, P_, S_, U_, X_, B_, R_ = 0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000, 0x10000, 0x20000, 0x40000
    rt_types = []
    for c in range(256):
        f = 0
        ch = chr(c)
        if c < 128:
            if ch.isalpha(): f |= A_ | G_ | R_
            if ch.isdigit(): f |= D_ | G_ | R_ | X_ | (0x30 << 0 if False else 0)
            if ch in "abcdefABCDEF": f |= X_
            if ch.islower(): f |= L_
            if ch.isupper(): f |= U_
            if ch in " \t\n\v\f\r": f |= S_
            if ch in " \t": f |= B_
            if c < 32 or c == 127: f |= C_
            if 32 <= c < 127: f |= R_
            if 33 <= c < 127: f |= G_
            if ch in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~": f |= P_
        rt_types.append(f)
    rl = e.alloc_static(0x34 + 3 * 1024 + 64)
    blob = bytearray(0x34 + 3 * 1024 + 64)
    blob[0:8] = b"RuneMagA"; blob[8:16] = b"NONE\0\0\0\0"
    struct.pack_into("<256I", blob, 0x34, *rt_types)
    struct.pack_into("<256I", blob, 0x34 + 1024, *[ord(chr(c).lower()) if c < 128 else c for c in range(256)])
    struct.pack_into("<256I", blob, 0x34 + 2048, *[ord(chr(c).upper()) if c < 128 else c for c in range(256)])
    e.wr(rl, bytes(blob))
    e.hle_data["__DefaultRuneLocale"] = rl
