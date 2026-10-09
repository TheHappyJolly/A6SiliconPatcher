"""Garage free-roam input: the game turns the keyboard into ONE direction id (1 up, 2 down, 4 left, 8 right) and its
consumer only understands exactly those four values, so two keys at once (e.g. Up+Left) give no movement at all.
We keep the real set of held arrow/WASD keys as a mask in guest memory (MASK_ADDR, see launch.remap_key) and replace the
consumer's four-way dispatch with code that builds the (x, y) vector from the mask (falling back to the game's own id)."""
import struct

MASK_ADDR = 0xE00A0300
_CODE = bytes.fromhex("8b1500030ae085d2740289d00f57c00f57c9a8017408f30f580d04266d00a8027408f30f580d181f6d00a8047408f30f580504266d00a8087408f30f5805181f6d00f30f1145e0f30f114de4e900000000")      # assembled routine; ends with a jmp rel32 placeholder
_ORIG_AT_HOOK = bytes.fromhex("83f8020f84")         # cmpl $2,%eax ; je ...
_ORIG_AT_BLOCK = bytes.fromhex("0f57c9f30f114de0")  # xorps xmm1,xmm1 ; movss xmm1,-0x20(%ebp)
HOOK, BLOCK, BACK = 0x202464, 0x202692, 0x202490


def apply(e):
    if bytes(e.rd(HOOK, 5)) != _ORIG_AT_HOOK or bytes(e.rd(BLOCK, 8)) != _ORIG_AT_BLOCK:
        return False                                  # not the expected binary: do nothing
    code = bytearray(_CODE)
    end = BLOCK + len(code)
    code[-4:] = struct.pack("<i", BACK - end)         # final jmp back into the original code
    e.wr(BLOCK, bytes(code))
    e.wr(HOOK, b"\xE9" + struct.pack("<i", BLOCK - (HOOK + 5)) + b"\x90\x90\x90\x90")   # replaces cmpl+je (9 bytes)
    return True
