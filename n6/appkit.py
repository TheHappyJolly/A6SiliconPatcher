"""AppKit stand-ins: application, windows, views, OpenGL context, timers, events."""
import os, struct, sys, time, collections, ctypes
from .ucshim import *
from .objc_rt import HObj
from .cocoa import A

import objc
from AppKit import (NSApplication, NSWindow, NSView, NSScreen, NSMakeRect, NSBackingStoreBuffered,
                    NSOpenGLContext, NSOpenGLPixelFormat, NSEvent, NSApplicationActivationPolicyRegular,
                    NSCursor, NSColor, NSOpenGLPFADoubleBuffer, NSOpenGLPFADepthSize, NSOpenGLPFAStencilSize,
                    NSOpenGLPFAColorSize, NSOpenGLPFAAccelerated, NSOpenGLPFAAlphaSize, NSOpenGLPFASampleBuffers,
                    NSOpenGLPFASamples, NSOpenGLPFANoRecovery)
from Foundation import NSObject, NSDate, NSRunLoop, NSDefaultRunLoopMode


def rect_pack(x, y, w, h): return struct.pack("<4f", x, y, w, h)


class HostDelegate(NSObject):
    """Quit handling for the host window / menu."""
    def quit_(self, sender): self.rt.running = False
    def windowWillClose_(self, note): self.rt.running = False
    def windowShouldClose_(self, w): return True
    # native full-screen transitions: ours (requested by the game) or the user's (green button, Ctrl-Cmd-F, Esc)
    def windowWillEnterFullScreen_(self, n): self.rt.fs_will(True)
    def windowWillExitFullScreen_(self, n): self.rt.fs_will(False)
    def windowDidEnterFullScreen_(self, n): self.rt.fs_did(True)
    def windowDidExitFullScreen_(self, n): self.rt.fs_did(False)
    def windowDidFailToEnterFullScreen_(self, w): self.rt.fs_did(False)
    def windowDidFailToExitFullScreen_(self, w): self.rt.fs_did(True)
    def window_willUseFullScreenPresentationOptions_(self, w, proposed):
        # hide the menu bar and the Dock completely (also when the user keeps them visible in full screen)
        return (int(proposed) & ~((1 << 0) | (1 << 2))) | (1 << 1) | (1 << 3) | (1 << 10)



class GameWindow(NSWindow):
    """Borderless full-screen windows must still be able to take key/main status."""
    def canBecomeKeyWindow(self): return True
    def canBecomeMainWindow(self): return True


class GameView(NSView):
    """Real NSView that captures input and forwards it to a python queue."""
    def initWithFrame_(self, f):
        self = objc.super(GameView, self).initWithFrame_(f)
        return self
    def acceptsFirstResponder(self): return True
    def isFlipped(self): return False
    def mouseDown_(self, ev): self.q.append(("mouseDown:", ev))
    def mouseUp_(self, ev): self.q.append(("mouseUp:", ev))
    def mouseDragged_(self, ev): self.q.append(("mouseDragged:", ev))
    def mouseMoved_(self, ev): self.q.append(("mouseMoved:", ev))
    def rightMouseDown_(self, ev): self.q.append(("mouseDown:", ev))
    def rightMouseUp_(self, ev): self.q.append(("mouseUp:", ev))
    def keyDown_(self, ev): self.q.append(("keyDown:", ev))
    def keyUp_(self, ev): self.q.append(("keyUp:", ev))
    def mouseEntered_(self, ev): self.q.append(("mouseEntered:", ev))
    def mouseExited_(self, ev): self.q.append(("mouseExited:", ev))


