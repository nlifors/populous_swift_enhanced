"""
Populous HD - procedural asset builder.

Builds every 3D model for the HD remaster from code (no external meshes or
textures), saves an editable .blend (one collection per model), exports each
model as a .pmesh for the game, and renders preview images.

Run from the repository root:
    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python Assets/blender/build_assets.py

Optional arguments after `--`:
    --only name,name   build/export/render only these models (fast iteration)
    --no-render        skip all preview renders
    --no-blend         do not save the .blend
    --no-extras        skip sheet / diorama / castle test renders

.pmesh format (little-endian): "PMSH", uint32 version=1, uint32 N, then
N x 10 float32 (px py pz nx ny nz r g b surface). Non-indexed triangles,
flat normals, sRGB colours, surface 3 = ordinary, 4 = team colour, 5 = glow.
Faces whose material is named "Team" are team-coloured, "Glow" glow unlit.
"""

import bpy
import bmesh
import colorsys
import math
import os
import random
import struct
import sys
import zlib
from math import cos, sin, pi, radians
from mathutils import Vector, Matrix

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

# --------------------------------------------------------------------------
# Paths and arguments
# --------------------------------------------------------------------------
try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = os.path.join(os.getcwd(), "Assets", "blender")
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODEL_DIR = os.path.join(ROOT, "Sources", "PopulousHD", "Resources", "Models")
PREVIEW_DIR = os.path.join(ROOT, "Assets", "previews")
BLEND_PATH = os.path.join(HERE, "populous_assets.blend")

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg(flag):
    return flag in ARGV


def _argval(flag):
    if flag in ARGV:
        i = ARGV.index(flag)
        if i + 1 < len(ARGV):
            return ARGV[i + 1]
    return None


ONLY = set(_argval("--only").split(",")) if _argval("--only") else None
DO_RENDER = not _arg("--no-render")
DO_BLEND = not _arg("--no-blend")
DO_EXTRAS = not _arg("--no-extras") and ONLY is None

# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------
PAINT, TEAM, GLOW = 0, 1, 2
JIT = 0.06  # default per-face brightness jitter (+-6%)


