"""Adds the "3D Wheels in Race" toggle to the Visuals page of the game's Options movie (data/file000516.dat, a plain SWF/AS2
movie with a changed signature). Usage: patch_options_swf.py <original file000516.dat> <output file>

Layout (component_video sprite): the speed unit, Map/Screen and Camera groups are nudged apart and a copy of the Map label and
button is placed between the second and third group. The texts come from three keys served by the host (see n6/a6text.py):
General:A6_WHEELS_LABEL (title), General:A6_WHEELS_STATE (current value) and General:A6_WHEELS_TOGGLE (flip, returns the new value)."""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swflib import *
from asasm import *

SPRITE = 125                      # component_video
DY = {"txt_units": -38, "txt_kmh": -38, "txt_mph": -38, "component_slider_OnOff_units": -38,
      "txt_detail": -108, "txt_screen": -108, "btn_detail": -108, "btn_screen": -108,
      "txt_camera": 10, "component_camera": 10, None: 10}                  # None: the unnamed decoration of the camera selector
ROW = 166                         # distance of the new row below the Map row


def enc_matrix(tx, ty, scale=None):
    """MATRIX without rotation, translation in twips, optional uniform scale"""
    nb = max(abs(tx), abs(ty), 1).bit_length() + 1
    head = "0"
    if scale is not None:
        fx = int(round(scale * 65536)); sn = fx.bit_length() + 1
        head = "1" + format(sn, "05b") + "".join(format(fx & ((1 << sn) - 1), "0%db" % sn) for _ in (0, 1))
    bits = head + "0" + format(nb, "05b") + "".join(format(v & ((1 << nb) - 1), "0%db" % nb) for v in (tx, ty))
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def tag_bytes(code, body):
    return struct.pack("<HI", (code << 6) | 0x3F, len(body)) + body


def parse_place(b, s, q, l):
    d = place2(b, q, l)
    m = d["matrix"]
    assert m[:4] == (1.0, 1.0, 0.0, 0.0), "scaled placement"
    d["tail"] = b[d["end"]:q + l]              # clip actions
    return d


def build_place(d, depth, name, tx, ty, scale=None):
    body = bytes([d["flags"]]) + struct.pack("<HH", depth, d["char"]) + enc_matrix(tx, ty, scale) + name.encode() + b"\0" + d["tail"]
    return tag_bytes(26, body)


def actions(name_chain, member_tail):
    pass


def chain(*names):
    """_root.a.b.c as stack code (leaves the object on the stack)"""
    c = push(s("_root")) + GETVAR
    for n in names:
        c += push(s(n)) + GETMEMBER
    return c


def settext_id(clip, key, page=("menu_video", "component_video")):
    """_root.<page>.<clip>.setTextID(key)"""
    return push(s(key), i(1)) + chain(*page, *clip) + push(s("setTextID")) + CALLMETHOD + POP


def define_function(code):
    return b"\x9b" + struct.pack("<H", 1 + 2 + 2) + b"\0" + struct.pack("<H", 0) + struct.pack("<H", len(code)) + code


def row_actions(page, txt, btn, label_key, state_key, toggle_key, direct=False):
    """texts + click handler of one toggle row. direct=True assigns onRelease (the component's own release handling broadcasts an
    event that makes the Gameplay page leave to the garage) and replays the button's "released" animation itself."""
    c = settext_id([txt], label_key, page)
    c += settext_id([btn, "mc_label"], state_key, page)
    body = chain(*page, btn, "mc_label", "tf") + push(s("text"), s(toggle_key), i(1), s("getString")) + CALLFUNC + SETMEMBER
    if direct:
        body += push(s("released"), i(1), s("this")) + GETVAR + push(s("gotoAndPlay")) + CALLMETHOD + POP
    c += chain(*page, btn) + push(s("onRelease" if direct else "onUp")) + define_function(body) + SETMEMBER
    return c


def new_actions():
    return (row_actions(("menu_video", "component_video"), "txt_wheels", "btn_wheels",
                        "General:A6_WHEELS_LABEL", "General:A6_WHEELS_STATE", "General:A6_WHEELS_TOGGLE") +
            row_actions(("menu_controls", "component_controls"), "txt_duel", "btn_duel",
                        "General:A6_DUEL_LABEL", "General:A6_DUEL_STATE", "General:A6_DUEL_TOGGLE", direct=True))


CONTROLS = 165                    # component_controls (the Controls page, renamed "Gameplay")
DUEL_SCALE = 0.62                 # smaller: the row must stay clear of the hit areas of the main menu behind this page
DUEL_LABEL_XY = (680.1, 522.5)    # label origin / button origin on that page (top right, above the mouse picture)
DUEL_BTN_XY = (333.6, 240.7)


