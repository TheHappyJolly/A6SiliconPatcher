"""Offline helper (arm64 venv, uses lief): extracts what the x86_64 runtime needs from the Mach-O binary and the
guest helper objects, as JSON/binary blobs."""
import json, struct, sys, os, lief
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)

def objects():
    out = {}
    for name in ("libc", "qs"):
        o = os.path.join(ROOT, "guestlib", name + ".o")
        b = lief.parse(o)
        text = [x for x in b.sections if x.name == "__text"][0]
        code = bytearray(bytes(text.content))
        for r in text.relocations:
            if getattr(r, 'pc_relative', getattr(r, 'is_pc_relative', False)) and r.size == 32 and r.has_symbol:   # section-relative ones are already correct
                tgt = r.symbol.value
                struct.pack_into("<i", code, r.address, tgt - (r.address + 4))
        open(os.path.join(ROOT, "guestlib", name + ".bin"), "wb").write(bytes(code))
        out[name] = {s.name: s.value for s in b.symbols if s.name.startswith("_g_")}
    return out

def macho(path):
    b = lief.parse(path)
    segs = [dict(name=s.name, vmaddr=s.virtual_address, vmsize=s.virtual_size, fileoff=s.file_offset,
                 filesize=s.file_size) for s in b.segments if s.name != "__PAGEZERO"]
    secs = {s.name: dict(addr=s.virtual_address, size=s.size) for s in b.sections}
    def sec_of(a):
        for s in b.sections:
            if s.virtual_address <= a < s.virtual_address + s.size: return s.name
    binds = [[x.address, x.symbol.name, x.addend, sec_of(x.address)] for x in b.bindings]
    exports = {s.name: s.value for s in b.symbols if s.value}
    return dict(segments=segs, sections=secs, bindings=binds, exports=exports, entry=b.entrypoint)

if __name__ == "__main__":
    meta = macho(sys.argv[1])
    meta["objects"] = objects()
    json.dump(meta, open(os.path.join(ROOT, "game_meta.json"), "w"))
    print("ok", len(meta["bindings"]), "bindings,", len(meta["exports"]), "exports")
