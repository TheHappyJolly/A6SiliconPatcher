"""Host-side Foundation/AppKit stand-ins used by the guest's Objective-C shell."""
import os, struct, sys, time
from .ucshim import *
from .objc_rt import RT, HObj, GClass


def A(e, n):
    """n-th message argument (after self, _cmd), honouring stret shift."""
    return e.arg(2 + n + e.sb)


def install(e):
    rt = RT(e)
    e.sb = 0
    e.strict_objc = False
    h = e.hle

    # data symbols
    cfs = e.alloc_static(64)
    e.hle_data["___CFConstantStringClassReference"] = cfs
    rt.cfstring_isa = cfs
    e.hle_data["_NSApp"] = e.alloc_static(4)
    for n in ("_NSErrorFailingURLStringKey", "_NSLocaleCountryCode", "_NSStringPboardType",
              "_NSViewGlobalFrameDidChangeNotification", "_NSWorkspaceDidWakeNotification",
              "_NSWorkspaceWillSleepNotification", "_kCFBundleVersionKey", "_kCFLocaleCountryCode",
              "_kCFRunLoopDefaultMode", "_kCFAllocatorDefault", "_kIOMasterPortDefault"):
        e.hle_data[n] = e.alloc_static(8)

    hierarchy = [
        ("NSObject", None), ("NSProxy", None),
        ("NSString", "NSObject"), ("NSMutableString", "NSString"),
        ("NSData", "NSObject"), ("NSMutableData", "NSData"),
        ("NSArray", "NSObject"), ("NSMutableArray", "NSArray"),
        ("NSDictionary", "NSObject"), ("NSMutableDictionary", "NSDictionary"),
        ("NSNumber", "NSObject"), ("NSDate", "NSObject"), ("NSURL", "NSObject"),
        ("NSURLRequest", "NSObject"), ("NSBundle", "NSObject"), ("NSFileManager", "NSObject"),
        ("NSAutoreleasePool", "NSObject"), ("NSNotificationCenter", "NSObject"),
        ("NSUserDefaults", "NSObject"), ("NSLocale", "NSObject"), ("NSThread", "NSObject"),
        ("NSTimer", "NSObject"), ("NSDateFormatter", "NSObject"), ("NSAlert", "NSObject"),
        ("NSPasteboard", "NSObject"), ("NSGarbageCollector", "NSObject"), ("NSWorkspace", "NSObject"),
        ("NSScreen", "NSObject"), ("NSColor", "NSObject"), ("NSCursor", "NSObject"),
        ("NSImage", "NSObject"), ("NSFont", "NSObject"), ("NSEvent", "NSObject"),
        ("NSOpenGLContext", "NSObject"), ("NSOpenGLPixelFormat", "NSObject"), ("NSMenuItem", "NSObject"),
        ("NSResponder", "NSObject"), ("NSView", "NSResponder"), ("NSWindow", "NSResponder"),
        ("NSApplication", "NSResponder"), ("NSViewController", "NSResponder"),
        ("NSControl", "NSView"), ("NSButton", "NSControl"), ("NSTextField", "NSControl"),
        ("NSProgressIndicator", "NSView"), ("WebView", "NSView"), ("QTMovie", "NSObject"),
        ("QTMovieView", "NSView"),
    ]
    for n, s in hierarchy:
        rt.host_class(n, s)
    rt.load_guest()

    def M(cls, sels, meta=False):
        def deco(fn):
            for s in sels.split():
                (rt.get_class(cls).cmeths if meta else rt.get_class(cls).imeths)[s] = fn
            return fn
        return deco

    def ret_self(rt_, e_, r, s): return r
    def ret_nil(rt_, e_, r, s): return 0
    def ret_yes(rt_, e_, r, s): return 1

    # ----------------------------------------------------------- send hooks
    def mk_send(super_=False, stret=False, fpret=False):
        def f(e_):
            e_.sb = 1 if stret else 0
            return rt.send(e_, super_, stret, fpret)
        return f
    h["_objc_msgSend"] = mk_send()
    h["_objc_msgSend_fpret"] = mk_send(fpret=True)
    h["_objc_msgSend_stret"] = mk_send(stret=True)
    h["_objc_msgSendSuper"] = mk_send(super_=True)
    h["_objc_enumerationMutation"] = lambda e_: None

    # ------------------------------------------------------------- NSObject
    def alloc(rt_, e_, r, s):
        c, meta = rt.class_of(r)
        sz = max(16, getattr(c, "size", 16))
        # guest-defined classes inherit size from their own table; host ones use 16
        a = e_.malloc(sz)
        e_.wr(a, bytes(sz))
        e_.w32(a, c.addr)
        rt.objs[a] = HObj(c.name)
        return a
    M("NSObject", "alloc allocWithZone:", meta=True)(alloc)
    M("NSObject", "new", meta=True)(lambda rt_, e_, r, s: rt.call_init(alloc(rt_, e_, r, s)))
    rt.call_init = lambda a: a
    M("NSObject", "init retain autorelease self")(ret_self)
    M("NSObject", "release dealloc")(lambda rt_, e_, r, s: 0)
    M("NSObject", "retainCount")(lambda rt_, e_, r, s: 1)
    M("NSObject", "class")(lambda rt_, e_, r, s: rt.class_of(r)[0].addr)
    M("NSObject", "class", meta=True)(ret_self)
    M("NSObject", "isProxy")(ret_nil)
    M("NSObject", "hash")(lambda rt_, e_, r, s: r)
    M("NSObject", "isEqual:")(lambda rt_, e_, r, s: 1 if r == A(e_, 0) else 0)
    M("NSObject", "isKindOfClass:")(lambda rt_, e_, r, s: 1 if rt.is_kind(rt.class_of(r)[0], rt.by_addr[A(e_, 0)][0].name) else 0)
    M("NSObject", "isMemberOfClass:")(lambda rt_, e_, r, s: 1 if rt.class_of(r)[0] is rt.by_addr[A(e_, 0)][0] else 0)
    M("NSObject", "respondsToSelector:")(lambda rt_, e_, r, s: 1 if rt.lookup(*rt.class_of(r)[:1], rt.selname(A(e_, 0)), False) is not None else 0)

    def perform(rt_, e_, r, s):
        # rewrite stack: self, performSelector:..., sel, obj... -> self, sel, obj...
        sel = A(e_, 0)
        if s == "performSelector:withObject:withObject:":
            o1, o2 = A(e_, 1), A(e_, 2)
            e_.w32(rt._argaddr(e_, 2), o1); e_.w32(rt._argaddr(e_, 3), o2)
        elif s == "performSelector:withObject:":
            o1 = A(e_, 1)
            e_.w32(rt._argaddr(e_, 2), o1)
        e_.w32(rt._argaddr(e_, 1), sel)
        return rt.send(e_)
    M("NSObject", "performSelector: performSelector:withObject: performSelector:withObject:withObject:")(perform)
    M("NSAutoreleasePool", "release drain")(ret_nil)

    # ------------------------------------------------------------- describe
    def describe(a):
        o = rt.obj(a) if a else None
        if a == 0: return "(null)"
        if o is not None:
            if hasattr(o, "s"): return o.s
            if hasattr(o, "num"): return str(o.num)
        return "<%s: %#x>" % (rt.class_of(a)[0].name, a)
    rt.describe = describe
    M("NSObject", "description")(lambda rt_, e_, r, s: rt.nsstr(describe(r)))

    from . import foundation
    foundation.install(rt, e, M)
    from . import appkit
    appkit.install(rt, e, M)

    def app_main(e_):
        e_.stopped_on = "NSApplicationMain"
        e_.uc.emu_stop()
        return 0
    h["_NSApplicationMain"] = app_main
    return rt
