"""Gameloft Live / More Games (IGP) are in-game browsers for services that no longer exist; opening them crashed the game
or turned its window into the desktop background. The game's entry points are replaced by `xor eax,eax ; ret`, so the
buttons simply do nothing."""
ENTRY_POINTS = (0x219a50, 0x2112a0, 0x21a8e0, 0x212680, 0x212820, 0x21aa70)
# startIgpHtmlUsingMMButton: / startGLLiveHtmlUsingMMButton: / startIgpHtmlWithLanguage:: / startGLLiveHtmlWithLanguage:: / startIgpHtml (x2)
# isIGPTerminate / isGLLiveTerminate: the game polls these to learn that the browser was closed; a dummy browser is "closed"
TERMINATE_POINTS = (0x219730, 0x210c40)


def apply(e):
    n = 0
    for a in TERMINATE_POINTS:
        if bytes(e.rd(a, 3)) == b"\x55\x89\xe5":
            e.wr(a, b"\xb8\x01\x00\x00\x00\xc3"); n += 1
    for a in ENTRY_POINTS:
        if bytes(e.rd(a, 3)) == b"\x55\x89\xe5":          # push ebp ; mov ebp,esp : the expected method prologue
            e.wr(a, b"\x31\xc0\xc3"); n += 1
    return n