def hexc(h):
    if not isinstance(h, str):
        return tuple(h)
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def s2l(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def l2s(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def mix(a, b, t):
    a, b = hexc(a), hexc(b)
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# Palette (sRGB). Warm "painted miniature" colours after the style reference.
C = {k: hexc(v) for k, v in dict(
    grass="#8cbc3c", grass_l="#a2cc48", grass_d="#6f9e2e", meadow="#9fc65a",
    sand="#e6d29a", sand_d="#cbb47a", soil="#6e4a2e", soil_l="#87603c",
    thatch="#c59139", thatch_l="#d8ad55", thatch_d="#a2712b", thatch_dd="#8a5e24",
    stone="#b2a68f", stone_l="#cbbfa5", stone_d="#978b78", mortar="#857a68",
    stone_w="#c0ad8c",
    slate="#59626f", slate_l="#6c7684", slate_d="#464f5c",
    wood_d="#563620", wood="#7b5131", wood_l="#a3743f", plank="#916338",
    endgrain="#c99c62",
    plaster="#ece0c2", plaster_d="#d9c9a4",
    mud="#b08a5c", mud_d="#94724a", mud_l="#c29d6c",
    hide="#d4b07c", hide_d="#b8925e", hide_l="#e3c798",
    door="#6a4227", glass="#2c3542", dark="#2a2019", iron="#4b4b52",
    tile="#b85a3a", tile_d="#984630", tile_l="#c86c48", terracotta="#b4603e",
    conifer="#2f5f2f", conifer_l="#46783a", conifer_d="#244a26",
    leaf="#5e9a30", leaf_l="#7fb63e", leaf_d="#467a26",
    trunk="#6b4a2e", trunk_d="#54391f", bark_grey="#958b80", bark_dark="#5c4532",
    rock="#978d80", rock_l="#ada395", rock_d="#7b7268", moss="#6f9134", moss_d="#5a7a2a",
    skin="#eab48a", hair="#5b3a1e", hair_l="#8a5a2a", trousers="#6c4a2c",
    boots="#3d2a1b", belt="#4a3220",
    metal="#b7bcc5", metal_d="#868d97", gold="#e6b53a", gold_d="#c08f1f",
    gold_l="#f4d36a",
    bone="#ece2c8", bone_d="#c9bb98", horn="#b8a27a", horn_d="#5a4630",
    swamp="#4f5b2e", swamp_d="#3c4724", swamp_l="#66733a", scum="#7f8f3c",
    reed="#7f9a3a", reed_d="#9a8a4a", lily="#4f9436", cattail="#5e3e22",
    fruit="#d8382a", cactus="#5b8c3c", cactus_l="#73a64a", cactus_d="#46733a",
    palm="#4f9230", palm_l="#76ad3a", palm_trunk="#8a6a46", palm_trunk_d="#6e5236",
    coconut="#5e4227", dry="#a08a58", dry_d="#7c6844", twig="#857560",
    ember="#ff8a1e", flame="#ffc23a", wheat="#dcb24a", wheat_l="#ebc962",
    cabbage="#74ad3e", carrot="#e47a26", water="#2f6fb4", water_d="#1f4f94",
    water_l="#4a9ccc", foam="#f2f6f4", char="#2e2620", ash="#6a645e",
    team_blue="#3164d8", team_red="#cc3a2c", white="#f0ece0",
    gem="#3a7ae0", ruby="#c0283a",
).items()}


# --------------------------------------------------------------------------
# Geometry builder
# --------------------------------------------------------------------------
def newell(P):
    n = Vector((0.0, 0.0, 0.0))
    m = len(P)
    for i in range(m):
        a, c = P[i], P[(i + 1) % m]
        n.x += (a.y - c.y) * (a.z + c.z)
        n.y += (a.z - c.z) * (a.x + c.x)
        n.z += (a.x - c.x) * (a.y + c.y)
    return n


def T(x, y=0.0, z=0.0):
    if isinstance(x, (Vector, tuple, list)):
        return Matrix.Translation(Vector(x))
    return Matrix.Translation(Vector((x, y, z)))


def Rz(a):
    return Matrix.Rotation(a, 4, "Z")


def Ry(a):
    return Matrix.Rotation(a, 4, "Y")


def Rx(a):
    return Matrix.Rotation(a, 4, "X")


def S(x, y=None, z=None):
    if y is None:
        y = z = x
    return Matrix.Diagonal((x, y, z, 1.0))


class B:
    """Collects coloured polygons in model space."""

    def __init__(self, seed):
        self.faces = []  # (world points, srgb colour, material)
        self.rng = random.Random(seed)
        self.stack = [Matrix.Identity(4)]
        self.dets = [1.0]

    @property
    def M(self):
        return self.stack[-1]

    def push(self, m):
        self.stack.append(self.M @ m)
        self.dets.append(self.stack[-1].determinant())

    def pop(self):
        self.stack.pop()
        self.dets.pop()

    def jitter(self, col, amt):
        col = hexc(col)
        if amt is None:
            amt = JIT
        r = self.rng
        h, s, v = colorsys.rgb_to_hsv(*col)
        v *= 1.0 + r.uniform(-amt, amt)
        h = (h + r.uniform(-amt, amt) * 0.12) % 1.0
        s = max(0.0, min(1.0, s * (1.0 + r.uniform(-amt, amt) * 0.6)))
        return tuple(max(0.0, min(1.0, c)) for c in colorsys.hsv_to_rgb(h, s, v))

    def face(self, pts, col, mat=PAINT, jit=None, out=None):
        P = [Vector(p) for p in pts]
        if out is not None:
            n = newell(P)
            c = sum(P, Vector()) / len(P)
            if n.dot(c - Vector(out)) < 0:
                P.reverse()
        M = self.M
        W = [(M @ p) for p in P]
        if self.dets[-1] < 0:
            W.reverse()
        n = newell(W)
        if n.length < 1e-10:
            return
        n.normalize()
        # Faces lying on the ground plane facing down can never be seen.
        if n.z < -0.98 and max(p.z for p in W) < 2e-3:
            return
        if mat == TEAM:
            colr = (1.0, 1.0, 1.0)
        else:
            colr = col(n, W) if callable(col) else col
        self.faces.append((W, self.jitter(colr, jit), mat))

    def tri_count(self):
        return sum(len(f[0]) - 2 for f in self.faces)


class xf:
    def __init__(self, b, m):
        self.b, self.m = b, m

    def __enter__(self):
        self.b.push(self.m)
        return self.b

    def __exit__(self, *a):
        self.b.pop()


_ICO = {}


def ico_points(sub):
    if sub not in _ICO:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=1.0)
        _ICO[sub] = [v.co.copy() for v in bm.verts]
        bm.free()
    return _ICO[sub]


def hull_polys(points):
    bm = bmesh.new()
    for p in points:
        bm.verts.new(Vector(p))
    try:
        bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
    except Exception:
        bm.free()
        return []
    if not bm.faces:
        bm.free()
        return []
    bmesh.ops.dissolve_limit(bm, angle_limit=radians(0.6),
                             verts=bm.verts[:], edges=bm.edges[:])
    used = {v for f in bm.faces for v in f.verts}
    cen = sum((v.co for v in used), Vector()) / max(1, len(used))
    polys = []
    for f in bm.faces:
        P = [v.co.copy() for v in f.verts]
        n = newell(P)
        c = sum(P, Vector()) / len(P)
        if n.dot(c - cen) < 0:
            P.reverse()
        polys.append(P)
    bm.free()
    return polys


def solid(b, pts, col, mat=PAINT, jit=None, cull=None):
    """Convex solid = convex hull of points, with flat coloured faces."""
    for P in hull_polys(pts):
        if cull is not None:
            n = newell(P)
            if n.length > 0 and cull(n.normalized(), P):
                continue
        b.face(P, col, mat, jit)


def box(b, x0, y0, z0, x1, y1, z1, col, bev=0.0, mat=PAINT, jit=None,
        cull=None, bevbot=None):
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    if z0 > z1:
        z0, z1 = z1, z0
    if bevbot is None:
        bevbot = z0 > 1e-4
    hx, hy, hz = (x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2
    cx, cy, cz = x0 + hx, y0 + hy, z0 + hz
    e = min(bev, 0.45 * min(hx, hy, hz))
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                if e <= 0:
                    pts.append((cx + sx * hx, cy + sy * hy, cz + sz * hz))
                elif sz < 0 and not bevbot:
                    pts += [(cx + sx * hx, cy + sy * (hy - e), z0),
                            (cx + sx * (hx - e), cy + sy * hy, z0)]
                else:
                    pts += [(cx + sx * hx, cy + sy * (hy - e), cz + sz * (hz - e)),
                            (cx + sx * (hx - e), cy + sy * hy, cz + sz * (hz - e)),
                            (cx + sx * (hx - e), cy + sy * (hy - e), cz + sz * hz)]
    solid(b, pts, col, mat, jit, cull)


def frame_to(p0, p1):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    q = d.normalized().to_track_quat("Z", "Y").to_matrix().to_4x4()
    return Matrix.Translation(p0) @ q, d.length


def cyl(b, p0, p1, r0, r1, n, col, rot=0.0, mat=PAINT, jit=None, sq=(1.0, 1.0),
        cull=None):
    M, L = frame_to(p0, p1)
    pts = []
    for k in range(n):
        a = rot + 2 * pi * k / n
        pts.append(M @ Vector((r0 * cos(a) * sq[0], r0 * sin(a) * sq[1], 0)))
        if r1 > 0:
            pts.append(M @ Vector((r1 * cos(a) * sq[0], r1 * sin(a) * sq[1], L)))
    if r1 <= 0:
        pts.append(Vector(p1))
    solid(b, pts, col, mat, jit, cull)


def tube(b, path, radii, n, col, rot=0.0, mat=PAINT, jit=None):
    for i in range(len(path) - 1):
        cyl(b, path[i], path[i + 1], radii[i], radii[i + 1], n, col, rot, mat, jit)


def blob(b, c, r, col, sub=1, noise=0.12, mat=PAINT, jit=None, zmin=None):
    rng = b.rng
    if not isinstance(r, (tuple, list)):
        r = (r, r, r)
    pts = []
    for v in ico_points(sub):
        k = 1.0 + rng.uniform(-noise, noise)
        p = Vector((c[0] + v.x * r[0] * k, c[1] + v.y * r[1] * k, c[2] + v.z * r[2] * k))
        if zmin is not None:
            p.z = max(p.z, zmin)
        pts.append(p)
    solid(b, pts, col, mat, jit)


def wall_m(c, n, width, z=0.0):
    """Frame for a wall face: local X along the wall (left->right seen from
    outside), local -Y = outward normal, local Z = up. Origin at the wall's
    bottom-left outside corner."""
    n = Vector(n).normalized()
    u = Vector((0, 0, 1)).cross(n)
    o = Vector(c) - u * (width / 2) + Vector((0, 0, z))
    M = Matrix.Identity(4)
    for i in range(3):
        M[i][0] = u[i]
        M[i][1] = -n[i]
        M[i][2] = (0, 0, 1)[i]
        M[i][3] = o[i]
    return M


def pick(rng, items):
    """items: list of (colour, weight)."""
    tot = sum(w for _, w in items)
    x = rng.uniform(0, tot)
    for c, w in items:
        x -= w
        if x <= 0:
            return c
    return items[-1][0]


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def STONE_CULL(n, P):
    return n.y > 0.9 or n.z < -0.9


def stones(b, W, H, cols, t0=0.0, sw=0.1, sh=0.068, holes=(), prot=(0.005, 0.013),
           gap=0.007, jit=0.1, s_lo=0.0):
    """Masonry: individual raised stones on a wall face (in a wall frame)."""
    rng = b.rng
    t = t0
    row = 0
    while t < H - 0.012:
        h = sh * rng.uniform(0.8, 1.2)
        if H - (t + h) < sh * 0.45:
            h = H - t
        s = s_lo - (rng.uniform(0.3, 0.7) * sw if row % 2 else rng.uniform(0, 0.2) * sw)
        while s < W - 0.005:
            w = sw * rng.uniform(0.65, 1.35)
            a, c = max(s, s_lo), min(s + w, W)
            if W - c < 0.025:
                c = W
            rect = (a, t, c, t + h)
            if c - a > 0.02 and not any(_overlap(rect, hl) for hl in holes):
                d = rng.uniform(*prot)
                g = gap / 2
                e = min(0.0045, (c - a) * 0.2, h * 0.2)
                pts = []
                for (ss, tt) in ((a + g, t + g), (c - g, t + g), (c - g, t + h - g), (a + g, t + h - g)):
                    pts.append((ss, 0.002, tt))
                for (ss, tt) in ((a + g + e, t + g + e), (c - g - e, t + g + e),
                                 (c - g - e, t + h - g - e), (a + g + e, t + h - g - e)):
                    pts.append((ss, -d, tt))
                solid(b, pts, pick(rng, cols), jit=jit, cull=STONE_CULL)
            s += w
            if c >= W:
                break
        t += h
        row += 1


STONE_COLS = [(C["stone"], 5), (C["stone_l"], 2), (C["stone_d"], 2), (C["stone_w"], 2)]


def beam2(b, p, q, w, d0, d1, col, jit=None):
    """Flat beam in a wall frame between 2D points p and q (s,t)."""
    p, q = Vector((p[0], 0, p[1])), Vector((q[0], 0, q[1]))
    dirv = (q - p).normalized()
    side = Vector((-dirv.z, 0, dirv.x)) * (w / 2)
    pts = []
    for a in (p, q):
        for sgn in (-1, 1):
            for d in (d0, d1):
                v = a + side * sgn
                pts.append((v.x, d, v.z))
    solid(b, pts, col, jit=jit, cull=STONE_CULL)


def window(b, s, t, w, h, frame=None, glass=None, shutters=None, cross=True,
           sill=True, glow=False, arch=False):
    frame = frame or C["wood_d"]
    glass = glass or C["glass"]
    box(b, s - w / 2, -0.003, t, s + w / 2, 0.004, t + h, glass,
        mat=GLOW if glow else PAINT, jit=0.03)
    f = 0.012
    box(b, s - w / 2 - f, -0.012, t, s - w / 2, 0.004, t + h, frame)
    box(b, s + w / 2, -0.012, t, s + w / 2 + f, 0.004, t + h, frame)
    if arch:
        beam2(b, (s - w / 2 - f / 2, t + h), (s, t + h + w * 0.45), f * 1.2, -0.012, 0.004, frame)
        beam2(b, (s + w / 2 + f / 2, t + h), (s, t + h + w * 0.45), f * 1.2, -0.012, 0.004, frame)
        # dark fill of arch head
        solid(b, [(s - w / 2, -0.003, t + h), (s + w / 2, -0.003, t + h), (s, -0.003, t + h + w * 0.42),
                  (s - w / 2, 0.004, t + h), (s + w / 2, 0.004, t + h), (s, 0.004, t + h + w * 0.42)],
              glass, jit=0.03, cull=STONE_CULL)
    else:
        box(b, s - w / 2 - f, -0.012, t + h, s + w / 2 + f, 0.004, t + h + f, frame)
    if sill:
        box(b, s - w / 2 - f * 1.6, -0.022, t - f, s + w / 2 + f * 1.6, 0.004, t, frame,
            cull=STONE_CULL)
    if cross:
        box(b, s - 0.0035, -0.008, t, s + 0.0035, 0.002, t + h, frame, cull=STONE_CULL)
        box(b, s - w / 2, -0.008, t + h * 0.5 - 0.0035, s + w / 2, 0.002, t + h * 0.5 + 0.0035,
            frame, cull=STONE_CULL)
    if shutters:
        sw = w * 0.5
        for sg in (-1, 1):
            x0 = s + sg * (w / 2 + f + 0.002)
            box(b, x0, -0.014, t - 0.004, x0 + sg * sw, -0.004, t + h + 0.004, shutters,
                cull=STONE_CULL)


def door(b, s, w, h, t0=0.0, col=None, frame=None, arch=False, frame_w=0.016,
         stone_frame=False):
    col = col or C["door"]
    frame = frame or C["wood_d"]
    rng = b.rng
    n = max(3, int(round(w / 0.024)))
    top = t0 + h
    for i in range(n):
        x0 = s - w / 2 + i * w / n
        x1 = x0 + w / n - 0.0025
        box(b, x0, -0.004 - rng.uniform(0, 0.003), t0, x1, 0.004, top, col, jit=0.09,
            cull=STONE_CULL)
    # iron straps
    for tt in (t0 + h * 0.22, t0 + h * 0.72):
        box(b, s - w / 2, -0.0085, tt, s + w / 2 - 0.004, -0.004, tt + 0.008, C["iron"],
            cull=STONE_CULL)
    f = frame_w
    box(b, s - w / 2 - f, -0.013, t0, s - w / 2, 0.004, top + (0 if arch else f), frame,
        cull=STONE_CULL)
    box(b, s + w / 2, -0.013, t0, s + w / 2 + f, 0.004, top + (0 if arch else f), frame,
        cull=STONE_CULL)
    if arch:
        r = w / 2
        solid(b, [(s - w / 2, -0.004, top)] + [(s - r * cos(a), -0.004, top + r * 0.8 * sin(a))
                                                for a in (0.5, 1.0, pi / 2, pi - 1.0, pi - 0.5)]
              + [(s + w / 2, -0.004, top), (s - w / 2, 0.004, top), (s + w / 2, 0.004, top),
                 (s, 0.004, top + r * 0.8)], col, cull=STONE_CULL)
        k = 7
        for i in range(k):
            a0, a1 = pi - pi * i / k, pi - pi * (i + 1) / k
            p0 = (s + (r + f / 2) * cos(a0), top + (r + f / 2) * 0.8 * sin(a0))
            p1 = (s + (r + f / 2) * cos(a1), top + (r + f / 2) * 0.8 * sin(a1))
            beam2(b, p0, p1, f * 1.3, -0.014, 0.004, frame)
    else:
        box(b, s - w / 2 - f * 1.5, -0.016, top, s + w / 2 + f * 1.5, 0.004, top + f * 1.3,
            frame, cull=STONE_CULL)
    box(b, s + w * 0.26, -0.014, t0 + h * 0.45, s + w * 0.34, -0.006, t0 + h * 0.53,
        C["iron"], cull=STONE_CULL)


# ---------------- roofs ----------------
def gable_roof(b, L, W, ze, zr, oe, og, style, axis="x", at=(0, 0)):
    """Gable roof with the ridge along `axis`. W is the wall-to-wall width,
    ze eave height (at the wall line), zr ridge height, oe eave overhang,
    og gable overhang."""
    rng = b.rng
    w = W / 2
    k = (zr - ze) / w
    ye = w + oe
    nrm = Vector((0, k, 1)).normalized()

    def zs(y):
        return zr - k * y

    xa, xb = -L / 2 - og, L / 2 + og
    rot = Matrix.Identity(4) if axis == "x" else Rz(pi / 2)
    with xf(b, T(at[0], at[1], 0) @ rot):
        for side in (1, -1):
            with xf(b, S(1, side, 1)):
                if style in ("thatch", "slate", "tile"):
                    if style == "thatch":
                        K, T0, T1, seg, ovl = 4, 0.026, 0.017, 0.12, 0.1
                        cols = [(C["thatch"], 6), (C["thatch_l"], 1), (C["thatch_d"], 1)]
                        jit, drop = 0.035, 0.005
                    elif style == "slate":
                        K = max(5, int(ye * math.sqrt(1 + k * k) / 0.055))
                        T0, T1, seg, ovl = 0.017, 0.006, 0.17, 0.25
                        cols = [(C["slate"], 5), (C["slate_l"], 2), (C["slate_d"], 2)]
                        jit, drop = 0.08, 0.003
                    else:
                        K = max(5, int(ye * math.sqrt(1 + k * k) / 0.045))
                        T0, T1, seg, ovl = 0.018, 0.006, 0.1, 0.25
                        cols = [(C["tile"], 5), (C["tile_l"], 2), (C["tile_d"], 2)]
                        jit, drop = 0.08, 0.003
                    for r in range(K):
                        a0 = max(0.0, r / K - (ovl / K if r > 0 else 0.0))
                        a1 = min(1.0, (r + 1) / K + 0.01)
                        yl, yu = ye * (1 - a0), ye * (1 - a1)
                        # segment breakpoints along the ridge direction
                        xs = [xa]
                        while xs[-1] < xb - 1e-6:
                            xs.append(min(xb, xs[-1] + seg * rng.uniform(0.6, 1.4)))
                        if len(xs) > 2 and xs[-1] - xs[-2] < seg * 0.3:
                            xs.pop(-2)
                        rowcol = pick(rng, cols)
                        for i in range(len(xs) - 1):
                            x0, x1 = xs[i], xs[i + 1]
                            if style == "thatch":
                                x0 -= 0.004
                                x1 += 0.004
                            t0 = T0 * rng.uniform(0.9, 1.12) * (1.8 if r == 0 and style == "thatch" else 1.0)
                            dz = rng.uniform(0, drop)
                            pts = []
                            for x in (x0, x1):
                                pl = Vector((x, yl, zs(yl) - dz))
                                pu = Vector((x, yu, zs(yu)))
                                pts += [pl, pl + nrm * t0, pu, pu + nrm * T1]
                            col = rowcol if rng.random() < 0.85 else pick(rng, cols)
                            solid(b, pts, col, jit=jit)
                    if style == "thatch":
                        # rounded, thick eave edge
                        c0 = Vector((0, ye, zs(ye))) + nrm * 0.026 + Vector((0, 0.004, -0.004))
                        cyl(b, (xa - 0.006, c0.y, c0.z), (xb + 0.006, c0.y, c0.z), 0.03, 0.03, 7,
                            lambda n, P: pick(rng, [(C["thatch_d"], 2), (C["thatch"], 1)]), rot=0.2, jit=0.05)
                elif style == "plank":
                    bw = 0.05
                    n = int((xb - xa) / bw)
                    bw = (xb - xa) / n
                    for i in range(n):
                        x0 = xa + i * bw + 0.002
                        x1 = x0 + bw - 0.004
                        th = 0.014 + (0.008 if i % 2 else 0.0)
                        ylo = ye + rng.uniform(-0.008, 0.008)
                        pts = []
                        for x in (x0, x1):
                            pl = Vector((x, ylo, zs(ylo)))
                            pu = Vector((x, 0.0, zs(0.0)))
                            pts += [pl, pl + nrm * th, pu, pu + nrm * th]
                        col = pick(rng, [(C["plank"], 4), (C["wood"], 2), (C["wood_l"], 2)])
                        solid(b, pts, col, jit=0.09)
        # ridge
        if style == "thatch":
            cyl(b, (xa - 0.012, 0, zr + 0.03), (xb + 0.012, 0, zr + 0.03), 0.042, 0.042, 7,
                C["thatch_d"], rot=pi / 2, jit=0.05)
            # decorative liggers (crossed rods) along the ridge roll
            n = int((xb - xa) / 0.07)
            for i in range(n):
                x = xa + (i + 0.5) * (xb - xa) / n
                for sg in (-1, 1):
                    cyl(b, (x - 0.018, sg * 0.036, zr + 0.025), (x + 0.018, sg * 0.03, zr + 0.06),
                        0.005, 0.005, 4, C["wood_l"])
        elif style in ("slate", "tile"):
            col = C["slate_d"] if style == "slate" else C["tile_d"]
            seg = 0.08
            n = max(1, int((xb - xa) / seg))
            for i in range(n):
                x0 = xa - 0.005 + i * (xb - xa + 0.01) / n
                x1 = x0 + (xb - xa + 0.01) / n - 0.004
                pts = []
                for x in (x0, x1):
                    pts += [(x, 0.035, zs(0.035) + 0.012), (x, -0.035, zs(0.035) + 0.012),
                            (x, 0, zr + 0.035), (x, 0.02, zs(0.02) + 0.03), (x, -0.02, zs(0.02) + 0.03)]
                solid(b, pts, col, jit=0.07)
        elif style == "plank":
            for sg in (-1, 1):
                pts = []
                for x in (xa - 0.01, xb + 0.01):
                    pts += [(x, 0, zr + 0.03), (x, sg * 0.05, zs(0.05) + 0.024),
                            (x, sg * 0.05, zs(0.05) + 0.012), (x, 0, zr + 0.012)]
                solid(b, pts, C["wood_d"], jit=0.05)


def cone_roof(b, cx, cy, R, z0, z1, n, style="thatch", tiers=3):
    rng = b.rng
    h = z1 - z0

    def rad(z):
        return R * (z1 - z) / h

    if style == "thatch":
        cols = [(C["thatch"], 5), (C["thatch_l"], 2), (C["thatch_d"], 2)]
        tout, rj, drop, jit = 0.045, 0.012, 0.012, 0.07
    else:
        cols = [(C["slate"], 5), (C["slate_l"], 2), (C["slate_d"], 2)]
        tout, rj, drop, jit = 0.016, 0.002, 0.002, 0.08
    rot0 = rng.uniform(0, 2 * pi)
    for i in range(tiers):
        fa = (i / tiers) * 0.88 - (0.05 if i else 0.0)
        fb = ((i + 1) / tiers) * 0.88 if i < tiers - 1 else 1.0
        za, zb = z0 + h * max(0, fa), z0 + h * fb
        pts = []
        rot = rot0 + (pi / n if i % 2 else 0)
        for k in range(n):
            a = rot + 2 * pi * k / n
            ro = rad(za) + tout * (1.2 if i == 0 else 1.0) + rng.uniform(-rj, rj)
            zz = za - rng.uniform(0, drop)
            pts.append((cx + ro * cos(a), cy + ro * sin(a), zz))
            pts.append((cx + rad(za) * 0.82 * cos(a), cy + rad(za) * 0.82 * sin(a), zz + 0.01))
            if i < tiers - 1:
                rb = rad(zb) + 0.006
                pts.append((cx + rb * cos(a), cy + rb * sin(a), zb))
        if i == tiers - 1:
            pts.append((cx, cy, z1 + 0.01))
        solid(b, pts, lambda nn, P, c=pick(rng, cols): c if rng.random() < 0.6 else pick(rng, cols),
              jit=jit)


def chimney(b, x, y, z0, z1, w, d, pots=1):
    box(b, x - w / 2, y - d / 2, z0, x + w / 2, y + d / 2, z1,
        lambda n, P: pick(b.rng, STONE_COLS), bev=0.005, jit=0.1)
    # coursing bands give the chimney a masonry read
    zz = z0 + 0.06
    while zz < z1 - 0.06:
        box(b, x - w / 2 - 0.004, y - d / 2 - 0.004, zz, x + w / 2 + 0.004, y + d / 2 + 0.004,
            zz + 0.008, C["stone_d"], jit=0.05)
        zz += 0.055
    box(b, x - w / 2 - 0.014, y - d / 2 - 0.014, z1 - 0.004, x + w / 2 + 0.014, y + d / 2 + 0.014,
        z1 + 0.018, C["stone_l"], bev=0.004)
    if pots:
        for i in range(pots):
            px = x + (i - (pots - 1) / 2) * (w * 0.5)
            cyl(b, (px, y, z1 + 0.018), (px, y, z1 + 0.06), 0.019, 0.016, 7, C["terracotta"])
            cyl(b, (px, y, z1 + 0.058), (px, y, z1 + 0.0605), 0.012, 0.012, 7, C["dark"])
    else:
        box(b, x - w / 2 + 0.012, y - d / 2 + 0.012, z1 + 0.018, x + w / 2 - 0.012,
            y + d / 2 - 0.012, z1 + 0.02, C["dark"])


def fence_line(b, p0, p1, h=0.13, every=0.17, rails=2, gap=None, col=None):
    rng = b.rng
    col = col or C["wood"]
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    L = d.length
    n = max(1, int(round(L / every)))
    posts = [p0 + d * (i / n) for i in range(n + 1)]
    for p in posts:
        hh = h * rng.uniform(0.95, 1.08)
        box(b, p.x - 0.013, p.y - 0.013, 0, p.x + 0.013, p.y + 0.013, hh,
            lambda nn, P: C["wood_d"] if nn.z > 0.9 else col, bev=0.004, jit=0.1)
    for i in range(n):
        a, c = posts[i], posts[i + 1]
        mid = (a + c) / 2
        if gap is not None and (mid - Vector(gap)).length < 0.05:
            continue
        for r in range(rails):
            z = h * (0.4 + 0.45 * r / max(1, rails - 1)) if rails > 1 else h * 0.7
            z += rng.uniform(-0.008, 0.008)
            cyl(b, (a.x, a.y, z), (c.x, c.y, z + rng.uniform(-0.01, 0.01)), 0.008, 0.008, 5,
                pick(rng, [(C["wood"], 2), (C["wood_l"], 1)]), jit=0.1)


def stone_pebble(b, c, size, cols=None, flat=0.6, n=10):
    rng = b.rng
    cols = cols or [(C["rock"], 3), (C["rock_l"], 2), (C["rock_d"], 1)]
    pts = []
    for i in range(n):
        v = Vector((rng.gauss(0, 1), rng.gauss(0, 1), abs(rng.gauss(0, 1)))).normalized()
        pts.append((c[0] + v.x * size, c[1] + v.y * size, c[2] + v.z * size * flat))
    for k in range(5):
        a = 2 * pi * k / 5 + rng.uniform(0, 1)
        pts.append((c[0] + cos(a) * size * 0.9, c[1] + sin(a) * size * 0.9, c[2]))
    solid(b, pts, pick(rng, cols), jit=0.1)


def tuft(b, x, y, h=0.07, n=5, col=None):
    rng = b.rng
    for i in range(n):
        a = rng.uniform(0, 2 * pi)
        lean = rng.uniform(0.1, 0.45)
        tip = (x + cos(a) * h * lean, y + sin(a) * h * lean, h * rng.uniform(0.7, 1.1))
        bx, by = x + cos(a) * 0.006, y + sin(a) * 0.006
        cyl(b, (bx, by, 0), tip, 0.008, 0, 3, col or pick(rng, [(C["grass_d"], 2), (C["grass"], 2), (C["grass_l"], 1)]),
            rot=rng.uniform(0, 2), jit=0.08)


# --------------------------------------------------------------------------
# Models: settlements
# --------------------------------------------------------------------------
def campfire(b, x, y):
    rng = b.rng
    for k in range(7):
        a = 2 * pi * k / 7 + rng.uniform(-0.2, 0.2)
        stone_pebble(b, (x + 0.05 * cos(a), y + 0.05 * sin(a), 0), 0.018, flat=0.8)
    for k in range(3):
        a = 2 * pi * k / 3 + 0.4
        cyl(b, (x + 0.045 * cos(a), y + 0.045 * sin(a), 0.006), (x - 0.01 * cos(a), y - 0.01 * sin(a), 0.035),
            0.009, 0.007, 5, C["wood_d"])
    blob(b, (x, y, 0.0), (0.03, 0.03, 0.012), C["ember"], sub=0, mat=GLOW, jit=0.1)
    cyl(b, (x, y, 0.01), (x + 0.004, y, 0.075), 0.022, 0, 5, C["ember"], mat=GLOW, jit=0.05)
    cyl(b, (x + 0.006, y - 0.004, 0.01), (x + 0.004, y, 0.055), 0.014, 0, 5, C["flame"], mat=GLOW,
        rot=0.5, jit=0.05)


def _tent_body(b):
    rng = b.rng
    w, zr, th = 0.2, 0.3, 0.012
    x0, x1 = -0.23, 0.23
    ye = w + 0.02
    nrm = Vector((0, zr, ye)).normalized()
    hide = [(C["hide"], 4), (C["hide_d"], 2), (C["hide_l"], 2)]
    for side in (1, -1):
        with xf(b, S(1, side, 1)):
            nx, ns = 3, 2
            for i in range(nx):
                for j in range(ns):
                    xa = x0 + (x1 - x0) * i / nx - 0.004
                    xb = x0 + (x1 - x0) * (i + 1) / nx + 0.004
                    a0, a1 = j / ns, (j + 1) / ns + 0.02
                    pts = []
                    for x in (xa, xb):
                        for a in (a0, min(a1, 1.0)):
                            p = Vector((x, ye * (1 - a), zr * a))
                            pts += [p, p + nrm * th * (1.4 if j == 0 else 1.0)]
                    solid(b, pts, pick(rng, hide), jit=0.08)
            # lacing seam lines along each panel joint
            for i in range(1, nx):
                x = x0 + (x1 - x0) * i / nx
                p0 = Vector((x, ye, 0)) + nrm * th * 1.4
                p1 = Vector((x, 0, zr)) + nrm * th
                cyl(b, p0, p1, 0.004, 0.004, 4, C["wood_d"])
    # back end
    solid(b, [(x0, -ye, 0), (x0, ye, 0), (x0, 0, zr), (x0 - th, -ye, 0), (x0 - th, ye, 0),
              (x0 - th, 0, zr + 0.005)], pick(rng, hide))
    # front: dark interior, two tied-back flaps
    solid(b, [(x1 - 0.03, -ye + 0.02, 0), (x1 - 0.03, ye - 0.02, 0), (x1 - 0.03, 0, zr - 0.02),
              (x1 - 0.02, -ye + 0.02, 0), (x1 - 0.02, ye - 0.02, 0), (x1 - 0.02, 0, zr - 0.02)],
          C["dark"], jit=0.02)
    for sg in (-1, 1):
        solid(b, [(x1, sg * ye, 0), (x1, 0, zr), (x1, sg * 0.075, 0),
                  (x1 + th, sg * ye, 0), (x1 + th, 0, zr), (x1 + th, sg * 0.075, 0)],
              C["hide_d"], jit=0.05)
        cyl(b, (x1 + 0.012, sg * 0.08, 0.0), (x1 + 0.012, sg * 0.02, zr * 0.82), 0.013, 0.008, 6,
            C["hide_l"])
    # poles
    cyl(b, (x0 - 0.05, 0, zr + 0.012), (x1 + 0.05, 0, zr + 0.012), 0.012, 0.012, 6, C["wood"])
    for x in (x0 - 0.015, x1 + 0.025):
        for sg in (-1, 1):
            cyl(b, (x, sg * (ye + 0.005), 0), (x, -sg * 0.05, zr + 0.065), 0.009, 0.008, 5,
                C["wood"])
    # guy ropes and pegs
    for (a, p) in [((x0 - 0.05, 0, zr + 0.01), (-0.42, 0, 0)), ((x1 + 0.05, 0, zr + 0.01), (0.42, -0.0, 0)),
                   ((0, ye * 0.45, zr * 0.55 + 0.012), (0.0, 0.42, 0)),
                   ((0, -ye * 0.45, zr * 0.55 + 0.012), (0.0, -0.42, 0))]:
        cyl(b, a, (p[0], p[1], 0.02), 0.0035, 0.0035, 4, C["hide_l"])
        box(b, p[0] - 0.008, p[1] - 0.008, 0, p[0] + 0.008, p[1] + 0.008, 0.03, C["wood_d"])


def m_tent(b):
    # ridge along Y so both hide slopes catch the sun; the open flap faces +Y
    with xf(b, Rz(pi / 2)):
        _tent_body(b)
    campfire(b, 0.2, 0.36)
    cyl(b, (0.31, 0.2, 0.02), (0.35, 0.36, 0.02), 0.02, 0.02, 6,
        lambda n, P: C["endgrain"] if abs(n.y) > 0.8 else C["wood"], jit=0.08)
    # a hide drying on a small frame, and a stack of firewood
    for xx in (0.3, 0.42):
        cyl(b, (xx, -0.33, 0), (xx, -0.33, 0.16), 0.007, 0.007, 4, C["wood_d"])
    cyl(b, (0.29, -0.33, 0.155), (0.43, -0.33, 0.155), 0.006, 0.006, 4, C["wood_d"])
    box(b, 0.305, -0.335, 0.05, 0.415, -0.325, 0.15, C["hide_l"])
    woodpile(b, -0.33, 0.3, along="x", n=2, length=0.12)


def m_mud_hut(b):
    rng = b.rng
    R, H, n = 0.235, 0.17, 14
    for k in range(20):
        a = 2 * pi * k / 20 + rng.uniform(-0.1, 0.1)
        stone_pebble(b, ((R + 0.008) * cos(a), (R + 0.008) * sin(a), 0), 0.026, flat=0.9)
    bands = [(0.0, 0.05, R + 0.004, C["mud_d"]), (0.05, 0.1, R, C["mud"]),
             (0.1, 0.15, R + 0.003, C["mud_l"]), (0.15, 0.2, R - 0.002, C["mud"]), (0.2, 0.26, R - 0.004, C["mud_l"])]
    for (z0, z1, rr, col) in bands:
        cyl(b, (0, 0, z0), (0, 0, z1), rr, rr * 0.99, n, col, rot=rng.uniform(0, 1), jit=0.09)
    # wattle stakes
    for k in range(7):
        a = 2 * pi * k / 7 + 0.3
        if abs(((a - pi / 4 + pi) % (2 * pi)) - pi) < 0.35:
            continue
        cyl(b, ((R + 0.006) * cos(a), (R + 0.006) * sin(a), 0), ((R + 0.004) * cos(a), (R + 0.004) * sin(a), 0.15),
            0.011, 0.009, 5, C["wood"])
    # door facing the camera
    with xf(b, wall_m(Vector((cos(pi / 4), sin(pi / 4), 0)) * (R - 0.004), (cos(pi / 4), sin(pi / 4), 0), 0.0)):
        box(b, -0.05, -0.014, 0, 0.05, 0.03, 0.14, C["dark"], jit=0.02)
        for sg in (-1, 1):
            box(b, sg * 0.05, -0.022, 0, sg * 0.068, 0.02, 0.15, C["wood_d"], bev=0.004)
        box(b, -0.075, -0.026, 0.14, 0.075, 0.02, 0.16, C["wood_d"], bev=0.004)
        box(b, -0.05, -0.02, 0.03, -0.012, -0.014, 0.14, C["hide"], jit=0.05)
        box(b, -0.06, -0.06, 0, 0.06, -0.02, 0.012, C["stone"], bev=0.004)
    cone_roof(b, 0, 0, 0.29, 0.215, 0.46, n, "thatch", tiers=3)
    for k in range(3):
        a = 2 * pi * k / 3 + 0.5
        cyl(b, (0, 0, 0.41), (0.03 * cos(a), 0.03 * sin(a), 0.51), 0.007, 0.005, 4, C["wood_d"])
    cyl(b, (0, 0, 0.445), (0, 0, 0.47), 0.03, 0.02, 7, C["wood_d"])
    # clay pots and a woodpile by the door
    for (x, y, r) in ((0.3, 0.06, 0.03), (0.33, 0.13, 0.022)):
        cyl(b, (x, y, 0), (x, y, r * 1.6), r * 0.7, r, 7, C["terracotta"])
        cyl(b, (x, y, r * 1.6), (x, y, r * 2.0), r * 0.7, r * 0.75, 7, C["terracotta"])
    for i in range(4):
        z = 0.012 + (i // 2) * 0.022
        yy = -0.08 + (i % 2) * 0.03 + (0.015 if i >= 2 else 0)
        cyl(b, (0.24, yy - 0.16, z), (0.34, yy - 0.18, z), 0.011, 0.011, 6,
            lambda nn, P: C["endgrain"] if abs(nn.x) > 0.8 else C["wood"])


def body_gable(b, L, W, z0, H, zr, col, axis="x"):
    """Convex house body with gable ends."""
    if axis == "x":
        pts = [(sx * L / 2, sy * W / 2, z) for sx in (-1, 1) for sy in (-1, 1) for z in (z0, H)]
        pts += [(sx * L / 2, 0, zr) for sx in (-1, 1)]
    else:
        pts = [(sx * L / 2, sy * W / 2, z) for sx in (-1, 1) for sy in (-1, 1) for z in (z0, H)]
        pts += [(0, sy * W / 2, zr) for sy in (-1, 1)]
    solid(b, pts, col, jit=0.04)


def timber_frame(b, Wd, t0, t1, posts, col=None, braces=(), rails=()):
    col = col or C["wood_d"]
    for s in posts:
        box(b, s - 0.011, -0.01, t0, s + 0.011, 0.004, t1, col, jit=0.08, cull=STONE_CULL)
    for (t, sa, sb) in rails:
        box(b, sa, -0.01, t - 0.009, sb, 0.004, t + 0.009, col, jit=0.08, cull=STONE_CULL)
    for (p, q) in braces:
        beam2(b, p, q, 0.017, -0.009, 0.004, col, jit=0.08)


def gable_frame(b, Wd, t0, ze, zr, col=None):
    """Timber on a gable triangle in a wall frame (s from 0..Wd)."""
    col = col or C["wood_d"]
    beam2(b, (0.0, ze), (Wd / 2, zr), 0.018, -0.01, 0.004, col)
    beam2(b, (Wd, ze), (Wd / 2, zr), 0.018, -0.01, 0.004, col)
    box(b, Wd / 2 - 0.01, -0.01, ze, Wd / 2 + 0.01, 0.004, zr - 0.01, col, cull=STONE_CULL)


def woodpile(b, x, y, along="y", n=3, length=0.12):
    rng = b.rng
    for row in range(n):
        for i in range(n - row):
            z = 0.013 + row * 0.022
            o = (i - (n - row - 1) / 2) * 0.026
            if along == "y":
                p0, p1 = (x + o, y - length / 2, z), (x + o, y + length / 2, z)
            else:
                p0, p1 = (x - length / 2, y + o, z), (x + length / 2, y + o, z)
            cyl(b, p0, p1, 0.012, 0.012, 6,
                lambda nn, P: C["endgrain"] if abs(nn.y if along == "y" else nn.x) > 0.8 else C["wood"],
                rot=rng.uniform(0, 1), jit=0.08)


def barrel(b, x, y, r=0.035, h=0.075):
    cyl(b, (x, y, 0), (x, y, h * 0.5), r * 0.86, r, 8, C["wood"], jit=0.08)
    cyl(b, (x, y, h * 0.5), (x, y, h), r, r * 0.86, 8, C["wood"], jit=0.08)
    for z in (h * 0.2, h * 0.78):
        cyl(b, (x, y, z), (x, y, z + 0.008), r * 0.96, r * 0.97, 8, C["iron"], jit=0.05)
    cyl(b, (x, y, h - 0.002), (x, y, h + 0.002), r * 0.8, r * 0.8, 8, C["wood_l"])


def m_thatch_hut(b):
    rng = b.rng
    L, W, H, zr = 0.44, 0.36, 0.23, 0.46
    box(b, -L / 2 - 0.015, -W / 2 - 0.015, 0, L / 2 + 0.015, W / 2 + 0.015, 0.035,
        lambda n, P: pick(rng, STONE_COLS), bev=0.008)
    for i in range(10):  # footing stones
        s = -L / 2 + (i + 0.5) * L / 10
        stone_pebble(b, (s, W / 2 + 0.015, 0), 0.02, flat=0.9)
    body_gable(b, L, W, 0.03, H, zr - 0.02, C["plaster"])
    # +Y long wall: windows (the low thatch eave shades this side)
    with xf(b, wall_m((0, W / 2, 0), (0, 1, 0), L)):
        timber_frame(b, L, 0.03, H, [0.011, L / 2, L - 0.011],
                     rails=[(0.04, 0, L), (H - 0.01, 0, L)],
                     braces=[((0.02, 0.05), (0.09, H - 0.02)), ((L - 0.02, 0.05), (L - 0.09, H - 0.02))])
        window(b, L * 0.27, 0.09, 0.055, 0.05, shutters=C["wood"])
        window(b, L * 0.73, 0.09, 0.055, 0.05, shutters=C["wood"])
    # +X gable end: front door, loft window
    with xf(b, wall_m((L / 2, 0, 0), (1, 0, 0), W)):
        timber_frame(b, W, 0.03, H, [0.011, W - 0.011], rails=[(0.04, 0, W), (H - 0.01, 0, W)])
        gable_frame(b, W, 0.03, H, zr - 0.02)
        door(b, W * 0.5, 0.085, 0.13, t0=0.035)
        window(b, W / 2, H + 0.035, 0.04, 0.045, cross=False)
        box(b, W * 0.5 - 0.06, -0.05, 0, W * 0.5 + 0.06, -0.002, 0.018, C["stone"], bev=0.004)
    gable_roof(b, L, W, H - 0.005, zr, 0.045, 0.05, "thatch")
    woodpile(b, -L / 2 - 0.06, 0.02, along="y", n=3, length=0.16)
    barrel(b, L / 2 + 0.05, W / 2 + 0.06)


def m_thatch_stone(b):
    rng = b.rng
    L, W, H, zr = 0.5, 0.42, 0.26, 0.53  # ridge along Y here: L along x is the eave side
    Hs = 0.11
    # main body (ridge along Y => gables at +-Y)
    body_gable(b, L, W, 0.0, H, zr - 0.02, C["plaster"], axis="y")
    box(b, -L / 2 - 0.012, -W / 2 - 0.012, 0, L / 2 + 0.012, W / 2 + 0.012, Hs, C["mortar"])
    with xf(b, wall_m((L / 2 + 0.012, 0, 0), (1, 0, 0), W + 0.024)):
        stones(b, W + 0.024, Hs, STONE_COLS, sw=0.075, sh=0.05)
    with xf(b, wall_m((L / 2, 0, 0), (1, 0, 0), W)):
        timber_frame(b, W, Hs, H, [0.011, W * 0.5, W - 0.011], rails=[(Hs + 0.006, 0, W), (H - 0.008, 0, W)],
                     braces=[((0.02, Hs + 0.02), (W * 0.5 - 0.02, H - 0.02)), ((W - 0.02, Hs + 0.02), (W * 0.5 + 0.02, H - 0.02))])
        window(b, W * 0.25, 0.135, 0.05, 0.055, shutters=C["wood"])
        window(b, W * 0.75, 0.135, 0.05, 0.055, shutters=C["wood"])
    ds = L * 0.5
    with xf(b, wall_m((0, W / 2 + 0.012, 0), (0, 1, 0), L + 0.024)):
        stones(b, L + 0.024, Hs, STONE_COLS, sw=0.075, sh=0.05, holes=[(ds - 0.05, 0, ds + 0.074, 0.2)])
    with xf(b, wall_m((0, W / 2, 0), (0, 1, 0), L)):
        timber_frame(b, L, Hs, H, [0.011, L * 0.33, L * 0.67, L - 0.011], rails=[(Hs + 0.006, 0, L), (H - 0.008, 0, L)])
        gable_frame(b, L, Hs, H, zr - 0.02)
        door(b, ds, 0.09, 0.16, t0=0.0, arch=False)
        box(b, ds - 0.065, -0.06, 0, ds + 0.065, -0.01, 0.018, C["stone"], bev=0.004)
        window(b, L * 0.17, 0.135, 0.05, 0.055, shutters=C["wood"])
        window(b, L * 0.83, 0.135, 0.05, 0.055, shutters=C["wood"])
        window(b, L * 0.5, 0.32, 0.045, 0.05, cross=False, sill=False)
    gable_roof(b, W, L, H - 0.005, zr, 0.05, 0.055, "thatch", axis="y")
    # lean-to woodshed on the -Y gable
    y0, y1 = -W / 2 - 0.2, -W / 2
    for (x, y) in ((-0.17, y0 + 0.02), (0.17, y0 + 0.02)):
        box(b, x - 0.012, y - 0.012, 0, x + 0.012, y + 0.012, 0.17, C["wood"], bev=0.004)
    pts = []
    for x in (-0.22, 0.22):
        for (y, z) in ((y0 - 0.03, 0.15), (y1, 0.27)):
            pts += [(x, y, z), (x, y, z + 0.035)]
    solid(b, pts, C["thatch_d"], jit=0.08)
    for i in range(6):
        solid(b, [(x, y, z + 0.035 + 0.006) for x in (-0.2 + i * 0.07, -0.14 + i * 0.07)
                  for (y, z) in ((y0 - 0.03, 0.15), (y1, 0.27))] +
              [(x, y, z + 0.035 + 0.02) for x in (-0.2 + i * 0.07, -0.14 + i * 0.07)
               for (y, z) in ((y0 - 0.02, 0.152),)], pick(rng, [(C["thatch"], 2), (C["thatch_l"], 1)]), jit=0.07)
    woodpile(b, 0.0, y0 + 0.09, along="x", n=3, length=0.3)
    barrel(b, L / 2 + 0.06, W / 2 + 0.02)


def log_wall(b, L, W, courses, r, holes_y=(), holes_x=()):
    """Log walls for a rectangle. Logs along x on the +-Y walls and along y on
    the +-X walls, interlocking at the corners."""
    rng = b.rng
    c = r * 1.75
    ext = 0.035

    def logcol(axis):
        return lambda n, P: C["endgrain"] if abs(n[axis]) > 0.8 else pick(rng, [(C["wood"], 3), (C["wood_l"], 1), (C["plank"], 1)])

    for k in range(courses):
        for axis in ("x", "y"):
            z = r + k * c + (c / 2 if axis == "y" else 0.0)
            if z + r > courses * c + c / 2:
                continue
            for sg in (-1, 1):
                if axis == "x":
                    y = sg * W / 2
                    segs = [(-L / 2 - ext, L / 2 + ext)]
                    holes = holes_y if sg > 0 else ()
                else:
                    x = sg * L / 2
                    segs = [(-W / 2 - ext, W / 2 + ext)]
                    holes = holes_x if sg > 0 else ()
                for (h0, h1, hz0, hz1) in holes:
                    if hz0 - r < z < hz1 + r:
                        new = []
                        for (a, bb) in segs:
                            if h1 <= a or h0 >= bb:
                                new.append((a, bb))
                            else:
                                if h0 > a:
                                    new.append((a, h0))
                                if h1 < bb:
                                    new.append((h1, bb))
                        segs = new
                for (a, bb) in segs:
                    if bb - a < 0.02:
                        continue
                    rr = r * rng.uniform(0.92, 1.08)
                    if axis == "x":
                        cyl(b, (a, y, z), (bb, y, z), rr, rr, 6, logcol(0), rot=rng.uniform(0, 1), jit=0.08)
                    else:
                        cyl(b, (x, a, z), (x, bb, z), rr, rr, 6, logcol(1), rot=rng.uniform(0, 1), jit=0.08)


def m_log_cabin(b):
    rng = b.rng
    L, W = 0.54, 0.42
    r = 0.02
    courses = 6
    Hw = courses * r * 1.75 + r
    zr = 0.42
    box(b, -L / 2 + 0.01, -W / 2 + 0.01, 0, L / 2 - 0.01, W / 2 - 0.01, Hw, C["wood_d"])
    # door on the +X gable end, windows on the +Y long side
    log_wall(b, L, W, courses, r,
             holes_y=[(-0.18, -0.08, 0.07, 0.14), (0.08, 0.18, 0.07, 0.14)],
             holes_x=[(-0.055, 0.055, 0.0, 0.15)])
    with xf(b, wall_m((0, W / 2 - 0.01, 0), (0, 1, 0), L - 0.02)):
        for xw in (-0.13, 0.13):
            window(b, (L - 0.02) / 2 - xw, 0.075, 0.07, 0.055)
    with xf(b, wall_m((L / 2 - 0.01, 0, 0), (1, 0, 0), W - 0.02)):
        door(b, (W - 0.02) / 2, 0.09, 0.14, t0=0.0)
        box(b, (W - 0.02) / 2 - 0.07, -0.07, 0, (W - 0.02) / 2 + 0.07, -0.02, 0.02, C["plank"], bev=0.003)
    # gable ends: stacked shorter logs
    for sg in (-1, 1):
        x = sg * L / 2
        z = Hw
        k = 0
        while z < zr - 0.04:
            half = (W / 2) * (zr - z) / (zr - Hw)
            cyl(b, (x, -half, z), (x, half, z), r, r, 6,
                lambda nn, P: C["endgrain"] if abs(nn.y) > 0.8 else C["wood"], rot=rng.uniform(0, 1))
            z += r * 1.75
            k += 1
    gable_roof(b, L + 0.02, W + 0.02, Hw - 0.01, zr, 0.05, 0.06, "plank")
    # stone chimney on the back (-X) gable
    cx = -L / 2 - 0.05
    box(b, cx - 0.06, -0.08, 0, cx + 0.045, 0.08, 0.2, lambda n, P: pick(rng, STONE_COLS), bev=0.008, jit=0.1)
    chimney(b, cx, 0.0, 0.2, 0.6, 0.075, 0.09, pots=0)
    # porch bits
    barrel(b, 0.25, W / 2 + 0.07)
    cyl(b, (-0.3, 0.3, 0), (-0.3, 0.3, 0.05), 0.035, 0.032, 7,
        lambda n, P: C["endgrain"] if n.z > 0.8 else C["wood"])
    cyl(b, (-0.3, 0.3, 0.05), (-0.27, 0.32, 0.11), 0.005, 0.005, 4, C["wood_l"])
    box(b, -0.315, 0.28, 0.045, -0.29, 0.31, 0.07, C["iron"])
    woodpile(b, 0.34, -0.2, along="y", n=3, length=0.12)


def m_timber_house(b):
    rng = b.rng
    L, W = 0.64, 0.44
    H1, H2 = 0.17, 0.33
    jet = 0.025
    zr = 0.6
    # stone ground floor
    box(b, -L / 2, -W / 2, 0, L / 2, W / 2, H1, C["mortar"])
    door_s = L * 0.6
    with xf(b, wall_m((0, W / 2, 0), (0, 1, 0), L)):
        stones(b, L, H1, STONE_COLS, sw=0.08, sh=0.055,
               holes=[(door_s - 0.065, 0, door_s + 0.065, 0.16), (L * 0.22 - 0.05, 0.05, L * 0.22 + 0.05, 0.13)])
        door(b, door_s, 0.09, 0.13, arch=True, frame=C["stone_l"], frame_w=0.018)
        window(b, L * 0.22, 0.06, 0.06, 0.06, frame=C["stone_l"])
        box(b, door_s - 0.07, -0.05, 0, door_s + 0.07, -0.004, 0.02, C["stone_l"], bev=0.005)
    with xf(b, wall_m((L / 2, 0, 0), (1, 0, 0), W)):
        stones(b, W, H1, STONE_COLS, sw=0.08, sh=0.055, holes=[(W / 2 - 0.05, 0.05, W / 2 + 0.05, 0.13)])
        window(b, W / 2, 0.06, 0.06, 0.06, frame=C["stone_l"])
    # jettied upper floor (overhangs on the long +-Y sides)
    body_gable(b, L, W + 2 * jet, H1, H2, zr - 0.02, C["plaster"])
    box(b, -L / 2, -W / 2 - jet - 0.004, H1 - 0.012, L / 2, W / 2 + jet + 0.004, H1 + 0.012, C["wood_d"], jit=0.06)
    for i in range(7):  # joist ends under the jetty
        x = -L / 2 + 0.04 + i * (L - 0.08) / 6
        box(b, x - 0.01, W / 2, H1 - 0.03, x + 0.01, W / 2 + jet, H1 - 0.01, C["wood_d"])
    with xf(b, wall_m((0, W / 2 + jet, 0), (0, 1, 0), L)):
        posts = [0.011 + i * (L - 0.022) / 6 for i in range(7)]
        timber_frame(b, L, H1, H2, posts, rails=[(H1 + 0.02, 0, L), (H2 - 0.009, 0, L)],
                     braces=[((posts[0], H1 + 0.03), (posts[1], H2 - 0.02)),
                             ((posts[6], H1 + 0.03), (posts[5], H2 - 0.02)),
                             ((posts[3], H1 + 0.03), (posts[2] + 0.02, H2 - 0.02))])
        for s in (L * 0.18 + 0.03, L * 0.5 + 0.045, L * 0.82 - 0.02):
            window(b, s, H1 + 0.045, 0.055, 0.06, frame=C["wood_d"])
    with xf(b, wall_m((L / 2, 0, 0), (1, 0, 0), W + 2 * jet)):
        Wd = W + 2 * jet
        timber_frame(b, Wd, H1, H2, [0.011, Wd * 0.3, Wd * 0.7, Wd - 0.011],
                     rails=[(H1 + 0.02, 0, Wd), (H2 - 0.009, 0, Wd)],
                     braces=[((0.02, H1 + 0.03), (Wd * 0.3 - 0.01, H2 - 0.02)),
                             ((Wd - 0.02, H1 + 0.03), (Wd * 0.7 + 0.01, H2 - 0.02))])
        gable_frame(b, Wd, H1, H2, zr - 0.02)
        window(b, Wd / 2, H1 + 0.045, 0.06, 0.06)
        window(b, Wd / 2, H2 + 0.06, 0.045, 0.05, cross=False)
    gable_roof(b, L, W + 2 * jet, H2 - 0.005, zr, 0.05, 0.04, "tile")
    chimney(b, -L / 2 + 0.11, -0.05, 0.4, 0.74, 0.085, 0.085, pots=2)
    # small extras: a cart wheel leaning and a bench
    with xf(b, T(L / 2 + 0.07, -0.15, 0)):
        box(b, -0.02, -0.08, 0.04, 0.02, 0.08, 0.055, C["wood"], bev=0.004)
        for yy in (-0.065, 0.065):
            box(b, -0.012, yy - 0.01, 0, 0.012, yy + 0.01, 0.04, C["wood_d"])
    barrel(b, -L / 2 + 0.05, W / 2 + jet + 0.06)
    barrel(b, -L / 2 + 0.12, W / 2 + jet + 0.075, r=0.03, h=0.065)


def dormer(b, x, y_face, zbase, w, h, ridge_h):
    """Small gabled dormer facing +Y."""
    box(b, x - w / 2, y_face - 0.12, zbase, x + w / 2, y_face, zbase + h, C["stone"])
    with xf(b, wall_m((x, y_face, 0), (0, 1, 0), w, z=zbase)):
        window(b, w / 2, 0.02, w * 0.5, h * 0.6, frame=C["wood_d"], sill=False)
    zt = zbase + h
    for sg in (-1, 1):
        pts = []
        for y in (y_face + 0.025, y_face - 0.13):
            pts += [(x + sg * (w / 2 + 0.025), y, zt - 0.012), (x, y, zt + ridge_h),
                    (x + sg * (w / 2 + 0.025), y, zt + 0.004), (x, y, zt + ridge_h + 0.016)]
        solid(b, pts, pick(b.rng, [(C["slate"], 2), (C["slate_d"], 1)]), jit=0.06)


def m_stone_house(b):
    rng = b.rng
    L, W, H, zr = 0.76, 0.56, 0.42, 0.66
    body_gable(b, L, W, 0.0, H, zr - 0.02, C["mortar"])
    # plinth course
    box(b, -L / 2 - 0.012, -W / 2 - 0.012, 0, L / 2 + 0.012, W / 2 + 0.012, 0.045, C["stone_d"], bev=0.006)
    ds = L / 2
    wins_y = [L * 0.17, L * 0.83]
    holes = [(ds - 0.08, 0, ds + 0.08, 0.24)] + [(s - 0.05, 0.07, s + 0.05, 0.17) for s in wins_y] + \
            [(s - 0.05, 0.26, s + 0.05, 0.36) for s in (L * 0.17, L * 0.5, L * 0.83)]
    with xf(b, wall_m((0, W / 2, 0), (0, 1, 0), L)):
        stones(b, L, H, STONE_COLS, t0=0.045, sw=0.12, sh=0.07, holes=holes)
        box(b, 0, -0.016, 0.205, L, 0.004, 0.222, C["stone_l"], jit=0.04, cull=STONE_CULL)  # string course
        door(b, ds, 0.1, 0.15, t0=0.045, arch=True, frame=C["stone_l"], frame_w=0.02)
        for i, (ww, hh) in enumerate([(0.11, 0.025), (0.14, 0.012)]):  # steps
            box(b, ds - ww / 2 - 0.02, -0.03 - 0.03 * (1 - i), 0, ds + ww / 2 + 0.02, 0.0, 0.045 - i * 0.022 + 0.0,
                C["stone_l"], bev=0.004)
        for s in wins_y:
            window(b, s, 0.08, 0.065, 0.08, frame=C["stone_l"], shutters=C["slate_d"])
        for s in (L * 0.17, L * 0.5, L * 0.83):
            window(b, s, 0.27, 0.065, 0.08, frame=C["stone_l"])
        # corner quoins
        for i in range(6):
            t = 0.05 + i * 0.062
            ln = 0.07 if i % 2 else 0.045
            for (a, c) in ((0, ln), (L - ln, L)):
                box(b, a - 0.002, -0.02, t, c + 0.002, 0.004, t + 0.055, C["stone_l"], bev=0.004)
    with xf(b, wall_m((L / 2, 0, 0), (1, 0, 0), W)):
        hx = [(W / 2 - 0.05, 0.07, W / 2 + 0.05, 0.17), (W / 2 - 0.05, 0.26, W / 2 + 0.05, 0.36),
              (W / 2 - 0.035, 0.44, W / 2 + 0.035, 0.53)]
        stones(b, W, H, STONE_COLS, t0=0.045, sw=0.12, sh=0.07, holes=hx)
        # gable masonry: a few courses of stones inside the triangle
        for k in range(3):
            t = H + k * 0.065
            half = (W / 2) * (zr - 0.02 - t - 0.065) / (zr - 0.02 - H)
            if half > 0.06:
                with xf(b, T(W / 2 - half, 0, 0)):
                    stones(b, 2 * half, t + 0.065, STONE_COLS, t0=t, sw=0.1, sh=0.065,
                           holes=[(half - 0.035 - (W / 2 - half) * 0, 0.44, half + 0.035, 0.53)] if False else
                           [(W / 2 - 0.035 - (W / 2 - half), 0.44, W / 2 + 0.035 - (W / 2 - half), 0.53)])
        box(b, 0, -0.016, 0.205, W, 0.004, 0.222, C["stone_l"], jit=0.04, cull=STONE_CULL)
        window(b, W / 2, 0.08, 0.065, 0.08, frame=C["stone_l"], shutters=C["slate_d"])
        window(b, W / 2, 0.27, 0.065, 0.08, frame=C["stone_l"])
        window(b, W / 2, 0.45, 0.045, 0.055, frame=C["stone_l"], cross=False, arch=True)
        for i in range(6):
            t = 0.05 + i * 0.062
            ln = 0.045 if i % 2 else 0.07
            for (a, c) in ((0, ln), (W - ln, W)):
                box(b, a - 0.002, -0.02, t, c + 0.002, 0.004, t + 0.055, C["stone_l"], bev=0.004)
    gable_roof(b, L, W, H - 0.005, zr, 0.05, 0.035, "slate")
    dormer(b, 0.0, W / 2 - 0.03, H + 0.02, 0.1, 0.1, 0.055)
    for sg in (-1, 1):
        chimney(b, sg * (L / 2 - 0.035), 0.0, 0.5, 0.84, 0.1, 0.12, pots=2)
    # low garden wall stub and a bench by the door
    for i in range(4):
        stone_pebble(b, (L / 2 + 0.05, -0.2 + i * 0.05, 0), 0.025, flat=1.0)
    box(b, -0.25, W / 2 + 0.03, 0.035, -0.12, W / 2 + 0.07, 0.045, C["wood"], bev=0.003)
    for x in (-0.24, -0.13):
        box(b, x - 0.006, W / 2 + 0.035, 0, x + 0.006, W / 2 + 0.065, 0.035, C["wood_d"])


def round_courses(b, cx, cy, R, z0, z1, n, courses, cols, rj=0.003, inner=None, skip=None):
    rng = b.rng
    hh = (z1 - z0) / courses
    for c in range(courses):
        off = (pi / n) * (c % 2)
        for k in range(n):
            a0 = off + 2 * pi * k / n
            a1 = a0 + 2 * pi / n
            if skip and skip((a0 + a1) / 2):
                continue
            ro = R + rng.uniform(-rj, rj)
            za, zb = z0 + c * hh, z0 + (c + 1) * hh
            pts = []
            for a in (a0, a1):
                for z in (za, zb):
                    pts.append((cx + ro * cos(a), cy + ro * sin(a), z))
                    if inner is not None:
                        pts.append((cx + inner * cos(a), cy + inner * sin(a), z))
                    else:
                        pts.append((cx + ro * 0.6 * cos(a), cy + ro * 0.6 * sin(a), z))
            solid(b, pts, pick(rng, cols), jit=0.08,
                  cull=(lambda nn, P: abs(nn.z) > 0.9 and (inner is None)) if c < courses - 1 else None)


def round_stack(b, cx, cy, R, z0, z1, n, courses, cols=None, rj=0.004):
    """Round tower body as stacked, slightly irregular stone courses."""
    rng = b.rng
    cols = cols or STONE_COLS
    hh = (z1 - z0) / courses
    for c in range(courses):
        r = R + rng.uniform(-rj, rj)
        last = c == courses - 1
        cyl(b, (cx, cy, z0 + c * hh), (cx, cy, z0 + (c + 1) * hh + 0.0005), r, r, n,
            lambda nn, P: pick(rng, cols), rot=(pi / n) * (c % 2) + rng.uniform(-0.05, 0.05), jit=0.08,
            cull=(None if last else (lambda nn, P: abs(nn.z) > 0.9)))


def battlement_ring(b, cx, cy, R, z, n, h=0.06, th=0.035, col=None):
    for k in range(n):
        a = 2 * pi * (k + 0.5) / n
        da = pi / n * 0.55
        pts = []
        for aa in (a - da, a + da):
            for r in (R - th, R):
                for zz in (z, z + h):
                    pts.append((cx + r * cos(aa), cy + r * sin(aa), zz))
        solid(b, pts, col or pick(b.rng, STONE_COLS), jit=0.08)


def m_round_fort(b):
    rng = b.rng
    Ro, Ri, H = 0.43, 0.37, 0.25
    gate = pi / 4
    half = radians(19)

    def in_gate(a):
        d = abs(((a - gate + pi) % (2 * pi)) - pi)
        return d < half

    round_courses(b, 0, 0, Ro, 0, H, 24, 3, STONE_COLS, inner=Ri, skip=in_gate)
    # merlons on the outer edge
    for k in range(24):
        a = 2 * pi * (k + 0.5) / 24
        if k % 2 or in_gate(a) or abs(((a - gate + pi) % (2 * pi)) - pi) < half + 0.15:
            continue
        da = pi / 24 * 0.8
        pts = []
        for aa in (a - da, a + da):
            for r in (Ro - 0.035, Ro + 0.003):
                for zz in (H, H + 0.06):
                    pts.append((r * cos(aa), r * sin(aa), zz))
        solid(b, pts, pick(rng, STONE_COLS), jit=0.08)
    # gate towers
    for sg in (-1, 1):
        a = gate + sg * (half + 0.06)
        tx, ty = (Ro - 0.02) * cos(a), (Ro - 0.02) * sin(a)
        round_stack(b, tx, ty, 0.07, 0, H + 0.1, 9, 4)
        cyl(b, (tx, ty, H + 0.1), (tx, ty, H + 0.12), 0.085, 0.085, 10, C["stone_l"])
        cone_roof(b, tx, ty, 0.09, H + 0.12, H + 0.25, 10, "slate", tiers=2)
    # gate arch block and doors
    with xf(b, wall_m(Vector((cos(gate), sin(gate), 0)) * (Ro - 0.01), (cos(gate), sin(gate), 0), 0.0)):
        box(b, -0.1, 0.0, H - 0.07, 0.1, 0.06, H, C["stone_l"], bev=0.004)
        box(b, -0.1, -0.003, H, 0.1, 0.06, H + 0.06, C["stone"], bev=0.004)
        for x in (-0.09, -0.045, 0.0, 0.045):
            box(b, x, -0.003, H, x + 0.028, 0.0, H + 0.085, C["stone"], bev=0.004)
        door(b, 0, 0.13, H - 0.075, t0=0.0, col=C["wood"], frame=C["stone_l"])
    # inner round tower
    tx, ty, tr = -0.08, -0.08, 0.155
    round_stack(b, tx, ty, tr, 0, 0.56, 12, 7)
    cyl(b, (tx, ty, 0.53), (tx, ty, 0.57), tr + 0.02, tr + 0.02, 12, C["stone_l"])
    battlement_ring(b, tx, ty, tr + 0.02, 0.57, 8, h=0.06)
    cone_roof(b, tx, ty, tr + 0.005, 0.57, 0.9, 12, "slate", tiers=3)
    with xf(b, wall_m((tx + tr * cos(gate) - 0.005, ty + tr * sin(gate) - 0.005, 0), (cos(gate), sin(gate), 0), 0.0)):
        door(b, 0, 0.075, 0.12, t0=0.0, arch=True, frame=C["stone_l"])
        for t in (0.25, 0.4):
            box(b, -0.011, -0.012, t, 0.011, 0.01, t + 0.06, C["dark"], jit=0.02)
            box(b, -0.022, -0.016, t - 0.012, 0.022, 0.006, t, C["stone_l"])
    # well and a haystack in the courtyard
    wx, wy = 0.2, -0.05
    round_courses(b, wx, wy, 0.045, 0, 0.05, 8, 2, STONE_COLS, inner=0.03)
    cyl(b, (wx, wy, 0.02), (wx, wy, 0.045), 0.03, 0.03, 8, C["water_d"])
    for sg in (-1, 1):
        box(b, wx - 0.006, wy + sg * 0.05 - 0.006, 0, wx + 0.006, wy + sg * 0.05 + 0.006, 0.13, C["wood"])
    gable_roof(b, 0.06, 0.12, 0.12, 0.17, 0.02, 0.02, "plank", axis="y", at=(wx, wy))
    blob(b, (0.0, 0.22, 0.0), (0.08, 0.07, 0.11), C["thatch_l"], sub=1, noise=0.08, zmin=0.0)


def square_tower(b, A, H, stone_faces=((1, 0, 0), (0, 1, 0)), holes=None, sw=0.13, sh=0.08,
                 batter=0.045, parapet=True, merlon_n=5, t0=0.11):
    rng = b.rng
    # battered plinth
    solid(b, [(sx * (A + batter), sy * (A + batter), 0) for sx in (-1, 1) for sy in (-1, 1)] +
          [(sx * A, sy * A, t0) for sx in (-1, 1) for sy in (-1, 1)],
          lambda n, P: pick(rng, [(C["stone_d"], 2), (C["stone"], 1)]), jit=0.07)
    box(b, -A, -A, 0, A, A, H, C["mortar"])
    for n in stone_faces:
        nv = Vector(n)
        with xf(b, wall_m(nv * A, n, 2 * A)):
            stones(b, 2 * A, H - 0.04, STONE_COLS, t0=t0 - 0.01, sw=sw, sh=sh,
                   holes=(holes or {}).get(tuple(n), ()))
    if parapet:
        Ap = A + 0.025
        box(b, -Ap, -Ap, H - 0.045, Ap, Ap, H, C["stone_l"], bev=0.005)
        for k in range(4):
            with xf(b, Rz(k * pi / 2)):
                box(b, Ap - 0.05, -Ap, H, Ap, Ap, H + 0.045, lambda n, P: pick(rng, STONE_COLS), jit=0.06)
                step = (2 * Ap) / merlon_n
                for i in range(merlon_n):
                    y0 = -Ap + i * step + step * 0.22
                    y1 = y0 + step * 0.56
                    box(b, Ap - 0.05, y0, H + 0.045, Ap, y1, H + 0.115, lambda n, P: pick(rng, STONE_COLS),
                        jit=0.08)
        m = 4
        d0 = -Ap + 0.05
        st = (2 * Ap - 0.1) / m
        for i in range(m):
            for j in range(m):
                x0, y0 = d0 + i * st, d0 + j * st
                b.face([(x0, y0, H + 0.004), (x0 + st, y0, H + 0.004), (x0 + st, y0 + st, H + 0.004),
                        (x0, y0 + st, H + 0.004)], pick(rng, [(C["stone_d"], 2), (C["stone"], 1)]),
                       jit=0.06, out=(0, 0, -5))


def arrow_slit(b, s, t, h=0.09):
    box(b, s - 0.011, -0.006, t, s + 0.011, 0.004, t + h, C["dark"], jit=0.02)
    box(b, s - 0.024, -0.016, t - 0.014, s + 0.024, 0.004, t, C["stone_l"], cull=STONE_CULL)
    box(b, s - 0.024, -0.016, t + h, s + 0.024, 0.004, t + h + 0.014, C["stone_l"], cull=STONE_CULL)


def m_keep_tower(b):
    rng = b.rng
    A, H = 0.33, 0.86
    bw = 0.125  # buttress half width (= castle wall half thickness)
    holes = {}
    for n in ((1, 0, 0), (0, 1, 0)):
        holes[n] = [(A - bw - 0.01, 0, A + bw + 0.01, 0.62),
                    (0.1 - 0.03, 0.33, 0.1 + 0.03, 0.46), (2 * A - 0.1 - 0.03, 0.33, 2 * A - 0.1 + 0.03, 0.46),
                    (0.1 - 0.03, 0.6, 0.1 + 0.03, 0.73), (2 * A - 0.1 - 0.03, 0.6, 2 * A - 0.1 + 0.03, 0.73)]
    holes[(1, 0, 0)].append((0.055, 0, 0.165, 0.24))
    square_tower(b, A, H, holes=holes, merlon_n=4, sw=0.12)
    # pilaster buttresses at mid-face reaching the cell edge, so castle walls join them
    BH = 0.5
    for k in range(4):
        R = Rz(k * pi / 2)
        with xf(b, R):
            box(b, A - 0.01, -bw, 0, 0.5, bw, BH, C["mortar"])
            solid(b, [(x, sy * bw, z) for sy in (-1, 1) for (x, z) in
                      ((A - 0.01, BH), (0.5, BH), (A - 0.01, 0.66), (0.5, BH + 0.03))],
                  lambda n, P: pick(rng, [(C["stone_l"], 2), (C["stone"], 1)]), jit=0.06)
            faces = [((1, 0, 0), (0.5, 0, 0), 2 * bw), ((0, 1, 0), ((A + 0.5) / 2, bw, 0), 0.5 - A + 0.01),
                     ((0, -1, 0), ((A + 0.5) / 2, -bw, 0), 0.5 - A + 0.01)]
            for (n, c, wd) in faces:
                wn = R.to_3x3() @ Vector(n)
                if wn.x + wn.y < 0.1:
                    continue
                with xf(b, wall_m(c, n, wd)):
                    stones(b, wd, BH, STONE_COLS, sw=0.11, sh=0.085)
    for n in ((1, 0, 0), (0, 1, 0)):
        with xf(b, wall_m(Vector(n) * A, n, 2 * A)):
            for s in (0.1, 2 * A - 0.1):
                arrow_slit(b, s, 0.35)
                arrow_slit(b, s, 0.62)
    with xf(b, wall_m(Vector((A, 0, 0)), (1, 0, 0), 2 * A)):
        door(b, 0.11, 0.08, 0.15, t0=0.06, arch=True, frame=C["stone_l"], frame_w=0.018)
        for i in range(3):
            box(b, 0.055, -0.02 - 0.025 * (2 - i), 0, 0.165, 0.0, 0.02 + i * 0.02, C["stone_l"], bev=0.004)
    # stair turret on the back corner
    tx, ty = -A + 0.02, -A + 0.02
    round_stack(b, tx, ty, 0.095, H - 0.2, H + 0.14, 10, 4)
    cyl(b, (tx, ty, H + 0.13), (tx, ty, H + 0.16), 0.11, 0.11, 10, C["stone_l"])
    cone_roof(b, tx, ty, 0.115, H + 0.16, H + 0.28, 10, "slate", tiers=2)


def m_castle_center(b):
    rng = b.rng
    A, H = 0.37, 1.0
    tr = 0.1
    ds = A  # door at the centre of the +X face
    holes = {(1, 0, 0): [(ds - 0.1, 0, ds + 0.1, 0.32), (ds - 0.06, 0.48, ds + 0.06, 0.66),
                         (0, 0, 0.1, H), (2 * A - 0.1, 0, 2 * A, H)],
             (0, 1, 0): [(0, 0, 0.1, H), (2 * A - 0.1, 0, 2 * A, H)] +
                        [(s - 0.06, t, s + 0.06, t + 0.17) for s in (A - 0.17, A + 0.17) for t in (0.3, 0.55)]}
    square_tower(b, A, H, holes=holes, merlon_n=5, batter=0.05, sw=0.15, sh=0.095)
    with xf(b, wall_m(Vector((A, 0, 0)), (1, 0, 0), 2 * A)):
        door(b, ds, 0.13, 0.2, t0=0.07, arch=True, frame=C["stone_l"], frame_w=0.024)
        window(b, ds, 0.5, 0.07, 0.1, frame=C["stone_l"], arch=True)
        for i in range(3):
            box(b, ds - 0.11 + i * 0.012, -0.03 - 0.03 * (2 - i), 0, ds + 0.11 - i * 0.012, 0.0, 0.025 + i * 0.023,
                C["stone_l"], bev=0.004)
    with xf(b, wall_m(Vector((0, A, 0)), (0, 1, 0), 2 * A)):
        for s in (A - 0.17, A + 0.17):
            window(b, s, 0.32, 0.06, 0.1, frame=C["stone_l"], arch=True)
            window(b, s, 0.57, 0.06, 0.1, frame=C["stone_l"], arch=True)
    # corner turrets
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * (A - 0.02), sy * (A - 0.02)
            round_stack(b, cx, cy, tr, 0, H + 0.14, 10, 7)
            cyl(b, (cx, cy, H + 0.11), (cx, cy, H + 0.15), tr + 0.02, tr + 0.02, 12, C["stone_l"])
            battlement_ring(b, cx, cy, tr + 0.02, H + 0.15, 6, h=0.05, th=0.03)
            cone_roof(b, cx, cy, tr + 0.005, H + 0.15, H + 0.55, 10, "slate", tiers=3)
    # central hall roof rising above the battlements
    cone_roof(b, 0, 0, 0.37, H - 0.02, H + 0.42, 4, "slate", tiers=3)
    chimney(b, -0.12, -0.12, H + 0.1, H + 0.3, 0.06, 0.06, pots=1)


def m_castle_wall(b, axis):
    rng = b.rng
    X, Y, H = 0.505, 0.125, 0.45
    with xf(b, Matrix.Identity(4) if axis == "x" else Rz(pi / 2)):
        solid(b, [(sx * X, sy * (Y + 0.04), 0) for sx in (-1, 1) for sy in (-1, 1)] +
              [(sx * X, sy * Y, 0.1) for sx in (-1, 1) for sy in (-1, 1)],
              lambda n, P: pick(rng, [(C["stone_d"], 2), (C["stone"], 1)]), jit=0.07)
        box(b, -X, -Y, 0, X, Y, H, C["mortar"])
        for sg in (-1, 1):
            with xf(b, wall_m((0, sg * Y, 0), (0, sg, 0), 2 * X)):
                stones(b, 2 * X, H, STONE_COLS, t0=0.095, sw=0.13, sh=0.08,
                       holes=[(X + p - 0.03, 0.2, X + p + 0.03, 0.33) for p in (-0.3, 0.3)])
                for p in (-0.3, 0.3):
                    arrow_slit(b, X + p, 0.22, 0.08)
            # parapet + merlons
            box(b, -X, sg * Y, H - 0.04, X, sg * (Y + 0.02), H, C["stone_l"], bev=0.004)
            box(b, -X, sg * (Y + 0.02), H, X, sg * (Y - 0.035), H + 0.045,
                lambda n, P: pick(rng, STONE_COLS), jit=0.06)
            for x in (-0.4, -0.2, 0.0, 0.2, 0.4):
                box(b, x - 0.05, sg * (Y + 0.02), H + 0.045, x + 0.05, sg * (Y - 0.035), H + 0.12,
                    lambda n, P: pick(rng, STONE_COLS), bev=0.004, jit=0.08)
        box(b, -X, -Y + 0.03, H - 0.01, X, Y - 0.03, H + 0.004, C["stone_d"], jit=0.04)


# --------------------------------------------------------------------------
# Magnets
# --------------------------------------------------------------------------
def chamfer_prism(b, outline, th, ch, col, jit=None, mat=PAINT):
    """Convex 2D outline in the local XZ plane, extruded along Y with chamfer."""
    cx = sum(p[0] for p in outline) / len(outline)
    cz = sum(p[1] for p in outline) / len(outline)
    pts = []
    for (x, z) in outline:
        dx, dz = x - cx, z - cz
        d = math.hypot(dx, dz) or 1
        ix, iz = x - dx / d * ch, z - dz / d * ch
        for sy in (-1, 1):
            pts.append((x, sy * (th / 2 - ch), z))
            pts.append((ix, sy * th / 2, iz))
    solid(b, pts, col, mat=mat, jit=jit)


def m_ankh(b):
    rng = b.rng
    gold = lambda n, P: pick(rng, [(C["gold"], 4), (C["gold_l"], 2), (C["gold_d"], 1)])
    # stepped plinth
    box(b, -0.21, -0.21, 0, 0.21, 0.21, 0.06, lambda n, P: pick(rng, STONE_COLS), bev=0.012)
    box(b, -0.155, -0.155, 0.06, 0.155, 0.155, 0.115, C["stone_l"], bev=0.01)
    box(b, -0.165, -0.165, 0.098, 0.165, 0.165, 0.108, C["gold_d"], bev=0.003)
    for k in range(4):  # little offering bowls / flowers at the plinth corners
        a = pi / 4 + k * pi / 2
        x, y = 0.18 * cos(a), 0.18 * sin(a)
        cyl(b, (x, y, 0.06), (x, y, 0.08), 0.014, 0.02, 7, C["gold_d"])
        blob(b, (x, y, 0.085), 0.012, C["fruit"] if k % 2 else C["white"], sub=0)
    with xf(b, Rz(radians(135))):
        th = 0.06
        z0 = 0.115
        chamfer_prism(b, [(-0.055, z0), (0.055, z0), (0.036, 0.44), (-0.036, 0.44)], th, 0.012, gold)
        chamfer_prism(b, [(-0.06, 0.42), (0.06, 0.42), (0.06, 0.49), (-0.06, 0.49)], th, 0.012, gold)
        for sg in (-1, 1):
            chamfer_prism(b, [(sg * 0.05, 0.425), (sg * 0.17, 0.405), (sg * 0.17, 0.505), (sg * 0.05, 0.485)],
                          th, 0.012, gold)
        # oval loop
        zc, ax, az, wdt = 0.6, 0.085, 0.115, 0.05
        n = 18
        for k in range(n):
            a0, a1 = 2 * pi * k / n - pi / 2, 2 * pi * (k + 1) / n - pi / 2
            pts = []
            for a in (a0, a1):
                for (dr, dy) in ((-wdt / 2, th / 2 - 0.012), (-wdt / 2 + 0.012, th / 2),
                                 (wdt / 2 - 0.012, th / 2), (wdt / 2, th / 2 - 0.012)):
                    for sy in (-1, 1):
                        pts.append(((ax + dr) * cos(a), sy * dy, zc + (az + dr) * sin(a)))
            solid(b, pts, gold, jit=0.05)
        # gem on the crossing
        blob(b, (0, -th / 2 - 0.004, 0.455), (0.022, 0.012, 0.022), C["gem"], sub=0, noise=0.0)


def m_skull(b):
    rng = b.rng
    rockc = [(C["rock_d"], 3), ("#5d5852", 2), (C["rock"], 1)]
    # rough plinth
    pts = []
    for k in range(10):
        a = 2 * pi * k / 10 + rng.uniform(-0.15, 0.15)
        r = 0.2 * rng.uniform(0.92, 1.06)
        pts.append((r * cos(a), r * sin(a), 0))
        pts.append((r * 0.85 * cos(a), r * 0.85 * sin(a), 0.07 + rng.uniform(-0.01, 0.01)))
    solid(b, pts, lambda n, P: pick(rng, rockc), jit=0.1)
    for k in range(5):
        a = 2 * pi * k / 5 + 0.3
        stone_pebble(b, (0.2 * cos(a), 0.2 * sin(a), 0), 0.04, cols=rockc, flat=1.0)
    # spikes around
    for k in range(6):
        a = 2 * pi * k / 6 + 0.2
        cyl(b, (0.15 * cos(a), 0.15 * sin(a), 0.05), (0.19 * cos(a), 0.19 * sin(a), 0.15), 0.014, 0, 5,
            C["horn_d"])
    with xf(b, Rz(pi / 4)):
        cyl(b, (0, 0, 0.06), (0, 0, 0.47), 0.035, 0.03, 6, C["wood_d"])
        # crossed bones lashed to the post
        for sg in (-1, 1):
            p0, p1 = Vector((0.04, -0.12 * sg, 0.24)), Vector((0.04, 0.12 * sg, 0.36))
            cyl(b, p0, p1, 0.012, 0.012, 5, C["bone"])
            for p in (p0, p1):
                blob(b, p, 0.02, C["bone_d"], sub=0)
        box(b, 0.025, -0.04, 0.29, 0.05, 0.04, 0.31, C["hide_d"])
        # skull
        zc = 0.55
        blob(b, (-0.01, 0, zc), (0.105, 0.095, 0.095), C["bone"], sub=1, noise=0.04)
        box(b, 0.02, -0.07, 0.43, 0.115, 0.07, 0.52, C["bone"], bev=0.02)
        box(b, -0.02, -0.075, 0.47, 0.06, 0.075, 0.53, C["bone_d"], bev=0.015)  # cheekbones
        for sg in (-1, 1):
            # eye sockets with glowing embers
            box(b, 0.075, sg * 0.04 - 0.027, 0.5, 0.108, sg * 0.04 + 0.027, 0.555, C["dark"], bev=0.008, jit=0.02)
            blob(b, (0.105, sg * 0.04, 0.522), (0.006, 0.012, 0.012), "#ff3a1a", sub=0, noise=0.0, mat=GLOW)
        solid(b, [(0.112, -0.016, 0.495), (0.112, 0.016, 0.495), (0.112, 0, 0.462),
                  (0.098, -0.016, 0.495), (0.098, 0.016, 0.495), (0.098, 0, 0.462)], C["dark"], jit=0.02)
        for i in range(6):  # upper teeth
            y = -0.05 + i * 0.02
            box(b, 0.108, y - 0.008, 0.425, 0.12, y + 0.008, 0.447, C["white"], jit=0.05)
        box(b, 0.0, -0.065, 0.385, 0.105, 0.065, 0.42, C["bone_d"], bev=0.012)  # jaw
        for i in range(5):
            y = -0.04 + i * 0.02
            box(b, 0.096, y - 0.008, 0.415, 0.108, y + 0.008, 0.432, C["white"], jit=0.05)
        # curling horns
        for sg in (-1, 1):
            path = [(0.0, 0.08, 0.6), (-0.02, 0.15, 0.63), (-0.01, 0.21, 0.68), (0.02, 0.24, 0.75),
                    (0.04, 0.235, 0.82), (0.05, 0.21, 0.87), (0.055, 0.185, 0.89)]
            path = [(x, sg * y, z) for (x, y, z) in path]
            radii = [0.04, 0.036, 0.03, 0.024, 0.017, 0.01, 0.003]
            for i in range(len(path) - 1):
                col = mix(C["horn"], C["horn_d"], i / (len(path) - 2))
                cyl(b, path[i], path[i + 1], radii[i], radii[i + 1], 7, col, jit=0.05)


# --------------------------------------------------------------------------
# Rocks and scenery
# --------------------------------------------------------------------------
def boulder(b, M, r, npts=24, moss=0.55, cols=None):
    rng = b.rng
    cols = cols or [(C["rock"], 4), (C["rock_l"], 2), (C["rock_d"], 2)]
    pts = []
    for i in range(npts):
        v = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0.35, 1))).normalized()
        k = rng.uniform(0.82, 1.08)
        p = M @ Vector((v.x * r[0] * k, v.y * r[1] * k, max(-0.3, v.z) * r[2] * k))
        p.z = max(0.0, p.z)
        pts.append(p)
    for k in range(6):
        a = 2 * pi * k / 6 + rng.uniform(0, 0.5)
        p = M @ Vector((cos(a) * r[0] * 0.9, sin(a) * r[1] * 0.9, 0))
        p.z = max(0.0, p.z)
        pts.append(p)

    def col(n, P):
        top = max(p.z for p in P)
        if moss and n.z > moss and rng.random() < 0.85:
            return pick(rng, [(C["moss"], 3), (C["moss_d"], 2)])
        return pick(rng, cols)

    solid(b, pts, col, jit=0.08)