def main(src, dst):
    b = open(src, "rb").read()
    r, p = rect(b, 8); p += 4
    out = [b[:p]]
    src_txt = src_btn = None                       # the Map label and button of the Visuals page are the models of every new row
    for t, st, q, l in tags(b, p, len(b)):
        if t == 39 and struct.unpack_from("<H", b, q)[0] == SPRITE:
            for t2, s2, q2, l2 in tags(b, q + 4, q + l):
                if t2 == 26:
                    d = parse_place(b, s2, q2, l2)
                    if d.get("name") == "txt_detail": src_txt = d
                    if d.get("name") == "btn_detail": src_btn = d
    assert src_txt and src_btn
    for t, st, q, l in tags(b, p, len(b)):
        raw = b[st:q + l]
        if t == 39 and struct.unpack_from("<H", b, q)[0] == CONTROLS:
            cid, fr = struct.unpack_from("<HH", b, q)
            kids = []; added = False
            for t2, s2, q2, l2 in tags(b, q + 4, q + l):
                if t2 == 1 and not added:
                    kids.append(build_place(src_txt, 120, "txt_duel", int(DUEL_LABEL_XY[0] * 20), int(DUEL_LABEL_XY[1] * 20), DUEL_SCALE))
                    kids.append(build_place(src_btn, 122, "btn_duel", int(DUEL_BTN_XY[0] * 20), int(DUEL_BTN_XY[1] * 20), DUEL_SCALE))
                    added = True
                kids.append(b[s2:q2 + l2])
            out.append(tag_bytes(39, struct.pack("<HH", cid, fr) + b"".join(kids))); continue
        if t == 39 and struct.unpack_from("<H", b, q)[0] == SPRITE:
            cid, fr = struct.unpack_from("<HH", b, q)
            kids = []; added = False
            txt_d = btn_d = None
            for t2, s2, q2, l2 in tags(b, q + 4, q + l):
                if t2 == 26:
                    d = parse_place(b, s2, q2, l2)
                    if d.get("name") == "txt_detail": txt_d = (d, s2, q2, l2)
                    if d.get("name") == "btn_detail": btn_d = (d, s2, q2, l2)
            assert txt_d and btn_d
            for t2, s2, q2, l2 in tags(b, q + 4, q + l):
                if t2 == 26:
                    d = parse_place(b, s2, q2, l2)
                    nm = d.get("name")
                    if nm in DY or (nm is None and d["char"] == 114):
                        m = d["matrix"]
                        kids.append(build_place(d, d["depth"], nm or "", int(m[4]), int(m[5] + DY.get(nm, DY[None]) * 20)) if nm else
                                    tag_bytes(26, bytes([d["flags"]]) + struct.pack("<HH", d["depth"], d["char"]) + enc_matrix(int(m[4]), int(m[5] + DY[None] * 20)) + d["tail"]))
                        continue
                if t2 == 1 and not added:
                    (dt, *_), (db, *_) = txt_d, btn_d
                    mt, mb = dt["matrix"], db["matrix"]
                    ms = [x for x in ()]
                    # screen counterparts for the horizontal centre
                    xs = {}
                    for t3, s3, q3, l3 in tags(b, q + 4, q + l):
                        if t3 == 26:
                            d3 = parse_place(b, s3, q3, l3)
                            if d3.get("name") in ("txt_screen", "btn_screen"): xs[d3["name"]] = d3["matrix"]
                    cx_t = int((mt[4] + xs["txt_screen"][4]) / 2); cx_b = int((mb[4] + xs["btn_screen"][4]) / 2)
                    ty_new = int(mt[5] + (DY["txt_detail"] + ROW) * 20); by_new = int(mb[5] + (DY["btn_detail"] + ROW) * 20)
                    kids.append(build_place(dt, 60, "txt_wheels", cx_t, ty_new))
                    kids.append(build_place(db, 62, "btn_wheels", cx_b, by_new))
                    added = True
                kids.append(b[s2:q2 + l2])
            body = struct.pack("<HH", cid, fr) + b"".join(kids)
            out.append(tag_bytes(39, body)); continue
        if t == 12 and b.find(b"\x88\xdf\x0f\x10\x01popMenu", q, q + l) == q:
            assert b[q + l - 1] == 0 and b[q + l - 2] == 0x4f, "unexpected end of the action block"
            out.append(tag_bytes(12, b[q:q + l - 1] + new_actions() + b"\0")); continue
        out.append(raw)
    res = b"".join(out)
    res = res[:4] + struct.pack("<I", len(res)) + res[8:]
    open(dst, "wb").write(res)
    print("written", dst, len(b), "->", len(res))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
