"""libstdc++ 4.2 (COW std::string, iostream bits, rb-tree helpers, cxa) implemented host-side."""
import struct, sys, time

NPOS = 0xFFFFFFFF


def install(e):
    h = e.hle
    # empty rep: {len=0, cap=0, refcnt=0x40000000} + NUL
    empty_rep = e.alloc_static(32)
    e.wr(empty_rep, struct.pack("<III", 0, 0, 0x40000000) + b"\0")
    EMPTY = empty_rep + 12
    e.w32(0xE00A0100, EMPTY)           # the guest-native std::string helpers need it
    e.hle_data["__ZNSs4_Rep20_S_empty_rep_storageE"] = empty_rep

    def rep_create(cap):
        a = e.malloc(cap + 1 + 12 + 4)
        e.wr(a, struct.pack("<III", 0, cap, 0))
        e.wr(a + 12 + cap, b"\0")
        return a

    def sget(this):
        p = e.u32(this)
        n = e.u32(p - 12)
        return e.rd(p, n)

    def sset(this, b, keep_cap=0):
        old = e.u32(this)
        if old != EMPTY and e.u32(old - 12 + 8) <= 0x7FFFFFFF:
            # unique/leaked owner: free the old rep
            e.free(old - 12)
        if not b and not keep_cap:
            e.w32(this, EMPTY); return
        cap = max(len(b), keep_cap)
        r = rep_create(cap)
        e.wr(r + 12, b + b"\0")
        e.w32(r, len(b))
        e.w32(this, r + 12)

    def sstr(e, a):  # std::string& arg -> bytes
        return sget(a)

    h["__ZNSsC1Ev"] = lambda e: (e.w32(e.arg(0), EMPTY), None)[1]
    def c_from_cstr(e):
        s = e.cstr(e.arg(1)); e.w32(e.arg(0), EMPTY); sset(e.arg(0), s)
    h["__ZNSsC1EPKcRKSaIcE"] = c_from_cstr
    def c_copy(e):
        b = sget(e.arg(1)); e.w32(e.arg(0), EMPTY); sset(e.arg(0), b)
    h["__ZNSsC1ERKSs"] = c_copy
    def c_sub(e):
        b = sget(e.arg(1)); pos, n = e.arg(2), e.arg(3)
        if pos > len(b): raise RuntimeError("basic_string::basic_string out of range")
        e.w32(e.arg(0), EMPTY); sset(e.arg(0), b[pos:pos + n if n != NPOS else None])
    h["__ZNSsC1ERKSsmm"] = c_sub
    def dtor(e):
        p = e.u32(e.arg(0))
        if p != EMPTY: e.free(p - 12)
    h["__ZNSsD1Ev"] = dtor

    def assign_c(e):
        sset(e.arg(0), e.rd(e.arg(1), e.arg(2))); return e.arg(0)
    h["__ZNSs6assignEPKcm"] = assign_c
    h["__ZNSs6assignERKSs"] = h["__ZNSsaSERKSs"] = lambda e: (sset(e.arg(0), sget(e.arg(1))), e.arg(0))[1]
    h["__ZNSsaSEPKc"] = lambda e: (sset(e.arg(0), e.cstr(e.arg(1))), e.arg(0))[1]
    h["__ZNSs6appendEPKcm"] = lambda e: (sset(e.arg(0), sget(e.arg(0)) + e.rd(e.arg(1), e.arg(2))), e.arg(0))[1]
    h["__ZNSs6appendERKSs"] = h["__ZNSspLERKSs"] = lambda e: (sset(e.arg(0), sget(e.arg(0)) + sget(e.arg(1))), e.arg(0))[1]
    h["__ZNSspLEPKc"] = lambda e: (sset(e.arg(0), sget(e.arg(0)) + e.cstr(e.arg(1))), e.arg(0))[1]
    h["__ZNSspLEc"] = lambda e: (sset(e.arg(0), sget(e.arg(0)) + bytes([e.arg(1) & 0xFF])), e.arg(0))[1]

    def reserve(e):
        b = sget(e.arg(0))
        if e.arg(1) > e.u32(e.u32(e.arg(0)) - 12 + 4):
            sset(e.arg(0), b, keep_cap=e.arg(1))
    h["__ZNSs7reserveEm"] = reserve
    def replace_c(e):
        b = sget(e.arg(0)); pos, n = e.arg(1), e.arg(2)
        s = e.rd(e.arg(3), e.arg(4)) if e.arg(4) is not None and False else None
    def replace_cstr(e):
        b = sget(e.arg(0)); pos, n = e.arg(1), e.arg(2)
        if pos > len(b): raise RuntimeError("basic_string::replace out of range")
        sset(e.arg(0), b[:pos] + e.cstr(e.arg(3)) + b[pos + min(n, len(b) - pos):]); return e.arg(0)
    h["__ZNSs7replaceEmmPKc"] = replace_cstr
    def replace_cn(e):
        b = sget(e.arg(0)); pos, n = e.arg(1), e.arg(2)
        if pos > len(b): raise RuntimeError("basic_string::replace out of range")
        sset(e.arg(0), b[:pos] + e.rd(e.arg(3), e.arg(4)) + b[pos + min(n, len(b) - pos):]); return e.arg(0)
    h["__ZNSs7replaceEmmPKcm"] = replace_cn
    def erase(e):
        b = sget(e.arg(0)); pos, n = e.arg(1), e.arg(2)
        if pos > len(b): raise RuntimeError("basic_string::erase out of range")
        sset(e.arg(0), b[:pos] + b[pos + min(n, len(b) - pos):]); return e.arg(0)
    h["__ZNSs5eraseEmm"] = erase
    def at(e):
        b = sget(e.arg(0))
        if e.arg(1) >= len(b): raise RuntimeError("basic_string::at")
        return e.u32(e.arg(0)) + e.arg(1)
    h["__ZNSs2atEm"] = at
    h["__ZNKSsixEm"] = lambda e: e.u32(e.arg(0)) + e.arg(1)
    h["__ZNSs12_M_leak_hardEv"] = lambda e: None
    def rep_create_imp(e):
        cap, old = e.arg(0), e.arg(1)
        if cap > 0x3ffffffc: raise RuntimeError("length_error")
        if cap > old and cap < 2 * old: cap = 2 * old
        return rep_create(cap)
    h["__ZNSs4_Rep9_S_createEmmRKSaIcE"] = rep_create_imp
    h["__ZNSs4_Rep10_M_destroyERKSaIcE"] = lambda e: e.free(e.arg(0))

    h["__ZNKSs4sizeEv"] = h["__ZNKSs6lengthEv"] = lambda e: e.u32(e.u32(e.arg(0)) - 12)
    h["__ZNKSs5emptyEv"] = lambda e: 1 if e.u32(e.u32(e.arg(0)) - 12) == 0 else 0
    h["__ZNKSs5c_strEv"] = lambda e: e.u32(e.arg(0))

    def find_sub(b, s, pos):
        if pos > len(b): return NPOS
        i = b.find(s, pos); return NPOS if i < 0 else i
    h["__ZNKSs4findEPKcmm"] = lambda e: find_sub(sget(e.arg(0)), e.rd(e.arg(1), e.arg(3)), e.arg(2))
    h["__ZNKSs4findEPKcm"] = lambda e: find_sub(sget(e.arg(0)), e.cstr(e.arg(1)), e.arg(2))
    h["__ZNKSs4findEcm"] = lambda e: find_sub(sget(e.arg(0)), bytes([e.arg(1) & 0xFF]), e.arg(2))
    def rfind_c(e):
        b = sget(e.arg(0)); pos = e.arg(2)
        end = len(b) if pos == NPOS else min(len(b), pos + 1)
        i = b.rfind(bytes([e.arg(1) & 0xFF]), 0, end); return NPOS if i < 0 else i
    h["__ZNKSs5rfindEcm"] = rfind_c
    def find_last_of(e):
        b = sget(e.arg(0)); chars = e.rd(e.arg(1), e.arg(3)); pos = e.arg(2)
        start = len(b) - 1 if pos == NPOS or pos >= len(b) else pos
        for i in range(start, -1, -1):
            if b[i] in chars: return i
        return NPOS
    h["__ZNKSs12find_last_ofEPKcmm"] = find_last_of
    def substr(e):
        sret, this, pos, n = e.arg(0), e.arg(1), e.arg(2), e.arg(3)
        b = sget(this)
        if pos > len(b): raise RuntimeError("basic_string::substr")
        e.w32(sret, EMPTY); sset(sret, b[pos:pos + n if n != NPOS else None]); e.extra_pop = 4; return sret
    h["__ZNKSs6substrEmm"] = substr

    def cmp(a, b): return ((a > b) - (a < b)) & 0xFFFFFFFF
    h["__ZNKSs7compareEPKc"] = lambda e: cmp(sget(e.arg(0)), e.cstr(e.arg(1)))
    h["__ZNKSs7compareERKSs"] = lambda e: cmp(sget(e.arg(0)), sget(e.arg(1)))
    def compare_pos(e):
        b = sget(e.arg(0)); pos, n = e.arg(1), e.arg(2)
        return cmp(b[pos:pos + n], sget(e.arg(3)))
    h["__ZNKSs7compareEmmRKSs"] = compare_pos
    h["__ZStlsISt11char_traitsIcEERSt13basic_ostreamIcT_ES5_PKc"] = lambda e: ostream_write(e.arg(0), e.cstr(e.arg(1)))

    # ----------------------------------------------------------------- allocator/locale/ios
    h["__ZNSaIcEC1Ev"] = h["__ZNSaIcED1Ev"] = lambda e: None
    h["__ZNSt6localeC1Ev"] = h["__ZNSt6localeD1Ev"] = lambda e: None
    h["__ZNSt8ios_base4InitC1Ev"] = h["__ZNSt8ios_base4InitD1Ev"] = lambda e: None
    h["___cxa_guard_acquire"] = lambda e: 0 if e.rd(e.arg(0), 1)[0] else 1
    h["___cxa_guard_release"] = lambda e: e.wr(e.arg(0), b"\1")
    h["___cxa_pure_virtual"] = lambda e: (_ for _ in ()).throw(RuntimeError("pure virtual called"))
    h["___cxa_begin_catch"] = lambda e: e.arg(0)
    h["___cxa_end_catch"] = lambda e: None
    h["___cxa_rethrow"] = lambda e: (_ for _ in ()).throw(RuntimeError("rethrow"))
    h["___gxx_personality_v0"] = lambda e: (_ for _ in ()).throw(RuntimeError("personality"))
    h["__Unwind_Resume"] = lambda e: (_ for _ in ()).throw(RuntimeError("unwind"))
    for n in ("__ZSt17__throw_bad_allocv", "__ZSt19__throw_logic_errorPKc",
              "__ZSt20__throw_length_errorPKc", "__ZSt20__throw_out_of_rangePKc"):
        h[n] = (lambda n: lambda e: (_ for _ in ()).throw(RuntimeError("C++ throw: " + n)))(n)

    # nothrow tag object
    e.hle_data["__ZSt7nothrow"] = e.alloc_static(8)

    # ---- std::list node helpers (circular doubly linked: next at +0, prev at +4)
    def hook(e):  # node.hook(position): insert this before position
        n, pos = e.arg(0), e.arg(1)
        p = e.u32(pos + 4)
        e.w32(n, pos); e.w32(n + 4, p); e.w32(p, n); e.w32(pos + 4, n)
    h["__ZNSt15_List_node_base4hookEPS_"] = hook
    def unhook(e):
        n = e.arg(0); nx, pv = e.u32(n), e.u32(n + 4)
        e.w32(pv, nx); e.w32(nx + 4, pv)
    h["__ZNSt15_List_node_base6unhookEv"] = unhook
    def transfer(e):
        pos, first, last = e.arg(0), e.arg(1), e.arg(2)
        if pos == last: return
        lp = e.u32(last + 4)
        fp = e.u32(first + 4)
        e.w32(fp, last); e.w32(last + 4, fp)       # unhook range
        pp = e.u32(pos + 4)
        e.w32(pp, first); e.w32(first + 4, pp)
        e.w32(lp, pos); e.w32(pos + 4, lp)
    h["__ZNSt15_List_node_base8transferEPS_S0_"] = transfer
    def lswap(e):
        a, b = e.arg(0), e.arg(1)
        an, ap, bn, bp = e.u32(a), e.u32(a + 4), e.u32(b), e.u32(b + 4)
        a_empty, b_empty = an == a, bn == b
        if not a_empty and not b_empty:
            e.w32(a, bn); e.w32(a + 4, bp); e.w32(b, an); e.w32(b + 4, ap)
            e.w32(bn + 4, a); e.w32(bp, a); e.w32(an + 4, b); e.w32(ap, b)
        elif a_empty and not b_empty:
            e.w32(a, bn); e.w32(a + 4, bp); e.w32(bn + 4, a); e.w32(bp, a)
            e.w32(b, b); e.w32(b + 4, b)
        elif not a_empty and b_empty:
            e.w32(b, an); e.w32(b + 4, ap); e.w32(an + 4, b); e.w32(ap, b)
            e.w32(a, a); e.w32(a + 4, a)
    h["__ZNSt15_List_node_base4swapERS_S0_"] = lswap

    # ---- red/black tree (std::map/set): node = {color(int), parent, left, right}
    RED, BLACK = 0, 1
    def L(n): return e.u32(n + 8)
    def R(n): return e.u32(n + 12)
    def P(n): return e.u32(n + 4)
    def C(n): return e.u32(n)
    def sL(n, v): e.w32(n + 8, v)
    def sR(n, v): e.w32(n + 12, v)
    def sP(n, v): e.w32(n + 4, v)
    def sC(n, v): e.w32(n, v)

    def increment(n):
        if R(n):
            n = R(n)
            while L(n): n = L(n)
        else:
            y = P(n)
            while n == R(y):
                n = y; y = P(y)
            if R(n) != y: n = y
        return n
    def decrement(n):
        if C(n) == RED and P(P(n)) == n:
            n = R(n)
        elif L(n):
            y = L(n)
            while R(y): y = R(y)
            n = y
        else:
            y = P(n)
            while n == L(y):
                n = y; y = P(y)
            n = y
        return n
    h["__ZSt18_Rb_tree_incrementPSt18_Rb_tree_node_base"] = lambda e: increment(e.arg(0))
    h["__ZSt18_Rb_tree_incrementPKSt18_Rb_tree_node_base"] = lambda e: increment(e.arg(0))
    h["__ZSt18_Rb_tree_decrementPSt18_Rb_tree_node_base"] = lambda e: decrement(e.arg(0))

    def rotate_left(x, root_ref):
        y = R(x); sR(x, L(y))
        if L(y): sP(L(y), x)
        sP(y, P(x))
        if x == e.u32(root_ref): e.w32(root_ref, y)
        elif x == L(P(x)): sL(P(x), y)
        else: sR(P(x), y)
        sL(y, x); sP(x, y)
    def rotate_right(x, root_ref):
        y = L(x); sL(x, R(y))
        if R(y): sP(R(y), x)
        sP(y, P(x))
        if x == e.u32(root_ref): e.w32(root_ref, y)
        elif x == R(P(x)): sR(P(x), y)
        else: sL(P(x), y)
        sR(y, x); sP(x, y)

    def insert_rebalance(e):
        # (bool insert_left, node* x, node* p, node& header)
        left, x, p, header = e.arg(0) & 0xFF, e.arg(1), e.arg(2), e.arg(3)
        root_ref = header + 4
        sP(x, p); sL(x, 0); sR(x, 0); sC(x, RED)
        if left:
            sL(p, x)
            if p == header:
                e.w32(header + 4, x); e.w32(header + 12, x)
            elif p == L(header):
                sL(header, x)
        else:
            sR(p, x)
            if p == R(header): sR(header, x)
        # fixed: header layout is {color, parent(root), left(leftmost), right(rightmost)}
        while x != e.u32(root_ref) and C(P(x)) == RED:
            xpp = P(P(x))
            if P(x) == L(xpp):
                y = R(xpp)
                if y and C(y) == RED:
                    sC(P(x), BLACK); sC(y, BLACK); sC(xpp, RED); x = xpp
                else:
                    if x == R(P(x)):
                        x = P(x); rotate_left(x, root_ref)
                    sC(P(x), BLACK); sC(P(P(x)), RED); rotate_right(P(P(x)), root_ref)
            else:
                y = L(xpp)
                if y and C(y) == RED:
                    sC(P(x), BLACK); sC(y, BLACK); sC(xpp, RED); x = xpp
                else:
                    if x == L(P(x)):
                        x = P(x); rotate_right(x, root_ref)
                    sC(P(x), BLACK); sC(P(P(x)), RED); rotate_left(P(P(x)), root_ref)
        sC(e.u32(root_ref), BLACK)
    h["__ZSt29_Rb_tree_insert_and_rebalancebPSt18_Rb_tree_node_baseS0_RS_"] = insert_rebalance

    def rebalance_for_erase(e):
        z, header = e.arg(0), e.arg(1)
        root_ref = header + 4
        root = lambda: e.u32(root_ref)
        y = z; x = 0; xp = 0
        if not L(y): x = R(y)
        elif not R(y): x = L(y)
        else:
            y = R(y)
            while L(y): y = L(y)
            x = R(y)
        if y != z:
            sP(L(z), y); sL(y, L(z))
            if y != R(z):
                xp = P(y)
                if x: sP(x, P(y))
                sL(P(y), x); sR(y, R(z)); sP(R(z), y)
            else:
                xp = y
            if root() == z: e.w32(root_ref, y)
            elif L(P(z)) == z: sL(P(z), y)
            else: sR(P(z), y)
            sP(y, P(z))
            cy, cz = C(y), C(z); sC(y, cz); sC(z, cy)
            y = z
        else:
            xp = P(y)
            if x: sP(x, P(y))
            if root() == z: e.w32(root_ref, x)
            elif L(P(z)) == z: sL(P(z), x)
            else: sR(P(z), x)
            if L(header) == z:
                if not R(z): e.w32(header + 8, P(z))
                else:
                    m = x
                    while L(m): m = L(m)
                    e.w32(header + 8, m)
            if R(header) == z:
                if not L(z): e.w32(header + 12, P(z))
                else:
                    m = x
                    while R(m): m = R(m)
                    e.w32(header + 12, m)
        if C(y) != RED:
            while x != root() and (not x or C(x) == BLACK):
                if x == L(xp):
                    w = R(xp)
                    if C(w) == RED:
                        sC(w, BLACK); sC(xp, RED); rotate_left(xp, root_ref); w = R(xp)
                    if (not L(w) or C(L(w)) == BLACK) and (not R(w) or C(R(w)) == BLACK):
                        sC(w, RED); x = xp; xp = P(xp)
                    else:
                        if not R(w) or C(R(w)) == BLACK:
                            sC(L(w), BLACK); sC(w, RED); rotate_right(w, root_ref); w = R(xp)
                        sC(w, C(xp)); sC(xp, BLACK)
                        if R(w): sC(R(w), BLACK)
                        rotate_left(xp, root_ref); break
                else:
                    w = L(xp)
                    if C(w) == RED:
                        sC(w, BLACK); sC(xp, RED); rotate_right(xp, root_ref); w = L(xp)
                    if (not R(w) or C(R(w)) == BLACK) and (not L(w) or C(L(w)) == BLACK):
                        sC(w, RED); x = xp; xp = P(xp)
                    else:
                        if not L(w) or C(L(w)) == BLACK:
                            sC(R(w), BLACK); sC(w, RED); rotate_left(w, root_ref); w = L(xp)
                        sC(w, C(xp)); sC(xp, BLACK)
                        if L(w): sC(L(w), BLACK)
                        rotate_right(xp, root_ref); break
            if x: sC(x, BLACK)
        return y
    h["__ZSt28_Rb_tree_rebalance_for_erasePSt18_Rb_tree_node_baseRS_"] = rebalance_for_erase

    # ------------------------------------------------------------ iostream
    # libstdc++ 4.2 / i386 layout facts recovered from the game's inlined constructors:
    #   ostringstream: vptr@0, stringbuf@4 (streambuf: vptr, in_beg/cur/end @4/8/c, out_beg/cur/end @10/14/18), ios @ +0x2c
    #   ifstream:      vptr@0, gcount@4, filebuf@8, ios @ +0x20c;  ios_base::_M_streambuf_state @ ios+0x14
    def mk_table(vbase, name_vt, name_vtt):
        vt = e.alloc_static(0x100)
        e.w32(vt, vbase)                         # vbase offset of the primary vptr (vt+0xc)
        e.w32(vt + 0x14, 0); e.w32(vt + 0x18, (-vbase) & 0xFFFFFFFF)   # header of the ios sub-vtable (vt+0x20)
        for off in (0xc, 0x10, 0x20, 0x24, 0x28, 0x2c, 0x30):
            e.w32(vt + off, e.trap("__stream_noop"))
        vtt = e.alloc_static(0x40)
        e.w32(vtt, vt + 0xc); e.w32(vtt + 4, vt + 0xc); e.w32(vtt + 8, vt + 0x20)
        e.hle_data[name_vt] = vt; e.hle_data[name_vtt] = vtt
    h["__stream_noop"] = lambda e: 0
    mk_table(0x2c, "__ZTVSt19basic_ostringstreamIcSt11char_traitsIcESaIcEE", "__ZTTSt19basic_ostringstreamIcSt11char_traitsIcESaIcEE")
    mk_table(0x20c, "__ZTVSt14basic_ifstreamIcSt11char_traitsIcEE", "__ZTTSt14basic_ifstreamIcSt11char_traitsIcEE")
    for n in ("__ZTVSt13basic_filebufIcSt11char_traitsIcEE", "__ZTVSt15basic_streambufIcSt11char_traitsIcEE",
              "__ZTVSt15basic_stringbufIcSt11char_traitsIcESaIcEE", "__ZTVSt9basic_iosIcSt11char_traitsIcEE",
              "__ZTVN10__cxxabiv117__class_type_infoE", "__ZTVN10__cxxabiv120__si_class_type_infoE"):
        a_ = e.alloc_static(0x100)
        for off in range(0, 0x80, 4): e.w32(a_ + off, e.trap("__stream_noop"))
        e.hle_data[n] = a_
    h["__ZNSt8ios_baseC2Ev"] = h["__ZNSt8ios_baseD2Ev"] = lambda e: None
    h["__ZNSt9basic_iosIcSt11char_traitsIcEE4initEPSt15basic_streambufIcS1_E"] = lambda e: None
    h["__ZNSt9basic_iosIcSt11char_traitsIcEE5clearESt12_Ios_Iostate"] = lambda e: e.w32(e.arg(0) + 0x14, e.arg(1))
    for n in ("__ZNSdD0Ev", "__ZNSdD1Ev", "__ZNSiD0Ev", "__ZNSiD1Ev", "__ZNSoD0Ev", "__ZNSoD1Ev",
              "__ZThn8_NSdD0Ev", "__ZThn8_NSdD1Ev", "__ZTv0_n12_NSdD0Ev", "__ZTv0_n12_NSdD1Ev",
              "__ZTv0_n12_NSiD0Ev", "__ZTv0_n12_NSiD1Ev", "__ZTv0_n12_NSoD0Ev", "__ZTv0_n12_NSoD1Ev",
              "__ZNSt15basic_streambufIcSt11char_traitsIcEE4syncEv", "__ZNSt15basic_streambufIcSt11char_traitsIcEE5imbueERKSt6locale",
              "__ZNSt15basic_streambufIcSt11char_traitsIcEE5uflowEv", "__ZNSt15basic_streambufIcSt11char_traitsIcEE6xsgetnEPci",
              "__ZNSt15basic_streambufIcSt11char_traitsIcEE6xsputnEPKci", "___cxa_pure_virtual"):
        e.native.setdefault(n, e.trap("__stream_noop"))

    # ---- ostringstream: append into the stringbuf put area (pbase/pptr/epptr), as libstdc++ would
    _SD = [0]
    def ostream_write(this, b):
        # the put area lives in a std::string rep owned by the stringbuf's _M_string member (sb+0x24), exactly like
        # libstdc++: the game's own (inlined) string destructor then frees it through _Rep::_M_destroy
        sb = this + 4
        base, cur, end = e.u32(sb + 0x10), e.u32(sb + 0x14), e.u32(sb + 0x18)
        if _SD[0] < 12 and __import__('os').environ.get('A6_STREAMDBG'):
            _SD[0] += 1; print('[stream]', hex(this), 'words@sb+0x1c..0x34', [hex(e.u32(sb + o)) for o in range(0x1c, 0x38, 4)], 'EMPTY', hex(EMPTY), file=sys.stderr)
        if cur + len(b) > end:
            used = cur - base
            cap = max(64, 2 * (end - base), used + len(b) + 16)
            rep = e.malloc(12 + cap + 1)
            e.wr(rep, struct.pack("<III", used, cap, 0))
            nb = rep + 12
            if used: e.wr(nb, e.rd(base, used))
            old = e.u32(sb + 0x24)
            if old and old != EMPTY and e.py_block_size(old - 12) is not None: e.free(old - 12)
            e.w32(sb + 0x24, nb)
            base, cur, end = nb, nb + used, nb + cap
            e.w32(sb + 0x10, base); e.w32(sb + 0x18, end)
        e.wr(cur, b); e.w32(sb + 0x14, cur + len(b))
        return this
    h["__ZNSo5writeEPKci"] = lambda e: ostream_write(e.arg(0), e.rd(e.arg(1), e.arg(2)))
    h["__ZSt16__ostream_insertIcSt11char_traitsIcEERSt13basic_ostreamIT_T0_ES6_PKS3_i"] = \
        lambda e: ostream_write(e.arg(0), e.rd(e.arg(1), e.arg(2)))
    h["__ZStlsISt11char_traitsIcEERSt13basic_ostreamIcT_ES5_PKc"] = lambda e: ostream_write(e.arg(0), e.cstr(e.arg(1)))
    h["__ZNSolsEi"] = lambda e: ostream_write(e.arg(0), str(struct.unpack("<i", struct.pack("<I", e.arg(1)))[0]).encode())
    h["__ZNSo9_M_insertImEERSoT_"] = lambda e: ostream_write(e.arg(0), str(e.arg(1)).encode())
    def ins_double(e):
        v = struct.unpack("<d", e.rd(e.uc.reg_read(UC_X86_REG_ESP) + 8, 8))[0]
        return ostream_write(e.arg(0), ("%g" % v).encode())
    h["__ZNSo9_M_insertIdEERSoT_"] = ins_double
    h["__ZNSt15basic_stringbufIcSt11char_traitsIcESaIcEE7_M_syncEPcmm"] = lambda e: None

    # ---- file streams (ifstream/fstream): python file objects keyed by the stream/filebuf address
    files = {}
    def reg(keys, rec):
        for k in keys: files[k] = rec
    def lookup(this):
        for k in (this, this + 8, this - 8):
            if k in files: return files[k]
        return None
    def omode(m):
        mode = "r" if (m & 8 and not m & 16) else ("w" if (m & 16 and not m & 8) else "r+")
        if m & 1: mode = "a"
        if m & 32 and mode == "r+": mode = "w+"
        return mode + "b"
    def fb_open(e):
        this, path, m = e.arg(0), e.cstr(e.arg(1)).decode("utf-8", "replace"), e.arg(2)
        rec = files.get(this) or {}
        try:
            if getattr(e, "dbg", False): print("[ifstream open]", path, m, file=sys.stderr)
            rec["f"] = open(path, omode(m))
            if m & 2: rec["f"].seek(0, 2)
        except OSError:
            return 0
        files[this] = rec; files[this - 8] = rec
        return this
    h["__ZNSt13basic_filebufIcSt11char_traitsIcEE4openEPKcSt13_Ios_Openmode"] = fb_open
    def fb_ctor(e): files[e.arg(0)] = {"f": None}; files[e.arg(0) - 8] = files[e.arg(0)]
    h["__ZNSt13basic_filebufIcSt11char_traitsIcEEC1Ev"] = fb_ctor
    def fb_close(e):
        rec = files.get(e.arg(0))
        if rec and rec["f"]: rec["f"].close(); rec["f"] = None; return e.arg(0)
        return 0
    h["__ZNSt13basic_filebufIcSt11char_traitsIcEE5closeEv"] = fb_close
    def isopen_basic(e):
        for k in (e.arg(0) - 0x4c, e.arg(0)):
            r = files.get(k)
            if r: return 1 if r["f"] else 0
        return 0
    h["__ZNKSt12__basic_fileIcE7is_openEv"] = isopen_basic
    h["__ZNSt12__basic_fileIcED1Ev"] = lambda e: None
    # fstream proper
    def fs_ctor(e): files[e.arg(0)] = {"f": None}
    def fs_open(e):
        this = e.arg(0); rec = files.setdefault(this, {"f": None})
        try: rec["f"] = open(e.cstr(e.arg(1)).decode("utf-8", "replace"), omode(e.arg(2)))
        except OSError: pass
    h["__ZNSt13basic_fstreamIcSt11char_traitsIcEEC1Ev"] = fs_ctor
    h["__ZNSt13basic_fstreamIcSt11char_traitsIcEE4openEPKcSt13_Ios_Openmode"] = fs_open
    h["__ZNSt13basic_fstreamIcSt11char_traitsIcEE5closeEv"] = lambda e: (files.get(e.arg(0), {}).get("f") and files[e.arg(0)]["f"].close(), None)[1]
    h["__ZNSt13basic_fstreamIcSt11char_traitsIcEE7is_openEv"] = lambda e: 1 if files.get(e.arg(0), {}).get("f") else 0
    h["__ZNSt13basic_fstreamIcSt11char_traitsIcEED1Ev"] = lambda e: files.pop(e.arg(0), None) and None
    def ios_of(this):
        vptr = e.u32(this)
        return this + struct.unpack("<i", struct.pack("<I", e.u32(vptr - 12)))[0]
    def i_read(e):
        this, buf, n = e.arg(0), e.arg(1), e.arg(2)
        rec = lookup(this)
        data = rec["f"].read(n) if rec and rec["f"] else b""
        e.wr(buf, data); e.w32(this + 4, len(data))
        if len(data) < n:
            st = ios_of(this) + 0x14; e.w32(st, e.u32(st) | 3)
        return this
    h["__ZNSi4readEPci"] = i_read
    def i_seekg(e):
        this = e.arg(0); off = struct.unpack("<q", struct.pack("<II", e.arg(1), e.arg(2)))[0]; wh = e.arg(3)
        rec = lookup(this)
        if rec and rec["f"]: rec["f"].seek(off, wh)
        return this
    h["__ZNSi5seekgExSt12_Ios_Seekdir"] = i_seekg
    def i_tellg(e):
        sret, this = e.arg(0), e.arg(1)
        rec = lookup(this)
        pos = rec["f"].tell() if rec and rec["f"] else -1
        e.wr(sret, struct.pack("<qQ", pos, 0)); e.extra_pop = 4
        return sret
    h["__ZNSi5tellgEv"] = i_tellg
    def fpos_cvt(e):
        v = struct.unpack("<q", e.rd(e.arg(0), 8))[0]
        e.uc.reg_write(UC_X86_REG_EDX, (v >> 32) & 0xFFFFFFFF)
        return v & 0xFFFFFFFF
    h["__ZNKSt4fposI11__mbstate_tEcvxEv"] = fpos_cvt
    return None