def m_rock_a(b):
    boulder(b, T(-0.02, -0.06, 0) @ Rz(0.4), (0.3, 0.26, 0.46), npts=40)
    boulder(b, T(0.24, 0.17, 0) @ Rz(1.2), (0.16, 0.13, 0.22), npts=30)
    boulder(b, T(-0.24, 0.2, 0) @ Rz(0.3), (0.14, 0.12, 0.15), npts=26)
    boulder(b, T(0.28, -0.24, 0), (0.1, 0.09, 0.09), moss=0, npts=20)
    boulder(b, T(0.02, 0.3, 0), (0.08, 0.07, 0.07), moss=0, npts=18)
    stone_pebble(b, (-0.33, -0.2, 0), 0.04)
    for (x, y) in ((0.14, 0.36), (-0.1, 0.33), (0.37, 0.0), (-0.36, 0.0)):
        tuft(b, x, y, 0.06)


def m_rock_b(b):
    for (x, y, rz, r, tilt) in ((-0.12, -0.1, 0.6, (0.34, 0.12, 0.38), 0.25),
                                (0.12, 0.1, 0.55, (0.3, 0.11, 0.3), 0.3),
                                (0.27, -0.2, 0.7, (0.15, 0.08, 0.18), 0.2),
                                (-0.28, 0.22, 0.5, (0.12, 0.07, 0.13), 0.2)):
        boulder(b, T(x, y, 0) @ Rz(rz) @ Rx(tilt), r, npts=32, moss=0.6)
    stone_pebble(b, (-0.05, 0.33, 0), 0.05)
    stone_pebble(b, (0.33, 0.12, 0), 0.04)
    for (x, y) in ((0.05, 0.25), (-0.36, -0.05), (0.36, 0.25)):
        tuft(b, x, y, 0.06)


