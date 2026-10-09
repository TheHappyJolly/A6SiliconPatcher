"""Legacy (ObjC 1 / 32-bit Mac) runtime emulation: guest classes from __OBJC, host classes in Python."""
import struct, sys
from .ucshim import *


class GClass:
    def __init__(self, name):
        self.name = name
        self.addr = 0
        self.meta = 0
        self.super_name = None
        self.size = 0
        self.ivars = {}
        self.imeths = {}
        self.cmeths = {}
        self.is_guest = True


class HClass:
    """Host (Python) implemented class."""
    def __init__(self, name, super_name):
        self.name = name
        self.super_name = super_name
        self.addr = 0
        self.meta = 0
        self.imeths = {}
        self.cmeths = {}
        self.ivars = {}
        self.size = 16
        self.is_guest = False


class RT:
    def __init__(self, e):
        self.e = e
        self.classes = {}        # name -> GClass/HClass
        self.by_addr = {}        # class addr (and meta addr) -> (cls, is_meta)
        self.objs = {}           # instance addr -> python host object
        self.sel_cache = {}
        self.cfstring_isa = 0
        self.unimpl = set()
        self.trace = False
        e.rt = self

    # ------------------------------------------------------------------ parse
    def load_guest(self):
        e = self.e
        sec = e.sections["__module_info"]
        for off in range(0, sec.size, 16):
            m = sec.virtual_address + off
            symtab = e.u32(m + 12)
            ncls = struct.unpack("<H", e.rd(symtab + 8, 2))[0]
            for i in range(ncls):
                self._parse_class(e.u32(symtab + 12 + 4 * i))
        # resolve superclass links / patch pointers once every name is known
        for c in list(self.classes.values()):
            if c.is_guest:
                self._patch_class(c)
        self._patch_cls_refs()

    def _methods(self, mlists_ptr, info):
        e = self.e
        out = {}
        if not mlists_ptr:
            return out
        lists = []
        if not (info & 0x100):   # no CLS_METHOD_ARRAY: pointer is the list itself
            lists = [mlists_ptr]
        else:
            p = mlists_ptr
            while True:
                lp = e.u32(p)
                if lp == 0 or lp == 0xFFFFFFFF:
                    break
                lists.append(lp); p += 4
        for lp in lists:
            cnt = e.u32(lp + 4)
            for i in range(cnt):
                name = e.u32(lp + 8 + 12 * i)
                imp = e.u32(lp + 8 + 12 * i + 8)
                out[e.cstr(name).decode()] = imp
        return out

    def _parse_class(self, a):
        e = self.e
        isa, sup, nm, ver, info, isize, ivars, mlists = struct.unpack("<8I", e.rd(a, 32))
        name = e.cstr(nm).decode()
        c = GClass(name)
        c.addr = a
        c.meta = isa
        c.super_name = e.cstr(sup).decode() if sup else None
        c.size = isize
        c.imeths = self._methods(mlists, info)
        ma = struct.unpack("<8I", e.rd(isa, 32))
        c.cmeths = self._methods(ma[7], ma[4])
        if ivars:
            cnt = e.u32(ivars)
            for i in range(cnt):
                n, t, off = struct.unpack("<3I", e.rd(ivars + 4 + 12 * i, 12))
                c.ivars[e.cstr(n).decode()] = off
        self.classes[name] = c
        self.by_addr[a] = (c, False)
        self.by_addr[isa] = (c, True)

    def _patch_class(self, c):
        e = self.e
        sup = self.get_class(c.super_name) if c.super_name else None
        if sup:
            e.w32(c.addr + 4, sup.addr)
            e.w32(c.meta + 4, sup.meta)
        # metaclass isa -> root metaclass (NSObject's), leave as is if unknown
        root = self.get_class("NSObject")
        e.w32(c.meta, root.meta)

    def _patch_cls_refs(self):
        e = self.e
        sec = e.sections["__cls_refs"]
        for i in range(sec.size // 4):
            slot = sec.virtual_address + 4 * i
            name = e.cstr(e.u32(slot)).decode()
            e.w32(slot, self.get_class(name).addr)

    # --------------------------------------------------------------- classes
    def host_class(self, name, super_name="NSObject"):
        if name in self.classes:
            return self.classes[name]
        c = HClass(name, super_name if name != "NSObject" else None)
        self._alloc_class(c)
        self.classes[name] = c
        return c

    def _alloc_class(self, c):
        e = self.e
        c.addr = e.alloc_static(48)
        c.meta = e.alloc_static(48)
        nm = e.alloc_static(len(c.name) + 1)
        e.wr(nm, c.name.encode() + b"\0")
        e.wr(c.addr, struct.pack("<10I", c.meta, 0, nm, 0, 1, c.size, 0, 0, 0, 0))
        e.wr(c.meta, struct.pack("<10I", 0, 0, nm, 0, 2, 48, 0, 0, 0, 0))
        self.by_addr[c.addr] = (c, False)
        self.by_addr[c.meta] = (c, True)

    def get_class(self, name):
        c = self.classes.get(name)
        if c is None:
            # unknown framework class: create an empty host class so refs resolve
            c = self.host_class(name, "NSObject")
        return c

    def superclass(self, c):
        return self.get_class(c.super_name) if c.super_name else None

    def is_kind(self, c, name):
        while c:
            if c.name == name:
                return True
            c = self.superclass(c)
        return False

    # ------------------------------------------------------------- selectors
    def selname(self, sel):
        n = self.sel_cache.get(sel)
        if n is None:
            n = self.e.cstr(sel).decode()
            self.sel_cache[sel] = n
        return n

    # ----------------------------------------------------------------- lookup
    def class_of(self, obj):
        """Return (cls, is_meta) for a receiver."""
        if obj in self.objs:
            isa = self.e.u32(obj)
            if isa == self.cfstring_isa:
                return self.get_class("NSString"), False
            if isa not in self.by_addr:
                raise RuntimeError(f"object {obj:#x} ({self.objs[obj]!r}) has corrupt isa {isa:#x}; caller {self.e.arg(-1):#x}")
            return self.by_addr[isa][0], False
        isa = self.e.u32(obj)
        t = self.by_addr.get(isa)
        if t is not None:
            return t         # (cls, False) for instances, (cls, True) for class objects
        # constant CFString?
        if isa == self.cfstring_isa:
            return self.get_class("NSString"), False
        raise RuntimeError(f"msgSend to unknown object {obj:#x} isa={isa:#x}")

    def lookup(self, cls, sel, meta):
        c = cls
        while c is not None:
            d = c.cmeths if meta else c.imeths
            m = d.get(sel)
            if m is not None:
                return m
            c = self.superclass(c)
        if meta:
            # class objects respond to instance methods of NSObject (root class rule)
            c = self.get_class("NSObject")
            return c.imeths.get(sel)
        return None

    # ---------------------------------------------------------------- dispatch
    def send(self, e, super_=False, stret=False, fpret=False):
        base = 1 if stret else 0
        a0 = e.arg(base)
        sel = self.selname(e.arg(base + 1))
        if super_:
            recv = e.u32(a0)
            cls_addr = e.u32(a0 + 4)
            cls, meta = self.by_addr[cls_addr]
            # a "super" struct class of a metaclass-based send carries the metaclass? treat by address kind
            meta = self.by_addr[cls_addr][1]
            # first lookup starts at the *given* class (already the superclass)
        else:
            recv = a0
            if recv == 0:
                return 0
            cls, meta = self.class_of(recv)
        imp = self.lookup(cls, sel, meta)
        if self.trace:
            print(f"[msg] {'+' if meta else '-'}[{cls.name} {sel}] recv={recv:#x} -> {'guest %#x' % imp if isinstance(imp, int) else ('host' if imp else 'NONE')}", file=sys.stderr)
        if imp is None:
            if stret:
                e.extra_pop = 4
            return self._unimpl(e, recv, cls, sel, meta)
        if super_:
            # replace the objc_super* arg0 with the receiver so the callee sees self
            e.w32(self._argaddr(e, base), recv)
        if isinstance(imp, int):
            e.jump(imp)
            return None
        r = imp(self, e, recv, sel)
        if stret:
            e.extra_pop = 4     # objc_msgSend_stret pops the hidden struct pointer (ret $4)
        return r

    def _argaddr(self, e, n):
        return e.uc.reg_read(UC_X86_REG_ESP) + 4 + 4 * n

    def _unimpl(self, e, recv, cls, sel, meta):
        key = (cls.name, sel, meta)
        if key not in self.unimpl:
            self.unimpl.add(key)
            print(f"[objc] unimplemented {'+' if meta else '-'}[{cls.name} {sel}]  (from {e.arg(-1):#x})", file=sys.stderr)
        if getattr(e, "strict_objc", False):
            e.uc.emu_stop(); e.stopped_on = f"objc {cls.name} {sel}"
        return 0

    # ------------------------------------------------------------ host objects
    def new_obj(self, cls_name, **attrs):
        """Allocate a guest-visible instance of a host class, with python attrs."""
        c = self.get_class(cls_name)
        a = self.e.malloc(max(16, c.size))
        self.e.wr(a, bytes(max(16, c.size)))
        self.e.w32(a, c.addr)
        o = HObj(cls_name)
        o.__dict__.update(attrs)
        self.objs[a] = o
        return a

    def obj(self, a):
        o = self.objs.get(a)
        if o is None and a:
            isa = self.e.u32(a)
            if isa == self.cfstring_isa:
                # constant string
                ptr, ln = self.e.u32(a + 8), self.e.u32(a + 12)
                o = HObj("NSString"); o.s = self.e.rd(ptr, ln).decode("latin-1")
                self.objs[a] = o
        return o

    def py(self, a):
        """python str from an NSString-ish address (None for nil)."""
        if not a:
            return None
        o = self.obj(a)
        return getattr(o, "s", None)

    def nsstr(self, s):
        return self.new_obj("NSString", s=s)

    def call_method(self, recv, sel, *args):
        """Send a message from python to any object; run guest IMPs via nested emulation."""
        cls, meta = self.class_of(recv)
        imp = self.lookup(cls, sel, meta)
        if imp is None:
            raise RuntimeError(f"call_method: no {sel} on {cls.name}")
        if isinstance(imp, int):
            return self.e.call(imp, recv, self.e.sel_addr(sel), *args)
        return imp(self, self.e, recv, sel)  # host methods read args from stack; use call_host instead


class HObj:
    def __init__(self, cls_name):
        self.cls_name = cls_name
    def __repr__(self):
        return f"<HObj {self.cls_name} {self.__dict__}>"
