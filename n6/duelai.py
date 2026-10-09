"""Duel AI easing ("Original Duel Difficulty" = Off). With the setting On nothing here changes the game.

The opponent of a Duel (race type 3) is driven by AI::update (0x1c4050). Two things make it hard to pull away from:
 * a rubber band: a target speed = car stat * factor from float tables (RS_DUEL 0x6d4220, RS_START_DUEL 0x6d40a0, 4 floats per rank: the first
   two while the opponent is behind the player = 1.30 / 1.15, i.e. full throttle + nitro to catch up, the last two while it is ahead);
 * a fast car: the opponent's top speed stat (int at car + 0xbf8, also the cap of its physics) is that of the event's car.
With the setting Off the catch-up factors are limited to EASY_BEHIND and the opponent's speed stat is scaled by STAT_K and never taken
above the player's own car (min(opponent, player) * STAT_K)."""
import struct, sys, os

AI_UPDATE = 0x1c4050
RACE_PTR = 0x70570c                  # **ptr + 0x184 = race type (3 = Duel)
TABLES = (0x6d4220, 0x6d40a0)
EASY_BEHIND = float(os.environ.get("A6_DUEL_BEHIND", 1.04))
STAT_K = float(os.environ.get("A6_DUEL_STAT", 0.8))
PLAYER_CAR = 0xc9ee0                 # Car *getPlayerCar(int)
STAT = 0xbf8


def install(e):
    from . import a6settings, devtools
    if bytes(e.rd(AI_UPDATE, 5)) != b"\x55\x89\xe5\x57\x56":
        print("[patch] duel AI: unexpected binary", file=sys.stderr); return False
    orig = {a: list(struct.unpack('<24f', bytes(e.rd(a, 96)))) for a in TABLES}
    stat0 = {}                       # opponent car -> (its original speed stat, the value written)
    cur = [None]; dbg = os.environ.get("A6_DUELAI_LOG"); last = [0.0, 0]

    def apply(e_, easy):
        if cur[0] == easy: return
        cur[0] = easy
        for a, vals in orig.items():
            for i, v in enumerate(vals):
                if easy and i % 4 < 2: v = min(v, EASY_BEHIND)
                e_.wr(a + 4 * i, struct.pack("<f", v))
        print(f"[duelai] easy={easy}", file=sys.stderr)

    def on_update(e_, args, call_orig):
        try:
            race = e_.u32(e_.u32(e_.u32(RACE_PTR)) + 0x184) if e_.u32(RACE_PTR) else -1
            easy = race == 3 and (os.environ.get("A6_DUEL_EASY") == "1" or not a6settings.get("duel_original"))
            apply(e_, easy)
            ai = args[0]; car = e_.u32(ai); pl = e_.call(PLAYER_CAR, 0)
            if easy and car and pl and car != pl:
                v = e_.u32(car + STAT); rec = stat0.get(car)
                if rec is None or v != rec[1]:           # a new car (the address may be a reused one): its own stat is the original
                    new = int(min(v, e_.u32(pl + STAT)) * STAT_K); stat0[car] = rec = (v, new)
                e_.w32(car + STAT, rec[1])
            if dbg:
                import time
                if time.monotonic() - last[0] > 1.0:
                    last[0] = time.monotonic()
                    gap = e_.call_fp(0x197f40, car, pl, 1, 0); sa = e_.call_fp(0xd3940, car + 0x54, 0); sp = e_.call_fp(0xd3940, pl + 0x54, 0)
                    print(f"[duelai] ai={ai:#x} beh={e_.u32(ai + 0x40)} stat={e_.u32(car + STAT)} rank={e_.u32(car + 0x2b4c)} gap={gap:.0f} aispd={sa:.1f} plspd={sp:.1f} lap={e_.u32(car + 0xf0c)}/{e_.u32(pl + 0xf0c)}", file=sys.stderr)
        except Exception as ex:
            print("[duelai] error", ex, file=sys.stderr)
        return call_orig(*args)
    devtools.install_detour(e, AI_UPDATE, 5, 2, on_update, "duelai_update")
    return True
