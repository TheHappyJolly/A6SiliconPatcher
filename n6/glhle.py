"""Guest OpenGL calls -> host OpenGL.framework (ctypes), translating guest pointers."""
import ctypes, struct, sys, os
from ctypes import c_int, c_uint, c_float, c_double, c_void_p, c_char_p, c_ubyte, c_size_t
from .ucshim import *

GL = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")

GL_ARRAY_BUFFER = 0x8892
GL_ELEMENT_ARRAY_BUFFER = 0x8893
TYPE_SIZE = {0x1400: 1, 0x1401: 1, 0x1402: 2, 0x1403: 2, 0x1404: 4, 0x1405: 4, 0x1406: 4, 0x140A: 8}
FMT_CH = {0x1906: 1, 0x1907: 3, 0x1908: 4, 0x1909: 1, 0x190A: 2, 0x80E0: 3, 0x80E1: 4, 0x1902: 1, 0x84F9: 1,
          0x1900: 1, 0x1901: 1, 0x1903: 1, 0x1904: 1, 0x1905: 1, 0x8227: 2, 0x8228: 1}
PACKED = {0x8033: 2, 0x8034: 2, 0x8363: 2, 0x8032: 1, 0x8035: 4, 0x8036: 4, 0x84FA: 4, 0x8367: 4, 0x8368: 4, 0x8365: 4}


def fn(name, res, *args):
    f = getattr(GL, name, None)
    if f is None and name.endswith(("EXT", "ARB")):
        f = getattr(GL, name[:-3], None)
    if f is None:
        return None
    f.restype = res
    f.argtypes = list(args)
    return f


