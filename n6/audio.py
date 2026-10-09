"""CoreAudio AudioUnit output shim: the guest registers a render callback; we pump it from the main loop
and feed the PCM to the host sound device."""
import os, struct, sys, threading, collections, time
try:
    import sounddevice as sd
except Exception:   # audio is optional
    sd = None


class Audio:
    def __init__(self, e):
        self.e = e
        self.cb = 0
        self.refcon = 0
        self.rate = 44100
        self.channels = 2
        self.bits = 16
        self.running = False
        self.buf = collections.deque()
        self.buffered = 0
        self.lock = threading.Lock()
        self.stream = None
        self.abl = e.alloc_static(32)
        self.flags = e.alloc_static(8)
        self.ts = e.alloc_static(64)
        self.pcm = e.alloc_static(64 * 1024)
        self._install()

    # host audio thread
    def _out(self, outdata, frames, time_info, status):
        need = frames * self.channels * (self.bits // 8)
        with self.lock:
            out = bytearray()
            while len(out) < need and self.buf:
                b = self.buf.popleft()
                if len(out) + len(b) > need:
                    take = need - len(out)
                    self.buf.appendleft(b[take:]); b = b[:take]
                out += b
            self.buffered -= len(out)
        if len(out) < need:
            self.underruns = getattr(self, 'underruns', 0) + 1
            if os.environ.get('A6_AUDIO_STATS'): print(f'[audio] underrun at {time.monotonic():.2f}', file=sys.stderr)
            out += bytes(need - len(out))
        outdata[:] = bytes(out)

    def _open(self):
        """Blocking-write output: PortAudio's own (C) buffer feeds the device, so the realtime callback never needs the
        python GIL (a callback blocked behind a busy python thread was what made the sound stutter while loading)."""
        if sd is None or self.stream is not None:
            return
        try:
            self.stream = sd.RawOutputStream(samplerate=self.rate, channels=self.channels, dtype="int16",
                                             blocksize=0, latency=float(os.environ.get("A6_AUDIO_LATENCY", "0.02")))
            self.stream.start()
            if os.environ.get('A6_AUDIO_STATS'): print('[audio] stream latency', self.stream.latency, 'rate', self.stream.samplerate, file=sys.stderr)
        except Exception as ex:
            print("[audio] cannot open output:", ex, file=sys.stderr)
            self.stream = None

    def _render_loop(self):
        """Own host thread: the game's software mixer executes (natively) and its PCM is written to the device."""
        e = self.e
        frames = 512
        nbytes = frames * self.channels * (self.bits // 8)
        abl = e.alloc_static(32); flags = e.alloc_static(8); ts = e.alloc_static(64); pcm = e.alloc_static(64 * 1024)
        import time as _t
        _dump = open(os.environ['A6_AUDIO_DUMP'], 'wb') if os.environ.get('A6_AUDIO_DUMP') else None
        silent = bytes(nbytes)
        while self.running:
            if os.environ.get("A6_NO_AUDIO") == "1":
                _t.sleep(0.01); continue
            e.wr(pcm, silent)
            e.w32(abl, 1); e.w32(abl + 4, self.channels); e.w32(abl + 8, nbytes); e.w32(abl + 12, pcm)
            e.w32(flags, 0)
            _t0 = _t.perf_counter()
            e.call(self.cb, self.refcon, flags, ts, 0, frames, abl)
            _d = _t.perf_counter() - _t0
            if _d > 0.03 and os.environ.get('A6_AUDIO_STATS'): print(f'[audio] slow render {_d*1000:.0f} ms', file=sys.stderr)
            data = e.rd(pcm, nbytes)
            self.chunks = getattr(self, 'chunks', 0) + 1
            if data.count(0) != len(data): self.loud = getattr(self, 'loud', 0) + 1
            if _dump: _dump.write(data)
            if self.stream is not None:
                try:
                    if self.stream.write(data): self.underruns = getattr(self, 'underruns', 0) + 1
                except Exception as ex:
                    # the device changed under us (another app / the video player switched its sample rate): reopen
                    print("[audio] write failed, reopening:", ex, file=sys.stderr)
                    try:
                        self.stream.abort(); self.stream.close()
                    except Exception: pass
                    self.stream = None; _t.sleep(0.2); self._open()
            else:
                _t.sleep(frames / self.rate)

    def _install(self):
        e, h = self.e, self.e.hle
        h["_AudioComponentFindNext"] = lambda e_: 1
        def new(e_):
            e_.w32(e_.arg(1), 1); return 0
        h["_AudioComponentInstanceNew"] = new
        def setprop(e_):
            prop, data, size = e_.arg(1), e_.arg(4), e_.arg(5)
            if prop == 0x17:      # kAudioUnitProperty_SetRenderCallback
                self.cb, self.refcon = e_.u32(data), e_.u32(data + 4)
            elif prop == 8 and size >= 40:   # StreamFormat
                self.rate = int(struct.unpack("<d", e_.rd(data, 8))[0])
                self.channels = e_.u32(data + 28); self.bits = e_.u32(data + 32)
            return 0
        h["_AudioUnitSetProperty"] = setprop
        def getprop(e_):
            prop, data, sz = e_.arg(1), e_.arg(4), e_.arg(5)
            if prop == 0xE and data:           # MaximumFramesPerSlice
                e_.w32(data, 4096)
            return 0
        h["_AudioUnitGetProperty"] = getprop
        h["_AudioUnitInitialize"] = lambda e_: 0
        def start(e_):
            if not self.running:
                self.running = True; self._open()
                threading.Thread(target=self._render_loop, daemon=True).start()
            return 0
        def stop(e_):
            self.running = False; return 0
        h["_AudioOutputUnitStart"] = start
        h["_AudioOutputUnitStop"] = stop
        h["_AudioUnitUninitialize"] = h["_AudioComponentInstanceDispose"] = lambda e_: 0
