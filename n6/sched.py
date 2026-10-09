"""pthreads for the native runtime: guest threads are real host threads (python threads that run guest code via
host_guest_call); mutexes / condition variables are python primitives keyed by the guest object address."""
import os, sys, threading, time, struct, ctypes
from .ucshim import *


class _Mutex:
    __slots__ = ("lock", "owner", "count", "rec")
    def __init__(self, rec=False):
        self.lock = threading.Lock(); self.owner = None; self.count = 0; self.rec = rec


class Sched:
    def __init__(self, e):
        self.e = e
        self.mutexes = {}
        self.conds = {}
        self.mattr = {}
        self.threads = {}                 # handle -> [thread, retval, done_event]
        self.tl = threading.local()
        self.next_handle = 0x7100
        self.guard = threading.Lock()
        self.main_handle = 0x7000
        self.tl.handle = self.main_handle
        self._install()

    def handle(self):
        h = getattr(self.tl, "handle", None)
        if h is None:
            with self.guard:
                self.next_handle += 0x40; h = self.next_handle
            self.tl.handle = h
        return h

    def _mutex(self, addr):
        m = self.mutexes.get(addr)
        if m is None:
            with self.guard:
                m = self.mutexes.setdefault(addr, _Mutex())
        return m

    def spawn(self, fn, args):
        e = self.e
        with self.guard:
            self.next_handle += 0x40; h = self.next_handle
        done = threading.Event()
        rec = [None, 0, done]
        def run():
            self.tl.handle = h
            rec[1] = e.call(fn, *args)
            done.set()
            if os.environ.get('A6_PARK', '0') == '1':
                while True: time.sleep(3600)       # never let a Rosetta guest thread exit
            e.lib.host_thread_release()
        if os.environ.get('A6_LOGTHREADS'): print(f'[thread] spawn fn={fn:#x} args={[hex(a) for a in args]} handle={h:#x}', file=sys.stderr)
        t = threading.Thread(target=run, daemon=True)
        rec[0] = t
        self.threads[h] = rec
        t.start()
        return h

    def _install(self):
        e, h = self.e, self.e.hle

        # the lock state lives in the guest pthread_mutex_t (owner thread id at +4, recursion count at +8): the hot
        # lock/unlock paths are guest-native (guestlib/libc.c); python only initialises and re-acquires for cond waits
        def tid(): return ctypes.c_uint32.from_address(e._ctx + 0x50).value
        def mutex_init(e_):
            a = e_.arg(0); e_.w32(a + 4, 0); e_.w32(a + 8, 0)
            return 0
        def mutex_lock(e_):
            a = e_.arg(0); me = tid()
            if e_.u32(a + 4) == me: e_.w32(a + 8, e_.u32(a + 8) + 1); return 0
            e_.lib.host_mutex_lock(a, me); e_.w32(a + 8, 1)
            return 0
        def mutex_trylock(e_):
            a = e_.arg(0); me = tid()
            if e_.u32(a + 4) == me: e_.w32(a + 8, e_.u32(a + 8) + 1); return 0
            if e_.u32(a + 4) == 0:
                e_.lib.host_mutex_lock(a, me); e_.w32(a + 8, 1); return 0
            return 16
        def mutex_unlock(e_):
            a = e_.arg(0)
            if e_.u32(a + 4) == tid():
                n = e_.u32(a + 8) - 1
                if n <= 0: e_.w32(a + 8, 0); e_.w32(a + 4, 0)
                else: e_.w32(a + 8, n)
            return 0
        h["_pthread_mutex_init"] = mutex_init
        h["_pthread_mutex_lock"] = mutex_lock
        h["_pthread_mutex_trylock"] = mutex_trylock
        h["_pthread_mutex_unlock"] = mutex_unlock
        h["_pthread_mutex_destroy"] = lambda e_: 0
        h["_pthread_mutexattr_init"] = lambda e_: (self.mattr.__setitem__(e_.arg(0), 0), 0)[1]
        h["_pthread_mutexattr_settype"] = lambda e_: (self.mattr.__setitem__(e_.arg(0), e_.arg(1)), 0)[1]
        h["_pthread_mutexattr_destroy$UNIX2003"] = lambda e_: 0

        def cond_init(e_):
            self.conds[e_.arg(0)] = []; return 0
        def cond_wait(e_, timed=False):
            caddr, maddr = e_.arg(0), e_.arg(1)
            me = tid()
            sem = threading.Semaphore(0)
            q = self.conds.setdefault(caddr, [])
            q.append(sem)
            saved = e_.u32(maddr + 8)
            if e_.u32(maddr + 4) == me:            # release (fully, even if recursive)
                e_.w32(maddr + 8, 0); e_.w32(maddr + 4, 0)
            timeout = None
            if timed:
                ts = e_.arg(2)
                abs_t = e_.u32(ts) + e_.u32(ts + 4) / 1e9
                timeout = max(abs_t - time.time(), 0.0)
            ok = sem.acquire(timeout=timeout) if timeout is not None else sem.acquire()
            if not ok:
                try: q.remove(sem)
                except ValueError: pass
            e_.lib.host_mutex_lock(maddr, me); e_.w32(maddr + 8, saved or 1)
            return 0 if ok else 60
        def cond_signal(e_, all_=False):
            q = self.conds.get(e_.arg(0), [])
            while q:
                q.pop(0).release()
                if not all_: break
            return 0
        h["_pthread_cond_init$UNIX2003"] = cond_init
        h["_pthread_cond_destroy"] = lambda e_: 0
        h["_pthread_cond_wait$UNIX2003"] = e.idle_wrap(lambda e_: cond_wait(e_))
        h["_pthread_cond_timedwait$UNIX2003"] = e.idle_wrap(lambda e_: cond_wait(e_, True))
        h["_pthread_cond_signal"] = lambda e_: cond_signal(e_)
        h["_pthread_cond_broadcast"] = lambda e_: cond_signal(e_, True)

        def create(e_):
            if os.environ.get('A6_NOTHREADS'): return 11
            hnd = self.spawn(e_.arg(2), [e_.arg(3)])
            if e_.arg(0): e_.w32(e_.arg(0), hnd)
            return 0
        h["_pthread_create"] = create
        h["_pthread_self"] = lambda e_: self.handle()
        def join(e_):
            rec = self.threads.get(e_.arg(0))
            if rec is None: return 3
            rec[2].wait()
            if e_.arg(1): e_.w32(e_.arg(1), rec[1])
            return 0
        h["_pthread_join$UNIX2003"] = e.idle_wrap(join)
        def pexit(e_):
            raise SystemExit
        h["_pthread_detach"] = lambda e_: 0
        h["_pthread_cancel$UNIX2003"] = h["_pthread_kill"] = lambda e_: 0
        h["_usleep$UNIX2003"] = e.idle_wrap(lambda e_: (time.sleep(e_.arg(0) / 1e6), 0)[1], lambda e_: e_.arg(0) >= 300)
        h["_nanosleep$UNIX2003"] = e.idle_wrap(lambda e_: (time.sleep(e_.u32(e_.arg(0)) + e_.u32(e_.arg(0) + 4) / 1e9), 0)[1], lambda e_: e_.u32(e_.arg(0)) > 0 or e_.u32(e_.arg(0) + 4) >= 300000)
        h["_sched_yield"] = lambda e_: (time.sleep(0), 0)[1]

        def spawn_objc(target, selname, obj):
            if os.environ.get('A6_NOTHREADS'): return
            cls, meta = e.rt.class_of(target)
            imp = e.rt.lookup(cls, selname, meta)
            if not isinstance(imp, int):
                print("[thread] NSThread target is a host method:", selname, file=sys.stderr); return
            self.spawn(imp, [target, e.sel_addr(selname), obj])
        e.spawn_objc_thread = spawn_objc
