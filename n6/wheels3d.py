"""3D wheels in races. Every car model contains both wheel sets: the low one (Rim_xx / Wheel_xx nodes, flat sprite-like wheels) shown in
races and the high one (RimHiRes_xx / WheelHiRes_xx) shown in the garage. The game keeps the node pointers of a car in a table
(car + 0x2610 + 4 * index) and drives visibility and animation of the "Rim_xx / Wheel_xx" *slots*. Swapping the two sets of slots
makes the game's own race logic show and animate the high wheels instead."""
import ctypes, struct

CAR_VTABLE, CAR_VTABLE2 = 0x707e08, 0x707e98          # at +0 and +0x54 of a car object
TABLE = 0x2610
RIM, WHEEL, RIM_HI, WHEEL_HI = 10, 14, 18, 22         # first table index of each set of four (FL, FR, BL, BR)


def find_cars(e):
    hp = e.u32(0xE0080000)
    blob = ctypes.string_at(0x30000000, hp - 0x30000000)
    pat = struct.pack("<I", CAR_VTABLE); out = []; i = 0
    while True:
        i = blob.find(pat, i)
        if i < 0: break
        a = 0x30000000 + i; i += 1
        if a % 4 == 0 and e.u32(a + 0x54) == CAR_VTABLE2: out.append(a)
    return out


def swap_car(e, car):
    n = 0
    for lo, hi in ((RIM, RIM_HI), (WHEEL, WHEEL_HI)):
        for k in range(4):
            a, b = car + TABLE + 4 * (lo + k), car + TABLE + 4 * (hi + k)
            va, vb = e.u32(a), e.u32(b)
            if va and vb and va != vb:
                e.w32(a, vb); e.w32(b, va); n += 1
    return n


def swap_all(e):
    cars = find_cars(e)
    for c in cars: swap_car(e, c)
    return len(cars)


SETUP_NODES = 0xfd1d0            # void Car::findNodes(): fills the node table by name (called once per car)
UPDATE_CAR = 0xf3150             # void Car::update(): per-frame node visibility and wheel animation
GAME = 0xbaab0                   # Game *Game::instance(); the game state is the int at +0x64
RACE_STATES = range(25, 34)      # race loading ... race (the garage is state 5, the results screen "You Win!" is 34)


def install(e):
    import os, sys
    from . import a6settings, devtools
    for fn in (SETUP_NODES, UPDATE_CAR):
        if bytes(e.rd(fn, 5)) != b"\x55\x89\xe5\x57\x56":
            print("[patch] 3D wheels: unexpected binary", file=sys.stderr); return False
    dbg = os.environ.get("A6_WHEELS_DEBUG")
    swapped = {}                 # car -> are the wheel slots currently swapped
    game = [0]

    def state(e_):
        if not game[0]: game[0] = e_.call(GAME)
        return e_.u32(game[0] + 0x64)

    def on_create(e_, args, call_orig):
        r = call_orig(*args)
        swapped[args[0]] = False                     # a fresh table (the address of an earlier car may be reused)
        if dbg: print(f"[wheels3d] car {args[0]:#x} created, game state {state(e_)}", file=sys.stderr)
        return r

    trace = os.environ.get("A6_WHEELS_STATES"); last = [None, 0.0]

    def on_update(e_, args, call_orig):
        car = args[0]
        try:
            st = state(e_)
            if trace:                                  # dev: log game state changes, dump memory 6 s into state 34
                import time
                if st != last[0]:
                    last[0] = st; print(f"[wheels3d] game state -> {st}", file=sys.stderr)
                    if st == 34 and os.environ.get("A6_DUMP_AT_34"): last[1] = time.monotonic()
                if last[1] and time.monotonic() - last[1] > 6: last[1] = 0; devtools.memdump(e_, "state 34")
            want = a6settings.get("wheels3d") and st in RACE_STATES
            if car in swapped and swapped[car] != want:
                swap_car(e_, car); swapped[car] = want
                if dbg: print(f"[wheels3d] car {car:#x} wheels -> {'3D' if want else 'original'}", file=sys.stderr)
        except Exception as ex:
            print("[wheels3d] failed:", ex, file=sys.stderr)
        return call_orig(*args)
    devtools.install_detour(e, SETUP_NODES, 5, 1, on_create)
    devtools.install_detour(e, UPDATE_CAR, 5, 1, on_update, name="__wheels_update")
    return True
