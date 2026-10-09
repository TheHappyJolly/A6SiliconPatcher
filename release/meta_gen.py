"""Runs on the user's Mac (bundled x86_64 Python + lief): analyses the user's own game executable and writes game_meta.json
(segments, imports, symbol addresses) that the engine needs. Usage: meta_gen.py <game executable> <objects.json> <output json>
objects.json holds the symbol tables of the engine's own helper objects (not derived from the game)."""
import json, sys, lief


def macho(path):
    b = lief.parse(path)
    if hasattr(b, "at"): b = b.at(0)
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
    meta["objects"] = json.load(open(sys.argv[2]))
    json.dump(meta, open(sys.argv[3], "w"))
    print("ok", len(meta["bindings"]), "bindings,", len(meta["exports"]), "exports")
