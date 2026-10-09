"""Serves the three text keys used by the "3D Wheels in Race" row that tools/patch_options_swf.py adds to the Visuals page."""
import sys
from . import a6settings, devtools

TEXT_LOOKUP = 0x122390          # const char *text(Texts *, const char *table, const char *key)
LABEL = b"3D Wheels in Race"


def install(e):
    if bytes(e.rd(TEXT_LOOKUP, 5)) != b"\x55\x89\xe5\x57\x56":
        print("[patch] text lookup: unexpected binary", file=sys.stderr); return False
    def cstring(txt):
        a_ = e.alloc_static(len(txt) + 1, 1); e.wr(a_, txt + b"\0"); return a_
    labels = {b"A6_WHEELS_LABEL": cstring(b"3D Wheels in Race"), b"A6_DUEL_LABEL": cstring(b"Original Duel Difficulty")}
    gameplay = cstring(b"Gameplay")
    # the former "Controls" page / tile is now called Gameplay
    toggles = {b"A6_WHEELS": "wheels3d", b"A6_DUEL": "duel_original"}
    k_on = e.alloc_static(8, 1); e.wr(k_on, b"SP_ON\0"); k_off = e.alloc_static(8, 1); e.wr(k_off, b"SP_OFF\0")
    gen = e.alloc_static(8, 1); e.wr(gen, b"General\0")

    cnt = [0]
    def handler(e_, args, call_orig):
        if __import__("os").environ.get("A6_TEXTSTATS"):
            cnt[0] += 1
            if cnt[0] % 500 == 0: print("[a6text] lookups", cnt[0], file=sys.stderr)
        key = e_.cstr(args[2]) if args[2] else b""
        if key == b"BTN_CONTROLS": return gameplay
        if key.startswith(b"A6_"):
            if key in labels: return labels[key]
            for pre, name in toggles.items():
                if key == pre + b"_TOGGLE": a6settings.set(name, not a6settings.get(name))
                if key.startswith(pre + b"_"):
                    return call_orig(args[0], gen, k_on if a6settings.get(name) else k_off)   # the localized On / Off
        return call_orig(*args)
    devtools.install_detour(e, TEXT_LOOKUP, 5, 3, handler)
    return True
