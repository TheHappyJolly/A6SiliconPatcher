"""Asphalt 6 on Apple Silicon, native mode: the original i386 code runs under Rosetta 2's 32-bit support."""
import os, sys, struct, faulthandler
faulthandler.enable()          # python-level fatal errors are written to the log (stderr)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "x64", "site"))
sys.path.insert(0, HERE)
os.environ.setdefault("A6_FAST", "1")
from n6.emu import Emu
from n6 import hle, cocoa, launch

def session_header():
    import platform, subprocess, time
    def sh(c):
        try: return subprocess.run(c, capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception: return "?"
    print("=" * 70, file=sys.stderr)
    print("[session]", time.strftime("%Y-%m-%d %H:%M:%S"), "macOS", platform.mac_ver()[0], sh(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]),
          "mem", int(sh(["/usr/sbin/sysctl", "-n", "hw.memsize"]) or 0) // 2**30, "GB", "build", (open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "BUILD")).read().strip() if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "BUILD")) else "dev"), file=sys.stderr)
    print("[session] env:", {k: v for k, v in os.environ.items() if k.startswith("A6_")}, file=sys.stderr)
    sys.stderr.flush()


def main():
    session_header()
    e = Emu()
    hle.install(e)
    rt = cocoa.install(e)
    e.bind_all()
    e.dbg = os.environ.get("A6_DEBUG") == "1"
    e.hack_panel = os.environ.get("A6_PANEL_FIX") == "1"
    if os.environ.get("A6_SHOTS"):
        os.makedirs(os.environ["A6_SHOTS"], exist_ok=True)
        e.shots, e.shots_every = os.environ["A6_SHOTS"], float(os.environ.get("A6_SHOTS_EVERY", "5"))
    sec = e.sections["__mod_init_func"]
    for f in struct.unpack("<%dI" % (sec.size // 4), e.rd(sec.virtual_address, sec.size)):
        e.call(f)
    argv = e.alloc_static(16)
    e.wr(argv + 8, b"Asphalt 6\0")
    e.w32(argv, argv + 8); e.w32(argv + 4, 0)
    e.call(0xb46b0, 1, argv)               # main() -> NSApplicationMain (intercepted: aborts back to us)
    if e.stopped_on != "NSApplicationMain":
        raise SystemExit("unexpected stop: %s" % e.stopped_on)
    e.stopped_on = None
    launch.launch(e, rt)
    launch.run_loop(e, rt, seconds=float(os.environ["A6_SECONDS"]) if os.environ.get("A6_SECONDS") else None)

if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(0)
