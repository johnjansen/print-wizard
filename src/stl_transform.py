"""STL orientation transforms (pure Python, no deps).

Applied before slicing so the wizard can rotate / auto-orient a model without
a 3D viewer. Handles binary STL (the common case) and ASCII STL.

- rotate(bytes, x, y, z): rotate by 90*turns degrees around each axis.
- lay_flat(bytes): try the 16 axis-aligned orientations from combinations of
  X/Y 90-degree turns and pick the one with the smallest Z height -- the model
  resting on its largest footprint. A pragmatic auto-orient; full
  support-minimizing orientation (overhang analysis) is a later lift.

After any transform the model is normalized: min Z -> 0 (on the bed) and XY
centered on the bed (Ender-3, 235mm) so a rotated model never lands off-plate.
"""
from __future__ import annotations
import math
import struct

BED = 235.0


def _parse(b: bytes):
    if b[:5].lower().startswith(b"solid") and b"facet" in b[:512].lower():
        return _parse_ascii(b)
    return _parse_binary(b)


def _parse_binary(b):
    count = struct.unpack_from("<I", b, 80)[0]
    tris = []
    off = 84
    for _ in range(count):
        va = struct.unpack_from("<3f", b, off + 12)
        vb = struct.unpack_from("<3f", b, off + 24)
        vc = struct.unpack_from("<3f", b, off + 36)
        tris.append((va, vb, vc))
        off += 50
    return tris


def _parse_ascii(b):
    verts, tris = [], []
    for line in b.decode("utf-8", "replace").splitlines():
        p = line.split()
        if len(p) >= 4 and p[0] == "vertex":
            verts.append(tuple(map(float, p[1:4])))
            if len(verts) == 3:
                tris.append(tuple(verts))
                verts = []
    return tris


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / L, ny / L, nz / L


def _write_binary(tris):
    out = bytearray(b"\0" * 80)
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        out += struct.pack("<3f", *_normal(a, b, c))
        for v in (a, b, c):
            out += struct.pack("<3f", *v)
        out += struct.pack("<H", 0)
    return bytes(out)


def _rx(v, t):
    x, y, z = v; t %= 4
    return (x, y, z) if t == 0 else (x, -z, y) if t == 1 else (x, -y, -z) if t == 2 else (x, z, -y)

def _ry(v, t):
    x, y, z = v; t %= 4
    return (x, y, z) if t == 0 else (z, y, -x) if t == 1 else (-x, y, -z) if t == 2 else (-z, y, x)

def _rz(v, t):
    x, y, z = v; t %= 4
    return (x, y, z) if t == 0 else (-y, x, z) if t == 1 else (-x, -y, z) if t == 2 else (y, -x, z)


def _apply(tris, x, y, z):
    return [tuple(_rz(_ry(_rx(v, x), y), z) for v in tri) for tri in tris]


def _normalize(tris):
    if not tris:
        return tris
    xs = [v[0] for tri in tris for v in tri]
    ys = [v[1] for tri in tris for v in tri]
    zs = [v[2] for tri in tris for v in tri]
    minx, miny, minz = min(xs), min(ys), min(zs)
    maxx, maxy = max(xs), max(ys)
    dx = (BED - (maxx - minx)) / 2 - minx
    dy = (BED - (maxy - miny)) / 2 - miny
    dz = -minz
    return [tuple((v[0] + dx, v[1] + dy, v[2] + dz) for v in tri) for tri in tris]


def rotate(b: bytes, x: int = 0, y: int = 0, z: int = 0) -> bytes:
    if x % 4 == 0 and y % 4 == 0 and z % 4 == 0:
        return b  # identity -> leave uploaded coords untouched
    tris = _apply(_parse(b), x, y, z)
    return _write_binary(_normalize(tris))


def lay_flat(b: bytes) -> bytes:
    tris = _parse(b)
    best = None
    for x in range(4):
        for y in range(4):
            t = _apply(tris, x, y, 0)
            zs = [v[2] for tri in t for v in tri]
            h = max(zs) - min(zs)
            if best is None or h < best[0]:
                best = (h, x, y)
    return _write_binary(_normalize(_apply(tris, best[1], best[2], 0)))


if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], "rb").read()
    out = lay_flat(data) if (len(sys.argv) > 2 and sys.argv[2] == "flat") else rotate(data, 1, 0, 0)
    open("/tmp/_stl_out.stl", "wb").write(out)
    print("wrote /tmp/_stl_out.stl", len(out), "bytes")