def install(rt, e, M):
    nsstr = rt.nsstr
    py = rt.py
    h = e.hle
    hostapp = NSApplication.sharedApplication()
    hostapp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    events = collections.deque()
    hdel = HostDelegate.alloc().init(); hdel.rt = rt; rt.hdel = hdel
    from AppKit import NSMenu, NSMenuItem as _NSMenuItem, NSImage
    mm = NSMenu.alloc().init(); appitem = _NSMenuItem.alloc().init(); mm.addItem_(appitem)
    appmenu = NSMenu.alloc().init()
    qi = _NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit Asphalt 6", "quit:", "q"); qi.setTarget_(hdel)
    appmenu.addItem_(qi); appitem.setSubmenu_(appmenu); hostapp.setMainMenu_(mm)
    try:
        import os as _os
        icon = NSImage.alloc().initByReferencingFile_(_os.path.join(e.hle_data.get("_bundle", ""), "Contents/Resources/IconMacOSX.icns") if False else __import__("a6.emu", fromlist=["x"]).BUNDLE + "/Contents/Resources/IconMacOSX.icns")
        hostapp.setApplicationIconImage_(icon)
    except Exception:
        pass
    GameView.q = events
    rt.events = events
    rt.timers = []                # [(next_time, interval, target, selname, obj_addr, id)]
    rt.running = True
    rt.pres_opts = 0
    rt.fs_want = False            # the game asked for full screen (it believes it owns the whole screen)
    rt.pending_frame = None
    rt.fs_busy = False            # a native full-screen transition is in progress
    rt.fs_busy_t = 0.0
    rt.fs_req = None              # target of a transition we started ourselves
    rt.fs_user = None             # target of a transition the user started (green button, Esc ...)
    rt.game_fs = None             # what the game believes (borderless style = full screen)
    def _fs_will(direction):
        rt.fs_busy = True; rt.fs_busy_t = time.monotonic()
        if rt.fs_req is None: rt.fs_user = direction
    def _fs_did(direction):
        rt.fs_busy = False; rt.fs_req = None
        rt.backing_dirty = True       # the drawable was recreated by the Space transition: re-apply the surface size
    rt.fs_will, rt.fs_did = _fs_will, _fs_did
    rt.gvrect = None              # GL view rectangle inside the window container (points)
    MAXFPS = float(os.environ.get('A6_MAXFPS', '60'))
    FILL = os.environ.get("A6_FILL", "fit")      # fit: exact 16:9 with bars; crop: fill the screen, cropping the edges
    STRETCH = float(os.environ.get("A6_STRETCH", "1.03"))   # max. aspect distortion before cropping instead

    e.calc_V = lambda: calc_V()

    def native_fs(w): return bool(int(w.styleMask()) & (1 << 14))

    native_cache = {}
    def native_panel(sc):
        """physical pixel size (w, h) of the screen's panel: the mode flagged "native" (macOS scaled modes render at a larger size and
        resample, so the framebuffer size says nothing about how many pixels the panel really has); None if unknown"""
        try:
            import Quartz
            did = int(sc.deviceDescription()["NSScreenNumber"])
            if did not in native_cache:
                best = None
                for m in Quartz.CGDisplayCopyAllDisplayModes(did, {Quartz.kCGDisplayShowDuplicateLowResolutionModes: True}) or []:
                    if Quartz.CGDisplayModeGetIOFlags(m) & 0x02000000:
                        d = (int(Quartz.CGDisplayModeGetPixelWidth(m)), int(Quartz.CGDisplayModeGetPixelHeight(m)))
                        if best is None or d[0] * d[1] > best[0] * best[1]: best = d
                native_cache[did] = best
            return native_cache[did]
        except Exception:
            return None

    def calc_V():
        """Size (in the game's units) of the 16:9 frame the game renders into while full screen: as many pixels as the panel
        really shows times the supersampling factor (anti-aliasing), but never more than 2160 lines."""
        sc = NSScreen.mainScreen()
        f = sc.frame(); scale = float(sc.backingScaleFactor())
        hpx = min(float(f.size.height) * scale, 2160.0)
        npn = native_panel(sc)
        if npn: hpx = min(hpx, float(npn[1]), npn[0] * 9 / 16.0)         # the 16:9 frame is fitted into the panel
        # the 3D scene is rendered into an off-screen target that MSAA does not reach: draw SS times more lines than the panel has and let
        # the display scaler average them (supersampling), as the 1912-line frame on a 2560x1664 panel did before; never beyond the
        # backing store of the screen or 2160 lines
        ss = float(os.environ.get("A6_SS", 1.333))
        hpx = min(hpx * ss, float(f.size.height) * scale, 2160.0) if ss > 1.0 else hpx
        hpx = float(os.environ.get("A6_HEIGHT", 0)) or hpx
        hv = int(hpx); wv = int(round(hv * 16 / 9 / 2) * 2)
        return (float(wv), float(hv))

    def ret_self(rt_, e_, r, s): return r
    def ret_nil(rt_, e_, r, s): return 0
    def ret_yes(rt_, e_, r, s): return 1

    def sgn_i(v): return v - (1 << 32) if v & 0x80000000 else v

    def f4(n, base=0):
        return struct.unpack("<f", struct.pack("<I", A(e, base + n)))[0]

    # -------------------------------------------------------------- NSScreen
    scr = NSScreen.mainScreen().frame()
    SCREEN = (0.0, 0.0, float(scr.size.width), float(scr.size.height))
    vis = NSScreen.mainScreen().visibleFrame()
    VISIBLE = (float(vis.origin.x), float(vis.origin.y), float(vis.size.width), float(vis.size.height))
    main_screen = rt.new_obj("NSScreen")
    M("NSScreen", "mainScreen", meta=True)(lambda rt_, e_, r, s: main_screen)
    FAKE = [float(v) for v in os.environ["A6_FAKE"].split("x")] if os.environ.get("A6_FAKE") else None
    def scr_rect(which):
        if rt.fs_want: return (0.0, 0.0) + calc_V()
        if FAKE: return (0.0, 0.0, FAKE[0], FAKE[1])
        sc = NSScreen.mainScreen()
        f = sc.frame() if which == "frame" else sc.visibleFrame()
        return (float(f.origin.x), float(f.origin.y), float(f.size.width), float(f.size.height))
    M("NSScreen", "frame")(lambda rt_, e_, r, s: (e_.wr(e_.arg(0), rect_pack(*scr_rect("frame"))), e_.arg(0))[1])
    def vis_frame(rt_, e_, r, s):
        rc = scr_rect("visible")
        if rt.pres_opts & 0xA or rt.fs_want:         # menu bar and dock hidden -> AppKit reports the whole screen
            rc = scr_rect("frame")
        if getattr(e_, "dbg", False): print("[screen] visibleFrame", rc, "frame", scr_rect("frame"), file=sys.stderr)
        e_.wr(e_.arg(0), rect_pack(*rc)); return e_.arg(0)
    M("NSScreen", "visibleFrame")(vis_frame)
    M("NSColor", "blackColor", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSColor"))
    cursor_obj = rt.new_obj("NSCursor")
    cur_state = {"cnt": 0, "real": False}       # cnt: the game's nested hide/unhide count; real: our hide of the system cursor
    def cursor_hide(rt_, e_, r, s):
        if s == "hide": cur_state["cnt"] += 1
        else: cur_state["cnt"] = max(0, cur_state["cnt"] - 1)
        return 0
    def update_cursor(w):
        """The system cursor is hidden only while the game wants it hidden AND the pointer is inside the game window of
        the active app; leaving the window / switching apps always brings the real cursor back."""
        want = False
        try:
            if cur_state["cnt"] > 0 and w.isVisible() and hostapp.isActive():
                loc = NSEvent.mouseLocation()
                cr = w.contentRectForFrameRect_(w.frame())
                want = cr.origin.x <= loc.x <= cr.origin.x + cr.size.width and cr.origin.y <= loc.y <= cr.origin.y + cr.size.height
                if want and not w.isKeyWindow(): want = False
        except Exception:
            want = False
        import Quartz
        vis = bool(Quartz.CGCursorIsVisible())
        if want and cur_state["real"] and vis:
            cur_state["real"] = False                  # AppKit reset the hidden state behind our back (window change, cursor rects)
        if want and not cur_state["real"]:
            NSCursor.hide(); cur_state["real"] = True
        elif not want:
            if cur_state["real"] or not vis:
                for _ in range(8):                     # unwind until the real cursor is really back
                    NSCursor.unhide()
                    if Quartz.CGCursorIsVisible(): break
            cur_state["real"] = False
    M("NSCursor", "hide unhide", meta=True)(cursor_hide)

    # ----------------------------------------------------------- NSApplication
    app = rt.new_obj("NSApplication", delegate=0)
    e.w32(e.hle_data["_NSApp"], app)
    rt.app = app
    M("NSApplication", "sharedApplication", meta=True)(lambda rt_, e_, r, s: app)
    M("NSApplication", "setDelegate:")(lambda rt_, e_, r, s: setattr(rt.obj(r), "delegate", A(e_, 0)) or 0)
    M("NSApplication", "delegate")(lambda rt_, e_, r, s: rt.obj(r).delegate)
    def terminate(rt_, e_, r, s):
        rt.running = False; return 0
    M("NSApplication", "terminate:")(terminate)
    M("NSApplication", "currentSystemPresentationOptions")(lambda rt_, e_, r, s: rt.pres_opts)
    def set_pres(rt_, e_, r, s):
        rt.pres_opts = A(e_, 0)
        if getattr(e_, 'dbg', False): print('[app] setPresentationOptions', hex(A(e_, 0)), file=sys.stderr)
        return 0
    M("NSApplication", "setPresentationOptions:")(set_pres)

    # -------------------------------------------------------------- NSWindow
    def win_init(rt_, e_, r, s):
        o = rt.obj(r)
        x, y, w, hh = struct.unpack("<4f", struct.pack("<4I", *[A(e_, i) for i in range(4)]))
        o.frame = [x, y, w, hh]
        o.style = A(e_, 4)
        o.title = "Asphalt 6"
        o.content = 0
        o.real = None
        o.delegate = 0
        return r
    M("NSWindow", "initWithContentRect:styleMask:backing:defer:")(win_init)

    def real_window(o):
        if o.real is None:
            fr = NSMakeRect(*o.frame)
            w = GameWindow.alloc().initWithContentRect_styleMask_backing_defer_(fr, 15, NSBackingStoreBuffered, False)
            w.setReleasedWhenClosed_(False)
            w.setBackgroundColor_(NSColor.blackColor())
            w.setCollectionBehavior_(1 << 7)          # NSWindowCollectionBehaviorFullScreenPrimary
            w.setTitle_(o.title)
            cont = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, o.frame[2], o.frame[3]))
            w.setContentView_(cont)
            gv = GameView.alloc().initWithFrame_(NSMakeRect(0, 0, o.frame[2], o.frame[3]))
            gv.setAutoresizingMask_(18)
            cont.addSubview_(gv)
            w.makeFirstResponder_(gv)
            o.cont = cont
            w.setAcceptsMouseMovedEvents_(True)
            w.setDelegate_(hdel)
            w.center()
            try:
                from AppKit import NSTrackingArea
                ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(NSMakeRect(0, 0, 10, 10), 0x01 | 0x80 | 0x200 | 0x40, gv, None)  # enter/exit, always, in visible rect
                cont.addTrackingArea_(ta); o.ta = ta
            except Exception: pass
            o.real = w
            o.gv = gv
        return o.real
    rt.real_window = real_window

    def layout(win):
        """Place the GL view inside the window container. Windowed: it fills the window. Full screen: the game renders
        a fixed 16:9 frame (calc_V) which the window server stretches to fill the screen; aspect distortion is capped
        at STRETCH, beyond that the view is made larger than the window so the edges are cropped (no black bars)."""
        cs = win.cont.bounds().size; ws, hs = float(cs.width), float(cs.height)
        if rt.fs_want and ws > 0 and hs > 0:
            wv, hv = calc_V()
            r = (ws / hs) / (wv / hv)
            if FILL == "crop":
                q = min(max(r, 1 / STRETCH), STRETCH)
                wc, hc = (q / r, 1.0) if r < 1 else (1.0, r / q)
            else:                                   # default: exact 16:9, centered (black bars on other aspect ratios)
                wc, hc = (1.0, r) if r < 1 else (1 / r, 1.0)
            vw, vh = ws * wc, hs * hc
            rect = ((ws - vw) / 2, (hs - vh) / 2, vw, vh)
            win.gv.setAutoresizingMask_(0)
        else:
            rect = (0.0, 0.0, ws, hs)
            win.gv.setAutoresizingMask_(18)
        fr = win.gv.frame()
        if abs(float(fr.origin.x) - rect[0]) > 0.5 or abs(float(fr.origin.y) - rect[1]) > 0.5 or \
           abs(float(fr.size.width) - rect[2]) > 0.5 or abs(float(fr.size.height) - rect[3]) > 0.5:
            win.gv.setFrame_(NSMakeRect(*rect))
        rt.gvrect = rect
        return (ws, hs)

    def map_pt(x, y):
        """window-container point -> the game's coordinates"""
        g = rt.gvrect
        if not rt.fs_want or not g or g[2] <= 0: return (x, y)
        wv, hv = calc_V()
        return ((x - g[0]) * wv / g[2], (y - g[1]) * hv / g[3])
    rt.map_pt = map_pt

    def sync_window(send):
        """Host -> guest: when the real window changes size (fullscreen, user resize), update the guest's model and
        notify the game exactly as AppKit would (windowDidResize:, reshape). Also performs the full-screen requests."""
        win = rt.obj(rt.main_window)
        w = win.real
        if w is None: return
        now = time.monotonic()
        if rt.fs_busy and now - rt.fs_busy_t > 5.0: rt.fs_busy = False; rt.fs_req = None     # lost notification
        if rt.fs_user is not None and not rt.fs_busy:
            # the user changed full screen with the window controls: the game must follow (its own toggle keeps its
            # menu state consistent), and our layout switches to the 16:9 full-screen mapping
            tgt = rt.fs_user; rt.fs_user = None
            rt.fs_want = tgt
            if rt.game_fs is not None and rt.game_fs != tgt:
                send(rt.delegate, "toggleFullScreen")
        if w.isVisible() and not rt.fs_busy and native_fs(w) != rt.fs_want:
            rt.fs_req = rt.fs_want; rt.fs_busy = True; rt.fs_busy_t = now
            w.toggleFullScreen_(None)
        if not rt.fs_want and not rt.fs_busy and not native_fs(w) and rt.pending_frame:
            fr = rt.pending_frame; rt.pending_frame = None
            place_windowed(w, fr[2], fr[3], center=True)
        update_cursor(w)
        from .launch import keys_held, release_all_keys
        if keys_held() and (not w.isKeyWindow() or not hostapp.isActive()):
            release_all_keys()
        cs = layout(win)
        rt.movie_update()
        if rt.fs_want: cs = calc_V()
        if cs[0] < 100 or cs[1] < 100: return          # transitional (animation) size
        key = (round(cs[0]), round(cs[1]), rt.fs_want)
        last = getattr(win, "sync_key", None)
        win.sync_key = key
        if last is None or last == key: return
        win.frame = [0.0, 0.0, float(cs[0]), float(cs[1])] if rt.fs_want else \
            [float(w.frame().origin.x), float(w.frame().origin.y), float(w.frame().size.width), float(w.frame().size.height)]
        v = rt.obj(rt.view); v.frame = [0.0, 0.0, float(cs[0]), float(cs[1])]
        if getattr(e, 'dbg', False): print('[sync] window content', key, 'frame', win.frame, 'view', rt.gvrect, file=sys.stderr)
        send(rt.delegate, "windowDidResize:", 0)
        send(rt.view, "reshape")
    rt.sync_window = sync_window

    M("NSWindow", "setContentView:")(lambda rt_, e_, r, s: setattr(rt.obj(r), "content", A(e_, 0)) or 0)
    M("NSWindow", "contentView")(lambda rt_, e_, r, s: rt.obj(r).content)
    def make_key(rt_, e_, r, s):
        o = rt.obj(r)
        w = real_window(o)
        w.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return 0
    M("NSWindow", "makeKeyAndOrderFront:")(make_key)
    M("NSWindow", "setTitle:")(lambda rt_, e_, r, s: setattr(rt.obj(r), "title", py(A(e_, 0))) or 0)
    def win_frame(rt_, e_, r, s):
        o = rt.obj(r)
        if rt.fs_want: o.frame = [0.0, 0.0] + list(calc_V())
        elif o.real is not None:
            f = o.real.frame(); o.frame = [float(f.origin.x), float(f.origin.y), float(f.size.width), float(f.size.height)]
        e_.wr(e_.arg(0), rect_pack(*o.frame)); return e_.arg(0)
    M("NSWindow", "frame")(win_frame)
    def content_rect(rt_, e_, r, s):
        o = rt.obj(r)
        x, y, w, hh = struct.unpack("<4f", struct.pack("<4I", *[A(e_, i) for i in range(4)]))
        if rt.fs_want: pass
        elif o.real is not None:
            c = o.real.contentRectForFrameRect_(NSMakeRect(x, y, w, hh))
            x, y, w, hh = float(c.origin.x), float(c.origin.y), float(c.size.width), float(c.size.height)
        e_.wr(e_.arg(0), rect_pack(x, y, w, hh)); return e_.arg(0)
    M("NSWindow", "contentRectForFrameRect:")(content_rect)
    def place_windowed(w, fw, fh, center=False):
        """size the real window to the game's request but keep it fully on a screen (title bar below the menu bar);
        the game's own origin is ignored: it knows nothing about this Mac's screens"""
        cur = w.frame()
        scr = w.screen() or NSScreen.mainScreen()
        vis = scr.visibleFrame()
        fw = min(fw, float(vis.size.width)); fh = min(fh, float(vis.size.height))
        if center or float(cur.size.width) < 50:
            x = float(vis.origin.x) + (float(vis.size.width) - fw) / 2
            y = float(vis.origin.y) + (float(vis.size.height) - fh) / 2
        else:                                           # keep the top-left corner where the user has it
            x = float(cur.origin.x); y = float(cur.origin.y) + float(cur.size.height) - fh
        fr = scr.visibleFrame() if False else NSMakeRect(x, y, fw, fh)
        fr = w.constrainFrameRect_toScreen_(fr, scr)
        w.setFrame_display_(fr, True)

    def set_frame(rt_, e_, r, s):
        o = rt.obj(r)
        o.frame = list(struct.unpack("<4f", struct.pack("<4I", *[A(e_, i) for i in range(4)])))
        if getattr(e_, 'dbg', False): print('[win] setFrame', o.frame, file=sys.stderr)
        if rt.fs_want or rt.fs_busy or (o.real is not None and native_fs(o.real)):
            if not rt.fs_want: rt.pending_frame = list(o.frame)      # leaving full screen: applied once the window is back
            return 0
        if o.real:
            place_windowed(o.real, o.frame[2], o.frame[3])
        return 0
    M("NSWindow", "setFrame:display:")(set_frame)
    def set_style(rt_, e_, r, s):
        o = rt.obj(r); o.style = A(e_, 0)
        if getattr(e_, 'dbg', False): print('[win] setStyleMask', hex(o.style), file=sys.stderr)
        # a borderless window is how the game asks for "full screen"; we turn that into a native full-screen Space
        want = (o.style & 0xF) == 0
        rt.game_fs = want
        if want != rt.fs_want:
            rt.fs_want = want
            if want: rt.pending_frame = None
        return 0
    M("NSWindow", "setStyleMask:")(set_style)
    def set_level(rt_, e_, r, s):
        o = rt.obj(r); o.level = sgn_i(A(e_, 0))
        return 0
    M("NSWindow", "setLevel:")(set_level)
    def toggle_fs(rt_, e_, r, s):
        rt.fs_want = not rt.fs_want
        rt.game_fs = rt.fs_want
        return 0
    M("NSWindow", "toggleFullScreen:")(toggle_fs)
    M("NSWindow", "center")(ret_nil)
    M("NSWindow", "isMiniaturized")(ret_nil)
    M("NSWindow", "setDelegate:")(lambda rt_, e_, r, s: setattr(rt.obj(r), "delegate", A(e_, 0)) or 0)
    M("NSWindow", "makeFirstResponder:")(ret_yes)
    M("NSWindow", "setAcceptsMouseMovedEvents: setBackgroundColor: setHidesOnDeactivate: setOpaque: "
      "addTrackingRect:owner:userData:assumeInside: removeTrackingRect: performClose: miniaturize: "
      "beginSheetModalForWindow:modalDelegate:didEndSelector:contextInfo:")(ret_nil)

    # ---------------------------------------------------------------- NSView
    def view_init(rt_, e_, r, s):
        o = rt.obj(r)
        o.frame = [f4(i) for i in range(4)] if s.startswith("initWithFrame") else [0, 0, 100, 100]
        o.window = 0; o.superview = 0; o.subviews = []
        return r
    M("NSView", "initWithFrame:")(view_init)
    M("NSView", "frame bounds")(lambda rt_, e_, r, s: (e_.wr(e_.arg(0), rect_pack(*(rt.obj(r).frame if s == "frame" else [0, 0, rt.obj(r).frame[2], rt.obj(r).frame[3]]))), e_.arg(0))[1])
    M("NSView", "window")(lambda rt_, e_, r, s: rt.obj(r).window)
    M("NSView", "setFrame:")(lambda rt_, e_, r, s: setattr(rt.obj(r), "frame", [f4(i) for i in range(4)]) or 0)
    M("NSView", "setNeedsDisplay: setHidden: lockFocus removeFromSuperview addSubview: setView: setWantsBestResolutionOpenGLSurface: viewDidMoveToWindow")(ret_nil)
    # NSViewController (the in-game news / "Next Event" pages): the game writes ivars straight after [super init...]
    M("NSViewController", "initWithNibName:bundle: init")(ret_self)
    M("NSViewController", "view loadView setView: viewDidLoad")(ret_nil)
    M("NSView", "subviews")(lambda rt_, e_, r, s: rt.new_obj("NSArray", items=[]))
    M("NSView", "convertPoint:fromView:")(ret_nil)

    # -------------------------------------------------------------- NSEvent proxies
    def ev_obj(ev):
        loc = ev.locationInWindow() if hasattr(ev, "locationInWindow") else None
        return rt.new_obj("NSEvent", ev=ev)
    def loc_in_window(rt_, e_, r, s):
        # NSPoint (2 floats = 8 bytes) is returned in EAX:EDX on i386
        x, y = struct.unpack("<2I", struct.pack("<2f", *rt.obj(r).pt))
        e_.uc.reg_write(UC_X86_REG_EDX, y)
        return x
    M("NSEvent", "locationInWindow")(loc_in_window)
    M("NSEvent", "keyCode")(lambda rt_, e_, r, s: int(rt.obj(r).ev.keyCode()))
    M("NSEvent", "isARepeat")(lambda rt_, e_, r, s: 1 if rt.obj(r).ev.isARepeat() else 0)
    M("NSEvent", "characters")(lambda rt_, e_, r, s: nsstr(rt.obj(r).ev.characters() or ""))

    # ------------------------------------------------------------ OpenGL context
    def make_pf(rt_, e_, r, s):
        o = rt.obj(r)
        attrs = []
        p = A(e_, 0)
        while True:
            v = e_.u32(p); p += 4
            if v == 0: break
            attrs.append(v)
        o.attrs = attrs
        return r
    M("NSOpenGLPixelFormat", "initWithAttributes:")(make_pf)
    M("NSOpenGLPixelFormat", "CGLPixelFormatObj")(lambda rt_, e_, r, s: r)
    ctxs = {}
    def ctx_init(rt_, e_, r, s):
        o = rt.obj(r)
        o.pf = A(e_, 0); o.share = A(e_, 1); o.real = None; o.view = 0
        return r
    M("NSOpenGLContext", "initWithFormat:shareContext:")(ctx_init)

    def real_ctx(o):
        if o.real is None:
            attrs = [NSOpenGLPFADoubleBuffer, NSOpenGLPFADepthSize, 24, NSOpenGLPFAStencilSize, 8,
                     NSOpenGLPFAColorSize, 32, NSOpenGLPFAAlphaSize, 8, NSOpenGLPFAAccelerated, 0]
            pf = None
            msaa = int(os.environ.get("A6_MSAA", "4"))
            if msaa:
                pf = NSOpenGLPixelFormat.alloc().initWithAttributes_(attrs[:-1] + [NSOpenGLPFASampleBuffers, 1, NSOpenGLPFASamples, msaa, 0])
            if pf is None:
                pf = NSOpenGLPixelFormat.alloc().initWithAttributes_(attrs)
            share = rt.obj(o.share).real if o.share else None
            o.real = NSOpenGLContext.alloc().initWithFormat_shareContext_(pf, share)
            o.real.setValues_forParameter_([1], 222)      # NSOpenGLCPSwapInterval: vsync
        return o.real
    rt.real_ctx = real_ctx

    def set_view(rt_, e_, r, s):
        o = rt.obj(r); o.view = A(e_, 0); o.attached = False
        return 0
    M("NSOpenGLContext", "setView:")(set_view)

    def attach(o):
        if not getattr(o, "attached", False) and getattr(rt, "main_window", 0):
            v = rt.obj(o.view) if o.view else None
            win = rt.obj(v.window) if v and v.window else rt.obj(rt.main_window)
            rw = real_window(win)
            c = real_ctx(o)
            NSOpenGLContext.clearCurrentContext()
            try: win.gv.setWantsBestResolutionOpenGLSurface_(False)   # backing size is set explicitly (apply_backing)
            except Exception: pass
            c.setView_(win.gv)
            o.attached = True
            # let the window server create the drawable before GL is used
            hostapp_ = NSApplication.sharedApplication()
            for _ in range(25):
                while True:
                    ev = hostapp_.nextEventMatchingMask_untilDate_inMode_dequeue_(0xFFFFFFFFFFFFFFFF, NSDate.distantPast(), NSDefaultRunLoopMode, True)
                    if ev is None: break
                    hostapp_.sendEvent_(ev)
                time.sleep(0.02)
            c.update()
    _CGL = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
    _CGL.CGLSetParameter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    _CGL.CGLEnable.argtypes = [ctypes.c_void_p, ctypes.c_int]; _CGL.CGLDisable.argtypes = [ctypes.c_void_p, ctypes.c_int]
    def apply_backing(o):
        """Full screen: fix the GL surface at the game's 16:9 frame size (the compositor scales it to the view)."""
        want = calc_V() if rt.fs_want else None
        if getattr(o, "backing", "unset") == want and not getattr(rt, "backing_dirty", False): return
        rt.backing_dirty = False
        cgl = ctypes.c_void_p(o.real.CGLContextObj().pointerAsInteger)
        if want:
            dims = (ctypes.c_int * 2)(int(want[0]), int(want[1]))
            _CGL.CGLSetParameter(cgl, 304, dims)      # kCGLCPSurfaceBackingSize
            _CGL.CGLEnable(cgl, 305)                  # kCGLCESurfaceBackingSize
        else:
            _CGL.CGLDisable(cgl, 305)
        o.backing = want
        o.size_key = None
    def sync_size(o):
        c = o.real
        if c is None or not getattr(o, "attached", False): return
        v = c.view()
        if v is None: return
        apply_backing(o)
        b = v.bounds(); key = (b.size.width, b.size.height, v.window().frame().size.width if v.window() else 0, rt.fs_want)
        if getattr(o, "size_key", None) != key:
            o.size_key = key
            c.update()
    def make_current(rt_, e_, r, s):
        o = rt.obj(r)
        c = real_ctx(o)
        attach(o)
        c.makeCurrentContext()
        if os.environ.get("A6_CTXDBG"): print("[ctxdbg] makeCurrent", hex(r), "thread", __import__("threading").current_thread().name, "share", o.share, file=sys.stderr)
        sync_size(o)
        if getattr(e, 'dbg', False): print('[ctx] current', c, NSOpenGLContext.currentContext(), file=sys.stderr)
        rt.current_ctx = r
        return 0
    M("NSOpenGLContext", "makeCurrentContext")(make_current)
    shot = {"last": 0, "n": 0}
    def save_png(path, w, hh, rgba):
        import zlib
        rows = b"".join(b"\0" + rgba[(hh - 1 - y) * w * 4:(hh - y) * w * 4] for y in range(hh))
        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, hh, 8, 6, 0, 0, 0)) + \
              chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")
        open(path, "wb").write(png)
    def flush(rt_, e_, r, s):
        if getattr(e_, 'fps', False):
            shot['nf'] = shot.get('nf', 0) + 1
            _t = time.time()
            if _t - shot.get('tf', _t) >= 5:
                print(f"[present] {shot['nf'] / (_t - shot['tf']):.1f} flushes/s", file=sys.stderr); shot['nf'] = 0; shot['tf'] = _t
            shot.setdefault('tf', _t)
        getattr(e_, 'gl_state', {}).pop('panel_done', None)
        _ds = getattr(e_, "dr_state", 0)
        if _ds == 1: e_.drawrec = []; e_.dr_state = 2
        elif _ds == 2:
            e_.dr_state = 0; _r = e_.drawrec; e_.drawrec = None; e_.dr_n = getattr(e_, "dr_n", 0) + 1
            with open(f"/tmp/drawrec_{e_.dr_n}.txt", "w") as _f:
                for _x in _r: _f.write(repr(_x) + "\n")
            print("[drawrec] wrote", len(_r), "draws", file=sys.stderr)
        if getattr(e_, 'step_at', None) is not None:
            if getattr(e_, 'step_active', False):
                e_.step_active = False; e_.step_at = None
            elif time.time() - e_.t_start > e_.step_at:
                e_.step_active = True; e_.step_list = []
        if getattr(e_, 'gltrace', None) is not None and 'frame' in e_.gltrace:
            e_.last_frame_draws, e_.frame_draws = e_.frame_draws, []
        attach(rt.obj(r))
        real_ctx(rt.obj(r)).makeCurrentContext(); sync_size(rt.obj(r))
        if getattr(e_, "selftest", False) and not shot.get("st"):
            shot["st"] = 1
            import ctypes
            G = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
            G.glBindFramebufferEXT(0x8D40, 0); G.glDisable(0x0C11); G.glColorMask(1, 1, 1, 1)
            iv = (ctypes.c_int * 4)()
            for nm, pn in (("fbo_draw", 0x8CA6), ("fbo_read", 0x8CAA), ("drawbuf", 0x0C01), ("readbuf", 0x0C02), ("vp", 0x0BA2), ("scissor_on", 0x0C11), ("dbl", 0x0C32)):
                G.glGetIntegerv(pn, iv); print("   ", nm, [hex(x) for x in iv], file=sys.stderr)
            G.glClearColor.argtypes = [ctypes.c_float] * 4
            NSOpenGLContext.currentContext().update()
            G.glClearColor(1.0, 0.0, 0.0, 1.0); G.glClear(0x4000)
            _px = ctypes.create_string_buffer(4); G.glReadPixels(100, 100, 1, 1, 0x1908, 0x1401, _px); print("   (1) read right after clear:", _px.raw.hex(), file=sys.stderr)
            G.glDisable(0x0B71); G.glDisable(0x0B90); G.glDisable(0x0BE2); G.glDisable(0x0B44)
            G.glReadBuffer(0x405); G.glBindBuffer(0x88EB, 0); G.glPixelStorei(0x0D05, 4)
            G.glClear(0x4100)
            G.glReadPixels(100, 100, 1, 1, 0x1908, 0x1401, _px); print("   (2) after resetting state:", _px.raw.hex(), file=sys.stderr)
            px = ctypes.create_string_buffer(4); G.glReadPixels(100, 100, 1, 1, 0x1908, 0x1401, px)
            print("[selftest] view", NSOpenGLContext.currentContext().view(), "win", NSOpenGLContext.currentContext().view().window() if NSOpenGLContext.currentContext().view() else None, "visible", NSOpenGLContext.currentContext().view().window().isVisible() if NSOpenGLContext.currentContext().view() else None, "vscreen", NSOpenGLContext.currentContext().currentVirtualScreen(), file=sys.stderr)
            _v = NSOpenGLContext.currentContext().view(); print("   bounds", _v.bounds(), "backing", _v.convertRectToBacking_(_v.bounds()), "winframe", _v.window().frame(), file=sys.stderr)
            _w = NSOpenGLContext.currentContext().view().window()
            print("   occlusion", _w.occlusionState(), "key", _w.isKeyWindow(), "active", NSApplication.sharedApplication().isActive(), "onscreen", _w.isOnActiveSpace(), "level", _w.level(), "alpha", _w.alphaValue(), "style", _w.styleMask(), file=sys.stderr)
            fb = (ctypes.c_uint * 1)(); rb = (ctypes.c_uint * 1)()
            G.glGenFramebuffersEXT(1, fb); G.glGenRenderbuffersEXT(1, rb)
            G.glBindRenderbufferEXT(0x8D41, rb[0]); G.glRenderbufferStorageEXT(0x8D41, 0x8058, 64, 64)
            G.glBindFramebufferEXT(0x8D40, fb[0]); G.glFramebufferRenderbufferEXT(0x8D40, 0x8CE0, 0x8D41, rb[0])
            G.glClearColor(0.0, 1.0, 0.0, 1.0); G.glClear(0x4000)
            px2 = ctypes.create_string_buffer(4); G.glReadPixels(10, 10, 1, 1, 0x1908, 0x1401, px2)
            print("[selftest] FBO green clear:", px2.raw.hex(), "status", hex(G.glCheckFramebufferStatusEXT(0x8D40)), file=sys.stderr)
            G.glBindFramebufferEXT(0x8D40, 0)
            print("[selftest] after red clear:", px.raw.hex(), "err", G.glGetError(), "ctx", NSOpenGLContext.currentContext(), file=sys.stderr)
        if getattr(e_, "shots", None) and time.time() - shot["last"] > e_.shots_every:
            import ctypes
            GL = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
            vp = (ctypes.c_int * 4)(); GL.glGetIntegerv(0x0BA2, vp)
            w, hh = vp[2], vp[3]
            if w > 0 and hh > 0:
                buf = ctypes.create_string_buffer(w * hh * 4)
                GL.glReadPixels(0, 0, w, hh, 0x1908, 0x1401, buf)
                shot["n"] += 1; shot["last"] = time.time()
                print('[glstats]', {k[3:]: v for k, v in sorted(e_.glcounts.items())}, file=sys.stderr); e_.glcounts.clear()
                save_png(f"{e_.shots}/frame{shot['n']:03d}.png", w, hh, buf.raw)
        _w = rt.obj(rt.main_window).real if getattr(rt, "main_window", 0) else None
        if _w is not None and not (int(_w.occlusionState()) & 2) and not rt.movie_active():
            time.sleep(0.016)               # minimized / hidden: nothing to present, do not spin (the intro video covering the window does not count)
            rt.was_occluded = True
            return 0
        if getattr(rt, "was_occluded", False):
            rt.was_occluded = False         # the drawable may have been recreated meanwhile: refresh it before the first present
            ctx_ = real_ctx(rt.obj(r)); ctx_.update(); rt.obj(r).size_key = None; rt.backing_dirty = True
        real_ctx(rt.obj(r)).flushBuffer(); return 0
    M("NSOpenGLContext", "flushBuffer")(flush)
    M("NSOpenGLContext", "CGLContextObj")(lambda rt_, e_, r, s: r)
    M("NSOpenGLContext", "update")(lambda rt_, e_, r, s: (real_ctx(rt.obj(r)).update(), 0)[1])
    def set_values(rt_, e_, r, s):
        v, p = e_.u32(A(e_, 0)), A(e_, 1)
        if getattr(e_, "dbg", False): print(f"[ctx] setValues {v} forParameter {p}", file=sys.stderr)
        real_ctx(rt.obj(r)).setValues_forParameter_([v], p)
        return 0
    M("NSOpenGLContext", "setValues:forParameter:")(set_values)
    h["_CGLLockContext"] = h["_CGLUnlockContext"] = lambda e_: 0

    # --------------------------------------------------------------- NSTimer
    timer_id = [0]
    def sched_timer(rt_, e_, r, s):
        interval = struct.unpack("<d", e_.rd(e_.uc.reg_read(UC_X86_REG_ESP) + 4 + 4 * 2, 8))[0]
        # args: (double interval)(2 slots) target selector userInfo repeats
        target, sel, ui, rep = A(e_, 2), A(e_, 3), A(e_, 4), A(e_, 5)
        timer_id[0] += 1
        if getattr(e_, 'dbg', False): print(f'[timer] interval={interval} repeats={rep & 0xFF}', file=sys.stderr)
        if rep & 0xFF and interval < 1 / MAXFPS: interval = 1 / MAXFPS        # the game ticks on wall-clock time: 60 Hz is plenty (and halves the power draw on 120 Hz screens)
        t = rt.new_obj("NSTimer", tid=timer_id[0])
        rt.timers.append([time.monotonic() + interval, interval if rep & 0xFF else 0, target, sel, t, timer_id[0]])
        return t
    M("NSTimer", "scheduledTimerWithTimeInterval:target:selector:userInfo:repeats:", meta=True)(sched_timer)
    def invalidate(rt_, e_, r, s):
        tid = rt.obj(r).tid
        rt.timers[:] = [t for t in rt.timers if t[5] != tid]
        return 0
    M("NSTimer", "invalidate")(invalidate)

    # ------------------------------------------------------------- misc UI
    M("NSAlert", "alertWithMessageText:defaultButton:alternateButton:otherButton:informativeTextWithFormat:", meta=True)(
        lambda rt_, e_, r, s: (print("[alert]", py(A(e_, 0))), rt.new_obj("NSAlert"))[1])
    M("NSMenuItem", "setHidden:")(ret_nil)
    # display / CG stubs
    h["_CGMainDisplayID"] = lambda e_: 1
    h["_CGShieldingWindowLevel"] = lambda e_: 2000
    h["_CGWindowLevelForKey"] = lambda e_: 25
    h["_CGDisplayCapture"] = h["_CGReleaseAllDisplays"] = lambda e_: 0
    h["_CGDisplayCopyDisplayMode"] = lambda e_: 1
    h["_CGDisplayModeGetWidth"] = lambda e_: int(SCREEN[2])
    h["_CGDisplayModeGetHeight"] = lambda e_: int(SCREEN[3])
    h["_CGDisplayModeGetRefreshRate"] = lambda e_: 60.0
    h["_CGDisplayCopyAllDisplayModes"] = lambda e_: rt.new_obj("NSArray", items=[1])
    h["_CGDisplayModeCopyPixelEncoding"] = lambda e_: nsstr("PPPPPPPP")
    h["_CVDisplayLinkCreateWithActiveCGDisplays"] = lambda e_: (e_.w32(e_.arg(0), e_.malloc(16)), 0)[1]
    h["_CVDisplayLinkSetOutputCallback"] = h["_CVDisplayLinkRelease"] = h["_CVDisplayLinkStop"] = \
        h["_CVDisplayLinkSetCurrentCGDisplayFromOpenGLContext"] = lambda e_: 0
    h["_CVDisplayLinkIsRunning"] = lambda e_: 0

    from . import movie
    movie.install(rt, e, M, real_window)
