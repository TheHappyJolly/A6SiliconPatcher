"""Native runtime: runs the original i386 Mach-O code in IA-32e compatibility mode under Rosetta 2 (see
native/host.c) and provides the same Emu interface the HLE modules were written against."""
import os, sys, json, struct, ctypes, threading, collections, time
from .ucshim import *

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.environ.get("A6_BUNDLE", os.path.expanduser("~/Downloads/Asphalt 6.app"))
_i386 = os.path.join(BUNDLE, "Contents/Resources/engine/game.i386")
if not os.path.exists(_i386): _i386 = os.path.join(BUNDLE, "Contents/MacOS/Asphalt 6.i386")
APP = _i386 if os.path.exists(_i386) else os.path.join(BUNDLE, "Contents/MacOS/Asphalt 6")
HOOK_BASE = 0xF0000000
HOOK_STRIDE = 16
HEAP_BASE = 0x10000000          # static allocations (alloc_static)
PAGE = 0x1000
_u32 = ctypes.c_uint32
_TRACE = [t for t in os.environ.get('A6_TRACE', '').split(',') if t]

F_EAX, F_ECX, F_EDX, F_EBX, F_ESP, F_EBP, F_ESI, F_EDI, F_EIP = 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28
REGOFF = {UC_X86_REG_EAX: F_EAX, UC_X86_REG_ECX: F_ECX, UC_X86_REG_EDX: F_EDX, UC_X86_REG_EBX: F_EBX,
          UC_X86_REG_ESP: F_ESP, UC_X86_REG_EBP: F_EBP, UC_X86_REG_ESI: F_ESI, UC_X86_REG_EDI: F_EDI,
          UC_X86_REG_EIP: F_EIP}
C_FPRET, C_FLAGS, C_IDX, C_EXTRA, C_JUMP = 0x30, 0x38, 0x3C, 0x40, 0x44