def m_rock_c(b):
    for (x, y, r) in ((-0.2, -0.17, (0.16, 0.14, 0.26)), (0.1, -0.22, (0.14, 0.14, 0.2)),
                      (-0.22, 0.16, (0.14, 0.12, 0.18)), (0.18, 0.14, (0.17, 0.15, 0.24)),
                      (-0.02, -0.0, (0.11, 0.11, 0.14)), (0.32, -0.08, (0.08, 0.08, 0.09)),
                      (-0.04, 0.32, (0.08, 0.07, 0.08))):
        boulder(b, T(x, y, 0) @ Rz(b.rng.uniform(0, 3)), r, npts=24, moss=0.5)
    for (x, y) in ((0.08, 0.36), (-0.36, 0.0), (0.33, 0.33)):
        tuft(b, x, y, 0.055)


def shore_boulders(b, parts):
    """Granite boulders for the surf: weathered grey, darker where the sea wets
    them, a little moss and lichen on top. Built standing on z = 0; the game sinks
    them part-way into the water."""
    rng = b.rng
    dry = [("#9a9387", 4), ("#aca598", 2), ("#857e73", 2), ("#b3a27a", 1)]
    wet = [("#5f5a54", 3), ("#4c4843", 2)]
    for (x, y, r) in parts:
        M = T(x, y, 0) @ Rz(rng.uniform(0, 3))
        pts = []
        for i in range(30):
            v = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0.2, 1))).normalized()
            k = rng.uniform(0.85, 1.08)
            p = M @ Vector((v.x * r[0] * k, v.y * r[1] * k, max(-0.2, v.z) * r[2] * k))
            p.z = max(0.0, p.z)
            pts.append(p)

        def col(n, P, r=r):
            z = sum(p.z for p in P) / len(P)
            if z < r[2] * 0.3:
                return pick(rng, wet)
            if n.z > 0.75 and rng.random() < 0.3:
                return pick(rng, [(C["moss"], 2), ("#b9a35a", 1)])
            return pick(rng, dry)

        solid(b, pts, col, jit=0.07)


