"""Foundation classes: strings, data, collections, bundle, file manager, defaults, ..."""
import os, struct, sys, time, re
from .ucshim import *
from .objc_rt import HObj
from .cocoa import A

from .emu import BUNDLE
BASE = BUNDLE
RES = BASE + "/Contents/Resources"
HOME_SUPPORT = os.environ.get("A6_SUPPORT_DIR") or os.path.expanduser("~/Library/Application Support/Asphalt6arm64")


def install(rt, e, M):
    nsstr = rt.nsstr
    py = lambda a: rt.py(a) or ""

    def ret_self(rt_, e_, r, s): return r
    def ret_nil(rt_, e_, r, s): return 0
    def ret_yes(rt_, e_, r, s): return 1

    def set_s(a, s):
        rt.obj(a).s = s
        rt.obj(a).__dict__.pop("cbuf", None)
        return a

    # ---------------------------------------------------------- NSString
    def cstring(a):
        o = rt.obj(a)
        if getattr(o, "cbuf", None) is None:
            b = o.s.encode("utf-8") + b"\0"
            o.cbuf = e.malloc(len(b)); e.wr(o.cbuf, b)
        return o.cbuf
    M("NSString", "UTF8String cStringUsingEncoding: cString lossyCString")(lambda rt_, e_, r, s: cstring(r))
    M("NSString", "length")(lambda rt_, e_, r, s: len(rt.obj(r).s))
    M("NSString", "characterAtIndex:")(lambda rt_, e_, r, s: ord(rt.obj(r).s[A(e_, 0)]) if A(e_, 0) < len(rt.obj(r).s) else 0)
    M("NSString", "isEqualToString:")(lambda rt_, e_, r, s: 1 if py(r) == py(A(e_, 0)) else 0)
    M("NSString", "isEqual:")(lambda rt_, e_, r, s: 1 if (A(e_, 0) and py(r) == py(A(e_, 0))) else 0)
    M("NSString", "hasPrefix:")(lambda rt_, e_, r, s: 1 if py(r).startswith(py(A(e_, 0))) else 0)
    M("NSString", "hasSuffix:")(lambda rt_, e_, r, s: 1 if py(r).endswith(py(A(e_, 0))) else 0)
    M("NSString", "isAbsolutePath")(lambda rt_, e_, r, s: 1 if py(r).startswith("/") else 0)
    def compare(rt_, e_, r, s):
        a, b = py(r), py(A(e_, 0))
        return ((a > b) - (a < b)) & 0xFFFFFFFF
    M("NSString", "compare:")(compare)
    M("NSString", "substringToIndex:")(lambda rt_, e_, r, s: nsstr(py(r)[:A(e_, 0)]))
    M("NSString", "stringByAppendingPathComponent:")(lambda rt_, e_, r, s: nsstr(py(r).rstrip("/") + "/" + py(A(e_, 0)).lstrip("/") if py(r) else py(A(e_, 0))))
    M("NSString", "stringByExpandingTildeInPath")(lambda rt_, e_, r, s: nsstr(os.path.expanduser(py(r))))
    M("NSString", "stringByReplacingOccurrencesOfString:withString:")(lambda rt_, e_, r, s: nsstr(py(r).replace(py(A(e_, 0)), py(A(e_, 1)))))
    M("NSString", "stringByAddingPercentEscapesUsingEncoding:")(lambda rt_, e_, r, s: nsstr(__import__("urllib.parse", fromlist=["x"]).quote(py(r), safe="/:?=&%#")))
    def objc_format(fmt_a, first):
        ga, gd = e.stack_args(e, first)
        # stack_args indexes from the function's first arg; we call it with message-arg indexing
        return e.fmt(e, py(fmt_a).encode("utf-8"), ga, gd).decode("utf-8", "replace")
    def margs(first_msg_arg):
        return first_msg_arg + 2 + e.sb
    def fmt_from(e_, idx):
        ga, gd = e_.stack_args(e_, margs(idx) + 1)
        return e_.fmt(e_, py(e_.arg(margs(idx))).encode("utf-8"), ga, gd).decode("utf-8", "replace")
    M("NSString", "stringWithFormat:", meta=True)(lambda rt_, e_, r, s: nsstr(fmt_from(e_, 0)))
    M("NSString", "stringByAppendingFormat:")(lambda rt_, e_, r, s: nsstr(py(r) + fmt_from(e_, 0)))
    def init_fmt(rt_, e_, r, s):
        set_s(r, fmt_from(e_, 0)); return r
    M("NSString", "initWithFormat:")(init_fmt)
    M("NSString", "initWithString: initWithUTF8String: initWithCString:")(
        lambda rt_, e_, r, s: set_s(r, py(A(e_, 0)) if s == "initWithString:" else e_.cstr(A(e_, 0)).decode("utf-8", "replace")))
    M("NSString", "stringWithUTF8String:")(lambda rt_, e_, r, s: nsstr(e_.cstr(A(e_, 0)).decode("utf-8", "replace")), meta=True) if False else None
    M("NSString", "stringWithUTF8String: stringWithCString:encoding:", meta=True)(
        lambda rt_, e_, r, s: nsstr(e_.cstr(A(e_, 0)).decode("utf-8", "replace")))
    M("NSString", "initWithBytes:length:encoding:")(lambda rt_, e_, r, s: set_s(r, e_.rd(A(e_, 0), A(e_, 1)).decode("utf-8", "replace")))
    def init_data(rt_, e_, r, s):
        d = rt.obj(A(e_, 0)).data
        return set_s(r, bytes(d).decode("utf-8", "replace"))
    M("NSString", "initWithData:encoding:")(init_data)
    def get_cstring(rt_, e_, r, s):
        b = py(r).encode("utf-8") + b"\0"
        buf, mx = A(e_, 0), A(e_, 1)
        if len(b) > mx: return 0
        e_.wr(buf, b); return 1
    M("NSString", "getCString:maxLength:encoding:")(get_cstring)
    def range_of(rt_, e_, r, s):
        # NSRange is 8 bytes: returned in EAX (location) : EDX (length) on i386
        hay, needle, opts = py(r), py(A(e_, 0)), A(e_, 1)
        i = hay.rfind(needle) if opts & 4 else hay.find(needle)
        if i < 0:
            e_.uc.reg_write(UC_X86_REG_EDX, 0); return 0x7FFFFFFF
        e_.uc.reg_write(UC_X86_REG_EDX, len(needle)); return i
    M("NSString", "rangeOfString:options:")(range_of)
    def write_file(rt_, e_, r, s):
        try:
            open(py(A(e_, 0)), "w").write(py(r)); return 1
        except OSError: return 0
    M("NSString", "writeToFile:atomically:encoding:error:")(write_file)
    def string_from_file(rt_, e_, r, s):
        try:
            return nsstr(open(py(A(e_, 0)), "r", errors="replace").read())
        except OSError: return 0
    M("NSString", "stringWithContentsOfFile:encoding:error:", meta=True)(string_from_file)
    M("NSMutableString", "setString:")(lambda rt_, e_, r, s: set_s(r, py(A(e_, 0))) and 0)
    M("NSString", "copy mutableCopy")(lambda rt_, e_, r, s: nsstr(py(r)))

    # ------------------------------------------------------------- NSData
    def mk_data(b):
        return rt.new_obj("NSMutableData", data=bytearray(b), ptr=0)
    def data_ptr(a):
        o = rt.obj(a)
        n = max(len(o.data), 1)
        if not getattr(o, "ptr", 0) or o.ptr_len != n:
            o.ptr = e.malloc(n); o.ptr_len = n
        e.wr(o.ptr, bytes(o.data))
        return o.ptr
    M("NSData", "bytes mutableBytes")(lambda rt_, e_, r, s: data_ptr(r))  # note: writes via mutableBytes need sync
    M("NSData", "length")(lambda rt_, e_, r, s: len(rt.obj(r).data))
    M("NSData", "dataWithBytes:length: dataWithBytesNoCopy:length:freeWhenDone:", meta=True)(
        lambda rt_, e_, r, s: mk_data(e_.rd(A(e_, 0), A(e_, 1))))
    M("NSData", "dataWithLength:", meta=True)(lambda rt_, e_, r, s: mk_data(bytes(A(e_, 0))))
    M("NSData", "data", meta=True)(lambda rt_, e_, r, s: mk_data(b""))
    M("NSMutableData", "appendData:")(lambda rt_, e_, r, s: rt.obj(r).data.extend(rt.obj(A(e_, 0)).data) and 0)
    def set_length(rt_, e_, r, s):
        o = rt.obj(r); n = A(e_, 0)
        if n < len(o.data): del o.data[n:]
        else: o.data.extend(bytes(n - len(o.data)))
        return 0
    M("NSMutableData", "setLength:")(set_length)
    M("NSData", "isEqualToData:")(lambda rt_, e_, r, s: 1 if rt.obj(r).data == rt.obj(A(e_, 0)).data else 0)

    # ----------------------------------------------------- collections
    M("NSDictionary", "dictionary", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSMutableDictionary", d={}))
    M("NSDictionary", "objectForKey:")(lambda rt_, e_, r, s: rt.obj(r).d.get(py(A(e_, 0)) or A(e_, 0), 0))
    M("NSMutableDictionary", "setObject:forKey:")(lambda rt_, e_, r, s: rt.obj(r).d.__setitem__(py(A(e_, 1)) or A(e_, 1), A(e_, 0)) or 0)
    M("NSArray", "count")(lambda rt_, e_, r, s: len(rt.obj(r).items))
    M("NSArray", "objectAtIndex:")(lambda rt_, e_, r, s: rt.obj(r).items[A(e_, 0)])
    M("NSArray", "arrayWithObject:", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSArray", items=[A(e_, 0)]))
    def fast_enum(rt_, e_, r, s):
        return 0
    M("NSArray", "countByEnumeratingWithState:objects:count:")(fast_enum)

    # ------------------------------------------------------------ NSBundle
    main_bundle = rt.new_obj("NSBundle", path=BASE)
    info = rt.new_obj("NSMutableDictionary", d={
        "CFBundleVersion": nsstr("1.0.9"), "CFBundleShortVersionString": nsstr("1.0.9"),
        "CFBundleIdentifier": nsstr("com.gameloft.asphalt6mac"), "CFBundleName": nsstr("Asphalt 6"),
        "CFBundleDisplayName": nsstr("Asphalt 6"), "CFBundleExecutable": nsstr("Asphalt 6")})
    M("NSBundle", "mainBundle", meta=True)(lambda rt_, e_, r, s: main_bundle)
    M("NSBundle", "resourcePath")(lambda rt_, e_, r, s: nsstr(RES))
    M("NSBundle", "bundlePath")(lambda rt_, e_, r, s: nsstr(BASE))
    M("NSBundle", "executablePath")(lambda rt_, e_, r, s: nsstr(BASE + "/Contents/MacOS/Asphalt 6"))
    M("NSBundle", "infoDictionary")(lambda rt_, e_, r, s: info)
    M("NSBundle", "objectForInfoDictionaryKey:")(lambda rt_, e_, r, s: rt.obj(info).d.get(py(A(e_, 0)), 0))
    def path_for_resource(rt_, e_, r, s):
        n, t = py(A(e_, 0)), py(A(e_, 1))
        p = os.path.join(RES, n + ("." + t if t else ""))
        return nsstr(p) if os.path.exists(p) else 0
    M("NSBundle", "pathForResource:ofType:")(path_for_resource)

    # ------------------------------------------------------- NSFileManager
    fm = rt.new_obj("NSFileManager")
    M("NSFileManager", "defaultManager", meta=True)(lambda rt_, e_, r, s: fm)
    M("NSFileManager", "fileExistsAtPath:")(lambda rt_, e_, r, s: 1 if os.path.exists(py(A(e_, 0))) else 0)
    def exists_dir(rt_, e_, r, s):
        p = py(A(e_, 0))
        ex = os.path.exists(p)
        if A(e_, 1): e_.wr(A(e_, 1), b"\1" if os.path.isdir(p) else b"\0")
        return 1 if ex else 0
    M("NSFileManager", "fileExistsAtPath:isDirectory:")(exists_dir)
    def mkdir(rt_, e_, r, s):
        os.makedirs(py(A(e_, 0)), exist_ok=True); return 1
    M("NSFileManager", "createDirectoryAtPath:withIntermediateDirectories:attributes:error:")(mkdir)
    M("NSFileManager", "currentDirectoryPath")(lambda rt_, e_, r, s: nsstr(os.getcwd()))
    def dir_contents(rt_, e_, r, s):
        try: return rt.new_obj("NSArray", items=[nsstr(x) for x in sorted(os.listdir(py(A(e_, 0))))])
        except OSError: return 0
    M("NSFileManager", "directoryContentsAtPath:")(dir_contents)

    # --------------------------------------------------------- misc Foundation
    defaults = {"AppleLanguages": rt.new_obj("NSArray", items=[nsstr("en")])}
    def ud_object(rt_, e_, r, s):
        k = py(A(e_, 0))
        if getattr(e_, "dbg", False): print("[defaults] objectForKey:", k, file=sys.stderr)
        return defaults.get(k, 0)
    M("NSUserDefaults", "objectForKey: arrayForKey: stringForKey:")(ud_object)
    M("NSUserDefaults", "setObject:forKey:")(lambda rt_, e_, r, s: defaults.__setitem__(py(A(e_, 1)), A(e_, 0)) or 0)
    M("NSUserDefaults", "standardUserDefaults", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSUserDefaults"))
    M("NSLocale", "objectForKey:")(lambda rt_, e_, r, s: nsstr("US"))
    M("NSLocale", "currentLocale", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSLocale"))
    M("NSWorkspace", "notificationCenter")(lambda rt_, e_, r, s: rt.new_obj("NSNotificationCenter"))
    M("NSNotificationCenter", "defaultCenter", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSNotificationCenter"))
    M("NSNotificationCenter", "addObserver:selector:name:object: removeObserver:name:object:")(ret_nil)
    M("NSGarbageCollector", "defaultCollector", meta=True)(ret_nil)
    M("NSWorkspace", "sharedWorkspace", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSWorkspace"))
    M("NSWorkspace", "openURL:")(lambda rt_, e_, r, s: (print("[open URL]", py(rt.obj(A(e_, 0)).u) if A(e_, 0) else None), 1)[1])
    M("NSURL", "URLWithString:", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSURL", u=A(e_, 0)))
    M("NSURL", "absoluteString")(lambda rt_, e_, r, s: rt.obj(r).u)
    M("NSDate", "date", meta=True)(lambda rt_, e_, r, s: rt.new_obj("NSDate", t=time.time()))
    M("NSDateFormatter", "setDateFormat:")(ret_nil)
    M("NSDateFormatter", "stringFromDate:")(lambda rt_, e_, r, s: nsstr(time.strftime("%Y-%m-%d %H:%M:%S")))
    def detach(rt_, e_, r, s):
        # NSThread detachNewThreadSelector:toTarget:withObject:
        e_.spawn_objc_thread(A(e_, 1), rt.selname(A(e_, 0)), A(e_, 2))
        return 0
    M("NSThread", "detachNewThreadSelector:toTarget:withObject:", meta=True)(detach)
    M("NSNumber", "boolValue intValue")(lambda rt_, e_, r, s: int(rt.obj(r).num))

    # ------------------------------------------------------------- CF functions
    h = e.hle
    def cfstr(a): return rt.py(a)
    h["_CFBundleGetMainBundle"] = lambda e_: main_bundle
    h["_CFBundleGetValueForInfoDictionaryKey"] = lambda e_: rt.obj(info).d.get(cfstr(e_.arg(1)), 0)
    h["_CFRelease"] = lambda e_: None
    h["_CFStringCompare"] = lambda e_: ((cfstr(e_.arg(0)) > cfstr(e_.arg(1))) - (cfstr(e_.arg(0)) < cfstr(e_.arg(1)))) & 0xFFFFFFFF
    h["_CFLocaleCopyCurrent"] = h["_CFLocaleGetSystem"] = lambda e_: rt.new_obj("NSLocale")
    h["_CFLocaleGetValue"] = lambda e_: nsstr("US")
    h["_NSSearchPathForDirectoriesInDomains"] = lambda e_: _search_paths(e_)
    def _search_paths(e_):
        os.makedirs(HOME_SUPPORT, exist_ok=True)
        return rt.new_obj("NSArray", items=[nsstr(HOME_SUPPORT)])
    h["_CFArrayGetCount"] = lambda e_: len(rt.obj(e_.arg(0)).items)
    h["_CFArrayGetValueAtIndex"] = lambda e_: rt.obj(e_.arg(0)).items[e_.arg(1)]
    h["_CFDictionaryGetValue"] = lambda e_: rt.obj(e_.arg(0)).d.get(cfstr(e_.arg(1)) or e_.arg(1), 0)
    h["_CFBooleanGetValue"] = lambda e_: 1 if e_.arg(0) else 0
    h["_CFAllocatorGetDefault"] = lambda e_: 0
    h["_Gestalt"] = lambda e_: (e_.w32(e_.arg(1), 0x1090) if e_.arg(1) else None, 0)[1]
