"""Dev driver for the native runtime: scripted input + screenshots + fps. Same flags as run.py:
 -s secs  -p shot_every  -c "t:x:y,..." (clicks)  -ky "t:keycode:down|up,..."  -fps  -nohp"""
import os, sys, struct, faulthandler
faulthandler.enable()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "x64", "site")); sys.path.insert(0, HERE)
os.environ.setdefault("A6_FAST", "1")
os.environ.setdefault("A6_SUPPORT_DIR", "/tmp/a6_dev_support")   # dev runs never touch the real save files
from n6.emu import Emu
from n6 import hle, cocoa, launch
A = sys.argv
e = Emu(); e.freadlog = [] if os.environ.get('A6_FREADLOG') else None; hle.install(e); rt = cocoa.install(e); e.bind_all()
if os.environ.get('A6_TRACEFN_EARLY'):
    from n6 import devtools
    for spec in os.environ['A6_TRACEFN_EARLY'].split(','):
        ad, nb, na = spec.split(':')
        def _fmt(e_, args, r):
            try:
                x = repr(e_.cstr(r)[:60]) if 0x10000000 <= r < 0x70000000 else ''
                if os.environ.get('A6_TRACE_DUMP') == 'ctx':
                    c_ = args[0]; av = e_.u32(c_ + 0xc); n_ = e_.u32(c_ + 0x10)
                    x += f' ctx n={n_} ' + ' | '.join(' '.join(f'{e_.u32(av + 12 * k + 4 * j):x}' for j in range(3)) for k in range(min(n_, 4)))
                    for k in range(min(n_, 4)):
                        for j in range(3):
                            pv = e_.u32(av + 12 * k + 4 * j)
                            if 0x10000000 <= pv < 0x70000000:
                                try:
                                    t_ = e_.cstr(pv)[:40]
                                    if len(t_) > 2 and all(32 <= ch < 127 for ch in t_): x += f' str{k}.{j}={t_!r}'
                                except Exception: pass
                if os.environ.get('A6_TRACE_DUMP'):
                    for a_ in args[1:]:
                        if 0x1000 < a_ < 0x70000000: x += ' [' + ' '.join(f'{e_.u32(a_ + 4 * i):x}' for i in range(8)) + ']'
                return x
            except Exception: return ''
        devtools.install_trace(e, int(ad, 16), int(nb), int(na), fmt=_fmt)
e.slow_log = [] if '-slow' in A else None; e.ftrace = [] if '-ftrace' in A else None; e.dbg = '-d' in A; e.top_names = '-top' in A; e.fps = '-fps' in A; e.hack_panel = '-hp' in A
if os.environ.get('A6_GLTRACE'): e.gltrace = set(os.environ['A6_GLTRACE'].split(','))
e.script = []
if '-c' in A:
    e.script = sorted([(float(a) + d, 'mouse', float(b), float(c), k) for a, b, c in (x.split(':') for x in A[A.index('-c')+1].split(',')) for d, k in ((0, 'down'), (0.2, 'up'))])
if '-ky' in A:
    CH = {126: '', 125: '', 123: '', 124: '', 36: '\r', 49: ' '}
    for x in A[A.index('-ky')+1].split(','):
        t_, k_, d_ = x.split(':'); e.script.append((float(t_), 'key', int(k_), CH.get(int(k_), ''), d_))
    e.script.sort(key=lambda r: r[0])
if '-fs' in A:   # -fs t1,t2: toggle the game's own fullscreen at those times
    e.script += [(float(t), 'fs') for t in A[A.index('-fs')+1].split(',')]; e.script.sort(key=lambda r: r[0])
if '-win' in A:  # -win "20:mini,30:unmini,..."
    for x in A[A.index('-win')+1].split(','):
        t_, a_ = x.split(':'); e.script.append((float(t_), a_))
    e.script.sort(key=lambda r: r[0])
if '-snap' in A:  # -snap "t:label,..."
    for x in A[A.index('-snap')+1].split(','):
        t_, a_ = x.split(':'); e.script.append((float(t_), 'snap', a_))
    e.script.sort(key=lambda r: r[0])