def m_shore_rock_a(b):
    shore_boulders(b, [(0.0, 0.0, (0.26, 0.21, 0.24)), (0.2, 0.13, (0.12, 0.1, 0.11))])


def m_shore_rock_b(b):
    shore_boulders(b, [(-0.08, -0.05, (0.2, 0.16, 0.3)), (0.13, 0.08, (0.16, 0.14, 0.18)),
                       (-0.05, 0.2, (0.09, 0.08, 0.08))])


def m_shore_rock_c(b):
    shore_boulders(b, [(0.0, 0.0, (0.32, 0.18, 0.17)), (-0.22, -0.12, (0.1, 0.09, 0.1)),
                       (0.2, -0.16, (0.08, 0.08, 0.07))])


def conifer_tier(b, zb, zt, R, n=8, cols=None):
    rng = b.rng
    rot = rng.uniform(0, 2 * pi)
    rim, mid = [], []
    for k in range(2 * n):
        a = rot + pi * k / n
        rr = R * (1.0 if k % 2 == 0 else 0.74) * rng.uniform(0.93, 1.07)
        z = zb + (0.0 if k % 2 == 0 else 0.03) + rng.uniform(-0.008, 0.008)
        rim.append(Vector((rr * cos(a), rr * sin(a), z)))
        mr = rr * 0.6
        mid.append(Vector((mr * cos(a + pi / (2 * n)), mr * sin(a + pi / (2 * n)), zb + (zt - zb) * 0.42)))
    apex = Vector((0, 0, zt))
    cen = Vector((0, 0, zb + 0.04))
    low = Vector((0, 0, zb - 0.2))
    for k in range(2 * n):
        k1 = (k + 1) % (2 * n)
        b.face([rim[k], rim[k1], mid[k]], C["conifer"], out=low)
        b.face([rim[k1], mid[k1], mid[k]], C["conifer"], out=low)
        b.face([mid[k], mid[k1], apex], C["conifer_l"], out=low)
        b.face([rim[k1], rim[k], cen], C["conifer_d"], out=Vector((0, 0, zt + 1)))


def m_pine(b):
    cyl(b, (0, 0, 0), (0, 0, 0.3), 0.04, 0.026, 6, C["trunk"])
    for (x, y) in ((0.05, 0.02), (-0.03, 0.045)):
        cyl(b, (0, 0, 0.0), (x, y, 0.0), 0.02, 0.012, 4, C["trunk_d"])
    for (zb, zt, R) in ((0.12, 0.42, 0.27), (0.28, 0.58, 0.22), (0.43, 0.73, 0.165), (0.58, 0.92, 0.11)):
        conifer_tier(b, zb, zt, R)


def leafy(b, c, r, cols=None, sub=1):
    rng = b.rng

    def col(n, P):
        t = 0.5 + 0.5 * n.z + 0.15 * (n.x + n.y) * 0.5
        base = mix(C["leaf_d"], C["leaf_l"], t)
        return base if rng.random() < 0.8 else C["leaf"]

    blob(b, c, r, col, sub=sub, noise=0.14, jit=0.07)


# --------------------------------------------------------------------------
# Scenery: shrubs and small groves dotted over the grass. Only decoration; the
# game's own trees are the larger models above.
# --------------------------------------------------------------------------
SHRUB = [("#3f6e26", 3), ("#4f8030", 3), ("#2f5a22", 2), ("#64933a", 1)]


def bush(b, c, r):
    """A low rounded bush, darker than the grass, lit on top."""
    rng = b.rng

    def col(n, P):
        if n.z > 0.6 and rng.random() < 0.5:
            return pick(rng, [("#64933a", 2), ("#76a442", 1)])
        return pick(rng, SHRUB)

    blob(b, c, r, col, sub=2, noise=0.1, jit=0.08, zmin=0.0)


def flowers_on(b, c, r, n, cols):
    rng = b.rng
    for i in range(n):
        a = rng.uniform(0, 2 * pi)
        e = rng.uniform(0.15, 1.2)
        d = Vector((cos(a) * cos(e), sin(a) * cos(e), sin(e)))
        p = Vector(c) + Vector((d.x * r[0], d.y * r[1], d.z * r[2])) * 0.97
        blob(b, p, 0.014, pick(rng, cols), sub=0, noise=0.05, jit=0.05)


def small_pine(b, x, y, s):
    with xf(b, T(x, y, 0) @ S(s) @ Rz(b.rng.uniform(0, 2 * pi))):
        cyl(b, (0, 0, 0), (0, 0, 0.3), 0.045, 0.028, 6, C["trunk"])
        for (zb, zt, R) in ((0.1, 0.42, 0.27), (0.28, 0.6, 0.21), (0.45, 0.78, 0.15), (0.62, 0.98, 0.09)):
            conifer_tier(b, zb, zt, R, n=6)


def small_round_tree(b, x, y, s):
    with xf(b, T(x, y, 0) @ S(s)):
        tube(b, [(0, 0, 0), (0.01, 0.0, 0.2), (0.0, 0.01, 0.32)], [0.045, 0.032, 0.025], 6, C["trunk"])
        for (c, r) in (((0.0, 0.0, 0.48), 0.2), ((0.1, 0.05, 0.4), 0.14), ((-0.09, 0.07, 0.42), 0.14),
                       ((0.0, -0.08, 0.6), 0.13)):
            leafy(b, c, (r, r, r * 0.9))


def m_shrub_a(b):
    bush(b, (0, 0, 0.02), (0.13, 0.11, 0.11))
    bush(b, (0.12, 0.07, 0.01), (0.08, 0.07, 0.07))
    tuft(b, -0.13, 0.06, 0.06)


def m_shrub_b(b):
    for (c, r) in (((-0.05, 0.0, 0.02), (0.11, 0.1, 0.1)), ((0.09, -0.04, 0.02), (0.09, 0.08, 0.08))):
        bush(b, c, r)
        flowers_on(b, c, r, 9, [("#f4f1e4", 3), ("#f2d856", 2), ("#c9a0dc", 1)])


def m_shrub_c(b):
    for (x, y, r) in ((-0.13, -0.05, 0.08), (0.0, 0.0, 0.1), (0.13, 0.06, 0.08), (0.04, -0.12, 0.06)):
        bush(b, (x, y, 0.01), (r, r * 0.9, r * 0.85))
    tuft(b, 0.15, -0.1, 0.05)


def m_grove_a(b):
    small_pine(b, -0.12, -0.06, 0.55)
    small_pine(b, 0.1, -0.12, 0.45)
    small_pine(b, 0.02, 0.13, 0.4)
    bush(b, (0.17, 0.09, 0.01), (0.08, 0.07, 0.07))


def m_grove_b(b):
    small_round_tree(b, -0.08, 0.04, 0.6)
    small_pine(b, 0.13, -0.08, 0.5)
    bush(b, (0.06, 0.16, 0.01), (0.08, 0.07, 0.06))
    bush(b, (-0.17, -0.12, 0.01), (0.07, 0.06, 0.06))


