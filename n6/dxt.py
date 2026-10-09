"""DXT1/3/5 -> RGBA8 decoder (numpy)."""
import numpy as np


def _c565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], axis=-1).astype(np.uint16)


def decode(fmt, w, h, data):
    bw, bh = max((w + 3) // 4, 1), max((h + 3) // 4, 1)
    bsz = 8 if fmt in (0x83F0, 0x83F1) else 16
    n = bw * bh
    raw = np.frombuffer(data, dtype=np.uint8, count=n * bsz).reshape(n, bsz)
    cb = raw[:, bsz - 8:]
    c0 = cb[:, 0].astype(np.uint16) | (cb[:, 1].astype(np.uint16) << 8)
    c1 = cb[:, 2].astype(np.uint16) | (cb[:, 3].astype(np.uint16) << 8)
    idx = cb[:, 4:8].astype(np.uint32)
    idx = idx[:, 0] | (idx[:, 1] << 8) | (idx[:, 2] << 16) | (idx[:, 3] << 24)
    p0, p1 = _c565(c0), _c565(c1)
    pal = np.zeros((n, 4, 4), dtype=np.uint16)            # 4 colors RGBA
    pal[:, 0, :3] = p0; pal[:, 1, :3] = p1
    four = (c0 > c1) | (fmt not in (0x83F0, 0x83F1))
    pal[:, 2, :3] = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    pal[:, 3, :3] = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal[:, :, 3] = 255
    if fmt in (0x83F0, 0x83F1):
        pal[:, 3, 3] = np.where(four, 255, 0)
    shifts = (np.arange(16, dtype=np.uint32) * 2)
    sel = ((idx[:, None] >> shifts[None, :]) & 3).astype(np.int64)      # n x 16
    px = np.take_along_axis(pal, sel[:, :, None].repeat(4, axis=2), axis=1).astype(np.uint8)  # n x 16 x 4
    if fmt == 0x83F2:                                                   # explicit 4-bit alpha
        a = raw[:, :8].astype(np.uint16)
        a = a[:, 0::2] | (a[:, 1::2] << 8)                              # 4 rows of 16 bits
        al = np.zeros((n, 16), dtype=np.uint8)
        for i in range(16):
            al[:, i] = (((a[:, i // 4] >> ((i % 4) * 4)) & 15) * 17).astype(np.uint8)
        px[:, :, 3] = al
    elif fmt == 0x83F3:                                                 # interpolated alpha
        a0 = raw[:, 0].astype(np.uint16); a1 = raw[:, 1].astype(np.uint16)
        bits = np.zeros(n, dtype=np.uint64)
        for i in range(6):
            bits |= raw[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
        tab = np.zeros((n, 8), dtype=np.uint16)
        tab[:, 0], tab[:, 1] = a0, a1
        g = (a0 > a1)[:, None]
        for k in range(1, 7):
            tab[:, k + 1] = np.where(g[:, 0], ((7 - k) * a0 + k * a1) // 7, 0)
        for k in range(1, 5):
            tab[:, k + 1] = np.where(g[:, 0], tab[:, k + 1], ((5 - k) * a0 + k * a1) // 5)
        tab[:, 6] = np.where(g[:, 0], tab[:, 6], 0)
        tab[:, 7] = np.where(g[:, 0], tab[:, 7], 255)
        ash = np.arange(16, dtype=np.uint64) * np.uint64(3)
        ai = ((bits[:, None] >> ash[None, :]) & np.uint64(7)).astype(np.int64)
        px[:, :, 3] = np.take_along_axis(tab, ai, axis=1).astype(np.uint8)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w]).tobytes()