if '-act' in A:  # -act "t:nativefs,t:warp:x:y,t:cursor"
    for x in A[A.index('-act')+1].split(','):
        parts = x.split(':'); t_ = float(parts[0]); k_ = parts[1]
        e.script.append((t_, k_, *( [float(v) for v in parts[2:]] if k_ == 'warp' else [str(t_)] )))
    e.script.sort(key=lambda r: r[0])
if '-ws' in A:   # -ws t1,t2: capture the composited window to /tmp/win_<t>.png
    e.script += [(float(t), 'winshot', f'/tmp/win_{t}.png') for t in A[A.index('-ws')+1].split(',')]; e.script.sort(key=lambda r: r[0])
if '-p' in A:
    e.shots = os.path.join(HERE, "shots"); os.makedirs(e.shots, exist_ok=True); e.shots_every = float(A[A.index('-p')+1])
sec = e.sections["__mod_init_func"]
for f in struct.unpack("<%dI" % (sec.size // 4), e.rd(sec.virtual_address, sec.size)): e.call(f)
argv = e.alloc_static(16); e.wr(argv + 8, b"Asphalt 6\0"); e.w32(argv, argv + 8); e.w32(argv + 4, 0)
e.call(0xb46b0, 1, argv)
assert e.stopped_on == "NSApplicationMain", e.stopped_on
e.stopped_on = None
if os.environ.get('A6_CRASHINFO'):
    import threading
    def _dumper():
        import time
        while True:
            e._dump_hooks(); time.sleep(2)
    threading.Thread(target=_dumper, daemon=True).start()
if os.environ.get('A6_WATCH'):
    import threading, time as _tm
    exp = {}
    for addr, name, addend, sec in e.bindings:
        exp[addr] = e.u32(addr)
    def _watch():
        reported = set()
        while True:
            for addr, v in exp.items():
                if addr in reported: continue
                cur = e.u32(addr)
                if cur != v:
                    reported.add(addr)
                    nm = [b[1] for b in e.bindings if b[0] == addr][0]
                    print(f"[watch] binding slot {addr:#x} ({nm}) changed {v:#x} -> {cur:#x}", file=sys.stderr, flush=True)
            _tm.sleep(0.02)
    threading.Thread(target=_watch, daemon=True).start()
launch.launch(e, rt)
launch.run_loop(e, rt, seconds=float(A[A.index('-s')+1]) if '-s' in A else None)
if e.ftrace is not None: open('/tmp/ftrace.txt','w').write(' '.join(map(str, e.ftrace)))
if e.slow_log: print('[slow hooks]', e.slow_log[-30:], file=sys.stderr)
if getattr(e, 'snaps', None):
    import struct as _s
    for (la, da), (lb, db) in zip(e.snaps, e.snaps[1:]):
        ch = [(0x6f0000 + i, _s.unpack_from('<I', da, i)[0], _s.unpack_from('<I', db, i)[0]) for i in range(0, len(da) - 3, 4) if da[i:i+4] != db[i:i+4]]
        print(f'[snapdiff] {la} -> {lb}: {len(ch)} words changed', [(hex(a), hex(x), hex(y)) for a, x, y in ch[:40]], file=sys.stderr)
if e.malloc_stats:
    _lv = {}
    for a_, (k_, n_) in list(e.malloc_live.items()):
        z = _lv.setdefault(k_, [0, 0]); z[0] += 1; z[1] += n_
    for k, v in sorted(_lv.items(), key=lambda kv: -kv[1][1])[:10]: print('[live]', k, v, file=sys.stderr)
    for k, v in sorted(e.malloc_stats.items(), key=lambda kv: -kv[1][1])[:12]: print('[malloc]', k, v, file=sys.stderr)
print('[heap] guest arena used MB', (e.u32(0xE0080000) - 0x30000000) / 1048576, 'python arena', (e.u32(0xE0080000) and 0), file=sys.stderr)
print('[audio] chunks', getattr(e.audio, 'chunks', 0), 'non-silent', getattr(e.audio, 'loud', 0), 'buffered', e.audio.buffered, 'underruns', getattr(e.audio,'underruns',0), file=sys.stderr)
sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