def m_grove_c(b):
    for (x, y, s) in ((-0.1, -0.1, 0.5), (0.1, -0.06, 0.42), (-0.05, 0.11, 0.46), (0.15, 0.13, 0.36)):
        small_pine(b, x, y, s)


def m_fruit_tree(b):
    rng = b.rng
    tube(b, [(0, 0, 0), (0.01, 0.005, 0.18), (0.0, 0.01, 0.34)], [0.05, 0.035, 0.028], 7, C["trunk"])
    cyl(b, (0.005, 0.005, 0.2), (0.11, 0.05, 0.4), 0.02, 0.012, 5, C["trunk"])
    cyl(b, (0.005, 0.005, 0.22), (-0.09, 0.07, 0.4), 0.02, 0.012, 5, C["trunk"])
    blobs = [((0.0, 0.0, 0.56), 0.21), ((0.11, 0.07, 0.48), 0.15), ((-0.1, 0.08, 0.5), 0.15),
             ((0.05, -0.11, 0.5), 0.15), ((-0.02, 0.02, 0.69), 0.14)]
    for (c, r) in blobs:
        leafy(b, c, (r, r, r * 0.9))
    # fruit on the camera-facing side
    for i in range(10):
        c, r = blobs[i % len(blobs)]
        a = rng.uniform(-0.3, 1.9)
        e = rng.uniform(-0.5, 0.5)
        d = Vector((cos(a) * cos(e), sin(a) * cos(e), sin(e)))
        p = Vector(c) + d * r * 0.98
        blob(b, p, 0.022, C["fruit"], sub=0, noise=0.05, jit=0.08)


def branchy(b, p, d, length, r, depth, col, spread=0.6, up=0.35, kids=2, n=5):
    rng = b.rng
    p, d = Vector(p), Vector(d).normalized()
    end = p + d * length
    cyl(b, p, end, r, r * 0.68, n, col, rot=rng.uniform(0, 2))
    if depth == 0:
        return
    for i in range(kids + (1 if rng.random() < 0.3 else 0)):
        az = rng.uniform(0, 2 * pi)
        ang = rng.uniform(spread * 0.6, spread)
        perp = d.cross(Vector((cos(az), sin(az), 0.2))).normalized()
        nd = (d * cos(ang) + perp * sin(ang) + Vector((0, 0, up))).normalized()
        branchy(b, end, nd, length * rng.uniform(0.6, 0.78), r * 0.68, depth - 1, col, spread, up, kids, max(3, n - 1))


def m_dead_tree(b):
    col = lambda n, P: pick(b.rng, [(C["bark_grey"], 3), ("#a59a8c", 1), ("#7d746a", 1)])
    tube(b, [(0, 0, 0), (0.015, 0.0, 0.2), (-0.01, 0.01, 0.4)], [0.07, 0.05, 0.034], 7, col)
    branchy(b, (-0.01, 0.01, 0.39), (0.1, 0.0, 1), 0.16, 0.03, 3, col, spread=0.6, up=0.35)
    branchy(b, (0.01, 0.0, 0.22), (1, 0.5, 0.7), 0.15, 0.026, 2, col, spread=0.55, up=0.35)
    branchy(b, (0.0, 0.0, 0.3), (-0.8, -0.6, 0.8), 0.15, 0.024, 2, col, spread=0.55, up=0.35)
    branchy(b, (0.0, 0.0, 0.27), (-0.5, 0.9, 0.6), 0.12, 0.02, 2, col, spread=0.5, up=0.35)
    for k in range(4):
        a = 2 * pi * k / 4 + 0.5
        cyl(b, (0, 0, 0.03), (0.09 * cos(a), 0.09 * sin(a), 0.0), 0.022, 0.008, 4, col)


def m_bare_tree(b):
    col = lambda n, P: pick(b.rng, [(C["bark_dark"], 3), (C["trunk_d"], 2), (C["trunk"], 1)])
    tube(b, [(0, 0, 0), (0.03, -0.01, 0.12), (-0.01, 0.02, 0.24), (0.02, 0.0, 0.3)],
         [0.075, 0.06, 0.05, 0.045], 7, col)
    for (d, L) in (((1, 0.3, 0.45), 0.2), ((-0.7, 0.8, 0.5), 0.19), ((-0.4, -1, 0.45), 0.18),
                   ((0.6, -0.7, 0.6), 0.16)):
        branchy(b, (0.02, 0.0, 0.29), d, L, 0.035, 3, col, spread=0.7, up=0.18, kids=2, n=5)
    for k in range(5):
        a = 2 * pi * k / 5 + 0.2
        cyl(b, (0, 0, 0.05), (0.13 * cos(a), 0.13 * sin(a), 0.0), 0.03, 0.01, 5, col)


def m_palm(b):
    rng = b.rng
    n = 8
    pts = [Vector((0.09 * (i / n) ** 2, -0.07 * (i / n) ** 2, 0.76 * i / n)) for i in range(n + 1)]
    for i in range(n):
        r0 = 0.048 - 0.018 * i / n
        r1 = 0.048 - 0.018 * (i + 1) / n
        cyl(b, pts[i], pts[i + 1] + (pts[i + 1] - pts[i]) * 0.05, r0 * 1.12, r1 * 0.92, 7,
            lambda nn, P: pick(rng, [(C["palm_trunk"], 3), (C["palm_trunk_d"], 1)]), rot=i * 0.4, jit=0.07)
    top = pts[-1]
    for k in range(3):
        a = 2 * pi * k / 3 + 0.4
        blob(b, top + Vector((0.035 * cos(a), 0.035 * sin(a), -0.03)), 0.026, C["coconut"], sub=0, noise=0.05)
    nf = 8
    for f in range(nf):
        a = 2 * pi * f / nf + rng.uniform(-0.15, 0.15)
        dirh = Vector((cos(a), sin(a), 0))
        side = Vector((-sin(a), cos(a), 0))
        length = rng.uniform(0.3, 0.37)
        rise = rng.uniform(0.1, 0.16)
        seg = 6
        spine = []
        for j in range(seg + 1):
            u = j / seg
            spine.append(top + dirh * (length * u) + Vector((0, 0, rise * u * 1.4 - (rise + 0.2) * u * u)))
        for j in range(seg):
            u0, u1 = j / seg, (j + 1) / seg
            w0 = 0.06 * sin(pi * (0.12 + 0.88 * u0)) * (1.0 if j % 2 == 0 else 0.75)
            w1 = 0.06 * sin(pi * (0.12 + 0.88 * u1)) * (0.75 if j % 2 == 0 else 1.0)
            s0, s1 = spine[j], spine[j + 1]
            droop = Vector((0, 0, -0.022))
            col = mix(C["palm"], C["palm_l"], u0 * 0.8)
            for sg in (-1, 1):
                q = [s0, s1, s1 + side * sg * w1 + droop, s0 + side * sg * w0 + droop]
                b.face(q, col, out=(s0 + s1) / 2 + Vector((0, 0, -0.5)))
                b.face(list(reversed([v + Vector((0, 0, -0.003)) for v in q])), C["palm"],
                       out=(s0 + s1) / 2 + Vector((0, 0, 0.5)))


def ribbed_column(b, base, height, r, n=8, dome=True, col=None):
    """Saguaro-style ribbed column standing on `base` (vertical)."""
    rng = b.rng
    base = Vector(base)
    col = col or (lambda nn, P: pick(rng, [(C["cactus"], 3), (C["cactus_l"], 1), (C["cactus_d"], 1)]))
    levels = [(0.0, 1.0), (height * 0.5, 1.0), (height * 0.86, 0.98)]
    if dome:
        levels += [(height * 0.94, 0.78), (height * 0.985, 0.42)]
    rings = []
    for (z, k) in levels:
        ring = []
        for i in range(2 * n):
            a = pi * i / n
            rr = r * k * (1.0 if i % 2 == 0 else 0.8)
            ring.append(base + Vector((rr * cos(a), rr * sin(a), z)))
        rings.append(ring)
    apex = base + Vector((0, 0, height))
    for li in range(len(rings) - 1):
        ra, rb = rings[li], rings[li + 1]
        for i in range(2 * n):
            i1 = (i + 1) % (2 * n)
            ref = base + Vector((0, 0, (ra[i].z + rb[i].z) / 2 - base.z))
            b.face([ra[i], ra[i1], rb[i1], rb[i]], col, out=ref)
    if dome:
        last = rings[-1]
        for i in range(2 * n):
            i1 = (i + 1) % (2 * n)
            b.face([last[i], last[i1], apex], col, out=base + Vector((0, 0, height * 0.8)))


def m_cactus(b):
    rng = b.rng
    ribbed_column(b, (0, 0, 0), 0.66, 0.068)
    for (d, z0, out, h, r) in (((-0.7071, 0.7071), 0.2, 0.15, 0.25, 0.046),
                               ((0.7071, -0.7071), 0.3, 0.13, 0.2, 0.042)):
        dv = Vector((d[0], d[1], 0))
        p0 = Vector((0, 0, z0)) + dv * 0.03
        p1 = Vector((0, 0, z0 + 0.01)) + dv * (out - 0.03)
        p2 = Vector((0, 0, z0 + 0.06)) + dv * out
        col = lambda nn, P: pick(rng, [(C["cactus"], 3), (C["cactus_d"], 1)])
        cyl(b, p0, p1, r, r, 8, col)
        cyl(b, p1, p2, r, r, 8, col)
        ribbed_column(b, p2 - Vector((0, 0, 0.02)), h, r, n=7)
    for k in range(3):
        a = 2 * pi * k / 3
        blob(b, (0.03 * cos(a), 0.03 * sin(a), 0.655), 0.014, "#f07aa0" if k else "#f6e070", sub=0)
    for k in range(4):
        a = 2 * pi * k / 4 + 0.6
        stone_pebble(b, (0.17 * cos(a), 0.17 * sin(a), 0), 0.03,
                     cols=[(C["sand_d"], 2), (C["rock_l"], 1)])


def m_dead_bush(b):
    rng = b.rng
    blob(b, (0, 0, 0), (0.08, 0.07, 0.025), C["sand_d"], sub=0, zmin=0.0)
    col = lambda n, P: pick(rng, [(C["twig"], 3), (C["dry_d"], 1)])
    for i in range(10):
        a = 2 * pi * i / 10 + rng.uniform(-0.2, 0.2)
        el = rng.uniform(0.45, 1.05)
        d = Vector((cos(a) * cos(el), sin(a) * cos(el), sin(el)))
        branchy(b, (0, 0, 0.01), d, rng.uniform(0.09, 0.13), 0.009, 1, col, spread=0.7, up=0.25, kids=2, n=4)
    for i in range(6):
        a = rng.uniform(0, 2 * pi)
        r = rng.uniform(0.06, 0.13)
        blob(b, (r * cos(a), r * sin(a), rng.uniform(0.07, 0.15)), (0.035, 0.035, 0.022),
             lambda n, P: pick(rng, [(C["dry"], 3), ("#b59a5c", 1), (C["dry_d"], 1)]), sub=0, noise=0.2)


def m_pebbles(b):
    rng = b.rng
    spots = [(-0.25, -0.2), (-0.05, -0.28), (0.2, -0.22), (0.3, 0.02), (0.08, 0.05), (-0.18, 0.06),
             (-0.3, 0.27), (-0.02, 0.27), (0.24, 0.28), (0.14, -0.05), (-0.1, -0.1)]
    cols = [(C["rock"], 3), (C["rock_l"], 2), (C["sand_d"], 1), (C["rock_d"], 1)]
    for (x, y) in spots:
        stone_pebble(b, (x + rng.uniform(-0.04, 0.04), y + rng.uniform(-0.04, 0.04), 0),
                     rng.uniform(0.03, 0.065), cols=cols, flat=rng.uniform(0.5, 0.8), n=12)
    for (x, y) in ((0.0, -0.15), (0.32, 0.18), (-0.3, -0.05)):
        tuft(b, x, y, 0.05, n=4)


def m_swamp(b):
    rng = b.rng
    n = 24
    rad = []
    for k in range(n):
        a = 2 * pi * k / n
        r = 0.39 + 0.035 * sin(3 * a + 1) + 0.025 * cos(5 * a) + rng.uniform(-0.01, 0.01)
        rad.append(min(r, 0.455))
    ang = [2 * pi * k / n for k in range(n)]
    P = lambda k, s, z: Vector((rad[k % n] * s * cos(ang[k % n]), rad[k % n] * s * sin(ang[k % n]), z))
    cen = Vector((0, 0, 0.01))
    for k in range(n):
        b.face([cen, P(k, 0.93, 0.01), P(k + 1, 0.93, 0.01)],
               pick(rng, [(C["swamp"], 4), (C["swamp_d"], 2), (C["swamp_l"], 1)]), jit=0.06, out=(0, 0, -1))
        b.face([P(k, 0.88, 0.0125), P(k + 1, 0.88, 0.0125), P(k + 1, 1.04, 0.002), P(k, 1.04, 0.002)],
               pick(rng, [("#6a5a38", 3), ("#5a6a32", 2), ("#7a6a44", 1)]), jit=0.08, out=(0, 0, -1))
    # dark deep centre and floating scum
    for k in range(10):
        a0, a1 = 2 * pi * k / 10, 2 * pi * (k + 1) / 10
        b.face([Vector((0.02, -0.03, 0.0105)), Vector((0.02 + 0.17 * cos(a0), -0.03 + 0.15 * sin(a0), 0.0105)),
                Vector((0.02 + 0.17 * cos(a1), -0.03 + 0.15 * sin(a1), 0.0105))], C["swamp_d"], out=(0, 0, -1))
    for (x, y, r) in ((-0.15, 0.12, 0.07), (0.16, 0.14, 0.05), (0.1, -0.2, 0.06)):
        m = 6
        pts = [Vector((x + r * rng.uniform(0.6, 1.1) * cos(2 * pi * i / m), y + r * rng.uniform(0.6, 1.1) * sin(2 * pi * i / m), 0.011)) for i in range(m)]
        b.face(pts, C["scum"], jit=0.1, out=(x, y, -1))
    # lily pads with a couple of flowers
    for (x, y, r) in ((-0.05, 0.22, 0.045), (0.02, 0.27, 0.035), (0.2, -0.05, 0.04), (0.25, 0.05, 0.03),
                      (-0.22, -0.05, 0.042), (-0.12, -0.2, 0.035)):
        cyl(b, (x, y, 0.009), (x, y, 0.014), r, r, 8, lambda nn, P: pick(rng, [(C["lily"], 3), ("#6aa83e", 1)]),
            rot=rng.uniform(0, 1))
    for (x, y, c) in ((-0.05, 0.22, "#f4d0e0"), (0.2, -0.05, C["white"])):
        cyl(b, (x, y, 0.014), (x, y, 0.035), 0.016, 0.0, 5, c)
    # reed clumps around the rim
    for (k, cnt) in ((2, 7), (8, 6), (13, 5), (19, 7), (22, 4)):
        a = ang[k]
        bx, by = rad[k] * 0.9 * cos(a), rad[k] * 0.9 * sin(a)
        for i in range(cnt):
            ox, oy = rng.uniform(-0.03, 0.03), rng.uniform(-0.03, 0.03)
            h = rng.uniform(0.12, 0.23)
            lean = Vector((rng.uniform(-0.04, 0.04), rng.uniform(-0.04, 0.04), 0))
            p0 = Vector((bx + ox, by + oy, 0.0))
            tip = p0 + Vector((0, 0, h)) + lean
            cyl(b, p0, tip, 0.007, 0.0, 3, pick(rng, [(C["reed"], 3), (C["reed_d"], 2), (C["grass_d"], 1)]),
                rot=rng.uniform(0, 2))
            if rng.random() < 0.35:
                d = (tip - p0).normalized()
                cyl(b, p0 + d * h * 0.62, p0 + d * h * 0.82, 0.011, 0.011, 5, C["cattail"])
    cyl(b, (-0.3, 0.05, 0.012), (-0.12, 0.16, 0.0), 0.022, 0.018, 6,
        lambda nn, P: C["endgrain"] if abs(nn.dot(Vector((0.85, 0.52, 0)))) > 0.8 else C["wood_d"])