def align_up(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


class Sec:
    def __init__(self, d):
        self.virtual_address, self.size = d["addr"], d["size"]


class _TL(threading.local):
    ab = 0        # when set: address of arg0 of the async command being replayed (arguments live in the ring)
    ctx = 0


class UcShim:
    """Just enough of the old Unicorn object for the HLE code (registers of the *current hook*, emu_stop)."""
    def __init__(self, e): self.e = e
    def reg_read(self, r):
        if r == UC_X86_REG_ESP and self.e.tl.ab: return self.e.tl.ab - 4
        return _u32.from_address(self.e._ctx + REGOFF[r]).value
    def reg_write(self, r, v): _u32.from_address(self.e._ctx + REGOFF[r]).value = v & 0xFFFFFFFF
    def emu_stop(self): self.e.abort_guest()


# Fire-and-forget GL calls: queued by the guest (g_gl_stub) and replayed by the servicer thread in order.
# name -> (nargs, ptr_arg, count_arg, words_per_element); 0xFF = none
_N = 0xFF
ASYNC_GL = {"_glUniform1i": (2,), "_glUniform1f": (2,), "_glUniform2f": (3,), "_glUniform3f": (4,), "_glUniform4f": (5,),
            "_glUniform2i": (3,), "_glUniform3i": (4,), "_glUniform4i": (5,),
            "_glBindTexture": (2,), "_glActiveTextureARB": (1,), "_glActiveTexture": (1,),
            "_glVertexAttrib1f": (2,), "_glVertexAttrib2f": (3,), "_glVertexAttrib3f": (4,), "_glVertexAttrib4f": (5,),
            "_glVertexAttribPointer": (6,), "_glEnableVertexAttribArray": (1,), "_glDisableVertexAttribArray": (1,),
            "_glUseProgram": (1,), "_glBlendFunc": (2,), "_glBlendFuncSeparate": (4,), "_glBlendEquation": (1,),
            "_glDepthMask": (1,), "_glDepthFunc": (1,), "_glCullFace": (1,), "_glFrontFace": (1,),
            "_glBindBuffer": (2,), "_glEnable": (1,), "_glDisable": (1,), "_glViewport": (4,), "_glScissor": (4,),
            "_glClear": (1,), "_glClearColor": (4,), "_glColorMask": (4,), "_glPolygonOffset": (2,), "_glLineWidth": (1,), "_glBindFramebufferEXT": (2,),
            "_glBegin": (1,), "_glEnd": (0,), "_glVertex2f": (2,), "_glTexCoord2f": (2,), "_glColor4f": (4,)}
for _n, _c in (("1", 1), ("2", 2), ("3", 3), ("4", 4)):
    ASYNC_GL["_glUniform%sfv" % _n] = (3, 2, 1, _c); ASYNC_GL["_glUniform%siv" % _n] = (3, 2, 1, _c)
ASYNC_GL["_glUniformMatrix2fv"] = (4, 3, 1, 4); ASYNC_GL["_glUniformMatrix3fv"] = (4, 3, 1, 9)
ASYNC_GL["_glUniformMatrix4fv"] = (4, 3, 1, 16)
DESC_TABLE = 0xE0090000
C_OPS = {"_glUniform1i": 1, "_glUniform1f": 2, "_glUniform2f": 3, "_glUniform3f": 4, "_glUniform4f": 5,
         "_glUniform1fv": 6, "_glUniform2fv": 7, "_glUniform3fv": 8, "_glUniform4fv": 9,
         "_glUniform1iv": 10, "_glUniform2iv": 11, "_glUniform3iv": 12, "_glUniform4iv": 13,
         "_glUniformMatrix2fv": 14, "_glUniformMatrix3fv": 15, "_glUniformMatrix4fv": 16,
         "_glVertexAttrib1f": 17, "_glVertexAttrib2f": 18, "_glVertexAttrib3f": 19, "_glVertexAttrib4f": 20,
         "_glVertexAttribPointer": 21, "_glEnableVertexAttribArray": 22, "_glDisableVertexAttribArray": 23,
         "_glBindBuffer": 24, "_glBlendFunc": 25, "_glBlendFuncSeparate": 26, "_glBlendEquation": 27,
         "_glDepthMask": 28, "_glDepthFunc": 29, "_glCullFace": 30, "_glFrontFace": 31, "_glEnable": 32, "_glDisable": 33,
         "_glViewport": 34, "_glScissor": 35, "_glClear": 36, "_glClearColor": 37, "_glColorMask": 38,
         "_glPolygonOffset": 39, "_glLineWidth": 40, "_glDrawElements": 41, "_glDrawArrays": 42, "__gl_draw_client": 43, "_glBindFramebufferEXT": 44,
         "_glBegin": 45, "_glEnd": 46, "_glVertex2f": 47, "_glTexCoord2f": 48, "_glColor4f": 49}


class Emu:
    def __init__(self, path=APP, trace=False):
        sys.setswitchinterval(0.0005)      # busy python threads must not hold the GIL for long (audio, ring replay)
        self.lib = lib = ctypes.CDLL(os.path.join(ROOT, "native", "libhost.dylib"))
        lib.host_init.restype = ctypes.c_int
        lib.host_mmap_fixed.restype = ctypes.c_void_p
        lib.host_mmap_fixed.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.host_thread_init.restype = ctypes.c_void_p
        lib.host_make_stub.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.host_make_ret_stub.argtypes = [ctypes.c_uint32]
        lib.host_set_big_fbo.argtypes = [ctypes.c_uint32]; lib.host_set_rt_scale.argtypes = [ctypes.c_float]
        lib.host_get_rep_fbo.restype = ctypes.c_uint32
        lib.host_idle.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.host_mutex_lock.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.host_set_replay.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.host_guest_call_ex.restype = ctypes.c_uint64
        lib.host_guest_call_ex.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        if not os.environ.get('A6_NOCRASHINFO'):
            lib.host_install_crash_handler()          # on a fatal signal: registers / last hook calls go to the log
        if lib.host_init() != 0:
            raise RuntimeError("host_init failed (Rosetta 32-bit mode unavailable?)")
        self.main_ctx = lib.host_thread_init()
        self.meta = json.load(open(os.path.join(ROOT, "game_meta.json")))
        self.path = path
        self.tl = _TL()
        self.uc = UcShim(self)
        self.hle = {}
        self.hook_syms = []
        self.hook_addr = {}
        self.missing = collections.Counter()
        self.trace = trace
        self.skip_missing = True
        self.stopped_on = None
        self.count_hits = False
        self.hitcount = {}
        self.timing = False
        self.hook_ns = 0
        self.hook_calls = 0
        self.name_ns = {}
        self.slow_log = None
        self.main_ident = threading.get_ident()
        self.heap_ptr = HEAP_BASE
        self._static_lock = threading.Lock()
        self.exports = dict(self.meta["exports"])
        self.sections = {k: Sec(v) for k, v in self.meta["sections"].items()}
        self.bindings = self.meta["bindings"]
        self.native = {}
        self.hle_data = {}
        self.lib_ptr = 0xE0000000
        self._load()
        self.ret_stub = HOOK_BASE + 0x1F0000
        lib.host_make_ret_stub(self.ret_stub)
        self._cb = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(self._on_hook)
        lib.host_set_py_callback(self._cb)
        self._rcb = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(self._on_ring)
        lib.host_set_ring_callback(self._rcb)
        self._rings = {}
        self.ring_ns = 0; self.ring_cmds = 0
        self.gl_stub = 0
        self.async_gl = not os.environ.get('A6_NOASYNC')
        if os.environ.get('A6_CRASHINFO'):
            import atexit
            self._dump_hooks = lambda: json.dump(self.hook_syms, open('/tmp/a6_hooks.json', 'w'))

    # ------------------------------------------------------------------ memory
    def _map(self, addr, size):
        if not self.lib.host_mmap_fixed(addr, size):
            raise RuntimeError("cannot map guest memory at %#x" % addr)

    def _load(self):
        with open(self.path, "rb") as f:
            data = f.read()
        for seg in self.meta["segments"]:
            va, vs = seg["vmaddr"], align_up(seg["vmsize"])
            skip = max(0, 0x2000 - va)          # page 0x1000 holds the launcher's own Mach-O header: keep it
            self._map(va + skip, vs - skip)
            fo, fs = seg["fileoff"] + skip, seg["filesize"] - skip
            if fs > 0:
                ctypes.memmove(va + skip, data[fo:fo + fs], fs)
        self._map(HEAP_BASE, 0x10000000)      # static
        self._map(0x20000000, 0x10000000)     # python-side malloc arena
        self._map(0x30000000, 0x40000000)     # guest malloc arena
        self._map(0xE0000000, 0x100000)       # native guest helpers
        self._map(HOOK_BASE, 0x200000)        # import stubs

    def load_object(self, name):
        meta = self.meta["objects"][name]
        code = open(os.path.join(ROOT, "guestlib", name + ".bin"), "rb").read()
        base = (self.lib_ptr + 15) & ~15
        self.wr(base, code)
        self.lib_ptr = base + len(code)
        return {k: base + v for k, v in meta.items()}

    def rd(self, addr, n): return ctypes.string_at(addr, n)
    def wr(self, addr, b):
        b = bytes(b)
        ctypes.memmove(addr, b, len(b))
    def u32(self, addr): return _u32.from_address(addr).value
    def w32(self, addr, v): _u32.from_address(addr).value = v & 0xFFFFFFFF
    def cstr(self, addr, limit=1 << 20):
        return ctypes.string_at(addr) if addr else b""

    def alloc_static(self, n, align=16):
        with self._static_lock:
            self.heap_ptr = (self.heap_ptr + align - 1) & ~(align - 1)
            a = self.heap_ptr
            self.heap_ptr += n
        return a

    def sel_addr(self, name):
        d = self.__dict__.setdefault("_sels", {})
        if name not in d:
            a = self.alloc_static(len(name) + 1, 1)
            self.wr(a, name.encode() + b"\0")
            d[name] = a
        return d[name]

    # ------------------------------------------------------------------ hooks
    def trap(self, name):
        a = self.hook_addr.get(name)
        if a is None:
            idx = len(self.hook_syms)
            a = HOOK_BASE + idx * HOOK_STRIDE
            self.hook_syms.append(name)
            self.hook_addr[name] = a
            if getattr(self, 'gl_stub', 0): self._reg_replay(name, idx)
            d = ASYNC_GL.get(name) if (self.async_gl and self.gl_stub) else None
            _only = os.environ.get('A6_ASYNC_ONLY')
            if d and _only and name not in _only.split(','): d = None
            if d:
                nargs, pa, ca, wpe = (d + (0xFF, 0xFF, 0))[:4] if len(d) == 1 else d
                self.wr(DESC_TABLE + idx * 4, bytes([nargs, pa, ca, wpe]))
                self.wr(a, b"\xB8" + struct.pack("<I", idx) + b"\xE9" + struct.pack("<i", self.gl_stub - (a + 10)))
            else:
                self.lib.host_make_stub(a, idx)
        return a

    def _on_ring(self, ctxp):
        """Replay exactly one async GL command (the one at the ring tail) that has no native implementation."""
        try:
            self.tl.ctx = ctxp
            base = _u32.from_address(ctxp + 0x70).value
            ring = self._rings.get(base)
            if ring is None:
                ring = self._rings[base] = memoryview((ctypes.c_char * 0x400000).from_address(base)).cast('B').cast('I')
            tail = _u32.from_address(ctxp + 0xC0)
            t = tail.value
            off = t & 0xFFFFF
            idx = ring[off]; need = ring[off + 1]
            if idx != 0xFFFFFFFF:
                self.tl.ab = base + off * 4 + 8          # arg(n) reads [ab + 4n]
                try:
                    fn = self.hle.get(self.hook_syms[idx])
                    if fn is not None: fn(self)
                except BaseException:
                    nm = self.hook_syms[idx]
                    if nm not in self.missing:
                        self.missing[nm] += 1
                        import traceback
                        print(f"[!] async handler for {nm} raised:", file=sys.stderr); traceback.print_exc()
                finally:
                    self.tl.ab = 0
            tail.value = (t + need) & 0xFFFFFFFF
        except SystemExit:
            os._exit(0)

    @property
    def _ctx(self): return self.tl.ctx

    @property
    def extra_pop(self): return _u32.from_address(self._ctx + C_EXTRA).value
    @extra_pop.setter
    def extra_pop(self, v): _u32.from_address(self._ctx + C_EXTRA).value = v

    def jump(self, addr):
        _u32.from_address(self._ctx + C_JUMP).value = addr

    def abort_guest(self):
        """Unwind to the python caller of the outermost guest call (used by NSApplicationMain)."""
        self.jump(self.ret_stub)

    def _on_hook(self, ctxp):
        try:
            self.tl.ctx = ctxp
            idx = _u32.from_address(ctxp + C_IDX).value
            name = self.hook_syms[idx]
            fn = self.hle.get(name)
            if self.count_hits: self.hitcount[name] = self.hitcount.get(name, 0) + 1
            if _TRACE and any(name.startswith(t) for t in _TRACE):
                print(f"[trace] {name}({', '.join(hex(self.arg(i)) for i in range(5))})", file=sys.stderr)
            if fn is None:
                if name not in self.missing:
                    print(f"[!] unimplemented import: {name}  (ret={self.arg(-1):#x})", file=sys.stderr)
                self.missing[name] += 1
                fn = lambda e: 0
            if self.timing and threading.get_ident() == self.main_ident:
                _t0 = time.perf_counter_ns(); r = fn(self); _d = time.perf_counter_ns() - _t0
                self.hook_ns += _d; self.hook_calls += 1
                if _d > 6_000_000 and self.slow_log: self.slow_log.append((name, round(_d / 1e6, 1)))
                a_ = self.name_ns.setdefault(name, [0, 0]); a_[0] += _d; a_[1] += 1
            else:
                r = fn(self)
            if r is not None:
                if isinstance(r, float):
                    ctypes.c_double.from_address(ctxp + C_FPRET).value = r
                    _u32.from_address(ctxp + C_FLAGS).value |= 1
                else:
                    _u32.from_address(ctxp + F_EAX).value = r & 0xFFFFFFFF
        except SystemExit:
            os._exit(0)
        except BaseException:
            # never take the whole game down for one failing import: log once per symbol and return 0
            import traceback
            nm = self.hook_syms[_u32.from_address(ctxp + C_IDX).value]
            if nm not in self.missing:
                self.missing[nm] += 1
                print(f"[!] handler for {nm} raised:", file=sys.stderr); traceback.print_exc()
            _u32.from_address(ctxp + F_EAX).value = 0

    def idle_wrap(self, fn, min_sleep=None):
        """Wrap a blocking hook: the guest thread is stopped while the servicer waits (no spinning)."""
        lib = self.lib
        def w(e_):
            if min_sleep is not None and not min_sleep(e_): return fn(e_)
            c = e_._ctx
            lib.host_idle(c, 1)
            try: return fn(e_)
            finally: lib.host_idle(c, 0)
        return w

    # --------------------------------------------------------------- helpers
    def arg(self, n):
        ab = self.tl.ab
        if ab: return _u32.from_address(ab + 4 * n).value
        esp = _u32.from_address(self._ctx + F_ESP).value
        return _u32.from_address(esp + 4 + 4 * n).value

    def reg(self, r): return self.uc.reg_read(r)

    def symname(self, a):
        import bisect
        if not hasattr(self, "_symsorted"):
            self._symsorted = sorted((v, k) for k, v in self.exports.items() if 0x1000 < v < 0x704000)
            self._symaddrs = [v for v, k in self._symsorted]
        i = bisect.bisect_right(self._symaddrs, a) - 1
        if i < 0: return hex(a)
        v, k = self._symsorted[i]
        return f"{k}+{a - v:#x}" if a - v < 0x2000 else hex(a)

    # ---------------------------------------------------------------- binding
    def _reg_replay(self, name, idx):
        """queued GL calls with a native implementation are replayed in C (no python on the servicer)"""
        op = C_OPS.get(name)
        if op and not os.environ.get('A6_PYREPLAY') and getattr(self, "async_gl", False) and \
           not (getattr(self, "_panel", False) and name in ("_glDrawElements", "_glDrawArrays")):
            self.lib.host_set_replay(idx, op)

    def _setup_async_gl(self):
        """Route bookkeeping/draw GL calls through the guest-side command ring (see guestlib/libc.c)."""
        libc = self.libc_syms
        panel = os.environ.get('A6_PANEL_FIX') == '1' or '-hp' in sys.argv
        names = {"_glBindBuffer": "_g_glBindBuffer", "_glEnableVertexAttribArray": "_g_glEnableVertexAttribArray",
                 "_glDisableVertexAttribArray": "_g_glDisableVertexAttribArray",
                 "_glVertexAttribPointer": "_g_glVertexAttribPointer"}
        if not panel:
            names["_glDrawElements"] = "_g_glDrawElements"; names["_glDrawArrays"] = "_g_glDrawArrays"
        used = {b[1] for b in self.bindings}
        g = 0xE00A0000
        for nm in ("_glBindBuffer", "_glEnableVertexAttribArray", "_glDisableVertexAttribArray", "_glVertexAttribPointer",
                   "_glDrawElements", "_glDrawArrays"):
            self.trap(nm)                                   # make sure the hook (index, sync stub) exists
        idx = lambda n: self.hook_syms.index(n)
        self.hook_syms.append("__gl_draw_client"); self.hook_addr["__gl_draw_client"] = 0
        for i, v in enumerate((idx("_glBindBuffer"), idx("_glEnableVertexAttribArray"), idx("_glDisableVertexAttribArray"),
                               idx("_glVertexAttribPointer"), idx("_glDrawElements"), idx("_glDrawArrays"),
                               len(self.hook_syms) - 1, self.hook_addr["_glDrawElements"], self.hook_addr["_glDrawArrays"])):
            self.w32(g + 12 + 4 * i, v)
        for k, v in names.items():
            if k in used: self.native[k] = libc[v]
        # synchronous fallbacks for oversized client-array draws
        for nm, fld in (("__gl_de_sync", 7), ("__gl_da_sync", 8)):
            self.w32(g + 12 + 4 * fld, self.trap(nm))
        self._panel = panel
        for nm in self.hook_syms: self._reg_replay(nm, self.hook_syms.index(nm))

    def bind_all(self):
        if self.async_gl and self.gl_stub: self._setup_async_gl()
        for addr, name, addend, sec in self.bindings:
            if name == "dyld_stub_binder":
                target = self.trap(name)
            elif name in self.exports:
                target = self.exports[name]
            elif name in self.native:
                target = self.native[name]
            elif name in self.hle_data:
                target = self.hle_data[name]
            else:
                target = self.trap(name)
            self.w32(addr, target + (addend & 0xFFFFFFFF))

    # ------------------------------------------------------------ guest calls
    def call(self, addr, *args):
        arr = (ctypes.c_uint32 * max(len(args), 1))(*[a & 0xFFFFFFFF for a in args])
        r = self.lib.host_guest_call_ex(addr, len(args), arr, 0, None)
        return r & 0xFFFFFFFF

    def call64(self, addr, *args):
        arr = (ctypes.c_uint32 * max(len(args), 1))(*[a & 0xFFFFFFFF for a in args])
        return self.lib.host_guest_call_ex(addr, len(args), arr, 0, None)

    def call_fp(self, addr, *args):
        arr = (ctypes.c_uint32 * max(len(args), 1))(*[a & 0xFFFFFFFF for a in args])
        fp = ctypes.c_double()
        self.lib.host_guest_call_ex(addr, len(args), arr, 1, ctypes.byref(fp))
        return fp.value

    def dump_regs(self): pass
