"""QTKit replacement (QTMovie / QTMovieView) on top of AVFoundation: the Gameloft logo and the intro are .mov files
played by the game through QTKit, which no longer exists. The video is shown in a borderless child window that
covers the game window; input still reaches the game window (so its skip handling keeps working)."""
import sys, os
import objc
from Foundation import NSURL, NSNotificationCenter, NSOperationQueue
from AppKit import NSWindow, NSMakeRect, NSColor, NSView
from .cocoa import A

_av = {}


def _avf():
    if not _av:
        objc.loadBundle("AVFoundation", _av, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        _av["AVPlayer"] = objc.lookUpClass("AVPlayer"); _av["AVPlayerLayer"] = objc.lookUpClass("AVPlayerLayer")
    return _av


def install(rt, e, M, real_window):
    py = rt.py
    rt.movie_update = lambda: None
    rt.movie_active = lambda: False
    rt.video_view = 0
    dbg = lambda *a: print('[movie]', round(__import__('time').monotonic() % 1000, 2), *a, file=sys.stderr) if os.environ.get('A6_MOVIE_DEBUG') else None

    def movie_init(rt_, e_, r, s):
        o = rt.obj(r)
        o.player = None; o.ended = False; o.playing = False
        dbg('init', s)
        path = py(A(e_, 0)) if s.startswith("initWithFile") else None
        if path and os.path.exists(path):
            try:
                av = _avf()
                o.player = av["AVPlayer"].playerWithURL_(NSURL.fileURLWithPath_(path))
                item = o.player.currentItem()
                def done(note, o=o): o.ended = True          # no AppKit calls here: the window is removed from the main loop (update)
                o.obs = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                    "AVPlayerItemDidPlayToEndTimeNotification", item, NSOperationQueue.mainQueue(), done)
            except Exception as ex:
                print("[movie] cannot open", path, ex, file=sys.stderr); o.player = None
        if e_.arg(2 + 1 + e_.sb) and s.endswith("error:"): e_.w32(A(e_, 1), 0)
        return r if o.player else 0
    M("QTMovie", "initWithFile:error: initWithFile: initWithURL:error:")(movie_init)

    def show(o):
        if getattr(o, "overlay", None) is not None or o.player is None: return
        main = getattr(rt, "main_window", 0)
        dbg('show main', main)
        if not main: return
        rw = real_window(rt.obj(main))
        cr = rw.contentRectForFrameRect_(rw.frame())          # the video covers the content area, never the title bar
        ov = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(cr, 0, 2, False)
        ov.setBackgroundColor_(NSColor.blackColor()); ov.setReleasedWhenClosed_(False)
        ov.setIgnoresMouseEvents_(True); ov.setOpaque_(True)
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, cr.size.width, cr.size.height))
        v.setWantsLayer_(True); ov.setContentView_(v)
        layer = _avf()["AVPlayerLayer"].playerLayerWithPlayer_(o.player)
        layer.setFrame_(v.bounds()); v.layer().addSublayer_(layer)
        rw.addChildWindow_ordered_(ov, 1)
        ov.orderFront_(None)
        dbg('overlay frame', rw.frame(), 'visible', rw.isVisible(), 'ov visible', ov.isVisible())
        o.overlay, o.layer, o.rwin, o.ovview = ov, layer, rw, v
        active.append(o)

    active = []
    def update():
        """keep the video covering the game window (it starts while the window is still being resized / entering full screen)"""
        for o in list(active):
            ov = getattr(o, "overlay", None)
            if ov is None: active.remove(o); continue
            if getattr(o, "ended", False): hide(o); active.remove(o); continue
            fr = o.rwin.contentRectForFrameRect_(o.rwin.frame())
            cur = ov.frame()
            if abs(cur.size.width - fr.size.width) > 0.5 or abs(cur.size.height - fr.size.height) > 0.5 or abs(cur.origin.x - fr.origin.x) > 0.5 or abs(cur.origin.y - fr.origin.y) > 0.5:
                ov.setFrame_display_(fr, True)
                o.ovview.setFrame_(NSMakeRect(0, 0, fr.size.width, fr.size.height))
                o.layer.setFrame_(o.ovview.bounds())
    rt.movie_update = update
    rt.movie_active = lambda: bool(active)

    graveyard = []          # torn-down windows/layers are kept alive: AVFoundation / CoreAnimation may still touch them briefly
    def hide(o):
        rt.video_view = 0
        ov = getattr(o, "overlay", None)
        if ov is not None:
            o.overlay = None
            try:
                if o.player is not None: o.player.pause()
                o.layer.setPlayer_(None)                 # detach the player before the layer / window go away
                o.layer.removeFromSuperlayer()
                o.rwin.removeChildWindow_(ov)
                ov.orderOut_(None)
            except Exception as ex:
                dbg("hide failed", ex)
            graveyard.append((ov, o.layer, o.ovview))

    def play(rt_, e_, r, s):
        dbg('play')
        o = rt.obj(r)
        if o.player is None: return 0
        o.playing = True; show(o); o.player.play(); return 0
    def stop(rt_, e_, r, s):
        dbg('stop')
        o = rt.obj(r)
        if o.player is not None: o.player.pause()
        o.playing = False; hide(o); return 0
    def rate(rt_, e_, r, s):
        dbg('rate')
        o = rt.obj(r)
        return 1.0 if (o.player is not None and o.playing and not o.ended) else 0.0
    M("QTMovie", "play")(play)
    M("QTMovie", "stop pause")(stop)
    M("QTMovie", "rate")(rate)

    # QTMovieView: the game only attaches the movie; playing is started through the movie itself
    def view_set_movie(rt_, e_, r, s):
        v = rt.obj(r); v.movie = A(e_, 0); return 0
    M("QTMovieView", "setMovie:")(view_set_movie)
    M("QTMovieView", "movie")(lambda rt_, e_, r, s: getattr(rt.obj(r), "movie", 0))
    M("QTMovieView", "setControllerVisible: setEditable: setPreservesAspectRatio: setFillColor:")(lambda rt_, e_, r, s: 0)
    def view_remove(rt_, e_, r, s):
        m = getattr(rt.obj(r), "movie", 0)
        if m:
            mo = rt.obj(m)
            if getattr(mo, "player", None) is not None: mo.player.pause()
            mo.playing = False; hide(mo)
        return 0
    M("QTMovieView", "removeFromSuperview")(view_remove)

    def view_play(rt_, e_, r, s):
        m = getattr(rt.obj(r), "movie", 0)
        dbg("view play", s, m)
        rt.video_view = r            # the game's VideoView handles key/mouse skipping: send it the input meanwhile
        if m: play(rt_, e_, m, s)
        return 0
    M("QTMovieView", "play: start:")(view_play)
    def view_pause(rt_, e_, r, s):
        m = getattr(rt.obj(r), "movie", 0)
        if m: stop(rt_, e_, m, s)
        return 0
    M("QTMovieView", "pause: stop:")(view_pause)

    # a click on the movie skips it (QTKit's own mouse handling; VideoView forwards to super once skipping is enabled)
    def view_mouse(rt_, e_, r, s):
        m = getattr(rt.obj(r), "movie", 0)
        dbg("click skip", s)
        if m and s == "mouseDown:": stop(rt_, e_, m, s)
        return 0
    M("QTMovieView", "mouseDown: mouseUp:")(view_mouse)