# --------------------------------------------------------------------------
# People and flags
# --------------------------------------------------------------------------
def person(b, phase, kind):
    """kind: 'walker' | 'armed' | 'knight'. Built facing +X, turned to +X+Y."""
    rng = b.rng
    knight = kind == "knight"
    sw = radians(24) * phase
    L = 0.12
    hipz = L
    drop = L * (1 - cos(sw))
    leg_col = C["metal_d"] if knight else C["trousers"]
    boot_col = C["metal_d"] if knight else C["boots"]
    with xf(b, Rz(pi / 4)):
        with xf(b, T(0, 0, -drop)):
            for side in (1, -1):
                ang = -sw * side
                M = T(0, side * 0.031, hipz) @ Ry(ang)
                with xf(b, M):
                    box(b, -0.022, -0.021, -L + 0.03, 0.022, 0.021, 0.004, leg_col, bev=0.008)
                foot = (T(0, 0, -drop) @ M) @ Vector((0, 0, -L + 0.025))
                fx, fy = foot.x, foot.y
            # torso
                pass
        # feet are placed outside the drop transform, flat on the ground
        for side in (1, -1):
            ang = -sw * side
            foot = (T(0, 0, -drop) @ T(0, side * 0.031, hipz) @ Ry(ang)) @ Vector((0, 0, -L + 0.028))
            box(b, foot.x - 0.028, foot.y - 0.024, 0.0, foot.x + 0.04, foot.y + 0.024, 0.038, boot_col, bev=0.009)
            if knight:
                box(b, foot.x - 0.02, foot.y - 0.026, 0.03, foot.x + 0.02, foot.y + 0.026, 0.06, C["metal"])
        with xf(b, T(0, 0, -drop)):
            top = 0.245
            if knight:
                # mail underneath, surcoat (team) over it reaching lower
                solid(b, [(sx * 0.05, sy * 0.064, 0.105) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.046, sy * 0.07, 0.235) for sx in (-1, 1) for sy in (-1, 1)],
                      C["metal_d"])
                solid(b, [(sx * 0.056, sy * 0.069, 0.075) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.049, sy * 0.06, 0.16) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.05, sy * 0.068, 0.228) for sx in (-1, 1) for sy in (-1, 1)],
                      None, mat=TEAM)
                # white cross on the chest
                box(b, 0.049, -0.009, 0.12, 0.056, 0.009, 0.215, C["white"], jit=0.03)
                box(b, 0.049, -0.04, 0.17, 0.056, 0.04, 0.188, C["white"], jit=0.03)
                box(b, -0.047, -0.07, 0.218, 0.047, 0.07, 0.25, C["metal"], bev=0.01)  # pauldrons/gorget
            else:
                solid(b, [(sx * 0.052, sy * 0.066, 0.1) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.045, sy * 0.056, 0.16) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.047, sy * 0.068, 0.228) for sx in (-1, 1) for sy in (-1, 1)] +
                      [(sx * 0.035, sy * 0.05, top) for sx in (-1, 1) for sy in (-1, 1)],
                      None, mat=TEAM)
                box(b, -0.03, -0.035, 0.225, 0.03, 0.035, 0.252, C["skin"], bev=0.006)  # neck/collar opening
            box(b, -0.05 if not knight else -0.053, -0.06 if not knight else -0.066, 0.145,
                0.05 if not knight else 0.053, 0.06 if not knight else 0.066, 0.162, C["belt"], jit=0.05)
            box(b, 0.048, -0.01, 0.143, 0.058 if knight else 0.054, 0.01, 0.164, C["gold"], jit=0.05)
            # head
            hz0 = 0.248
            if knight:
                box(b, -0.047, -0.045, hz0, 0.048, 0.045, 0.345, C["metal"], bev=0.014)
                box(b, 0.042, -0.035, 0.286, 0.05, 0.035, 0.298, C["dark"], jit=0.02)  # visor slit
                box(b, 0.044, -0.004, 0.26, 0.051, 0.004, 0.286, C["metal_d"])  # nasal
                box(b, -0.03, -0.03, 0.34, 0.03, 0.03, 0.355, C["gold"], bev=0.004)
                # plume (team colour) sweeping back
                tube(b, [(0.02, 0, 0.345), (-0.005, 0, 0.385), (-0.045, 0, 0.395), (-0.08, 0, 0.37),
                         (-0.095, 0, 0.33)], [0.014, 0.02, 0.019, 0.014, 0.004], 6, None, mat=TEAM)
            else:
                box(b, -0.042, -0.042, hz0, 0.044, 0.042, 0.336, C["skin"], bev=0.016)
                for sg in (-1, 1):
                    box(b, 0.04, sg * 0.018 - 0.006, 0.284, 0.0465, sg * 0.018 + 0.006, 0.298, C["dark"], jit=0.02)
                box(b, 0.04, -0.006, 0.268, 0.052, 0.006, 0.282, C["skin"], jit=0.04)
                if kind == "armed":
                    # iron cap with a brim
                    solid(b, [(sx * 0.048, sy * 0.048, 0.305) for sx in (-1, 1) for sy in (-1, 1)] +
                          [(sx * 0.03, sy * 0.03, 0.36) for sx in (-1, 1) for sy in (-1, 1)] +
                          [(0, 0, 0.368)], C["metal_d"])
                    box(b, -0.052, -0.052, 0.3, 0.052, 0.052, 0.31, C["iron"])
                    box(b, -0.047, -0.046, 0.26, -0.01, 0.046, 0.305, C["hair"], bev=0.01)
                else:
                    box(b, -0.047, -0.047, 0.268, -0.0, 0.047, 0.347, C["hair"], bev=0.014)
                    box(b, -0.047, -0.047, 0.315, 0.048, 0.047, 0.347, C["hair"], bev=0.014)
                    box(b, 0.02, -0.047, 0.305, 0.05, -0.01, 0.33, C["hair_l"], bev=0.008)
            # arms
            for side in (1, -1):
                if kind == "armed" and side == -1:
                    ang = -radians(48 if phase > 0 else 40)
                elif kind == "knight" and side == -1:
                    ang = -radians(100 if phase > 0 else 55)
                elif kind in ("armed", "knight") and side == 1:
                    ang = -radians(30)
                else:
                    ang = sw * side * 0.9
                sleeve = C["metal"] if knight else None
                with xf(b, T(0, side * 0.083, 0.226) @ Ry(ang)):
                    if knight:
                        box(b, -0.021, -0.02, -0.095, 0.021, 0.02, 0.012, C["metal_d"], bev=0.008)
                        box(b, -0.024, -0.023, -0.105, 0.024, 0.023, -0.07, C["metal"], bev=0.008)
                        box(b, -0.024, -0.026, -0.0, 0.024, 0.026, 0.024, C["metal"], bev=0.01)
                    else:
                        box(b, -0.02, -0.02, -0.085, 0.02, 0.02, 0.012, None, bev=0.008, mat=TEAM)
                    box(b, -0.017, -0.017, -0.118, 0.017, 0.017, -0.085, C["skin"] if not knight else C["metal_d"],
                        bev=0.007)
                    if side == -1 and kind == "armed":
                        # spear held upright through the fist
                        with xf(b, T(0, 0, -0.1) @ Ry(-ang)):
                            cyl(b, (0.0, 0, -0.17), (0.0, 0, 0.25), 0.007, 0.007, 5, C["wood"])
                            cyl(b, (0.0, 0, 0.25), (0.0, 0, 0.3), 0.016, 0.0, 4, C["metal"])
                    if side == -1 and kind == "knight":
                        # sword pointing along the forearm
                        with xf(b, T(0, 0, -0.105)):
                            box(b, -0.006, -0.025, -0.005, 0.006, 0.025, 0.005, C["gold"])
                            box(b, -0.006, -0.008, 0.0, 0.006, 0.008, 0.025, C["wood_d"])
                            solid(b, [(sx * 0.004, sy * 0.012, -0.005) for sx in (-1, 1) for sy in (-1, 1)] +
                                  [(sx * 0.003, sy * 0.01, -0.15) for sx in (-1, 1) for sy in (-1, 1)] +
                                  [(0, 0, -0.17)], C["metal"], jit=0.03)
                    if side == 1 and kind in ("armed", "knight"):
                        with xf(b, T(0.01, 0.03, -0.06) @ Ry(-ang) @ Rz(-0.75)):
                            if knight:
                                # kite shield facing outward (+Y)
                                outline = [(-0.045, 0.06), (0.045, 0.06), (0.05, 0.0), (0.0, -0.085), (-0.05, 0.0)]
                                with xf(b, T(0, 0.012, 0)):
                                    chamfer_prism(b, outline, 0.014, 0.004, None, mat=TEAM)
                                    box(b, -0.008, 0.006, -0.06, 0.008, 0.011, 0.05, C["white"], jit=0.03)
                                    box(b, -0.035, 0.006, 0.012, 0.035, 0.011, 0.028, C["white"], jit=0.03)
                                pass
                            else:
                                cyl(b, (0, 0.0, 0), (0, 0.016, 0), 0.062, 0.058, 10,
                                    lambda nn, P: pick(rng, [(C["wood"], 2), (C["wood_l"], 1)]))
                                cyl(b, (0, 0.016, 0), (0, 0.026, 0), 0.016, 0.01, 6, C["iron"])


def _team_shield_fix(b):
    pass


def m_person_factory(kind, phase):
    def f(b):
        person(b, phase, kind)
        if kind == "knight":
            # recolour: kite shield faces were added with col None -> make them team
            fixed = []
            for (W, col, mat) in b.faces:
                fixed.append((W, col, mat))
            b.faces = fixed
    return f


def m_flag(b, phase):
    rng = b.rng
    for k in range(5):
        a = 2 * pi * k / 5 + 0.3
        stone_pebble(b, (0.045 * cos(a), 0.045 * sin(a), 0), 0.03, flat=1.0)
    stone_pebble(b, (0, 0, 0.03), 0.03, flat=1.0)
    cyl(b, (0, 0, 0), (0, 0, 0.5), 0.011, 0.009, 6, C["wood"])
    blob(b, (0, 0, 0.51), 0.018, C["gold"], sub=0, noise=0.0)
    with xf(b, Rz(radians(135))):
        nu, nv = 6, 3
        Wf, Hf, ztop = 0.22, 0.13, 0.49
        ph = 0.0 if phase > 0 else pi * 0.9

        def P(i, j, off):
            u = i / nu
            x = 0.01 + Wf * u
            z = ztop - Hf * j / nv - 0.012 * u * u
            if i == nu and j == 1:  # swallowtail notch
                x -= 0.05
            elif i == nu:
                x += 0.0
            y = 0.03 * u * sin(2 * pi * (u * 1.1) + ph) + 0.01 * u * sin(2 * pi * (j / nv) + ph)
            return Vector((x, y + off, z))

        for i in range(nu):
            for j in range(nv):
                if i == nu - 1 and j in (1,):
                    pass
                q = [P(i, j, -0.002), P(i + 1, j, -0.002), P(i + 1, j + 1, -0.002), P(i, j + 1, -0.002)]
                b.face([q[0], q[3], q[2], q[1]], None, mat=TEAM)
                q2 = [P(i, j, 0.002), P(i + 1, j, 0.002), P(i + 1, j + 1, 0.002), P(i, j + 1, 0.002)]
                b.face(q2, None, mat=TEAM)
        # hem along the pole side
        cyl(b, (0.0, 0, ztop + 0.004), (0.0, 0, ztop - Hf - 0.004), 0.014, 0.014, 6, C["white"])


# --------------------------------------------------------------------------
# Optional pieces
# --------------------------------------------------------------------------
def charred_beam(b, p0, p1, w=0.024):
    rng = b.rng
    M, L = frame_to(p0, p1)
    with xf(b, M):
        box(b, -w / 2, -w / 2, 0, w / 2, w / 2, L, lambda n, P: pick(rng, [(C["char"], 3), ("#3e3026", 1)]),
            bev=0.004, jit=0.1)


def ash_patch(b, x, y, r, z=0.003):
    rng = b.rng
    m = 7
    pts = [Vector((x + r * rng.uniform(0.7, 1.1) * cos(2 * pi * i / m), y + r * rng.uniform(0.7, 1.1) * sin(2 * pi * i / m), z)) for i in range(m)]
    b.face(pts, pick(rng, [(C["ash"], 2), ("#4a4440", 1)]), out=(x, y, -1), jit=0.08)


def broken_wall(b, x0, x1, y, hmax, th=0.06):
    rng = b.rng
    n = int((x1 - x0) / 0.07)
    for i in range(n):
        xa = x0 + i * (x1 - x0) / n
        xb = xa + (x1 - x0) / n
        h = hmax * rng.uniform(0.3, 1.0) * (1 - 0.6 * abs(i - n / 2) / n)
        zz = 0.0
        while zz < h - 0.02:
            hh = min(0.05, h - zz)
            box(b, xa, y - th / 2, zz, xb - 0.004, y + th / 2, zz + hh,
                lambda nn, P: pick(rng, [(C["stone_d"], 3), ("#6a645a", 2), (C["char"], 1)]),
                bev=0.006, jit=0.1)
            zz += hh + 0.002


def m_ruin_small(b):
    rng = b.rng
    ash_patch(b, 0.0, 0.0, 0.3)
    for i in range(9):
        a = rng.uniform(0, 2 * pi)
        r = rng.uniform(0.0, 0.25)
        stone_pebble(b, (r * cos(a), r * sin(a), 0), rng.uniform(0.03, 0.06),
                     cols=[(C["stone_d"], 2), (C["char"], 1), (C["rock_d"], 1)])
    broken_wall(b, -0.2, 0.1, -0.15, 0.14)
    charred_beam(b, (-0.2, 0.1, 0.0), (0.15, 0.0, 0.12))
    charred_beam(b, (0.05, -0.2, 0.0), (-0.05, 0.2, 0.06))
    charred_beam(b, (0.2, 0.2, 0.0), (0.1, 0.05, 0.1))


def m_ruin_large(b):
    rng = b.rng
    ash_patch(b, 0.0, 0.0, 0.4)
    broken_wall(b, -0.3, 0.3, -0.25, 0.32)
    with xf(b, Rz(pi / 2)):
        broken_wall(b, -0.25, 0.15, 0.3, 0.26)
    for i in range(14):
        a = rng.uniform(0, 2 * pi)
        r = rng.uniform(0.0, 0.32)
        stone_pebble(b, (r * cos(a), r * sin(a), 0), rng.uniform(0.03, 0.07),
                     cols=[(C["stone_d"], 2), (C["char"], 1), (C["rock_d"], 1)])
    charred_beam(b, (-0.25, -0.2, 0.28), (0.2, 0.1, 0.0))
    charred_beam(b, (0.28, -0.2, 0.24), (0.1, 0.25, 0.0))
    charred_beam(b, (-0.1, 0.25, 0.0), (-0.25, -0.05, 0.18))
    charred_beam(b, (-0.05, -0.05, 0.0), (0.05, 0.3, 0.05))
    charred_beam(b, (0.25, -0.22, 0.0), (0.25, -0.22, 0.3))


def m_plot(b, wheat=False):
    rng = b.rng
    X, Y = 0.36, 0.3
    box(b, -X + 0.02, -Y + 0.02, 0, X - 0.02, Y - 0.02, 0.012, C["soil"], jit=0.05)
    rows = [-0.2, -0.07, 0.07, 0.2] if not wheat else [-0.22, -0.13, -0.04, 0.05, 0.14, 0.23]
    for ri, y in enumerate(rows):
        pts = []
        for x in (-X + 0.05, X - 0.05):
            pts += [(x, y - 0.045, 0.012), (x, y + 0.045, 0.012), (x, y, 0.04 if not wheat else 0.025)]
        solid(b, pts, C["soil_l"], jit=0.06)
        nplants = 6 if not wheat else 9
        for i in range(nplants):
            x = -X + 0.08 + i * (2 * X - 0.16) / (nplants - 1) + rng.uniform(-0.01, 0.01)
            if wheat:
                for k in range(4):
                    ox, oy = rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02)
                    h = rng.uniform(0.14, 0.18)
                    tip = (x + ox * 1.5, y + oy * 1.5, h)
                    cyl(b, (x + ox, y + oy, 0.02), tip, 0.006, 0.003, 3, C["wheat"], rot=rng.uniform(0, 2))
                    cyl(b, tip, (tip[0] + 0.004, tip[1], tip[2] + 0.04), 0.009, 0.0, 4,
                        pick(rng, [(C["wheat_l"], 2), (C["wheat"], 1)]))
            elif ri in (0, 2):
                leafy_c = lambda n, P: pick(rng, [(C["cabbage"], 3), ("#94c45a", 1)])
                blob(b, (x, y, 0.035), (0.034, 0.034, 0.03), leafy_c, sub=0, noise=0.1)
            elif ri == 1:
                cyl(b, (x, y, 0.03), (x, y, 0.045), 0.012, 0.004, 5, C["carrot"])
                for k in range(3):
                    a = 2 * pi * k / 3 + rng.uniform(0, 1)
                    cyl(b, (x, y, 0.04), (x + 0.02 * cos(a), y + 0.02 * sin(a), 0.09), 0.006, 0.0, 3, C["leaf_l"])
            else:
                for k in range(4):
                    a = 2 * pi * k / 4
                    cyl(b, (x, y, 0.03), (x + 0.012 * cos(a), y + 0.012 * sin(a), 0.1), 0.006, 0.0, 3, C["leaf"])
    # fence with a gate gap on +X
    fence_line(b, (-X, -Y, 0), (X, -Y, 0))
    fence_line(b, (-X, Y, 0), (X, Y, 0))
    fence_line(b, (-X, -Y, 0), (-X, Y, 0))
    fence_line(b, (X, -Y, 0), (X, -0.06, 0))
    fence_line(b, (X, 0.06, 0), (X, Y, 0))


# --------------------------------------------------------------------------
# Model registry
# --------------------------------------------------------------------------
# (name, original tile, builder, category, preview zoom)
MODELS = [
    ("house_1_tent", 33, m_tent, "building", 1),
    ("house_2_mud_hut", 34, m_mud_hut, "building", 1),
    ("house_3_thatch_hut", 35, m_thatch_hut, "building", 1),
    ("house_4_thatch_stone", 36, m_thatch_stone, "building", 1),
    ("house_5_log_cabin", 37, m_log_cabin, "building", 1),
    ("house_6_timber_house", 38, m_timber_house, "building", 1),
    ("house_7_stone_house", 39, m_stone_house, "building", 1),
    ("house_8_round_fort", 40, m_round_fort, "building", 1),
    ("keep_tower", 41, m_keep_tower, "castle", 1),
    ("castle_center", 42, m_castle_center, "castle", 1),
    ("castle_wall_x", 43, lambda b: m_castle_wall(b, "x"), "castle", 1),
    ("castle_wall_y", 44, lambda b: m_castle_wall(b, "y"), "castle", 1),
    ("ankh", 45, m_ankh, "building", 1),
    ("skull", 46, m_skull, "building", 1),
    ("rock_a", 47, m_rock_a, "rock", 1),
    ("rock_b", 48, m_rock_b, "rock", 1),
    ("rock_c", 49, m_rock_c, "rock", 1),
    ("pine", None, m_pine, "tree", 1),
    ("fruit_tree", None, m_fruit_tree, "tree", 1),
    ("dead_tree", None, m_dead_tree, "tree", 1),
    ("palm", None, m_palm, "tree", 1),
    ("cactus", None, m_cactus, "tree", 1),
    ("dead_bush", None, m_dead_bush, "tree", 2),
    ("bare_tree", None, m_bare_tree, "tree", 1),
    ("pebbles", None, m_pebbles, "rock", 1),
    ("shrub_a", None, m_shrub_a, "rock", 2),
    ("shrub_b", None, m_shrub_b, "rock", 2),
    ("shrub_c", None, m_shrub_c, "rock", 2),
    ("grove_a", None, m_grove_a, "tree", 1),
    ("grove_b", None, m_grove_b, "tree", 1),
    ("grove_c", None, m_grove_c, "tree", 1),
    ("shore_rock_a", None, m_shore_rock_a, "rock", 2),
    ("shore_rock_b", None, m_shore_rock_b, "rock", 2),
    ("shore_rock_c", None, m_shore_rock_c, "rock", 2),
    ("swamp", 53, m_swamp, "rock", 1),
    ("walker_a", None, lambda b: person(b, 1, "walker"), "person", 2),
    ("walker_b", None, lambda b: person(b, -1, "walker"), "person", 2),
    ("armed_a", None, lambda b: person(b, 1, "armed"), "person", 2),
    ("armed_b", None, lambda b: person(b, -1, "armed"), "person", 2),
    ("knight_a", None, lambda b: person(b, 1, "knight"), "person", 2),
    ("knight_b", None, lambda b: person(b, -1, "knight"), "person", 2),
    ("flag_a", None, lambda b: m_flag(b, 1), "person", 2),
    ("flag_b", None, lambda b: m_flag(b, -1), "person", 2),
    ("ruin_small", None, m_ruin_small, "building", 1),
    ("ruin_large", None, m_ruin_large, "building", 1),
    ("plot", None, m_plot, "building", 1),
]
EXTRAS = [("wheat_field", lambda b: m_plot(b, wheat=True))]  # diorama only, not exported

BUDGET = {"building": 4000, "castle": 3000, "tree": 900, "person": 900, "rock": 800}


# --------------------------------------------------------------------------
# Blender objects, materials, export
# --------------------------------------------------------------------------
def node_mat(name, kind, color=None):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "Col"
    if kind == "glow":
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Strength"].default_value = 2.0
        nt.links.new(attr.outputs["Color"], em.inputs["Color"])
        nt.links.new(em.outputs[0], out.inputs["Surface"])
    else:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Roughness"].default_value = 0.85
        for key in ("Specular IOR Level", "Specular"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = 0.2
        if kind == "team":
            mul = nt.nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs["Factor"].default_value = 1.0
            mul.inputs[7].default_value = (*[s2l(c) for c in color], 1.0)
            nt.links.new(attr.outputs["Color"], mul.inputs[6])
            nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])
            m.diffuse_color = (*[s2l(c) for c in color], 1.0)
        else:
            nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


MATS = {}


def materials():
    if not MATS:
        MATS["Paint"] = node_mat("Paint", "paint")
        MATS["Team"] = node_mat("Team", "team", C["team_blue"])
        MATS["TeamRed"] = node_mat("TeamRed", "team", C["team_red"])
        MATS["Glow"] = node_mat("Glow", "glow")
    return MATS


def make_object(name, b, coll, clamp=True):
    M = materials()
    bm = bmesh.new()
    lay = bm.loops.layers.float_color.new("Col")
    for (W, col, mat) in b.faces:
        if clamp:
            W = [Vector((p.x, p.y, max(0.0, p.z))) for p in W]
        vs = [bm.verts.new(p) for p in W]
        try:
            f = bm.faces.new(vs)
        except ValueError:
            continue
        f.material_index = mat
        lin = (s2l(col[0]), s2l(col[1]), s2l(col[2]), 1.0)
        for l in f.loops:
            l[lay] = lin
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for key in ("Paint", "Team", "Glow"):
        me.materials.append(M[key])
    try:
        me.color_attributes.active_color = me.color_attributes["Col"]
        me.color_attributes.render_color_index = 0
    except Exception:
        pass
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def export_pmesh(ob, path):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    Mw = ob.matrix_world.copy()
    Rn = Mw.to_3x3().inverted().transposed()
    col = me.color_attributes.get("Col")
    data = []
    for tri in me.loop_triangles:
        poly = me.polygons[tri.polygon_index]
        mname = me.materials[poly.material_index].name if len(me.materials) else ""
        surf = 4.0 if mname.startswith("Team") else (5.0 if mname.startswith("Glow") else 3.0)
        P = [Mw @ me.vertices[v].co for v in tri.vertices]
        n = (P[1] - P[0]).cross(P[2] - P[0])
        if n.length < 1e-12:
            continue
        n.normalize()
        for li, p in zip(tri.loops, P):
            c = col.data[li].color if col else (1, 1, 1, 1)
            data += [p.x, p.y, p.z, n.x, n.y, n.z, l2s(c[0]), l2s(c[1]), l2s(c[2]), surf]
    ev.to_mesh_clear()
    nverts = len(data) // 10
    with open(path, "wb") as fh:
        fh.write(b"PMSH")
        fh.write(struct.pack("<II", 1, nverts))
        fh.write(struct.pack("<%df" % len(data), *data))
    return nverts // 3


# --------------------------------------------------------------------------
# Scene, lighting, rendering
# --------------------------------------------------------------------------
CAM_DIR = Vector((0.6124, 0.6124, 0.5)).normalized()


