"""POSIX / Mach / CoreAudio-ish system stubs for the guest."""
import os, struct, sys, time, socket
from .ucshim import *


def install(e):
    h = e.hle
    u = e.uc
    S = lambda v: struct.unpack("<i", struct.pack("<I", v & 0xFFFFFFFF))[0]

    def set_errno(n): e.w32(e.errno_addr, n)

    # ------------------------------------------------------------ path / stat
    def realpath(e_):
        p = e_.cstr(e_.arg(0)).decode("utf-8", "replace")
        try:
            r = os.path.realpath(p)
            if not os.path.exists(r):
                set_errno(2); return 0
        except OSError:
            set_errno(2); return 0
        buf = e_.arg(1) or e_.malloc(1100)
        e_.wr(buf, r.encode() + b"\0"); return buf
    h["_realpath$DARWIN_EXTSN"] = realpath

    def pack_stat(st):
        # i386 struct stat (INODE64): 108 bytes
        def ts(t): return struct.pack("<ii", int(t), int((t % 1) * 1e9))
        b = struct.pack("<iHHQIIi", st.st_dev & 0x7FFFFFFF, st.st_mode & 0xFFFF, st.st_nlink & 0xFFFF,
                        st.st_ino, st.st_uid, st.st_gid, getattr(st, "st_rdev", 0))
        b += ts(st.st_atime) + ts(st.st_mtime) + ts(st.st_ctime) + ts(getattr(st, "st_birthtime", st.st_ctime))
        b += struct.pack("<qqiIIi", st.st_size, st.st_blocks, st.st_blksize, 0, 0, 0)
        b += bytes(16)
        return b[:108].ljust(108, b"\0")
    def stat(e_):
        try:
            st = os.stat(e_.cstr(e_.arg(0)).decode("utf-8", "replace"))
        except OSError as ex:
            set_errno(ex.errno or 2); return 0xFFFFFFFF
        e_.wr(e_.arg(1), pack_stat(st)); return 0
    h["_stat$INODE64"] = stat

    def uname(e_):
        buf = e_.arg(0)
        for i, v in enumerate((b"Darwin", b"localhost", b"11.4.2", b"Darwin Kernel Version 11.4.2", b"i386")):
            e_.wr(buf + 256 * i, v.ljust(256, b"\0"))
        return 0
    h["_uname"] = uname

    SYSCTL_STR = {"hw.model": b"MacBookPro8,2", "kern.osversion": b"11E53", "kern.osrelease": b"11.4.2",
                  "kern.ostype": b"Darwin", "hw.machine": b"i386", "kern.hostname": b"localhost"}
    SYSCTL_INT = {"hw.ncpu": 4, "hw.activecpu": 4, "hw.physicalcpu": 4, "hw.logicalcpu": 4, "hw.cpufrequency": 2400000000,
                  "hw.memsize": 8 << 30, "hw.pagesize": 4096, "hw.busfrequency": 1000000000, "hw.cachelinesize": 64,
                  "hw.l2cachesize": 262144, "hw.tbfrequency": 1000000000, "hw.byteorder": 1234, "hw.cputype": 7,
                  "hw.cpusubtype": 8}
    def out_value(oldp, oldlenp, data):
        if oldp:
            n = e.u32(oldlenp) if oldlenp else len(data)
            e.wr(oldp, data[:n])
        if oldlenp: e.w32(oldlenp, len(data))
        return 0
    def sysctlbyname(e_):
        name = e_.cstr(e_.arg(0)).decode()
        oldp, oldlenp = e_.arg(1), e_.arg(2)
        if name in SYSCTL_STR:
            return out_value(oldp, oldlenp, SYSCTL_STR[name] + b"\0")
        if name in SYSCTL_INT:
            v = SYSCTL_INT[name]
            n = e_.u32(oldlenp) if oldlenp else 4
            return out_value(oldp, oldlenp, struct.pack("<Q", v)[:max(n, 4) if n in (4, 8) else 4])
        set_errno(2); return 0xFFFFFFFF
    h["_sysctlbyname"] = sysctlbyname
    def sysctl(e_):
        mib, n = e_.arg(0), e_.arg(1)
        m = [e_.u32(mib + 4 * i) for i in range(n)]
        oldp, oldlenp = e_.arg(2), e_.arg(3)
        if m[:1] == [6]:      # CTL_HW
            vals = {3: 4, 7: 4, 24: 4, 25: 8 << 30, 6: 8 << 30, 5: 4096, 4: 8 << 30, 2: 4096}
            if len(m) > 1 and m[1] in (3, 25, 6, 5, 7, 24, 4):
                v = vals.get(m[1], 4)
                n_ = e_.u32(oldlenp) if oldlenp else 4
                return out_value(oldp, oldlenp, struct.pack("<Q", v)[:8 if n_ >= 8 else 4])
            if len(m) > 1 and m[1] in (2, 1):  # HW_MODEL / HW_MACHINE
                return out_value(oldp, oldlenp, b"MacBookPro8,2\0")
        if m[:2] == [1, 4]:   # kern.version-ish
            return out_value(oldp, oldlenp, b"Darwin Kernel Version 11.4.2\0")
        set_errno(2); return 0xFFFFFFFF
    h["_sysctl"] = sysctl

    # ------------------------------------------------------------- directories
    dirs = {}
    def opendir(e_):
        p = e_.cstr(e_.arg(0)).decode("utf-8", "replace")
        try:
            ents = [".", ".."] + sorted(os.listdir(p))
        except OSError as ex:
            set_errno(ex.errno or 2); return 0
        a = e_.malloc(32)
        dirs[a] = [p, ents, 0, e_.malloc(1060)]
        return a
    h["_opendir$INODE64$UNIX2003"] = opendir
    def readdir(e_):
        d = dirs.get(e_.arg(0))
        if not d or d[2] >= len(d[1]): return 0
        name = d[1][d[2]]; d[2] += 1
        full = os.path.join(d[0], name)
        typ = 4 if os.path.isdir(full) else 8
        nb = name.encode("utf-8")
        ent = struct.pack("<QQHHB", hash(name) & 0xFFFFFFFF, d[2], 1056, len(nb), typ) + nb + b"\0"
        e_.wr(d[3], ent); return d[3]
    h["_readdir$INODE64"] = readdir
    h["_closedir$UNIX2003"] = lambda e_: (dirs.pop(e_.arg(0), None), 0)[1]

    # ---------------------------------------------------------------- fd I/O (rare)
    h["_close$UNIX2003"] = lambda e_: (os.close(e_.arg(0)) if e_.arg(0) > 2 else None, 0)[1]
    h["_fcntl$UNIX2003"] = lambda e_: 0
    h["_ioctl"] = lambda e_: 0xFFFFFFFF
    h["_aio_read"] = h["_aio_error"] = h["_aio_return"] = lambda e_: 0xFFFFFFFF
    h["_sigaction"] = h["_sigprocmask"] = lambda e_: 0
    h["_setjmp"] = h["__setjmp"] = lambda e_: 0   # TODO: real setjmp/longjmp if used for control flow
    h["_longjmp"] = lambda e_: (_ for _ in ()).throw(RuntimeError("longjmp"))
    h["_strerror_r"] = lambda e_: (e_.wr(e_.arg(1), b"error\0"), 0)[1]

    # --------------------------------------------------------------- networking: unavailable
    for n in ("_socket", "_connect$UNIX2003", "_bind$UNIX2003", "_listen$UNIX2003", "_accept$UNIX2003",
              "_send$UNIX2003", "_sendto$UNIX2003", "_recv$UNIX2003", "_recvfrom$UNIX2003", "_select$UNIX2003",
              "_setsockopt", "_getsockopt", "_getsockname$UNIX2003", "_getpeername$UNIX2003", "_gethostname",
              "_getnameinfo", "_getifaddrs"):
        h[n] = lambda e_: 0xFFFFFFFF
    h["_gethostbyname"] = h["_gethostbyaddr"] = lambda e_: 0
    h["_freeifaddrs"] = lambda e_: 0
    def inet_addr(e_):
        try: return struct.unpack("<I", socket.inet_aton(e_.cstr(e_.arg(0)).decode()))[0]
        except OSError: return 0xFFFFFFFF
    h["_inet_addr"] = inet_addr
    h["_inet_ntoa"] = lambda e_: (lambda a: (e_.wr(a, socket.inet_ntoa(struct.pack("<I", e_.arg(0))).encode() + b"\0"), a)[1])(e_.alloc_static(32))
    h["_inet_aton"] = lambda e_: 0
    # SCNetworkReachability: report "not reachable"
    h["_SCNetworkReachabilityCreateWithName"] = lambda e_: e_.malloc(16)
    h["_SCNetworkReachabilityGetFlags"] = lambda e_: (e_.w32(e_.arg(1), 0), 1)[1]

    # time breakdown
    def tm_pack(t, utc):
        tt = time.gmtime(t) if utc else time.localtime(t)
        return struct.pack("<9i", tt.tm_sec, tt.tm_min, tt.tm_hour, tt.tm_mday, tt.tm_mon - 1, tt.tm_year - 1900,
                           (tt.tm_wday + 1) % 7, tt.tm_yday - 1, tt.tm_isdst) + struct.pack("<ii", 0, 0)
    tm_buf = e.alloc_static(64)
    h["_localtime"] = lambda e_: (e_.wr(tm_buf, tm_pack(e_.u32(e_.arg(0)), False)), tm_buf)[1]
    h["_gmtime"] = lambda e_: (e_.wr(tm_buf, tm_pack(e_.u32(e_.arg(0)), True)), tm_buf)[1]
    h["_localtime_r"] = lambda e_: (e_.wr(e_.arg(1), tm_pack(e_.u32(e_.arg(0)), False)), e_.arg(1))[1]

    # ----------------------------------------------------------------- misc
    h["_swprintf"] = lambda e_: 0
    libc = e.load_object("libc")
    e.gl_stub = libc["_g_gl_stub"]; e.libc_syms = libc
    st = 0xE0080000
    e.w32(st, 0x30000000); e.w32(st + 4, 0x70000000)       # heap bump pointer / end
    e.w32(st + 272, e.trap("__py_free"))                    # struct state.slow_unlock: guest free() of python-side blocks
    rename = {"_g_tolower": "___tolower", "_g_toupper": "___toupper"}
    for k, v in libc.items():
        if k in ("_g_mutex_lock", "_g_mutex_unlock", "_g_pthread_self") or k.startswith("_g_gl_") or k in (
                "_g_glBindBuffer", "_g_glEnableVertexAttribArray", "_g_glDisableVertexAttribArray", "_g_glVertexAttribPointer",
                "_g_glDrawElements", "_g_glDrawArrays"):
            continue                       # real threads now: handled by the python pthread layer
        e.native[rename.get(k, "_" + k[3:])] = v
    for cxx, c in (("__Znwm", "_malloc"), ("__Znam", "_malloc"), ("__ZnamRKSt9nothrow_t", "_malloc"),
                   ("__ZdlPv", "_free"), ("__ZdaPv", "_free")):
        e.native[cxx] = e.native[c]
    lib = e.load_object("qs")
    qsort_stub = lib["_g_qsort"]
    def qsort(e_):
        # tail-call into guest implementation (same cdecl signature)
        e_.jump(qsort_stub)
    h["_qsort"] = qsort
    from .audio import Audio
    e.audio = Audio(e)