def install(e):
    h = e.hle
    u = e.uc
    I, F, D, P = c_int, c_float, c_double, c_void_p
    class _GState(dict):
        """array_buf / elem_buf live in the guest-side mirror (guestlib/libc.c, struct gls) when GL calls are queued."""
        mirror = False
        def __getitem__(self, k):
            if self.mirror:
                if k == "array_buf": return ctypes.c_uint32.from_address(0xE00A0000).value
                if k == "elem_buf": return ctypes.c_uint32.from_address(0xE00A0004).value
            return dict.__getitem__(self, k)
        def __setitem__(self, k, v):
            if self.mirror and k in ("array_buf", "elem_buf"): return
            dict.__setitem__(self, k, v)
    state = _GState({"unpack_align": 4, "unpack_rowlen": 0, "array_buf": 0, "elem_buf": 0,
             "bufsize": {}, "mapped": {}, "attribs": {}, "enabled": set(), "err": 0})
    state.mirror = bool(getattr(e, "async_gl", False) and getattr(e, "gl_stub", 0))
    e.gl_state = state

    def arg_i(n): return e.arg(n)
    def sgn(v): return struct.unpack("<i", struct.pack("<I", v & 0xFFFFFFFF))[0]
    def af(n): return struct.unpack("<f", struct.pack("<I", e.arg(n)))[0]
    def ad(n): return struct.unpack("<d", e.rd(u.reg_read(UC_X86_REG_ESP) + 4 + 4 * n, 8))[0]

    # ------------------------------------------------------ scalar-signature calls
    # letter codes: i int, f float, d double (two stack slots)
    simple = {
        "glActiveTextureARB": ("", "i"), "glAttachShader": ("", "ii"), "glBegin": ("", "i"),
        "glBindFramebufferEXT": ("", "ii"), "glBindRenderbufferEXT": ("", "ii"),
        "glBindTexture": ("", "ii"), "glBlendColor": ("", "ffff"), "glBlendEquation": ("", "i"),
        "glBlendFunc": ("", "ii"), "glCheckFramebufferStatusEXT": ("i", "i"), "glClear": ("", "i"),
        "glClearColor": ("", "ffff"), "glClearDepth": ("", "d"), "glClearStencil": ("", "i"),
        "glColor4f": ("", "ffff"), "glColorMask": ("", "iiii"), "glCompileShader": ("", "i"),
        "glCreateProgram": ("i", ""), "glCreateShader": ("i", "i"), "glCullFace": ("", "i"),
        "glDeleteProgram": ("", "i"), "glDeleteShader": ("", "i"), "glDepthFunc": ("", "i"),
        "glDepthMask": ("", "i"), "glDepthRange": ("", "dd"), "glDisable": ("", "i"),
        "glDisableVertexAttribArray": ("", "i"), "glDrawBuffer": ("", "i"), "glEnable": ("", "i"),
        "glEnableVertexAttribArray": ("", "i"), "glEnd": ("", ""), "glFlush": ("", ""),
        "glFramebufferRenderbuffer": ("", "iiii"), "glFramebufferRenderbufferEXT": ("", "iiii"),
        "glFramebufferTexture2DEXT": ("", "iiiii"), "glFrontFace": ("", "i"),
        "glGenerateMipmapEXT": ("", "i"), "glGetError": ("i", ""), "glLineWidth": ("", "f"),
        "glLinkProgram": ("", "i"), "glLoadIdentity": ("", ""), "glMatrixMode": ("", "i"),
        "glOrtho": ("", "dddddd"), "glPointSize": ("", "f"), "glPolygonMode": ("", "ii"),
        "glPolygonOffset": ("", "ff"), "glPopAttrib": ("", ""), "glPushAttrib": ("", "i"),
        "glReadBuffer": ("", "i"), "glRenderbufferStorageEXT": ("", "iiii"), "glSampleCoverage": ("", "fi"),
        "glScissor": ("", "iiii"), "glStencilFunc": ("", "iii"), "glStencilMask": ("", "i"),
        "glStencilOp": ("", "iii"), "glTexCoord2f": ("", "ff"), "glTexParameterf": ("", "iif"),
        "glTexParameteri": ("", "iii"), "glUniform1f": ("", "if"), "glUniform1i": ("", "ii"),
        "glUseProgram": ("", "i"), "glVertex2f": ("", "ff"), "glVertexAttrib4f": ("", "iffff"),
        "glViewport": ("", "iiii"), "glCopyTexSubImage2D": ("", "iiiiiiii"),
    }
    CT = {"i": I, "f": F, "d": D}
    for name, (res, sig) in simple.items():
        f = fn(name, I if res == "i" else None, *[CT[c] for c in sig])
        if f is None:
            h["_" + name] = (lambda n: lambda e_: print("[gl] missing host fn", n, file=sys.stderr))(name)
            continue
        def mk(f, sig, name):
            def call(e_):
                args = []; i = 0
                for c in sig:
                    if c == "i": args.append(sgn(e_.arg(i))); i += 1
                    elif c == "f": args.append(af(i)); i += 1
                    else: args.append(ad(i)); i += 2
                r = f(*args)
                if getattr(e_, "gltrace", None) is not None and "fbo" in e_.gltrace and name in (
                        "glCheckFramebufferStatusEXT", "glFramebufferTexture2DEXT", "glFramebufferRenderbufferEXT",
                        "glRenderbufferStorageEXT", "glBindFramebufferEXT", "glBindRenderbufferEXT", "glDrawBuffer", "glReadBuffer"):
                    print(f"[fbo] {name}{tuple(hex(a) if isinstance(a, int) else a for a in args)} -> {hex(r) if r is not None else ''}", file=sys.stderr)
                return r if r is not None and res_is_int(name) else None
            return call
        res_is_int = lambda n: simple[n][0] == "i"
        h["_" + name] = mk(f, sig, name)

    def host_bind_buffer(target, buf):
        if target == GL_ARRAY_BUFFER: state["array_buf"] = buf
        elif target == GL_ELEMENT_ARRAY_BUFFER: state["elem_buf"] = buf
    gEnableRaw = fn("glEnable", None, I)
    def enable(e_):
        if getattr(e_, "no_cull", False) and e_.arg(0) == 0x0B44: return
        gEnableRaw(e_.arg(0))
    h["_glEnable"] = enable
    gBindBuffer = fn("glBindBuffer", None, I, I)
    def bind_buffer(e_):
        host_bind_buffer(e_.arg(0), e_.arg(1)); gBindBuffer(e_.arg(0), e_.arg(1))
    h["_glBindBuffer"] = bind_buffer
    gGetString = fn("glGetString", c_char_p, I)
    strings = {}
    def get_string(e_):
        n = e_.arg(0)
        if n not in strings:
            s = gGetString(n) or b""
            print('[gl] glGetString', hex(n), s[:40], file=sys.stderr)
            a = e_.alloc_static(len(s) + 1); e_.wr(a, s + b"\0"); strings[n] = a
        return strings[n]
    h["_glGetString"] = get_string

    # ------------------------------------------------------------ gen/delete
    def mk_gen(name):
        f = fn(name, None, I, ctypes.POINTER(c_uint))
        def gen(e_):
            n = e_.arg(0); buf = (c_uint * max(n, 1))()
            f(n, buf)
            e_.wr(e_.arg(1), struct.pack("<%dI" % n, *buf[:n]))
        return gen
    def mk_del(name):
        f = fn(name, None, I, ctypes.POINTER(c_uint))
        def dele(e_):
            n = e_.arg(0)
            ids = struct.unpack("<%dI" % n, e_.rd(e_.arg(1), 4 * n)) if n else ()
            f(n, (c_uint * max(n, 1))(*ids))
        return dele
    for g in ("glGenBuffers", "glGenFramebuffersEXT", "glGenRenderbuffersEXT", "glGenTextures"):
        h["_" + g] = mk_gen(g)
    for d in ("glDeleteBuffers", "glDeleteFramebuffersEXT", "glDeleteRenderbuffersEXT", "glDeleteTextures"):
        h["_" + d] = mk_del(d)
    gDrawBuffers = fn("glDrawBuffers", None, I, ctypes.POINTER(c_uint))
    def draw_buffers(e_):
        n = e_.arg(0); ids = struct.unpack("<%dI" % n, e_.rd(e_.arg(1), 4 * n))
        gDrawBuffers(n, (c_uint * n)(*ids))
    h["_glDrawBuffers"] = draw_buffers

    # ------------------------------------------------------------- buffers
    gBufferData = fn("glBufferData", None, I, c_size_t, P, I)
    gBufferSubData = fn("glBufferSubData", None, I, c_size_t, c_size_t, P)
    def keep(b):
        buf = ctypes.create_string_buffer(bytes(b), len(b) or 1)
        return buf
    def buffer_data(e_):
        target, size, ptr, usage = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3)
        bound = state["array_buf"] if target == GL_ARRAY_BUFFER else state["elem_buf"] if target == GL_ELEMENT_ARRAY_BUFFER else 0
        state["bufsize"][(target, bound)] = size
        if ptr:
            b = keep(e_.rd(ptr, size)); gBufferData(target, size, ctypes.cast(b, P), usage)
        else:
            gBufferData(target, size, None, usage)
    h["_glBufferData"] = buffer_data
    def buffer_sub_data(e_):
        target, off, size, ptr = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3)
        b = keep(e_.rd(ptr, size)); gBufferSubData(target, off, size, ctypes.cast(b, P))
    h["_glBufferSubData"] = buffer_sub_data
    def map_buffer(e_):
        target = e_.arg(0)
        bound = state["array_buf"] if target == GL_ARRAY_BUFFER else state["elem_buf"]
        size = state["bufsize"].get((target, bound), 0)
        a = e_.malloc(size + 16)
        state["mapped"][target] = (a, size, bound)
        return a
    h["_glMapBuffer"] = map_buffer
    def unmap_buffer(e_):
        target = e_.arg(0)
        m = state["mapped"].pop(target, None)
        if m:
            a, size, bound = m
            b = keep(e_.rd(a, size)); gBufferSubData(target, 0, size, ctypes.cast(b, P))
            e_.free(a)
        return 1
    h["_glUnmapBuffer"] = unmap_buffer

    # ------------------------------------------------------ vertex attributes
    gVAP = fn("glVertexAttribPointer", None, c_uint, I, I, ctypes.c_ubyte, I, P)
    seen_va = set()
    def vattrib_pointer(e_):
        idx, size, typ, norm, stride, ptr = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3) & 0xFF, e_.arg(4), e_.arg(5)
        if getattr(e_, "gltrace", None) is not None and "va" in e_.gltrace:
            k = (idx, size, typ, norm, stride, bool(state["array_buf"]))
            if k not in seen_va:
                seen_va.add(k); print(f"[va] idx={idx} size={size} type={typ:#x} norm={norm} stride={stride} vbo={bool(state['array_buf'])}", file=sys.stderr)
        if state["array_buf"]:
            gVAP(idx, size, typ, norm, stride, ptr)   # offset into VBO
            state["attribs"].pop(idx, None)
            state.setdefault("vbo_attrs", set()).add(idx)
        else:
            state["attribs"][idx] = (size, typ, norm, stride, ptr)
            state.setdefault("vbo_attrs", set()).discard(idx)
    h["_glVertexAttribPointer"] = vattrib_pointer
    gEnVA = fn("glEnableVertexAttribArray", None, c_uint)
    gDisVA = fn("glDisableVertexAttribArray", None, c_uint)
    def en_va(e_): state["enabled"].add(e_.arg(0)); gEnVA(e_.arg(0))
    def dis_va(e_): state["enabled"].discard(e_.arg(0)); gDisVA(e_.arg(0))
    h["_glEnableVertexAttribArray"] = en_va
    h["_glDisableVertexAttribArray"] = dis_va
    gDrawArrays = fn("glDrawArrays", None, I, I, I)
    gDrawElements = fn("glDrawElements", None, I, I, I, P)
    gGenB = fn("glGenBuffers", None, I, ctypes.POINTER(c_uint))
    gBindB = fn("glBindBuffer", None, I, I)
    tmpbufs = {}

    def upload_client_arrays(first, count, max_index=None):
        """Copy client-side attribute arrays used by this draw to host memory."""
        keepers = []
        for idx, (size, typ, norm, stride, ptr) in list(state["attribs"].items()):
            if idx not in state["enabled"]:
                continue
            esz = size * TYPE_SIZE.get(typ, 4)
            st = stride or esz
            lo, hi = first, first + count
            if max_index is not None: lo, hi = 0, max_index + 1
            data = e.rd(ptr + lo * st, (hi - lo) * st) if hi > lo else b""
            buf = keep(data)
            keepers.append(buf)
            # use client memory pointer directly: ctypes buffer address stays valid while kept
            saved = state["array_buf"]
            if saved: gBindB(GL_ARRAY_BUFFER, 0)
            gVAP(idx, size, typ, norm, st, ctypes.cast(ctypes.byref(buf, 0 - lo * st) if False else ctypes.c_void_p(ctypes.addressof(buf) - lo * st), P))
            if saved: gBindB(GL_ARRAY_BUFFER, saved)
        return keepers

    def draw_arrays(e_):
        mode, first, count = e_.arg(0), sgn(e_.arg(1)), sgn(e_.arg(2))
        k = upload_client_arrays(first, count)
        gDrawArrays(mode, first, count)
    h["_glDrawArrays"] = draw_arrays
    dcount = {"n": 0}
    def count_est(what):
        import re as _r
        mm = _r.search(r'count=(\d+)', what); return int(mm.group(1)) if mm else 0
    e.frame_draws = []; e.last_frame_draws = []
    def dbg_draw(e_, what):
        if getattr(e_, "gltrace", None) is not None and "frame" in e_.gltrace:
            def gi(p):
                v = (c_int * 4)(); gGetIntegerv(p, v); return list(v)
            ndc = ""
            if "wvp" in state and gi(0x8B8D)[0] in (15, 81) and count_est(what) in (6, 12, 18):
                for idx, (size, typ, norm, stride, ptr) in state["attribs"].items():
                    if idx in state["enabled"] and size == 3 and typ == 0x1406:
                        m = state["wvp"][0]
                        for vi in range(4):
                            x, y, z = struct.unpack("<3f", e_.rd(ptr + vi * (stride or 12), 12))
                            cx = m[0]*x + m[4]*y + m[8]*z + m[12]; cy = m[1]*x + m[5]*y + m[9]*z + m[13]
                            cz = m[2]*x + m[6]*y + m[10]*z + m[14]; cw = m[3]*x + m[7]*y + m[11]*z + m[15]
                            ndc += f" v{vi}=({cx/cw if cw else 0:.2f},{cy/cw if cw else 0:.2f},{cz/cw if cw else 0:.2f},w={cw:.1f})"
                        break
            e_.frame_draws.append(f"{what}{ndc} prog={gi(0x8B8D)[0]} fbo={gi(0x8CA6)[0]} vp={gi(0x0BA2)} depthT={gi(0x0B71)[0]} blend={gi(0x0BE2)[0]} stencilT={gi(0x0B90)[0]} scissorT={gi(0x0C11)[0]} tex={gi(0x8069)[0]} bsrc={gi(0x80C9)[0]:#x} bdst={gi(0x80C8)[0]:#x} bsrcA={gi(0x80CB)[0]:#x} bdstA={gi(0x80CA)[0]:#x} bcol={gi(0x0BE0)[0]} cmask={gi(0x0C23)} dmask={gi(0x0B72)[0]} cull={gi(0x0B44)[0]} cmode={gi(0x0B45)[0]:#x} ffront={gi(0x0B46)[0]:#x} dfunc={gi(0x0B74)[0]:#x} drange={gi(0x0B70)[:2]} pmode={gi(0x0B40)[:2]} sfunc={gi(0x0B92)[0]:#x} sref={gi(0x0B97)[0]} smask={gi(0x0B93)[0]:#x} poff={gi(0x8037)[0]} samp={gi(0x809E)[0]} sctest={gi(0x0C11)[0]} dither={gi(0x0BD0)[0]} ")
        if getattr(e_, "gltrace", None) is None or "draw" not in e_.gltrace: return
        dcount["n"] += 1
        if dcount["n"] < getattr(e_, "draw_skip", 0) or dcount["n"] > getattr(e_, "draw_skip", 0) + 25: return
        def gi(p):
            v = (c_int * 4)(); gGetIntegerv(p, v); return list(v)
        print(f"[draw] {what} prog={gi(0x8B8D)[0]} fbo={gi(0x8CA6)[0]} vp={gi(0x0BA2)} depth={gi(0x0B71)[0]} blend={gi(0x0BE2)[0]} tex={gi(0x8069)[0]}", file=sys.stderr)
        for idx, (size, typ, norm, stride, ptr) in state["attribs"].items():
            if idx in state["enabled"]:
                vals = []
                for vi in range(3):
                    raw = e_.rd(ptr + vi * (stride or size * 4), size * 4 if typ == 0x1406 else size)
                    vals.append(struct.unpack("<%df" % size, raw) if typ == 0x1406 else raw.hex())
                print(f"    attrib{idx}: size={size} type={typ:#x} norm={norm} stride={stride} ptr={ptr:#x} v0..2={vals}", file=sys.stderr)
    def skip_draw(e_):
        if getattr(e_, "hack_panel", False):
            if state.get("fbo", 0) == 0:
                pr = state.get("prog", 0)
                if state.get("panel_done") and pr == 15 and e_.cur_count >= 12: return True
                if pr == 81 and e_.cur_count == 6: state["panel_done"] = True
        sp = getattr(e_, "skip_progs", None)
        if sp:
            v = (c_int * 4)(); gGetIntegerv(0x8B8D, v)
            if v[0] in sp: return True
        f = getattr(e_, "draw_filter", None)
        if not f: return False
        en = state["enabled"]
        has_vbo = any(i in en for i in state.get("vbo_attrs", ()))
        has_client = any(i in en for i in state["attribs"])
        if f == "skipvbo" and has_vbo: return True
        if f == "skipclient" and has_client: return True
        if f == "skipidxvbo" and state["elem_buf"]: return True
        if f == "skipidxclient" and not state["elem_buf"]: return True
        return False
    s28 = {"n": 0}
    def step_shot(e_, label):
        if not getattr(e_, 'step_active', False): return
        import zlib
        GL.glFinish()
        vp = (c_int * 4)(); gGetIntegerv(0x0BA2, vp); w, hh = vp[2], vp[3]
        fb = (c_int * 1)(); gGetIntegerv(0x8CA6, fb)
        if fb[0] != 0 or w <= 0: return
        buf = ctypes.create_string_buffer(w * hh * 4); GL.glReadPixels(0, 0, w, hh, 0x1908, 0x1401, buf)
        e_.step_n = getattr(e_, 'step_n', 0) + 1
        e_.step_list.append((e_.step_n, label, w, hh, buf.raw))
    def draw_elements(e_):
        mode, count, typ, ptr = e_.arg(0), sgn(e_.arg(1)), e_.arg(2), e_.arg(3)
        e_.cur_count = count
        if skip_draw(e_): return
        if getattr(e_, "gltrace", None) is not None and "bigdraw" in e_.gltrace and count in (4263, 1788):
            print(f"[bigdraw] count={count} enabled={sorted(state['enabled'])} vbo_attrs={sorted(state.get('vbo_attrs', ()))}", file=sys.stderr)
            for i, a in sorted(state["attribs"].items()):
                if i in state["enabled"]:
                    print(f"     attr{i}: size={a[0]} type={a[1]:#x} norm={a[2]} stride={a[3]} ptr={a[4]:#x}", file=sys.stderr)
                    if a[1] == 0x1401:
                        print("        first colors:", [e_.rd(a[4] + k * a[3], a[0]).hex() for k in range(8)], file=sys.stderr)
            idx = struct.unpack("<%dH" % min(count, 12), e_.rd(ptr, 2 * min(count, 12)))
            print("     idx:", idx, file=sys.stderr)
            import numpy as _np
            ii = _np.frombuffer(e_.rd(ptr, 2 * count), dtype=_np.uint16).astype(_np.int64)
            pa = state["attribs"][1]
            mx = int(ii.max()); raw = _np.frombuffer(e_.rd(pa[4], (mx + 1) * pa[3]), dtype=_np.uint8)
            pos = _np.ndarray((mx + 1, 3), dtype=_np.float32, buffer=bytes(raw), strides=(pa[3], 4)) if False else _np.array([struct.unpack_from("<3f", bytes(raw), v * pa[3]) for v in range(mx + 1)], dtype=_np.float64)
            m = _np.array(state["wvp"][0], dtype=_np.float64).reshape(4, 4)   # column-major
            clip = (_np.c_[pos, _np.ones(len(pos))] @ m)                       # row vector * column-major matrix
            tri = ii[: count - count % 3].reshape(-1, 3)
            w = clip[:, 3][tri]
            allneg = (w < 0).all(axis=1).sum(); allpos = (w > 0).all(axis=1).sum(); mixed = len(tri) - allneg - allpos
            print(f"     tris={len(tri)} all_w<0={allneg} all_w>0={allpos} crossing_w=0: {mixed}", file=sys.stderr)
        if getattr(e_, "force_depth_tex", None):
            tv = (c_int * 4)(); gGetIntegerv(0x8069, tv)
            if tv[0] in e_.force_depth_tex:
                GL.glEnable(0x0B71); GL.glDepthFunc(0x0203)
        if getattr(e_, "gltrace", None) is not None and "s28" in e_.gltrace and not state["elem_buf"] and s28["n"] < 4 \
                and any(a[3] == 28 for i, a in state["attribs"].items() if i in state["enabled"]):
            s28["n"] += 1
            idxs = struct.unpack("<%dH" % min(count, 24), e_.rd(ptr, 2 * min(count, 24)))
            print(f"[s28] count={count} idx[:24]={idxs}", file=sys.stderr)
            for i, a in state["attribs"].items():
                if i in state["enabled"]:
                    for vi in (0, 1, 2, max(idxs)):
                        raw = e_.rd(a[4] + vi * a[3], a[0] * 4)
                        print(f"     attr{i} size={a[0]} v{vi}={struct.unpack('<%df' % a[0], raw)}", file=sys.stderr)
        dbg_draw(e_, f"DrawElements mode={mode:#x} count={count} type={typ:#x} vbo_elem={state['elem_buf']}")
        isz = TYPE_SIZE[typ]
        if state["elem_buf"]:
            idxs = None
            k = upload_client_arrays(0, 0, max_index=_max_index_vbo(count, typ, ptr))
            gDrawElements(mode, count, typ, ptr)
        else:
            raw = e_.rd(ptr, count * isz)
            fmtc = {1: "B", 2: "H", 4: "I"}[isz]
            mi = max(struct.unpack("<%d%s" % (count, fmtc), raw)) if count else 0
            k = upload_client_arrays(0, 0, max_index=mi)
            b = keep(raw)
            gDrawElements(mode, count, typ, ctypes.cast(b, P))
            if getattr(e_, "step_active", False):
                pv = (c_int * 4)(); gGetIntegerv(0x8B8D, pv); tv = (c_int * 4)(); gGetIntegerv(0x8069, tv)
                step_shot(e_, f"count={count} prog={pv[0]} tex={tv[0]}")
            if getattr(e_, "gltrace", None) is not None and "draw" in e_.gltrace:
                px = ctypes.create_string_buffer(4); GL.glFinish()
                GL.glReadPixels(735, 420, 1, 1, 0x1908, 0x1401, px)
                print("   center pixel after draw:", px.raw.hex(), "err", GL.glGetError(), file=sys.stderr)
    def _max_index_vbo(count, typ, off):
        # indices live in an element buffer; client attribs + VBO indices is unusual, be conservative
        return 65535
    h["_glDrawElements"] = draw_elements

    # ------------------------------------------------------------- uniforms
    def mk_uniform(name, n, kind):
        ct = c_float if kind == "f" else c_int
        f = fn(name, None, I, I, ctypes.POINTER(ct))
        fmt = "<%d" + ("f" if kind == "f" else "i")
        def call(e_):
            loc, count, ptr = sgn(e_.arg(0)), e_.arg(1), e_.arg(2)
            vals = struct.unpack(fmt % (n * count), e_.rd(ptr, 4 * n * count))
            f(loc, count, (ct * len(vals))(*vals))
        return call
    for name, n, kind in (("glUniform1fv", 1, "f"), ("glUniform2fv", 2, "f"), ("glUniform3fv", 3, "f"),
                          ("glUniform4fv", 4, "f"), ("glUniform1iv", 1, "i"), ("glUniform2iv", 2, "i"),
                          ("glUniform3iv", 3, "i"), ("glUniform4iv", 4, "i")):
        h["_" + name] = mk_uniform(name, n, kind)
    gUM4 = fn("glUniformMatrix4fv", None, I, I, ctypes.c_ubyte, ctypes.POINTER(c_float))
    def uniform_matrix4(e_):
        loc, count, tr, ptr = sgn(e_.arg(0)), e_.arg(1), e_.arg(2) & 0xFF, e_.arg(3)
        vals = struct.unpack("<%df" % (16 * count), e_.rd(ptr, 64 * count))
        if getattr(e_, "gltrace", None) is not None and "mat" in e_.gltrace:
            e_.matn = getattr(e_, "matn", 0) + 1
            if e_.matn % 9000 < 6 and e_.matn > getattr(e_, "mat_skip", 0):
                print(f"[mat] #{e_.matn} loc={loc} n={count}: " + " ".join(f"{v:.3f}" for v in vals[:16]), file=sys.stderr)
        _pv = (c_int * 4)(); gGetIntegerv(0x8B8D, _pv)
        if state["uloc"].get((_pv[0], loc)) == "WorldViewProjectionMatrix":
            state["wvp"] = (vals[:16], tr)
        gUM4(loc, count, tr, (c_float * len(vals))(*vals))
    h["_glUniformMatrix4fv"] = uniform_matrix4

    # --------------------------------------------------------------- shaders
    gShaderSource = fn("glShaderSource", None, I, I, ctypes.POINTER(c_char_p), ctypes.POINTER(I))
    def shader_source(e_):
        sh, count, strs, lens = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3)
        srcs = []
        for i in range(count):
            p = e_.u32(strs + 4 * i)
            ln = sgn(e_.u32(lens + 4 * i)) if lens else -1
            srcs.append(e_.rd(p, ln) if ln >= 0 else e_.cstr(p))
        src = b"".join(srcs)
        import re as _re
        src = _re.sub(rb"#\s*define\s+GL_ES\s+0", b"", src)
        def _fixif(m):
            l = m.group(0)
            l = _re.sub(rb"defined\s*\(\s*GL_\w+\s*\)|defined\s+GL_\w+", b"0", l)
            return _re.sub(rb"\bGL_ES\b", b"0", l)
        src = _re.sub(rb"(?m)^[ \t]*#[ \t]*(?:if|elif)\b[^\r\n]*", _fixif, src)
        src = _re.sub(rb"(?m)^([ \t]*#[ \t]*(?:endif|else))[^\r\n]*", rb"\1", src)   # old drivers ignored junk after #endif/#else
        arr = (c_char_p * 1)(src)
        gShaderSource(sh, 1, arr, None)
        e_.last_shader_src = src
        state.setdefault("srcs", {})[sh] = src
        if getattr(e_, "dbg", False) and not hasattr(e_, "_shown"):
            e_._shown = 1; print("[gl] first shader source:\n" + src[:900].decode(errors="replace"), file=sys.stderr)
    h["_glShaderSource"] = shader_source
    state["attached"] = {}
    gAttach = fn("glAttachShader", None, I, I)
    def attach_shader(e_):
        state["attached"].setdefault(e_.arg(0), []).append(e_.arg(1))
        gAttach(e_.arg(0), e_.arg(1))
    h["_glAttachShader"] = attach_shader
    def mk_getiv(name):
        f = fn(name, None, I, I, ctypes.POINTER(I))
        def call(e_):
            v = c_int(0); f(e_.arg(0), e_.arg(1), ctypes.byref(v)); e_.w32(e_.arg(2), v.value & 0xFFFFFFFF)
            if e_.arg(1) in (0x8B81, 0x8B82) and v.value == 0 and getattr(e_, "dbg", False):
                lg = ctypes.create_string_buffer(4096); ln = c_int(0)
                (GL.glGetShaderInfoLog if name == "glGetShaderiv" else GL.glGetProgramInfoLog)(e_.arg(0), 4096, ctypes.byref(ln), lg)
                print(f"[gl] {name} status FAILED: {lg.value.decode(errors='replace')}", file=sys.stderr)
                if not state.get("shown_fail"):
                    state["shown_fail"] = 1
                    print(state.get("srcs", {}).get(e_.arg(0), b"").decode(errors="replace"), file=sys.stderr)
        return call
    h["_glGetShaderiv"] = mk_getiv("glGetShaderiv")
    h["_glGetProgramiv"] = mk_getiv("glGetProgramiv")
    def mk_infolog(name):
        f = fn(name, None, I, I, ctypes.POINTER(I), ctypes.c_char_p)
        def call(e_):
            sh, n, plen, buf = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3)
            b = ctypes.create_string_buffer(max(n, 1)); l = c_int(0)
            f(sh, n, ctypes.byref(l), b)
            e_.wr(buf, b.raw[:l.value] + b"\0")
            if plen: e_.w32(plen, l.value)
        return call
    h["_glGetShaderInfoLog"] = mk_infolog("glGetShaderInfoLog")
    h["_glGetProgramInfoLog"] = mk_infolog("glGetProgramInfoLog")
    gGetAttribLoc = fn("glGetAttribLocation", I, I, c_char_p)
    gGetUniformLoc = fn("glGetUniformLocation", I, I, c_char_p)
    h["_glGetAttribLocation"] = lambda e_: gGetAttribLoc(e_.arg(0), e_.cstr(e_.arg(1))) & 0xFFFFFFFF
    state["uloc"] = {}
    def get_uniform_loc(e_):
        loc = gGetUniformLoc(e_.arg(0), e_.cstr(e_.arg(1)))
        state["uloc"][(e_.arg(0), loc)] = e_.cstr(e_.arg(1)).decode()
        return loc & 0xFFFFFFFF
    h["_glGetUniformLocation"] = get_uniform_loc
    def mk_active(name):
        f = fn(name, None, I, I, I, ctypes.POINTER(I), ctypes.POINTER(I), ctypes.POINTER(c_uint), ctypes.c_char_p)
        def call(e_):
            prog, idx, bufsz, plen, psize, ptype, pname = [e_.arg(i) for i in range(7)]
            nm = ctypes.create_string_buffer(max(bufsz, 1)); l = c_int(); sz = c_int(); ty = c_uint()
            f(prog, idx, bufsz, ctypes.byref(l), ctypes.byref(sz), ctypes.byref(ty), nm)
            e_.wr(pname, nm.raw[:l.value] + b"\0")
            if plen: e_.w32(plen, l.value)
            e_.w32(psize, sz.value); e_.w32(ptype, ty.value)
        return call
    h["_glGetActiveAttrib"] = mk_active("glGetActiveAttrib")
    h["_glGetActiveUniform"] = mk_active("glGetActiveUniform")

    # ---------------------------------------------------------------- getters
    gGetIntegerv = fn("glGetIntegerv", None, I, ctypes.POINTER(I))
    gGetFloatv = fn("glGetFloatv", None, I, ctypes.POINTER(c_float))
    COUNTS = {0x0BA2: 4, 0x0C10: 4, 0x0C22: 4, 0x0C23: 4, 0x0BA6: 16, 0x0BA7: 16, 0x0BA8: 16, 0x0BA9: 16,
              0x846D: 2, 0x846E: 2, 0x0D3A: 2, 0x0B70: 2, 0x0B22: 2, 0x0B12: 2, 0x0B13: 2, 0x0B23: 2,
              0x0B53: 4, 0x0B50: 4, 0x0B46: 3, 0x0B40: 2, 0x0B66: 4, 0x0B62: 4}
    def count_for(pname):
        if pname == 0x86A3:
            v = c_int(0); gGetIntegerv(0x86A2, ctypes.byref(v)); return max(v.value, 1)
        return COUNTS.get(pname, 1)
    def get_integerv(e_):
        n = count_for(e_.arg(0))
        buf = (c_int * max(n, 16))(); gGetIntegerv(e_.arg(0), buf)
        e_.wr(e_.arg(1), struct.pack("<%di" % n, *buf[:n]))
    def get_floatv(e_):
        n = count_for(e_.arg(0))
        buf = (c_float * max(n, 16))(); gGetFloatv(e_.arg(0), buf)
        e_.wr(e_.arg(1), struct.pack("<%df" % n, *buf[:n]))
    h["_glGetIntegerv"] = get_integerv
    h["_glGetFloatv"] = get_floatv

    # --------------------------------------------------------------- textures
    gPixelStorei = fn("glPixelStorei", None, I, I)
    def pixel_storei(e_):
        if e_.arg(0) == 0x0CF5: state["unpack_align"] = sgn(e_.arg(1))
        if e_.arg(0) == 0x0CF2: state["unpack_rowlen"] = sgn(e_.arg(1))
        gPixelStorei(e_.arg(0), sgn(e_.arg(1)))
    h["_glPixelStorei"] = pixel_storei

    def pixel_size(w, hgt, d, fmt, typ):
        if typ in PACKED: bpp = PACKED[typ]
        else: bpp = FMT_CH.get(fmt, 4) * TYPE_SIZE.get(typ, 1)
        rowlen = state["unpack_rowlen"] or w
        row = (rowlen * bpp + state["unpack_align"] - 1) // state["unpack_align"] * state["unpack_align"]
        return row * hgt * max(d, 1)

    gTexImage2D = fn("glTexImage2D", None, I, I, I, I, I, I, I, I, P)
    gTexSubImage2D = fn("glTexSubImage2D", None, I, I, I, I, I, I, I, I, P)
    gTexImage3D = fn("glTexImage3D", None, I, I, I, I, I, I, I, I, I, P)
    gTexSubImage3D = fn("glTexSubImage3D", None, I, I, I, I, I, I, I, I, I, I, P)
    def tex_image2d(e_):
        t, lv, ifmt, w, hh, border, fmt, typ, px = [e_.arg(i) for i in range(9)]
        w, hh = sgn(w), sgn(hh)
        if getattr(e_, "gltrace", None) is not None and "tex" in e_.gltrace:
            sample = e_.rd(px, min(16, pixel_size(w, hh, 1, fmt, typ))).hex() if px else None
            print(f"[tex] bound={state['tex'].get(state['unit'])} unit={state['unit']} TexImage2D target={t:#x} lv={sgn(lv)} ifmt={ifmt:#x} {w}x{hh} fmt={fmt:#x} type={typ:#x} px={px:#x} first={sample}", file=sys.stderr)
        if px:
            b = keep(e_.rd(px, pixel_size(w, hh, 1, fmt, typ))); gTexImage2D(t, sgn(lv), sgn(ifmt), w, hh, sgn(border), fmt, typ, ctypes.cast(b, P))
        else:
            gTexImage2D(t, sgn(lv), sgn(ifmt), w, hh, sgn(border), fmt, typ, None)
    h["_glTexImage2D"] = tex_image2d
    def tex_sub_image2d(e_):
        t, lv, x, y, w, hh, fmt, typ, px = [e_.arg(i) for i in range(9)]
        b = keep(e_.rd(px, pixel_size(sgn(w), sgn(hh), 1, fmt, typ)))
        gTexSubImage2D(t, sgn(lv), sgn(x), sgn(y), sgn(w), sgn(hh), fmt, typ, ctypes.cast(b, P))
    h["_glTexSubImage2D"] = tex_sub_image2d
    def tex_image3d(e_):
        t, lv, ifmt, w, hh, d, border, fmt, typ, px = [e_.arg(i) for i in range(10)]
        if px:
            b = keep(e_.rd(px, pixel_size(sgn(w), sgn(hh), sgn(d), fmt, typ)))
            gTexImage3D(t, sgn(lv), sgn(ifmt), sgn(w), sgn(hh), sgn(d), sgn(border), fmt, typ, ctypes.cast(b, P))
        else:
            gTexImage3D(t, sgn(lv), sgn(ifmt), sgn(w), sgn(hh), sgn(d), sgn(border), fmt, typ, None)
    h["_glTexImage3D"] = tex_image3d
    def tex_sub_image3d(e_):
        t, lv, x, y, z, w, hh, d, fmt, typ, px = [e_.arg(i) for i in range(11)]
        b = keep(e_.rd(px, pixel_size(sgn(w), sgn(hh), sgn(d), fmt, typ)))
        gTexSubImage3D(t, sgn(lv), sgn(x), sgn(y), sgn(z), sgn(w), sgn(hh), sgn(d), fmt, typ, ctypes.cast(b, P))
    h["_glTexSubImage3D"] = tex_sub_image3d
    gCTI2 = fn("glCompressedTexImage2D", None, I, I, I, I, I, I, I, P)
    gCTSI2 = fn("glCompressedTexSubImage2D", None, I, I, I, I, I, I, I, I, P)
    gCTI3 = fn("glCompressedTexImage3D", None, I, I, I, I, I, I, I, I, P)
    gCTSI3 = fn("glCompressedTexSubImage3D", None, I, I, I, I, I, I, I, I, I, I, P)
    def cti2(e_):
        t, lv, fmt, w, hh, border, size, px = [e_.arg(i) for i in range(8)]
        if getattr(e_, "gltrace", None) is not None and "tex" in e_.gltrace:
            print(f"[tex] CompressedTexImage2D lv={sgn(lv)} fmt={fmt:#x} {sgn(w)}x{sgn(hh)} size={size}", file=sys.stderr)
        b = keep(e_.rd(px, size)) if px else None
        if t == 0x0DE1 and px:
            cur = state["tex"].get(state["unit"], 0)
            if sgn(lv) == 0 and fmt in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
                state["ctex"][cur] = {"fmt": fmt, "w": sgn(w), "h": sgn(hh), "data": e_.rd(px, size), "mips": False}
            elif cur in state["ctex"]:
                state["ctex"][cur]["mips"] = True
        gCTI2(t, sgn(lv), fmt, sgn(w), sgn(hh), sgn(border), size, ctypes.cast(b, P) if b else None)
    h["_glCompressedTexImage2D"] = cti2
    def ctsi2(e_):
        t, lv, x, y, w, hh, fmt, size, px = [e_.arg(i) for i in range(9)]
        b = keep(e_.rd(px, size)); gCTSI2(t, sgn(lv), sgn(x), sgn(y), sgn(w), sgn(hh), fmt, size, ctypes.cast(b, P))
    h["_glCompressedTexSubImage2D"] = ctsi2
    def cti3(e_):
        t, lv, fmt, w, hh, d, border, size, px = [e_.arg(i) for i in range(9)]
        b = keep(e_.rd(px, size)) if px else None
        gCTI3(t, sgn(lv), fmt, sgn(w), sgn(hh), sgn(d), sgn(border), size, ctypes.cast(b, P) if b else None)
    h["_glCompressedTexImage3D"] = cti3
    def ctsi3(e_):
        t, lv, x, y, z, w, hh, d, fmt, size, px = [e_.arg(i) for i in range(11)]
        b = keep(e_.rd(px, size)); gCTSI3(t, sgn(lv), sgn(x), sgn(y), sgn(z), sgn(w), sgn(hh), sgn(d), fmt, size, ctypes.cast(b, P))
    h["_glCompressedTexSubImage3D"] = ctsi3
    state.update(unit=0, tex={}, ctex={})
    gUseProgram = fn("glUseProgram", None, I)
    def use_program(e_):
        state["prog"] = e_.arg(0); gUseProgram(e_.arg(0))
    h["_glUseProgram"] = use_program
    gBindFB = fn("glBindFramebufferEXT", None, I, I)
    def bind_fb(e_):
        state["fbo"] = e_.arg(1); gBindFB(e_.arg(0), e_.arg(1))
    h["_glBindFramebufferEXT"] = bind_fb
    gBindTexture = fn("glBindTexture", None, I, I)
    gActiveTexture = fn("glActiveTextureARB", None, I)
    def bind_texture(e_):
        t, i = e_.arg(0), e_.arg(1)
        if t == 0x0DE1: state["tex"][state["unit"]] = i
        gBindTexture(t, i)
    def active_texture(e_):
        state["unit"] = e_.arg(0) - 0x84C0
        gActiveTexture(e_.arg(0))
    h["_glBindTexture"] = bind_texture
    h["_glActiveTextureARB"] = active_texture
    gGenMip = fn("glGenerateMipmapEXT", None, I)
    def gen_mipmap(e_):
        t = e_.arg(0)
        cur = state["tex"].get(state["unit"], 0)
        c = state["ctex"].get(cur) if t == 0x0DE1 else None
        if c and not c["mips"]:
            # compressed level-0 only: the driver cannot generate mips for compressed formats; decode on the host
            from . import dxt
            rgba = dxt.decode(c["fmt"], c["w"], c["h"], c["data"])
            if getattr(e_, "gltrace", None) is not None and "alpha" in e_.gltrace:
                import numpy as _np
                al = _np.frombuffer(rgba, dtype=_np.uint8)[3::4]
                raw0 = _np.frombuffer(c["data"], dtype=_np.uint8)[:64]
                print(f"[alpha] tex {cur} fmt={c['fmt']:#x} {c['w']}x{c['h']} alpha min={al.min()} max={al.max()} mean={al.mean():.1f} first raw={raw0[:16].tobytes().hex()}", file=sys.stderr)
            gTexImage2D(0x0DE1, 0, 0x8058, c["w"], c["h"], 0, 0x1908, 0x1401, ctypes.cast(keep(rgba), P))
            state["ctex"].pop(cur, None)
        gGenMip(t)
    h["_glGenerateMipmapEXT"] = gen_mipmap
    gReadPixels = fn("glReadPixels", None, I, I, I, I, I, I, P)
    def read_pixels(e_):
        x, y, w, hh, fmt, typ, px = [e_.arg(i) for i in range(7)]
        n = pixel_size(sgn(w), sgn(hh), 1, fmt, typ)
        b = ctypes.create_string_buffer(n); gReadPixels(sgn(x), sgn(y), sgn(w), sgn(hh), fmt, typ, ctypes.cast(b, P))
        e_.wr(px, b.raw)
    h["_glReadPixels"] = read_pixels

    # ================= fast paths (native runtime): guest memory IS host memory, so pointers go straight to GL =====
    import os as _os_env_mod; _os_env = _os_env_mod.environ
    gBufferDataP = fn("glBufferData", None, I, c_size_t, P, I)
    gBufferSubDataP = fn("glBufferSubData", None, I, c_size_t, c_size_t, P)
    def buffer_data_fast(e_):
        target, size, ptr, usage = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3)
        bound = state["array_buf"] if target == GL_ARRAY_BUFFER else state["elem_buf"] if target == GL_ELEMENT_ARRAY_BUFFER else 0
        state["bufsize"][(target, bound)] = size
        gBufferDataP(target, size, ptr or None, usage)
    _nf = _os_env.get("A6_NOFAST", "")
    if "buf" not in _nf:
        h["_glBufferData"] = buffer_data_fast
        h["_glBufferSubData"] = lambda e_: gBufferSubDataP(e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3) or None)
    def unmap_fast(e_):
        target = e_.arg(0)
        m = state["mapped"].pop(target, None)
        if m:
            a, size, bound = m
            gBufferSubDataP(target, 0, size, a); e_.free(a)
        return 1
    if "buf" not in _nf: h["_glUnmapBuffer"] = unmap_fast

    state["cat"] = {}
    import numpy as _np
    SCR_MAX = 1024 * 1024 * 1024
    scr = {"size": 0}
    def scr_ensure(need):
        """scratch buffer for client-array draws: grows on demand (a fixed size overflowed into other host memory on huge draws);
        a canary after the buffer detects any overrun. False if the draw is unreasonably large."""
        if need <= scr["size"]: return True
        if need > SCR_MAX: return False
        size = max(32 * 1024 * 1024, (need + 0xFFFFFF) & ~0xFFFFFF)
        buf = ctypes.create_string_buffer(size + 4096)
        scr["buf"], scr["size"], scr["base"] = buf, size, ctypes.addressof(buf)
        ctypes.memset(scr["base"] + size, 0xA5, 4096)
        return True
    scr_ensure(0 + 1)
    def _scr_check():
        if ctypes.string_at(scr["base"] + scr["size"], 64) != b"\xa5" * 64:
            ctypes.memset(scr["base"] + scr["size"], 0xA5, 4096)
            print("[heap] client-array scratch overrun detected", file=sys.stderr)
    def vattrib_fast(e_):
        idx, size, typ, norm, stride, ptr = e_.arg(0), e_.arg(1), e_.arg(2), e_.arg(3) & 0xFF, e_.arg(4), e_.arg(5)
        if state["array_buf"]:
            state["cat"].pop(idx, None)
            gVAP(idx, size, typ, norm, stride, ptr or None)       # offset into the bound VBO
        else:
            state["cat"][idx] = (size, typ, norm, stride, ptr)    # client array: bound to a scratch copy at draw time
    h["_glVertexAttribPointer"] = vattrib_fast

    def client_bytes(lo, hi):
        tot = 0
        for idx in state["enabled"]:
            c = state["cat"].get(idx)
            if c is None: continue
            size, typ, norm, stride, ptr = c
            tot += (((hi - lo + 1) * (stride or size * TYPE_SIZE.get(typ, 4))) + 15) & ~15
        return tot

    def bind_client_arrays(lo, hi, scr_off):
        """Copy vertices [lo, hi] of every enabled client array into the scratch buffer and point GL at them."""
        base = scr["base"]
        for idx in state["enabled"]:
            c = state["cat"].get(idx)
            if c is None: continue
            size, typ, norm, stride, ptr = c
            st = stride or size * TYPE_SIZE.get(typ, 4)
            n = (hi - lo + 1) * st
            ctypes.memmove(base + scr_off, ptr + lo * st, n)
            gVAP(idx, size, typ, norm, st, base + scr_off - lo * st)
            scr_off += (n + 15) & ~15
        return scr_off

    def _rec(e_, kind, mode, count, typ=0):
        r = getattr(e_, "drawrec", None)
        if r is None: return
        at = [(i, state["attribs"][i][:4]) for i in sorted(state["enabled"]) if i in state["attribs"]]
        r.append((kind, hex(mode), count, hex(typ), state.get("prog", 0), dict(state["tex"]), state["array_buf"], state["elem_buf"],
                  state["bufsize"].get((0x8892, state["array_buf"])), state["bufsize"].get((0x8893, state["elem_buf"])), at, sorted(state["cat"])))
    def draw_arrays_fast(e_):
        mode, first, count = e_.arg(0), sgn(e_.arg(1)), sgn(e_.arg(2))
        _rec(e_, "A", mode, count)
        if getattr(e_, "hack_panel", False) or getattr(e_, "skip_progs", None):
            e_.cur_count = count
            if skip_draw(e_): return
        if count > 0 and any(i in state["cat"] for i in state["enabled"]):
            if not scr_ensure(client_bytes(first, first + count - 1)): return
            bind_client_arrays(first, first + count - 1, 0)
            gDrawArrays(mode, first, count); _scr_check(); return
        gDrawArrays(mode, first, count)
    h["_glDrawArrays"] = draw_arrays_fast
    def draw_elements_fast(e_):
        mode, count, typ, ptr = e_.arg(0), sgn(e_.arg(1)), e_.arg(2), e_.arg(3)
        _rec(e_, "E", mode, count, typ)
        if getattr(e_, "hack_panel", False) or getattr(e_, "skip_progs", None):
            e_.cur_count = count
            if skip_draw(e_): return
        client = [i for i in state["enabled"] if i in state["cat"]]
        if not client:
            gDrawElements(mode, count, typ, ptr or None); return
        if state["elem_buf"] or count <= 0:
            return                                              # client vertices with VBO indices: not used by the game
        isz = TYPE_SIZE[typ]
        raw = ctypes.string_at(ptr, count * isz)
        ia = _np.frombuffer(raw, dtype={1: _np.uint8, 2: _np.uint16, 4: _np.uint32}[isz])
        lo, hi = int(ia.min()), int(ia.max())
        if not scr_ensure(client_bytes(lo, hi) + len(raw) + 64): return
        off = bind_client_arrays(lo, hi, 0)
        ctypes.memmove(scr["base"] + off, raw, len(raw))
        gDrawElements(mode, count, typ, scr["base"] + off); _scr_check()
    h["_glDrawElements"] = draw_elements_fast

    def draw_client(e_):
        # replay of a draw queued by the guest with its client arrays snapshotted into the ring (arguments live there)
        kind, mode, a, count, typ, idxptr, nat = [e_.arg(i) for i in range(7)]
        for k in range(nat):
            i, size, t, norm, st, ptr = [e_.arg(7 + 6 * k + j) for j in range(6)]
            gVAP(i, size, t, norm, st, ptr)
        if kind: gDrawElements(mode, sgn(count), typ, idxptr)
        else: gDrawArrays(mode, sgn(a), sgn(count))
    h["__gl_draw_client"] = draw_client

    def mk_sync_draw(which):
        def f(e_):          # synchronous fallback for oversized client-array draws: rebuild the tracking from the guest mirror
            g = 0xE00A0000; mask = e_.u32(g + 8)
            state["enabled"] = {i for i in range(16) if mask >> i & 1}
            state["cat"] = {i: tuple(e_.u32(g + 48 + 24 * i + 4 * j) for j in range(5)) for i in range(16) if e_.u32(g + 48 + 24 * i + 20)}
            (draw_elements_fast if which else draw_arrays_fast)(e_)
        return f
    h["__gl_de_sync"] = mk_sync_draw(True); h["__gl_da_sync"] = mk_sync_draw(False)

    def mk_uniform_fast(name, n, kind):
        f = fn(name, None, I, I, P)
        return lambda e_: f(sgn(e_.arg(0)), e_.arg(1), e_.arg(2))
    for name, n, kind in (("glUniform1fv", 1, "f"), ("glUniform2fv", 2, "f"), ("glUniform3fv", 3, "f"),
                          ("glUniform4fv", 4, "f"), ("glUniform1iv", 1, "i"), ("glUniform2iv", 2, "i"),
                          ("glUniform3iv", 3, "i"), ("glUniform4iv", 4, "i")):
        if "uni" not in _nf: h["_" + name] = mk_uniform_fast(name, n, kind)
    gUM4p = fn("glUniformMatrix4fv", None, I, I, ctypes.c_ubyte, P)
    def uniform_matrix4_fast(e_):
        gUM4p(sgn(e_.arg(0)), e_.arg(1), e_.arg(2) & 0xFF, e_.arg(3))
    if "uni" not in _nf: h["_glUniformMatrix4fv"] = uniform_matrix4_fast

    rtinfo = {"tex": None, "scale": None}
    def rt_scale():
        if rtinfo["scale"] is None:
            f = float(os.environ.get("A6_RTSCALE", 0)) if os.environ.get("A6_RTSCALE") else None
            if f is None and hasattr(e, "calc_V"): f = e.calc_V()[0] / 1280.0
            rtinfo["scale"] = max(1.0, min(f or 1.0, 3.0))
            if getattr(e, "lib", None): e.lib.host_set_rt_scale(rtinfo["scale"])
        return rtinfo["scale"]
    def tex_image2d_fast(e_):
        t, lv, ifmt, w, hh, border, fmt, typ, px = [e_.arg(i) for i in range(9)]
        if w == 2048 and hh == 1024 and not px and sgn(lv) == 0 and not os.environ.get("A6_NORTFIX"):
            # the 3D scene is rendered into a fixed 1280x720 (2048x1024 texture) target and upscaled: make it screen sized
            sc = rt_scale(); rtinfo["tex"] = state["tex"].get(state["unit"], 0)
            if os.environ.get('A6_RTDEBUG'): print('[rt] big texture', rtinfo['tex'], 'scale', sc, file=sys.stderr)
            w, hh = int(round(2048 * sc)), int(round(1024 * sc))
        gTexImage2D(t, sgn(lv), sgn(ifmt), sgn(w), sgn(hh), sgn(border), fmt, typ, px or None)
    if "tex" in _nf: pass
    else:
      h["_glTexImage2D"] = tex_image2d_fast
    def tex_sub_image2d_fast(e_):
        t, lv, x, y, w, hh, fmt, typ, px = [e_.arg(i) for i in range(9)]
        gTexSubImage2D(t, sgn(lv), sgn(x), sgn(y), sgn(w), sgn(hh), fmt, typ, px or None)
    h["_glTexSubImage2D"] = tex_sub_image2d_fast
    def tex_image3d_fast(e_):
        t, lv, ifmt, w, hh, d, border, fmt, typ, px = [e_.arg(i) for i in range(10)]
        gTexImage3D(t, sgn(lv), sgn(ifmt), sgn(w), sgn(hh), sgn(d), sgn(border), fmt, typ, px or None)
    h["_glTexImage3D"] = tex_image3d_fast
    def tex_sub_image3d_fast(e_):
        t, lv, x, y, z, w, hh, d, fmt, typ, px = [e_.arg(i) for i in range(11)]
        gTexSubImage3D(t, sgn(lv), sgn(x), sgn(y), sgn(z), sgn(w), sgn(hh), sgn(d), fmt, typ, px or None)
    h["_glTexSubImage3D"] = tex_sub_image3d_fast
    def cti2_fast(e_):
        t, lv, fmt, w, hh, border, size, px = [e_.arg(i) for i in range(8)]
        if t == 0x0DE1 and px:
            cur = state["tex"].get(state["unit"], 0)
            if sgn(lv) == 0 and fmt in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
                state["ctex"][cur] = {"fmt": fmt, "w": sgn(w), "h": sgn(hh), "data": e_.rd(px, size), "mips": False}
            elif cur in state["ctex"]:
                state["ctex"][cur]["mips"] = True
        gCTI2(t, sgn(lv), fmt, sgn(w), sgn(hh), sgn(border), size, px or None)
    h["_glCompressedTexImage2D"] = cti2_fast
    h["_glCompressedTexSubImage2D"] = lambda e_: gCTSI2(e_.arg(0), sgn(e_.arg(1)), sgn(e_.arg(2)), sgn(e_.arg(3)), sgn(e_.arg(4)), sgn(e_.arg(5)), e_.arg(6), e_.arg(7), e_.arg(8) or None)
    h["_glCompressedTexImage3D"] = lambda e_: gCTI3(e_.arg(0), sgn(e_.arg(1)), e_.arg(2), sgn(e_.arg(3)), sgn(e_.arg(4)), sgn(e_.arg(5)), sgn(e_.arg(6)), e_.arg(7), e_.arg(8) or None)
    h["_glCompressedTexSubImage3D"] = lambda e_: gCTSI3(e_.arg(0), sgn(e_.arg(1)), sgn(e_.arg(2)), sgn(e_.arg(3)), sgn(e_.arg(4)), sgn(e_.arg(5)), sgn(e_.arg(6)), sgn(e_.arg(7)), e_.arg(8), e_.arg(9), e_.arg(10) or None)
    # big scene render target: scaled renderbuffer + remember which FBO owns it (viewport/scissor get scaled in C)
    _rbs = h.get("_glRenderbufferStorageEXT"); _fbt = h.get("_glFramebufferTexture2DEXT")
    gRBS = fn("glRenderbufferStorageEXT", None, I, I, I, I)
    def rbs_scaled(e_):
        t, fmt, w, hh = e_.arg(0), e_.arg(1), sgn(e_.arg(2)), sgn(e_.arg(3))
        if w == 2048 and hh == 1024 and rtinfo["tex"] is not None and not os.environ.get("A6_NORTFIX"):
            sc = rt_scale(); w, hh = int(round(2048 * sc)), int(round(1024 * sc))
        gRBS(t, fmt, w, hh)
    def fbt_track(e_):
        if os.environ.get('A6_RTDEBUG'): print('[rt] fbo tex', [hex(e_.arg(i)) for i in range(5)], 'rep_fbo', e_.lib.host_get_rep_fbo(), file=sys.stderr)
        if rtinfo["tex"] and e_.arg(3) == rtinfo["tex"] and e_.arg(1) == 0x8CE0 and getattr(e_, "lib", None):
            e_.lib.host_set_big_fbo(e_.lib.host_get_rep_fbo())
        return _fbt(e_)
    if gRBS: h["_glRenderbufferStorageEXT"] = rbs_scaled
    if _fbt: h["_glFramebufferTexture2DEXT"] = fbt_track
    h["_glReadPixels"] = lambda e_: gReadPixels(sgn(e_.arg(0)), sgn(e_.arg(1)), sgn(e_.arg(2)), sgn(e_.arg(3)), e_.arg(4), e_.arg(5), e_.arg(6))

    # ---- optional error checking (debug): wrap every gl* handler
    gErr = GL.glGetError
    gErr.restype = c_int
    errcount = {"n": 0}
    e.glcounts = {}
    e.callog = []
    def wrap(name, f):
        def w(e_):
            e_.glcounts[name] = e_.glcounts.get(name, 0) + 1
            if getattr(e_, "step_active", False) and name not in ("_glGetError",):
                a = [e_.arg(i) for i in range(4)]
                e_.callog.append(f"{name}({', '.join(hex(x) for x in a)})")
            if getattr(e_, "gltrace", None) and name[3:] in e_.gltrace:
                if name == "_glVertexAttrib4f":
                    print(f"[gltrace] {name} idx={e_.arg(0)} v=({af(1):.3f},{af(2):.3f},{af(3):.3f},{af(4):.3f})", file=sys.stderr)
                else:
                    print(f"[gltrace] {name} {[hex(e_.arg(i)) for i in range(5)]}", file=sys.stderr)
            r = f(e_)
            if getattr(e_, "glcheck", False) and name not in ("_glGetError", "_glBegin", "_glEnd"):
                err = gErr()
                if err and errcount["n"] < 60:
                    errcount["n"] += 1
                    print(f"[glerr] {name} -> {err:#x} (args {[hex(e_.arg(i)) for i in range(6)]})", file=sys.stderr)
            return r
        return w
    import os as _os
    if not _os.environ.get("A6_FAST"):
        for k in [k for k in h if k.startswith("_gl")]:
            h[k] = wrap(k, h[k])