def setup_scene():
    scn = bpy.context.scene
    scn.render.engine = "BLENDER_EEVEE"
    try:
        scn.eevee.taa_render_samples = 48
    except Exception:
        pass
    for attr, val in (("use_shadows", True), ("use_raytracing", True), ("use_fast_gi", True),
                      ("fast_gi_distance", 0.6), ("shadow_ray_count", 2), ("shadow_step_count", 8)):
        try:
            setattr(scn.eevee, attr, val)
        except Exception:
            pass
    scn.view_settings.view_transform = "Standard"
    try:
        scn.view_settings.look = "None"
    except Exception:
        pass
    scn.view_settings.exposure = 0.0
    scn.render.film_transparent = False
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    # world: soft blue sky fill
    w = bpy.data.worlds.get("Sky") or bpy.data.worlds.new("Sky")
    try:
        w.use_nodes = True
    except Exception:
        pass
    scn.world = w
    bg = None
    for n in w.node_tree.nodes:
        if n.type == "BACKGROUND":
            bg = n
    if bg is None:
        bg = w.node_tree.nodes.new("ShaderNodeBackground")
        out = w.node_tree.nodes.get("World Output") or w.node_tree.nodes.new("ShaderNodeOutputWorld")
        w.node_tree.links.new(bg.outputs[0], out.inputs[0])
    bg.inputs[0].default_value = (0.55, 0.68, 0.9, 1.0)
    bg.inputs[1].default_value = 0.75

    stage = bpy.data.collections.new("Stage")
    scn.collection.children.link(stage)
    sun_d = bpy.data.lights.new("Sun", "SUN")
    sun_d.energy = 3.6
    sun_d.color = (1.0, 0.95, 0.86)
    try:
        sun_d.angle = radians(6)
    except Exception:
        pass
    sun = bpy.data.objects.new("Sun", sun_d)
    sun.rotation_euler = (radians(40), 0, 0)  # light travels toward +Y and down: comes from -Y, 50 deg up
    stage.objects.link(sun)
    cam_d = bpy.data.cameras.new("GameCam")
    cam_d.type = "ORTHO"
    cam_d.clip_start = 0.1
    cam_d.clip_end = 200
    cam = bpy.data.objects.new("GameCam", cam_d)
    stage.objects.link(cam)
    scn.camera = cam
    # label text parented to the camera
    cu = bpy.data.curves.new("Label", "FONT")
    cu.align_x = "CENTER"
    cu.body = ""
    lab = bpy.data.objects.new("Label", cu)
    lm = bpy.data.materials.new("LabelMat")
    try:
        lm.use_nodes = True
    except Exception:
        pass
    nt = lm.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = (0.02, 0.02, 0.03, 1)
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], o.inputs[0])
    cu.materials.append(lm)
    lab.parent = cam
    stage.objects.link(lab)
    # preview ground
    gb = B(1)
    G = 3.0
    gb.face([(-G, -G, 0), (G, -G, 0), (G, G, 0), (-G, G, 0)], "#a2bf6c", jit=0.0)
    for (x0, y0, x1, y1) in ((-0.5, -0.5, 0.5, -0.49), (-0.5, 0.49, 0.5, 0.5), (-0.5, -0.5, -0.49, 0.5),
                             (0.49, -0.5, 0.5, 0.5)):
        gb.face([(x0, y0, 0.0008), (x1, y0, 0.0008), (x1, y1, 0.0008), (x0, y1, 0.0008)], "#86a85a", jit=0.0)
    ground = make_object("PreviewGround", gb, stage)
    return stage, cam, lab, ground


def aim_camera(cam, target, ortho, res):
    scn = bpy.context.scene
    target = Vector(target)
    cam.location = target + CAM_DIR * 40
    cam.rotation_euler = (-CAM_DIR).to_track_quat("-Z", "Y").to_euler()
    cam.data.ortho_scale = ortho
    scn.render.resolution_x, scn.render.resolution_y = res
    scn.render.resolution_percentage = 100


def set_label(lab, cam, text, res):
    lab.data.body = text
    o = cam.data.ortho_scale
    aspect = res[1] / res[0]
    lab.data.size = o * 0.045
    lab.location = (0, -o * aspect / 2 + o * 0.03, -1.0)
    lab.hide_render = not text


def render_to(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def load_pixels(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    arr = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    bpy.data.images.remove(img)
    return arr.reshape(h, w, 4)


def save_pixels(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new("tmp_out", w, h, alpha=False)
    img.pixels.foreach_set(arr.astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def make_sheet(names, path, cols=8, cell=256):
    tiles = []
    for n in names:
        p = os.path.join(PREVIEW_DIR, n + ".png")
        if os.path.exists(p):
            a = load_pixels(p)
            f = a.shape[0] // cell
            a = a[:f * cell, :f * cell].reshape(cell, f, cell, f, 4).mean(axis=(1, 3))
            tiles.append(a)
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.ones((rows * cell, cols * cell, 4), dtype=np.float32) * 0.15
    sheet[..., 3] = 1
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        # image rows are bottom-up
        y0 = (rows - 1 - r) * cell
        sheet[y0:y0 + cell, c * cell:(c + 1) * cell] = t
        sheet[y0:y0 + cell, c * cell:c * cell + 1] = 0.1
        sheet[y0:y0 + 1, c * cell:(c + 1) * cell] = 0.1
    save_pixels(sheet, path)


# --------------------------------------------------------------------------
# Diorama
# --------------------------------------------------------------------------
H_STEP = 0.408
WATER_Z = 0.12


def diorama_heights(N=14):
    hgt = [[0] * (N + 1) for _ in range(N + 1)]
    c = N / 2
    for i in range(N + 1):
        for j in range(N + 1):
            x, y = i - c, j - c
            a = math.atan2(y, x)
            r = math.hypot(x, y)
            R = 5.3 + 0.6 * sin(3 * a + 0.5) + 0.4 * cos(5 * a + 1.0)
            if r < R and 0 < i < N and 0 < j < N:
                lvl = 1
                lvl += round(2.4 * math.exp(-((i - 4.6) ** 2 + (j - 4.6) ** 2) / 5.0))
                lvl += round(1.4 * math.exp(-((i - 9.5) ** 2 + (j - 3.6) ** 2) / 3.0))
                lvl += round(1.2 * math.exp(-((i - 4.2) ** 2 + (j - 9.8) ** 2) / 2.5))
                hgt[i][j] = lvl
    # Populous rule: neighbouring corners differ by at most one step
    changed = True
    while changed:
        changed = False
        for i in range(N + 1):
            for j in range(N + 1):
                for (di, dj) in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    a, bb = i + di, j + dj
                    if 0 <= a <= N and 0 <= bb <= N and hgt[i][j] > hgt[a][bb] + 1:
                        hgt[i][j] = hgt[a][bb] + 1
                        changed = True
    return hgt


def build_diorama(models, coll):
    N = 14
    hgt = diorama_heights(N)
    rng = random.Random(4242)
    b = B(99)

    def Z(i, j):
        return hgt[i][j] * H_STEP

    tris = []
    for i in range(N):
        for j in range(N):
            cs = [Vector((i, j, Z(i, j))), Vector((i + 1, j, Z(i + 1, j))), Vector((i + 1, j + 1, Z(i + 1, j + 1))),
                  Vector((i, j + 1, Z(i, j + 1)))]
            cen = sum(cs, Vector()) / 4
            for k in range(4):
                tris.append([cs[k], cs[(k + 1) % 4], cen])
    for t in tris:
        zmin = min(p.z for p in t)
        zmax = max(p.z for p in t)
        zavg = sum(p.z for p in t) / 3
        if zmax < WATER_Z - 0.01:
            col = C["sand_d"]
        elif zavg < 0.3:
            col = pick(rng, [(C["sand"], 3), (C["sand_d"], 1)])
        elif zmin < 0.26 and zavg < 0.36:
            col = mix(C["sand"], C["grass"], 0.5)
        else:
            col = pick(rng, [(C["grass"], 5), (C["grass_l"], 3), (C["grass_d"], 2), (C["meadow"], 1)])
        b.face([p - Vector((N / 2, N / 2, 0)) for p in t], col, jit=0.05, out=t[2] - Vector((0, 0, 5)))
    # sea: coloured by distance to land, with foam on the shoreline
    land = [(i, j) for i in range(N + 1) for j in range(N + 1) if hgt[i][j] > 0]
    S_ = 0.5
    ext = 14
    for gi in range(int(2 * ext / S_)):
        for gj in range(int(2 * ext / S_)):
            x0, y0 = -ext + gi * S_, -ext + gj * S_
            cx, cy = x0 + S_ / 2 + N / 2, y0 + S_ / 2 + N / 2
            d = min(math.hypot(cx - i, cy - j) for (i, j) in land)
            t = max(0.0, min(1.0, (d - 0.5) / 3.0))
            col = mix(C["water_l"], C["water_d"], t)
            q = [(x0, y0, WATER_Z), (x0 + S_, y0, WATER_Z), (x0 + S_, y0 + S_, WATER_Z), (x0, y0 + S_, WATER_Z)]
            b.face([q[0], q[1], q[2]], col, jit=0.012, out=(0, 0, -10))
            b.face([q[0], q[2], q[3]], col, jit=0.012, out=(0, 0, -10))
    for t in tris:
        zs = [p.z for p in t]
        for (lev, width, colr, zoff) in ((WATER_Z + 0.002, 0.09, C["foam"], 0.004),
                                         (WATER_Z - 0.05, 0.07, mix(C["foam"], C["water_l"], 0.45), 0.003)):
            above = [p.z > lev for p in t]
            if all(above) or not any(above):
                continue
            pts = []
            for k in range(3):
                a, c2 = t[k], t[(k + 1) % 3]
                if (a.z > lev) != (c2.z > lev):
                    u = (lev - a.z) / (c2.z - a.z)
                    pts.append(a.lerp(c2, u))
            if len(pts) != 2:
                continue
            p0, p1 = pts
            # offset seaward: horizontal downhill direction of the triangle
            n = (t[1] - t[0]).cross(t[2] - t[0])
            dh = Vector((n.x, n.y, 0))
            if n.z < 0:
                dh = -dh
            if dh.length < 1e-6:
                continue
            dh.normalize()
            off = Vector((0, 0, 0))
            if lev < WATER_Z:
                off = dh * 0.12
            q = [p0 + off, p1 + off, p1 + off + dh * width, p0 + off + dh * width]
            q = [Vector((p.x - N / 2, p.y - N / 2, WATER_Z + zoff)) for p in q]
            b.face(q, colr, jit=0.03, out=(0, 0, -10))
    terrain = make_object("Diorama_Terrain", b, coll)

    def cell_z(i, j):
        return min(Z(i, j), Z(i + 1, j), Z(i, j + 1), Z(i + 1, j + 1))

    def flat(i, j):
        v = {hgt[i][j], hgt[i + 1][j], hgt[i][j + 1], hgt[i + 1][j + 1]}
        return len(v) == 1 and hgt[i][j] > 0

    used = set()

    def place(name, i, j, dx=0.0, dy=0.0, rot=0.0, red=False, z=None):
        src = models[name]
        ob = bpy.data.objects.new("D_" + name, src.data)
        ob.location = (i + 0.5 - N / 2 + dx, j + 0.5 - N / 2 + dy, cell_z(i, j) if z is None else z)
        ob.rotation_euler = (0, 0, rot)
        coll.objects.link(ob)
        if red:
            ob.material_slots[1].link = "OBJECT"
            ob.material_slots[1].material = MATS["TeamRed"]
        used.add((i, j))
        return ob

    def nearest(target, pred):
        best = None
        for i in range(N):
            for j in range(N):
                if (i, j) in used or not pred(i, j):
                    continue
                d = math.hypot(i - target[0], j - target[1])
                if best is None or d < best[0]:
                    best = (d, i, j)
        return best[1:] if best else None

    def put(name, target, pred=flat, **kw):
        c = nearest(target, pred)
        if c:
            return place(name, c[0], c[1], **kw)

    def shore(i, j):
        v = [hgt[i][j], hgt[i + 1][j], hgt[i][j + 1], hgt[i + 1][j + 1]]
        return min(v) == 0 and max(v) == 1

    def landcell(i, j):
        v = [hgt[i][j], hgt[i + 1][j], hgt[i][j + 1], hgt[i + 1][j + 1]]
        return min(v) >= 1

    def hill(i, j):
        v = [hgt[i][j], hgt[i + 1][j], hgt[i][j + 1], hgt[i + 1][j + 1]]
        return max(v) >= 2

    def coast(i, j):
        if not landcell(i, j):
            return False
        return any(0 <= i + di < N and 0 <= j + dj < N and shore(i + di, j + dj)
                   for di in (-1, 0, 1) for dj in (-1, 0, 1))

    def slope_z(i, j):
        v = [Z(i, j), Z(i + 1, j), Z(i, j + 1), Z(i + 1, j + 1)]
        return min(v) + 0.3 * (max(v) - min(v))

    # village on the flat lowland facing the camera
    put("house_7_stone_house", (9, 8))
    put("house_6_timber_house", (11, 7))
    put("house_4_thatch_stone", (8, 10))
    put("house_3_thatch_hut", (11, 10))
    put("house_5_log_cabin", (7, 7))
    put("house_2_mud_hut", (10, 5))
    put("wheat_field", (9, 11))
    put("wheat_field", (10, 11))
    put("plot", (12, 9))
    for nm, tgt in (("flag_a", (10, 8)), ("flag_b", (12, 11))):
        c = nearest(tgt, flat)
        if c:
            place(nm, c[0], c[1])
    # woods on the hills
    for tgt in ((4, 4), (5, 3), (3, 5), (5, 5), (6, 4), (4, 6), (3, 3), (6, 6), (2, 4)):
        c = nearest(tgt, landcell)
        if c:
            place("pine", c[0], c[1], rot=rng.uniform(0, 6), z=slope_z(*c),
                  dx=rng.uniform(-0.1, 0.1), dy=rng.uniform(-0.1, 0.1))
    for tgt in ((4, 9), (4, 10), (5, 10), (3, 9)):
        c = nearest(tgt, landcell)
        if c:
            place("fruit_tree", c[0], c[1], rot=rng.uniform(0, 6), z=slope_z(*c))
    for tgt in ((9, 3), (10, 3)):
        c = nearest(tgt, landcell)
        if c:
            place("pine", c[0], c[1], rot=rng.uniform(0, 6), z=slope_z(*c))
    c = nearest((7, 4), landcell)
    if c:
        place("bare_tree", c[0], c[1], z=slope_z(*c))
    # rocky outcrops and scenery along the coast
    for nm, tgt in (("rock_a", (12, 12)), ("rock_b", (13, 7)), ("rock_c", (6, 12)), ("rock_a", (2, 8)),
                    ("pebbles", (11, 12)), ("rock_c", (12, 4)), ("rock_b", (7, 1))):
        c = nearest(tgt, coast)
        if c:
            place(nm, c[0], c[1], rot=rng.choice((0, pi / 2, pi, 3 * pi / 2)), z=slope_z(*c))
    c = nearest((13, 5), coast)
    if c:
        place("palm", c[0], c[1], z=slope_z(*c))
    put("swamp", (6, 8))
    # people
    walkers = [("walker_a", (10, 9), 0.2, 0.25, False), ("walker_b", (9, 9), -0.2, 0.1, False),
               ("armed_a", (8, 8), 0.1, -0.2, False), ("walker_a", (11, 8), 0.25, -0.15, False),
               ("knight_b", (10, 10), -0.1, 0.2, False), ("walker_b", (7, 5), 0.0, 0.0, True),
               ("walker_a", (12, 10), 0.0, 0.1, False)]
    for nm, tgt, dx, dy, red in walkers:
        c = nearest(tgt, flat)
        if c:
            place(nm, c[0], c[1], dx=dx, dy=dy, red=red)
    return terrain


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    # clean factory scene
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    materials()
    scn = bpy.context.scene
    stage, cam, lab, ground = setup_scene()
    models_coll = bpy.data.collections.new("Models")
    scn.collection.children.link(models_coll)

    built = {}
    colls = {}
    report = []
    todo = [m for m in MODELS if ONLY is None or m[0] in ONLY]
    extras = [e for e in EXTRAS if ONLY is None or e[0] in ONLY]
    for (name, tile, fn, cat, zoom) in todo + [(n, None, f, "extra", 1) for (n, f) in extras]:
        b = B(zlib.crc32(name.encode()))
        fn(b)
        coll = bpy.data.collections.new(name)
        models_coll.children.link(coll)
        ob = make_object(name, b, coll)
        built[name] = ob
        colls[name] = coll
        if cat != "extra":
            ntri = export_pmesh(ob, os.path.join(MODEL_DIR, name + ".pmesh"))
            bb = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
            lo = Vector((min(v.x for v in bb), min(v.y for v in bb), min(v.z for v in bb)))
            hi = Vector((max(v.x for v in bb), max(v.y for v in bb), max(v.z for v in bb)))
            flag = "" if ntri <= BUDGET[cat] else "  OVER BUDGET %d" % BUDGET[cat]
            report.append("%-22s tile %-4s %5d tris  x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]%s" % (
                name, tile if tile else "-", ntri, lo.x, hi.x, lo.y, hi.y, lo.z, hi.z, flag))
    print("\n".join(["", "=== MODELS ==="] + report + [""]))

    if DO_RENDER:
        for c in colls.values():
            c.hide_render = True
        for (name, tile, fn, cat, zoom) in todo:
            colls[name].hide_render = False
            ortho = 2.1 / zoom
            tz = 0.42 if zoom == 1 else 0.16
            res = (512, 512)
            aim_camera(cam, (0, 0, tz), ortho, res)
            ntri = sum(len(p.vertices) - 2 for p in built[name].data.polygons)
            set_label(lab, cam, "%s%s  %d tris%s" % (name, (" (%d)" % tile) if tile else "",
                                                       ntri, "  [2x zoom]" if zoom != 1 else ""), res)
            render_to(os.path.join(PREVIEW_DIR, name + ".png"))
            colls[name].hide_render = True
        if DO_EXTRAS:
            make_sheet([m[0] for m in MODELS], os.path.join(PREVIEW_DIR, "sheet.png"))
            # castle test: 3x3 block
            cc = bpy.data.collections.new("CastleTest")
            scn.collection.children.link(cc)
            for (nm, x, y) in [("keep_tower", -1, -1), ("keep_tower", 1, -1), ("keep_tower", -1, 1),
                               ("keep_tower", 1, 1), ("castle_wall_x", 0, -1), ("castle_wall_x", 0, 1),
                               ("castle_wall_y", -1, 0), ("castle_wall_y", 1, 0), ("castle_center", 0, 0)]:
                o = bpy.data.objects.new("C_" + nm, built[nm].data)
                o.location = (x, y, 0)
                cc.objects.link(o)
            ground.scale = (1.6, 1.6, 1)
            aim_camera(cam, (0, 0, 0.5), 4.6, (1024, 1024))
            set_label(lab, cam, "castle 3x3 test", (1024, 1024))
            render_to(os.path.join(PREVIEW_DIR, "castle_test.png"))
            cc.hide_render = True
            ground.scale = (1, 1, 1)
            # diorama
            dc = bpy.data.collections.new("Diorama")
            scn.collection.children.link(dc)
            build_diorama(built, dc)
            ground.hide_render = True
            aim_camera(cam, (0.0, 0.0, 0.4), 11.5, (1600, 1100))
            set_label(lab, cam, "", (1600, 1100))
            render_to(os.path.join(PREVIEW_DIR, "diorama.png"))
            ground.hide_render = False
            cc.hide_render = False
            cc.hide_viewport = True
            dc.hide_render = False
            dc.hide_viewport = True
        for c in colls.values():
            c.hide_render = False

    if DO_BLEND:
        # every model sits on the origin (as exported); show one at a time in the viewport
        vl = bpy.context.view_layer.layer_collection

        def find(lc, name):
            if lc.name == name:
                return lc
            for ch in lc.children:
                r = find(ch, name)
                if r:
                    return r
            return None

        first = True
        for name in built:
            lc = find(vl, name)
            if lc:
                lc.hide_viewport = not first
                first = False
        for name in ("CastleTest", "Diorama"):
            lc = find(vl, name)
            if lc:
                lc.hide_viewport = True
        try:
            bpy.context.preferences.filepaths.save_version = 0
        except Exception:
            pass
        bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH, compress=True)
    print("done")


main()
