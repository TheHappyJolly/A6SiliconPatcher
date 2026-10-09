"""Dev-only helpers: call tracing of arbitrary guest functions (a detour whose handler logs and then calls the original)."""
import struct, sys, time


def install_trace(e, addr, nbytes, nargs, name=None, fmt=None, sink=None):
    """Replace the first `nbytes` (whole instructions, >= 5, position independent) of the guest function at `addr` with a jump to a
    python hook; the hook logs the call and runs the original through a copy of the displaced bytes."""
    name = name or f"__tr_{addr:x}"
    orig = bytes(e.rd(addr, nbytes))
    tr = e.alloc_static(nbytes + 16, 16)
    e.wr(tr, orig + b"\xE9" + struct.pack("<I", ((addr + nbytes) - (tr + nbytes + 5)) & 0xFFFFFFFF))
    t0 = time.monotonic()
    def hook(e_):
        args = [e_.arg(i) for i in range(nargs)]
        ret = e_.arg(-1)
        r = e_.call(tr, *args)
        line = f"[tr {name}] +{time.monotonic() - t0:.2f}s args={[hex(a) for a in args]} ret_to={ret:#x} -> {r:#x}"
        if fmt: line += " " + fmt(e_, args, r)
        if sink is not None: sink.append(line)
        else: print(line, file=sys.stderr)
        return r
    e.hle[name] = hook
    stub = e.trap(name)
    e.wr(addr, b"\xE9" + struct.pack("<I", (stub - (addr + 5)) & 0xFFFFFFFF) + b"\x90" * (nbytes - 5))
    return tr


def install_detour(e, addr, nbytes, nargs, handler, name=None):
    """Production variant of install_trace: handler(e, args, call_original) -> eax (call_original(*args) runs the original function)."""
    name = name or f"__dt_{addr:x}"
    orig = bytes(e.rd(addr, nbytes))
    tr = e.alloc_static(nbytes + 16, 16)
    e.wr(tr, orig + b"\xE9" + struct.pack("<I", ((addr + nbytes) - (tr + nbytes + 5)) & 0xFFFFFFFF))
    def hook(e_):
        args = [e_.arg(i) for i in range(nargs)]
        return handler(e_, args, lambda *a: e_.call(tr, *a))
    e.hle[name] = hook
    stub = e.trap(name)
    e.wr(addr, b"\xE9" + struct.pack("<I", (stub - (addr + 5)) & 0xFFFFFFFF) + b"\x90" * (nbytes - 5))


def memdump(e, tag=None):
    import ctypes as _c, json, sys
    e.md_n = getattr(e, "md_n", 0) + 1
    hp = e.u32(0xE0080000)
    rngs = [(0x6f0000, 0x100000), (0x10000000, e.heap_ptr - 0x10000000), (0x30000000, max(0, hp - 0x30000000))]
    with open(f"/tmp/memdump_{e.md_n}.bin", "wb") as f:
        for a, n in rngs: f.write(_c.string_at(a, n))
    json.dump(rngs, open(f"/tmp/memdump_{e.md_n}.json", "w"))
    print("[memdump]", e.md_n, tag or "", file=sys.stderr)
