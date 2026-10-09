"""Emulates NSApplicationMain: instantiate the nib objects, then run the main loop."""
import struct, sys, time, os
from .ucshim import *

def f2u(*fs): return list(struct.unpack("<%dI" % len(fs), struct.pack("<%df" % len(fs), *fs)))

_nomethod = set()


def send(e, rt, recv, sel, *args):
    """Call a message from python; guest IMPs are run in the emulator."""
    cls, meta = rt.class_of(recv)
    imp = rt.lookup(cls, sel, meta)
    if imp is None:
        if (cls.name, sel) not in _nomethod:
            _nomethod.add((cls.name, sel)); print(f"[launch] no method {sel} on {cls.name}", file=sys.stderr)
        return 0
    if not isinstance(imp, int):
        raise RuntimeError("host imp via send() not supported: " + sel)
    if getattr(e, 'dbg', False): print(f'[send] {cls.name} {sel} esp={e.reg(UC_X86_REG_ESP):#x}', file=sys.stderr)
    e.minsp = 0xFFFFFFFF
    r = e.call(imp, recv, e.sel_addr(sel), *args)
    if getattr(e, 'dbg', False): print(f'[send] done {sel} minsp={e.minsp:#x}', file=sys.stderr)
    return r

def launch(e, rt):
    app = rt.app
    G = rt.classes
    # delegate
    dcls = G["Asphalt6AppDelegate"]
    delegate = e.malloc(dcls.size); e.wr(delegate, bytes(dcls.size)); e.w32(delegate, dcls.addr)
    from .objc_rt import HObj
    rt.objs[delegate] = HObj(dcls.name)
    rt.obj(app).delegate = delegate
    # window (frame from the nib)
    wcls = G["CustomWindow"]
    win = e.malloc(wcls.size); e.wr(win, bytes(wcls.size)); e.w32(win, wcls.addr); rt.objs[win] = HObj(wcls.name)
    send(e, rt, win, "initWithContentRect:styleMask:backing:defer:", *f2u(70, 344, 1280, 556), 15, 2, 0)
    rt.main_window = win
    rt.obj(win).delegate = delegate
    # view
    vcls = G["EAGLView"]
    view = e.malloc(vcls.size); e.wr(view, bytes(vcls.size)); e.w32(view, vcls.addr); rt.objs[view] = HObj(vcls.name)
    send(e, rt, view, "initWithFrame:", *f2u(-400, 0, 2443, 1006))
    rt.obj(view).window = win
    rt.obj(win).content = view
    e.w32(delegate + dcls.ivars["m_viewWindow"], view)
    rt.delegate, rt.view = delegate, view
    send(e, rt, delegate, "applicationWillFinishLaunching:", 0)
    send(e, rt, delegate, "applicationDidFinishLaunching:", 0)
    from . import disable_patch
    if disable_patch.apply(e) != len(disable_patch.ENTRY_POINTS) + len(disable_patch.TERMINATE_POINTS): print('[patch] IGP/GLLive entry points: unexpected binary', file=sys.stderr)
    from . import a6text, wheels3d, duelai
    a6text.install(e); wheels3d.install(e); duelai.install(e)
    from . import garage_patch
    if not garage_patch.apply(e): print('[patch] garage input patch not applied (unexpected binary)', file=sys.stderr)


# W A S D mirror the arrow keys (the game only knows the arrows). Both can be held at the same time: a direction is
# "down" for the game while at least one of its keys is held, and released when the last one is.
_WASD = {13: (126, "\uf700"), 0: (123, "\uf702"), 1: (125, "\uf701"), 2: (124, "\uf703")}   # keyCode -> (arrow keyCode, arrow char)
_ARROWS = {126, 123, 125, 124}
_held = {}      # arrow keyCode -> set of source keyCodes currently down


_BIT = {126: 1, 125: 2, 123: 4, 124: 8}      # up, down, left, right: the ids the game's garage input code uses


def _publish_mask():
    """held arrow/WASD keys as a bit mask in guest memory (read by the patched garage input code, see garage_patch)"""
    import ctypes
    from . import garage_patch
    m = 0
    for arrow, held in _held.items():
        if held: m |= _BIT.get(arrow, 0)
    ctypes.c_uint32.from_address(garage_patch.MASK_ADDR).value = m


