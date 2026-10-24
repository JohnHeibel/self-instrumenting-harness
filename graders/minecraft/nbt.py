"""Minimal NBT reader (gzip'd .dat files: level.dat, playerdata/<uuid>.dat). Returns plain dicts, lists, ints."""
import gzip
import struct

_FIXED = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}


def load(path):
    data = open(path, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    pos = 0

    def take(fmt):
        nonlocal pos
        n = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, data, pos)[0]
        pos += n
        return v

    def string():
        nonlocal pos
        n = take(">H")
        s = data[pos:pos + n].decode("utf-8", "replace")
        pos += n
        return s

    def payload(t):
        nonlocal pos
        if t in _FIXED:
            return take(_FIXED[t])
        if t in (7, 11, 12):
            n = take(">i")
            size = {7: 1, 11: 4, 12: 8}[t]
            pos += n * size
            return None  # arrays are not needed
        if t == 8:
            return string()
        if t == 9:
            et, n = take(">b"), take(">i")
            return [payload(et) for _ in range(n)]
        if t == 10:
            out = {}
            while True:
                et = take(">b")
                if et == 0:
                    return out
                name = string()   # the name comes first in the stream: not `out[string()] = payload(et)`, which evaluates the value first
                out[name] = payload(et)
        raise ValueError(f"bad NBT tag {t}")

    t = take(">b")
    string()
    return payload(t)