def keys_held():
    return any(_held.values())


def release_all_keys():
    _held.clear(); _publish_mask()


def remap_key(sel, ev):
    from AppKit import NSEvent, NSEventTypeKeyDown, NSEventTypeKeyUp
    code = int(ev.keyCode())
    if code not in _WASD and code not in _ARROWS:
        return ev
    if int(ev.modifierFlags()) & (1 << 20):             # Cmd+key is a shortcut, never a game key
        return ev
    arrow, ch = _WASD.get(code, (code, None))
    held = _held.setdefault(arrow, set())
    if bool(ev.isARepeat()):
        return None if code in _WASD else ev
    out = ev
    if sel == "keyDown:":
        first = not held; held.add(code)
        if not first: out = None
        elif code in _WASD:
            out = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                NSEventTypeKeyDown, (0, 0), 0, ev.timestamp(), ev.windowNumber(), None, ch, ch, False, arrow)
    else:
        held.discard(code)
        if held: out = None
        elif code in _WASD:
            out = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                NSEventTypeKeyUp, (0, 0), 0, ev.timestamp(), ev.windowNumber(), None, ch, ch, False, arrow)
    _publish_mask()
    return out


def run_loop(e, rt, seconds=None):
    from AppKit import NSApplication, NSDate, NSDefaultRunLoopMode
    app = NSApplication.sharedApplication()
    NSAnyEventMask = 0xFFFFFFFFFFFFFFFF
    t0 = time.monotonic()
    frames = 0
    _cf = __import__('os').environ.get('A6_CMDFILE'); cmdf = [_cf, 0, 0.0] if _cf else None
    script = list(getattr(e, 'script', []))     # [(time, 'click', x, y) | (time, 'key', code, chars)]
    from AppKit import NSEvent, NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp, NSEventTypeKeyDown, NSEventTypeKeyUp
    while rt.running:
        if seconds is not None and time.monotonic() - t0 > seconds:
            break
        while True:
            ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(NSAnyEventMask, NSDate.distantPast(), NSDefaultRunLoopMode, True)
            if ev is None:
                break
            app.sendEvent_(ev)
        if cmdf and time.monotonic() - cmdf[2] > 0.5:     # dev: A6_CMDFILE, lines "click x y" | "key code down|up" | "shot name" | "quit", appended while the game runs
            cmdf[2] = time.monotonic()
            try:
                with open(cmdf[0]) as f_:
                    f_.seek(cmdf[1]); new_ = f_.read(); cmdf[1] += len(new_.encode())
                now_ = time.monotonic() - t0
                for ln_ in new_.splitlines():
                    w_ = ln_.split()
                    if not w_: continue
                    if w_[0] == 'click': script += [(now_, 'mouse', float(w_[1]), float(w_[2]), 'down'), (now_ + 0.2, 'mouse', float(w_[1]), float(w_[2]), 'up')]
                    elif w_[0] == 'key': script.append((now_, 'key', int(w_[1]), {36: '\r', 49: ' '}.get(int(w_[1]), ''), w_[2]))
                    elif w_[0] == 'shot': script.append((now_, 'winshot', '/tmp/cmd_%s.png' % w_[1]))
                    elif w_[0] == 'quit': seconds = 0
                script.sort(key=lambda r: r[0])
            except Exception as ex_: print('[cmdfile]', ex_, file=sys.stderr)
        while script and time.monotonic() - t0 >= script[0][0]:
            ts = script.pop(0)
            if ts[1] == 'drawdump': e.dr_state = 1; continue
            if ts[1] == 'swapwheels':        # dev: swap the low/hi wheel node slots of every car (see n6/wheels3d.py)
                from . import wheels3d
                print('[wheels3d] swapped', wheels3d.swap_all(e), 'cars', file=sys.stderr); continue
            if ts[1] == 'aspatch':           # dev: patch the Options movie's action stream: A6_ASPATCH="offset:file"
                import ctypes as _c
                hp = e.u32(0xE0080000); blob = _c.string_at(0x30000000, hp - 0x30000000)
                i0 = blob.find(b'\x88\xdf\x0f\x10\x01popMenu')
                for spec in __import__('os').environ['A6_ASPATCH'].split(','):
                    off_, fn_ = spec.split(':'); data_ = open(fn_, 'rb').read()
                    e.wr(0x30000000 + i0 + int(off_, 16), data_)
                    print('[aspatch] wrote', len(data_), 'bytes at movie+', off_, 'base', hex(0x30000000 + i0), file=sys.stderr)
                continue
            if ts[1] == 'trace':             # dev: A6_TRACEFN="addr:nbytes:nargs" (hex addr) -> log every call
                from . import devtools
                ad, nb, na = __import__('os').environ['A6_TRACEFN'].split(':')
                def fmt_(e_, args, r):
                    try: return repr(e_.cstr(r)[:60]) if 0x10000000 <= r < 0x70000000 else ''
                    except Exception: return ''
                devtools.install_trace(e, int(ad, 16), int(nb), int(na), fmt=fmt_); print('[trace] installed', ad, file=sys.stderr); continue
            if ts[1] == 'aiwatch':           # dev: watch the AI controller of the opponent in a duel (car + 0x2564)
                import ctypes as _c
                from . import wheels3d
                e.lib.host_watch_add.argtypes = [_c.c_uint32, _c.c_uint32]
                for c_ in wheels3d.find_cars(e):
                    sub = c_ + 0x2564
                    if e.u32(sub + 0x40) in (6, 7):
                        e.lib.host_watch_add(sub, sub + 0x100); print('[watch] AI object', hex(sub), 'beh', e.u32(sub + 0x40), file=sys.stderr)
                continue
            if ts[1] == 'watchrange':
                import ctypes as _c
                e.lib.host_watch_add.argtypes = [_c.c_uint32, _c.c_uint32]
                lo_, hi_ = int(__import__('os').environ['A6_WLO'], 16), int(__import__('os').environ['A6_WHI'], 16)
                e.lib.host_watch_add(lo_, hi_); print('[watch] range', hex(lo_), hex(hi_), file=sys.stderr); continue
            if ts[1] == 'watch':             # dev: -act "t:watch:lo:hi" (hex addresses given as floats are not allowed: use watchstr)
                continue
            if ts[1] == 'watchstr':          # dev: watch every copy of a string in guest memory (text from env A6_WATCH)
                import ctypes as _c
                e.lib.host_watch_add.argtypes = [_c.c_uint32, _c.c_uint32]
                txt = __import__('os').environ.get('A6_WATCH', 'Speed Unit').encode() + b'\0'; wl = []
                hp = e.u32(0xE0080000)
                for base, size in ((0x10000000, e.heap_ptr - 0x10000000), (0x20000000, e.pyheap["ptr"] - 0x20000000), (0x30000000, hp - 0x30000000)):
                    blob = _c.string_at(base, size); i = 0
                    while len(wl) < 4:
                        i = blob.find(txt, i)
                        if i < 0: break
                        wl.append(base + i); i += 1
                for a_ in wl:
                    e.lib.host_watch_add(a_, a_ + len(txt)); print('[watch] add', hex(a_), file=sys.stderr)
                print('[watch] mprotect errno', e.lib.host_watch_err(), file=sys.stderr)
                if __import__('os').environ.get('A6_WATCHTEST') and wl: _c.string_at(wl[0], 4)
                continue
            if ts[1] == 'watchdump':
                import ctypes as _c
                e.lib.host_watch_log.restype = _c.POINTER(_c.c_uint32); e.lib.host_watch_pos.restype = _c.c_uint32
                n = e.lib.host_watch_pos(); lg = e.lib.host_watch_log()
                e.lib.host_watch_stop()
                print('[watch] entries', n, file=sys.stderr)
                for k in range(min(n, 400)):
                    r = lg[k * 50:(k + 1) * 50]
                    fr = [hex(x) for x in r[2:18] if x]
                    st = [hex(x) for x in r[18:] if 0x2c10 <= x < 0x6abe92]
                    print(f'[watch] fault {r[0]:#x} esp {r[1]:#x} frames {fr} stack-text {st[:10]}', file=sys.stderr)
                continue
            if ts[1] == 'memdump':           # dev: write the guest's data/heap memory to /tmp/memdump_<n>.bin (+ .json with the ranges)
                import ctypes as _c, json
                e.md_n = getattr(e, 'md_n', 0) + 1
                hp = e.u32(0xE0080000)
                rngs = [(0x6f0000, 0x100000), (0x10000000, e.heap_ptr - 0x10000000), (0x30000000, max(0, hp - 0x30000000))]
                with open(f'/tmp/memdump_{e.md_n}.bin', 'wb') as _f:
                    for a, n in rngs: _f.write(_c.string_at(a, n))
                json.dump(rngs, open(f'/tmp/memdump_{e.md_n}.json', 'w'))
                print('[memdump]', e.md_n, rngs, file=sys.stderr)
                if getattr(e, 'freadlog', None) is not None: json.dump(e.freadlog, open(f'/tmp/freadlog_{e.md_n}.json', 'w'))
                continue
            if ts[1] == 'nativefs':          # dev: the user presses the green button
                rt.real_window(rt.obj(rt.main_window)).toggleFullScreen_(None); continue
            if ts[1] == 'warp':              # dev: move the real mouse pointer (global screen coordinates, top-left origin)
                import Quartz
                Quartz.CGWarpMouseCursorPosition((ts[2], ts[3])); continue
            if ts[1] == 'cursor':
                import Quartz
                w_ = rt.real_window(rt.obj(rt.main_window)); f_ = w_.frame()
                print(f'[cursor] t={ts[2]} system cursor visible={bool(Quartz.CGCursorIsVisible())} fs_want={rt.fs_want} win={tuple(round(float(x)) for x in (f_.origin.x, f_.origin.y, f_.size.width, f_.size.height))} fs={bool(int(w_.styleMask()) & (1<<14))}', file=sys.stderr); continue
            if ts[1] == 'snap':              # dev: snapshot of the game's data segment (find which words a key press changes)
                import ctypes as _c
                e.snaps = getattr(e, 'snaps', []); e.snaps.append((ts[2], _c.string_at(0x6f0000, 0x90000)))
                continue
            if ts[1] in ('mini', 'unmini', 'hide', 'unhide'):      # dev: window state changes
                w = rt.real_window(rt.obj(rt.main_window))
                if ts[1] == 'mini': w.miniaturize_(None)
                elif ts[1] == 'unmini': w.deminiaturize_(None)
                elif ts[1] == 'hide': app.hide_(None)
                else: app.unhide_(None)
                continue
            if ts[1] == 'winshot':          # dev: capture every window of this process as composited on screen
                import Quartz, os
                from AppKit import NSBitmapImageRep
                infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
                n = 0
                for inf in infos:
                    if inf.get("kCGWindowOwnerPID") != os.getpid() or not inf.get("kCGWindowIsOnscreen"): continue
                    img = Quartz.CGWindowListCreateImage(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, inf["kCGWindowNumber"], Quartz.kCGWindowImageBoundsIgnoreFraming)
                    if img is None: continue
                    NSBitmapImageRep.alloc().initWithCGImage_(img).representationUsingType_properties_(4, None).writeToFile_atomically_(ts[2].replace(".png", f"_{n}.png"), True)
                    print("[winshot]", ts[2], n, inf.get("kCGWindowBounds"), "layer", inf.get("kCGWindowLayer"), file=sys.stderr)
                    n += 1
                continue
            win = rt.real_window(rt.obj(rt.main_window))
            wn = win.windowNumber()
            if ts[1] == 'mouse':
                rt.trace = getattr(e, 'trace_click', False); rt.trace_until = time.monotonic() + 0.4
                typ = NSEventTypeLeftMouseDown if ts[4] == 'down' else NSEventTypeLeftMouseUp
                ev = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                    typ, (ts[2], ts[3]), 0, time.time(), wn, None, 0, 1, 1.0 if ts[4] == 'down' else 0.0)
                rt.events.append(("mouseDown:" if ts[4] == 'down' else "mouseUp:", ev))
            elif ts[1] == 'fs':
                send(e, rt, rt.delegate, 'toggleFullScreen')
            elif ts[1] == 'key':
                typ = NSEventTypeKeyDown if ts[4] == 'down' else NSEventTypeKeyUp
                ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    typ, (0, 0), 0, time.time(), wn, None, ts[3], ts[3], False, ts[2])
                rt.events.append(("keyDown:" if ts[4] == 'down' else "keyUp:", ev))
        # input events captured by the game view
        rt.sync_window(lambda recv, sel, *a: send(e, rt, recv, sel, *a))
        while rt.events:
            sel, ev = rt.events.popleft()
            if sel in ("keyDown:", "keyUp:"):
                ev = remap_key(sel, ev)
                if ev is None: continue
            loc = ev.locationInWindow() if hasattr(ev, "locationInWindow") else None
            o = rt.new_obj("NSEvent", ev=ev, pt=rt.map_pt(float(loc.x), float(loc.y)) if loc else (0.0, 0.0))
            send(e, rt, getattr(rt, 'video_view', 0) or rt.view, sel, o)
        now = time.monotonic()
        bg = 3.0 if not app.isActive() else 1.0          # another app is in front: the game still runs, but at a third of the rate (battery)
        for t in list(rt.timers):
            if t[0] <= now:
                if t[1]:
                    t[0] = now + t[1] * bg
                else:
                    rt.timers.remove(t)
                _t1 = time.perf_counter()
                send(e, rt, t[2], rt.selname(t[3]), t[4])
                if getattr(e, 'fps', False):
                    _d = (time.perf_counter() - _t1) * 1000
                    e.ftimes = getattr(e, 'ftimes', []); e.ftimes.append(_d)
                    if getattr(e, 'ftrace', None) is not None: e.ftrace.append(round(_d, 1))
                    e.timing = True
                    if len(e.ftimes) >= 300:
                        _s = sorted(e.ftimes); print(f'[frame ms] p50={_s[150]:.1f} p90={_s[270]:.1f} p99={_s[297]:.1f} max={_s[-1]:.1f} | hooks: {e.hook_ns / 1e6 / 300:.2f} ms/frame, {e.hook_calls / 300:.0f} calls/frame | ring {e.ring_ns / 1e6 / 300:.2f} ms/frame, {e.ring_cmds / 300:.0f} cmds', file=sys.stderr); e.ring_ns = 0; e.ring_cmds = 0; e.ftimes = []; e.hook_ns = 0; e.hook_calls = 0
                        if getattr(e, 'top_names', False):
                            tn = sorted(e.name_ns.items(), key=lambda kv: -kv[1][0])[:34]
                            print('   top: ' + ', '.join(f'{k}={v[0] / 1e6 / 300:.2f}ms/{v[1] / 300:.0f}' for k, v in tn), file=sys.stderr)
                        e.name_ns = {}
                frames += 1
                if getattr(e, 'fps', False) and frames % 3000 == 0:
                    import resource, os as _o, threading as _th
                    print(f'[mem] t={time.monotonic() - t0:.0f}s rss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576:.0f}MB objs={len(rt.objs)} '
                          f'threads={_th.active_count()} fds={len(_o.listdir("/dev/fd"))} guest_heap={(e.u32(0xE0080000) - 0x30000000) // 1048576}MB '
                          f'pyarena={(e.pyheap["ptr"] - 0x20000000) // 1024}KB/256MB static={(e.heap_ptr - 0x10000000) // 1024}KB/256MB', file=sys.stderr)
                if frames % 50 == 0 and (getattr(e, 'dbg', False) or getattr(e, 'fps', False)):
                    _n = time.monotonic(); print(f'[fps] {frames} ticks, {frames / (_n - t0):.1f}/s avg, {50 / max(_n - getattr(e, "_lastfps", t0), 1e-6):.1f}/s now', file=sys.stderr); e._lastfps = _n
        if e.stopped_on:
            print("stopped:", e.stopped_on); break
        if rt.trace and getattr(rt, 'trace_until', 0) and time.monotonic() > rt.trace_until: rt.trace = False
        _nxt = min([t[0] for t in rt.timers], default=time.monotonic() + 0.004) - time.monotonic()
        # sleep in the event queue until the next timer is due (input wakes us at once)
        _w = min(max(_nxt, 0.0005), 0.004 if script else 0.016)
        _ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(NSAnyEventMask, NSDate.dateWithTimeIntervalSinceNow_(_w), NSDefaultRunLoopMode, True)
        if _ev is not None: app.sendEvent_(_ev)
    print(f"loop ended, {frames} timer ticks in {time.monotonic() - t0:.1f}s")
