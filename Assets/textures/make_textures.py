#!/usr/bin/env python3
"""Procedural ground textures for Populous HD mode.

Run from the repository root:

    python3 Assets/textures/make_textures.py

Writes 512x512 seamlessly tileable sRGB PNGs to
Sources/PopulousHD/Resources/Textures/ and two preview sheets to Assets/previews/.

Everything is generated here from seeded random numbers (numpy + Pillow only), so
the output is identical on every run. Every noise field is periodic over the
512-texel tile: band-limited noise is made by filtering white noise in the
frequency domain (the FFT wraps), cellular patterns measure distances on a torus,
and painted dabs wrap their footprint around the edges.

Scale: the game maps one 512x512 texture over 2x2 map cells, so a cell is 256
texels and is drawn about 190 pixels wide at full HD.

Style: sunny, painted strategy-game art. Warm yellow-green tufted grass (and a
meadow variant with wildflower drifts on the very same base, so the two can be
mixed by a mask), rounded granite for rocky shores, creamy beach sand and a deep
saturated sea. Light comes from the upper left (small x, small y).
"""

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

N = 512
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(ROOT, "Sources", "PopulousHD", "Resources", "Textures")
PREVIEW_DIR = os.path.join(ROOT, "Assets", "previews")

YY, XX = np.mgrid[0:N, 0:N].astype(np.float64)
_F = np.fft.fftfreq(N)
FX, FY = np.meshgrid(_F, _F)  # cycles per texel; FY varies along rows (y)


# ---------------------------------------------------------------------------
# Periodic noise

def noise(rng, sigma, sx=1.0, sy=1.0, angle=0.0):
    """White noise blurred by a Gaussian of `sigma` texels (stretched by sx, sy and
    turned by `angle` degrees), on the torus. Zero mean, unit deviation."""
    w = rng.standard_normal((N, N))
    a = np.radians(angle)
    u = FX * np.cos(a) + FY * np.sin(a)
    v = -FX * np.sin(a) + FY * np.cos(a)
    h = np.exp(-2.0 * np.pi ** 2 * sigma ** 2 * ((u * sx) ** 2 + (v * sy) ** 2))
    out = np.real(np.fft.ifft2(np.fft.fft2(w) * h))
    out -= out.mean()
    return out / (out.std() + 1e-12)


def fbm(rng, sigmas, weights=None):
    weights = weights or [0.5 ** i for i in range(len(sigmas))]
    out = sum(wt * noise(rng, s) for s, wt in zip(sigmas, weights))
    return out / out.std()


def noise1d(rng, sigma, n=N):
    w = rng.standard_normal(n)
    f = np.fft.fftfreq(n)
    out = np.real(np.fft.ifft(np.fft.fft(w) * np.exp(-2.0 * np.pi ** 2 * sigma ** 2 * f ** 2)))
    out -= out.mean()
    return out / (out.std() + 1e-12)


def sample(img, x, y):
    """Bilinear lookup of a 2D or HxWxC array at (x, y), wrapping."""
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = x - x0
    fy = y - y0
    if img.ndim == 3:
        fx = fx[..., None]
        fy = fy[..., None]
    x0 %= N
    y0 %= N
    x1 = (x0 + 1) % N
    y1 = (y0 + 1) % N
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def warp(img, dx, dy):
    return sample(img, XX + dx, YY + dy)


def voronoi(rng, grid, x=None, y=None, jitter=0.9):
    """Cellular noise with one jittered point per cell of a grid x grid lattice on
    the torus. Returns F1, F2 (texels), the nearest point's cell index, and the
    vector from the nearest point."""
    x = XX if x is None else x
    y = YY if y is None else y
    cell = N / grid
    pts = (rng.random((grid, grid, 2)) - 0.5) * jitter + 0.5  # within-cell position
    cx = np.floor(x / cell).astype(np.int64)
    cy = np.floor(y / cell).astype(np.int64)
    f1 = np.full(x.shape, 1e9)
    f2 = np.full(x.shape, 1e9)
    idx = np.zeros(x.shape, np.int64)
    vx = np.zeros(x.shape)
    vy = np.zeros(x.shape)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            gx = cx + ox
            gy = cy + oy
            wx = gx % grid
            wy = gy % grid
            px = (gx + pts[wy, wx, 0]) * cell
            py = (gy + pts[wy, wx, 1]) * cell
            ddx = x - px
            ddy = y - py
            d = np.sqrt(ddx * ddx + ddy * ddy)
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            f1 = np.where(closer, d, f1)
            idx = np.where(closer, wy * grid + wx, idx)
            vx = np.where(closer, ddx, vx)
            vy = np.where(closer, ddy, vy)
    return f1, f2, idx, vx, vy


def voronoi_edge(rng, grid, x=None, y=None, jitter=0.9):
    """Like voronoi() but also returns the true distance to the nearest cell border
    (not F2 - F1), which gives even-width cracks."""
    x = XX if x is None else x
    y = YY if y is None else y
    cell = N / grid
    pts = (rng.random((grid, grid, 2)) - 0.5) * jitter + 0.5
    cx = np.floor(x / cell).astype(np.int64)
    cy = np.floor(y / cell).astype(np.int64)
    cands = []
    for oy in (-2, -1, 0, 1, 2):
        for ox in (-2, -1, 0, 1, 2):
            gx = cx + ox
            gy = cy + oy
            wx = gx % grid
            wy = gy % grid
            px = (gx + pts[wy, wx, 0]) * cell
            py = (gy + pts[wy, wx, 1]) * cell
            cands.append((px, py, wy * grid + wx))
    pxs = np.stack([c[0] for c in cands])
    pys = np.stack([c[1] for c in cands])
    ids = np.stack([c[2] for c in cands])
    d2 = (x - pxs) ** 2 + (y - pys) ** 2
    near = np.argmin(d2, axis=0)
    take = lambda a: np.take_along_axis(a, near[None], 0)[0]
    nx, ny, nid = take(pxs), take(pys), take(ids)
    f1 = np.sqrt(take(d2))
    edge = np.full(x.shape, 1e9)
    for k in range(len(cands)):
        ox = pxs[k] - nx
        oy = pys[k] - ny
        ln = np.sqrt(ox * ox + oy * oy)
        valid = ln > 1e-6
        # distance from the pixel to the bisector between the nearest point and k
        mx = (pxs[k] + nx) * 0.5
        my = (pys[k] + ny) * 0.5
        dist = ((mx - x) * ox + (my - y) * oy) / np.where(valid, ln, 1.0)
        edge = np.where(valid, np.minimum(edge, dist), edge)
    return f1, edge, nid


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ramp(t, stops):
    """Map t to colour by piecewise-linear stops [(t, (r, g, b)), ...]."""
    ts = np.array([s[0] for s in stops], np.float64)
    cs = np.array([s[1] for s in stops], np.float64)
    out = np.empty(t.shape + (3,))
    for c in range(3):
        out[..., c] = np.interp(t, ts, cs[:, c])
    return out


def rgb(*c):
    return np.array(c, np.float64)


def mix(a, b, t):
    if np.ndim(t) == 2:
        t = t[..., None]
    return a * (1 - t) + b * t


def equalize(v):
    """Rank-map to a uniform 0..1 distribution."""
    order = np.argsort(v, axis=None)
    out = np.empty(v.size)
    out[order] = np.linspace(0.0, 1.0, v.size)
    return out.reshape(v.shape)


def to01(v):
    return (v - v.min()) / (v.max() - v.min())


# ---------------------------------------------------------------------------
# Wrapped dabs

def dab(img, cx, cy, r, color, alpha=1.0, soft=0.6, sy=1.0, angle=0.0, shade=0.0):
    """Paint an antialiased (elliptical) dot that wraps around the tile. `shade`
    > 0 darkens the lower-right and lightens the upper-left (a rounded stone)."""
    ext = int(np.ceil(r * max(1.0, sy) + 2))
    xs = np.arange(int(np.floor(cx)) - ext, int(np.floor(cx)) + ext + 2)
    ys = np.arange(int(np.floor(cy)) - ext, int(np.floor(cy)) + ext + 2)
    gx, gy = np.meshgrid(xs + 0.5 - cx, ys + 0.5 - cy)
    a = np.radians(angle)
    u = gx * np.cos(a) + gy * np.sin(a)
    v = (-gx * np.sin(a) + gy * np.cos(a)) / sy
    d = np.sqrt(u * u + v * v) / max(r, 1e-6)
    cov = np.clip((1.0 - d) * r / max(soft, 1e-3) + 0.5, 0.0, 1.0) * alpha
    col = np.broadcast_to(np.asarray(color, np.float64), cov.shape + (3,)).copy()
    if shade:
        lit = np.clip(-(gx + gy) / (r * 1.6) + 0.15, -1.0, 1.0)
        col = col * (1.0 + shade * lit[..., None])
    iy = np.ix_(ys % N, xs % N)
    img[iy] = img[iy] * (1 - cov[..., None]) + col * cov[..., None]


def poisson_points(rng, count, min_dist, mask=None, tries=40):
    """Scattered points at least min_dist apart (on the torus), optionally
    accepted with probability mask[y, x]."""
    pts = []
    attempts = 0
    while len(pts) < count and attempts < count * tries:
        attempts += 1
        p = rng.random(2) * N
        if mask is not None and rng.random() > mask[int(p[1]) % N, int(p[0]) % N]:
            continue
        ok = True
        for q in pts:
            dx = abs(p[0] - q[0]); dx = min(dx, N - dx)
            dy = abs(p[1] - q[1]); dy = min(dy, N - dy)
            if dx * dx + dy * dy < min_dist * min_dist:
                ok = False
                break
        if ok:
            pts.append(p)
    return pts


# ---------------------------------------------------------------------------
# The textures. Each returns an N x N x 3 float array in 0..255 sRGB.

# Grass palette (shared by grass and meadow so the shader can mix them freely)
GRASS_RAMP = [
    (0.00, (52, 92, 26)),
    (0.22, (74, 118, 32)),
    (0.42, (94, 143, 42)),     # rich mid green #5E8F2A
    (0.66, (128, 172, 52)),
    (0.84, (148, 190, 60)),    # sunlit #8DB83A..#A6C94A
    (1.00, (172, 206, 80)),
]


def grad(h):
    """Periodic central-difference gradient (d/dx, d/dy)."""
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5
    return gx, gy


def blur(img, sigma):
    """Gaussian blur on the torus (2D field)."""
    h = np.exp(-2.0 * np.pi ** 2 * sigma ** 2 * (FX ** 2 + FY ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(img) * h))


def tuft_stamps(rng, count, size=20, radius=(2.8, 5.5), lean=-45.0):
    """Little grass tufts as a painter would dab them: a soft dome with a spiky
    (blade-tipped) outline, lit from the upper left and darker towards its
    lower-right rim, with a few light blade strokes on top. Returns (alpha,
    value) stacks; value is about -1 in shade and +1 in light. Rendered 4x
    supersampled."""
    ss = 4
    g = (np.arange(size * ss) + 0.5) / ss - size / 2
    gx, gy = np.meshgrid(g, g)
    th = np.arctan2(gy, gx)
    dist = np.hypot(gx, gy)
    alphas, values = [], []
    for _ in range(count):
        r = rng.uniform(*radius)
        k = rng.integers(5, 10)
        ph = rng.uniform(0, 2 * np.pi)
        spikes = np.abs(np.sin(k * th / 2 + ph)) ** 3
        rr = r * (0.82 + 0.32 * spikes + 0.08 * np.sin(2 * th + ph * 2))
        d = dist / rr
        A = np.clip((1.0 - d) * rr * 1.5 + 0.5, 0.0, 1.0)
        lit = -(gx + gy) / (r * 1.6)
        V = np.clip(0.15 + 0.75 * lit - 0.55 * d ** 2, -1.0, 1.0)
        # blade highlights from the middle towards the light
        for _ in range(rng.integers(2, 5)):
            a = np.radians(lean - 90 + rng.normal(0, 30))   # towards the upper left
            ln = r * rng.uniform(0.6, 1.1)
            ox, oy = rng.normal(0, r * 0.25, 2)
            dx, dy = np.cos(a), np.sin(a)
            px, py = gx - ox, gy - oy
            t = np.clip((px * dx + py * dy) / ln, 0.0, 1.0)
            dd = np.hypot(px - t * ln * dx, py - t * ln * dy)
            cov = np.clip((0.55 * (1 - 0.7 * t) - dd) * 3.0 + 0.5, 0.0, 1.0) * A
            V = V * (1 - cov * 0.8) + (0.6 + 0.5 * t) * cov * 0.8
        A = A.reshape(size, ss, size, ss).mean((1, 3))
        V = (V * 1.0).reshape(size, ss, size, ss).mean((1, 3))
        alphas.append(A)
        values.append(V)
    return alphas, values


def stamp(field, cover, cx, cy, A, V, alpha=1.0):
    """Composite one stamp (alpha A, value V) into field with wrap; nearer stamps
    painted later occlude earlier ones."""
    size = A.shape[0]
    x0 = int(round(cx)) - size // 2
    y0 = int(round(cy)) - size // 2
    iy = np.ix_(np.arange(y0, y0 + size) % N, np.arange(x0, x0 + size) % N)
    a = A * alpha
    field[iy] = field[iy] * (1 - a) + V * a
    cover[iy] = np.maximum(cover[iy], a)


def grass_base(rng):
    """Painterly lawn: broad sunlit and shaded drifts, darker rounded clumps and a
    dense carpet of soft blade tufts (dark roots, light tips)."""
    wx = noise(rng, 22) * 16
    wy = noise(rng, 22) * 16
    big = warp(fbm(rng, [48, 20], [1.0, 0.45]), wx, wy)
    mid = noise(rng, 14)
    clump = warp(noise(rng, 7), noise(rng, 8) * 5, noise(rng, 8) * 5)
    dark = smoothstep(0.4, 2.0, clump + 0.35 * big + 0.3 * noise(rng, 2.5))
    # tuft carpet
    A, V = tuft_stamps(rng, 48)
    S = np.zeros((N, N))
    C = np.zeros((N, N))
    D = np.zeros((N, N))
    ones = np.ones_like(A[0])
    for p in rng.random((7000, 2)) * N:
        k = rng.integers(len(A))
        a = rng.uniform(0.7, 1.0)
        stamp(S, C, p[0], p[1], A[k], V[k], alpha=a)
        # each tuft takes the clump shade at its root, so clump borders are tufty
        stamp(D, C, p[0], p[1], A[k], ones * dark[int(p[1]) % N, int(p[0]) % N], alpha=a)
    S = S * C - 0.6 * (1 - C)  # gaps between tufts are shaded
    D = D * C + dark * (1 - C)
    t = 0.66 + 0.07 * big + 0.06 * mid + 0.03 * noise(rng, 3) - 0.22 * D + 0.2 * S
    base = ramp(t, GRASS_RAMP)
    # warmer (yellower) where the sun sits, cooler in the clumps
    base[..., 0] += 6 * np.clip(big, -1.5, 1.5) - 8 * dark
    base[..., 2] += 5 * dark
    return base, dark


def make_grass():
    rng = np.random.default_rng(1001)
    base, _ = grass_base(rng)
    return base


def make_meadow():
    # same seed and recipe as grass, so the shared texels are identical and the
    # shader's noise-masked mix just fades the flowers in
    rng = np.random.default_rng(1001)
    img, dark = grass_base(rng)
    rng = np.random.default_rng(14014)
    # clover tufts: a few rounded dark trefoils
    for p in poisson_points(rng, 40, 30, mask=0.3 + 0.7 * dark):
        r = rng.uniform(2.0, 3.6)
        for k in range(3):
            a = rng.uniform(0, 2 * np.pi) if k == 0 else a + 2 * np.pi / 3
            ox, oy = np.cos(a) * r * 0.75, np.sin(a) * r * 0.75
            dab(img, p[0] + ox + 0.8, p[1] + oy + 0.9, r * 0.75, (40, 74, 22), alpha=0.35, soft=1.2)
            dab(img, p[0] + ox, p[1] + oy, r * 0.62, (66, 116, 40), alpha=0.9, soft=0.8, shade=0.35)
    # flowers in drifts
    zone = smoothstep(-0.2, 1.4, warp(noise(rng, 26), noise(rng, 12) * 10, noise(rng, 12) * 10))
    whites = [(246, 245, 232), (240, 240, 226), (250, 248, 238)]
    yellows = [(248, 226, 98), (244, 214, 74), (250, 236, 140)]
    violets = [(184, 150, 230), (166, 134, 222), (206, 176, 240)]
    centres = poisson_points(rng, 46, 44, mask=0.1 + 0.9 * zone)
    for c in centres:
        u = rng.random()
        pal = whites if u < 0.5 else (yellows if u < 0.85 else violets)
        count = rng.integers(25, 60)
        spread = rng.uniform(9, 22)
        for _ in range(count):
            p = c + rng.normal(0, spread, 2)
            r = rng.uniform(1.2, 2.0)
            col = pal[rng.integers(3)] if rng.random() < 0.85 else (whites + yellows)[rng.integers(6)]
            dab(img, p[0] + 0.7, p[1] + 0.9, r * 1.25, (44, 80, 22), alpha=0.4, soft=1.0)
            dab(img, p[0], p[1], r, col, alpha=0.95, soft=0.6)
            if pal is whites and r > 1.6:
                dab(img, p[0] + 0.2, p[1] + 0.2, 0.55, (232, 196, 64), alpha=0.9, soft=0.4)
    # loose singles
    for p in poisson_points(rng, 260, 10, mask=0.2 + 0.8 * zone):
        col = (whites + yellows + [violets[0]])[rng.integers(7)]
        dab(img, p[0] + 0.6, p[1] + 0.8, 1.5, (44, 80, 22), alpha=0.3, soft=1.0)
        dab(img, p[0], p[1], rng.uniform(1.0, 1.5), col, alpha=0.9, soft=0.6)
    return img


def make_sand():
    rng = np.random.default_rng(2002)
    big = fbm(rng, [40, 16], [1.0, 0.4])
    t = 0.5 + 0.17 * big
    base = ramp(t, [
        (0.0, (214, 196, 150)),
        (0.5, (232, 217, 174)),    # creamy beach #E8D9AE
        (1.0, (244, 234, 200)),
    ])
    # soft wind-smoothed ripples, very faint
    ph = 2 * np.pi * (3 * XX + 17 * YY) / N + noise(rng, 24) * 2.2
    rip = np.sin(ph + 0.35 * np.sin(ph))
    ripple_mask = smoothstep(-0.3, 0.8, noise(rng, 45))
    base *= (1.0 + 0.025 * rip * ripple_mask)[..., None]
    # grain
    g1 = noise(rng, 0.55)
    g2 = noise(rng, 1.4)
    base *= (1.0 + 0.03 * g1 + 0.025 * g2)[..., None]
    dark = smoothstep(2.3, 3.0, noise(rng, 0.6))
    base = mix(base, rgb(176, 150, 108), dark * 0.5)
    light = smoothstep(2.4, 3.1, noise(rng, 0.6))
    base = mix(base, rgb(252, 246, 228), light * 0.6)
    img = base
    # pebbles
    for p in poisson_points(rng, 30, 22):
        r = rng.uniform(0.9, 1.7)
        col = [(170, 160, 146), (188, 170, 140), (150, 140, 128), (206, 194, 168)][rng.integers(4)]
        dab(img, p[0] + 0.5, p[1] + 0.6, r * 1.1, (190, 166, 120), alpha=0.45, soft=1.0)
        dab(img, p[0], p[1], r, col, sy=rng.uniform(0.7, 1.0), angle=rng.uniform(0, 180), shade=0.35)
    # shells
    for p in poisson_points(rng, 16, 50):
        col = [(246, 236, 222), (240, 214, 200), (236, 224, 200)][rng.integers(3)]
        dab(img, p[0], p[1], rng.uniform(1.2, 1.9), col, sy=0.65, angle=rng.uniform(0, 180), shade=0.25)
    return img


def make_desert():
    rng = np.random.default_rng(3003)
    big = warp(fbm(rng, [42, 18], [1.0, 0.45]), noise(rng, 30) * 18, noise(rng, 30) * 18)
    t = 0.5 + 0.17 * big
    base = ramp(t, [
        (0.0, (176, 116, 70)),
        (0.45, (198, 140, 86)),
        (0.75, (212, 158, 100)),
        (1.0, (222, 174, 116)),
    ])
    # wind ripples (integer wave vector so they tile); asymmetric profile
    ph = 2 * np.pi * (4 * XX + 26 * YY) / N + noise(rng, 22) * 2.6
    s = np.sin(ph)
    rip = s + 0.45 * np.sin(2 * ph + 0.8)
    ripple_mask = smoothstep(-0.6, 0.6, noise(rng, 50))
    base *= (1.0 + 0.045 * rip * ripple_mask)[..., None]
    # faint cracks in the flats between ripple fields
    cw = noise(rng, 10) * 6
    f1, edge, idx = voronoi_edge(rng, 7, XX + cw, YY + noise(rng, 10) * 6)
    crack = 1.0 - smoothstep(0.4, 1.4, edge)
    f1b, edgeb, _ = voronoi_edge(rng, 15, XX + cw * 0.6, YY + noise(rng, 6) * 4)
    crack = np.maximum(crack, 0.6 * (1.0 - smoothstep(0.3, 1.1, edgeb)))
    crack_mask = (1.0 - ripple_mask) * smoothstep(-0.2, 0.7, noise(rng, 35))
    base = mix(base, base * 0.78, crack * crack_mask * 0.8)
    plate = rng.standard_normal(49)[idx]
    base *= (1.0 + 0.02 * plate * crack_mask)[..., None]
    # grain
    base *= (1.0 + 0.03 * noise(rng, 0.6) + 0.02 * noise(rng, 1.5))[..., None]
    img = base
    # sparse dry scrub specks, in small groups
    for c in poisson_points(rng, 34, 55):
        for _ in range(rng.integers(2, 7)):
            p = c + rng.normal(0, 5, 2)
            r = rng.uniform(0.8, 1.6)
            col = [(118, 104, 60), (104, 82, 52), (132, 116, 70)][rng.integers(3)]
            dab(img, p[0] + 0.7, p[1] + 0.7, r * 1.2, (150, 98, 58), alpha=0.45, soft=1.2)
            dab(img, p[0], p[1], r, col, alpha=0.9, soft=0.8)
    for p in poisson_points(rng, 30, 20):
        dab(img, p[0], p[1], rng.uniform(0.8, 1.3), (140, 108, 74), shade=0.3)
    return img


def make_snow():
    rng = np.random.default_rng(4004)
    # drifts: elongated soft shapes along one wind direction
    drift = fbm(rng, [40, 16], [1.0, 0.4])
    d2 = noise(rng, 16, sx=3.2, sy=1.0, angle=25)
    h = 0.45 * drift + 0.6 * d2
    h = warp(h, noise(rng, 25) * 10, noise(rng, 25) * 10)
    # soft baked light from the drift slope (gentle)
    gy, gx = np.gradient(np.pad(h, 2, mode="wrap"))
    gx = gx[2:-2, 2:-2]
    gy = gy[2:-2, 2:-2]
    slope = -(gx + gy)
    slope /= slope.std() + 1e-9
    t = 0.6 + 0.11 * h / h.std() + 0.09 * slope
    base = ramp(t, [
        (0.0, (186, 198, 216)),
        (0.35, (208, 218, 230)),
        (0.7, (228, 233, 240)),
        (1.0, (238, 241, 245)),
    ])
    base *= (1.0 + 0.012 * noise(rng, 0.8) + 0.012 * noise(rng, 2.0))[..., None]
    img = base
    # faint sparkle
    for p in poisson_points(rng, 260, 9):
        dab(img, p[0], p[1], 0.6, (252, 253, 255), alpha=rng.uniform(0.5, 0.9), soft=0.5)
    return img


def make_ash():
    rng = np.random.default_rng(5005)
    big = fbm(rng, [40, 15], [1.0, 0.5])
    t = 0.5 + 0.17 * big
    base = ramp(t, [
        (0.0, (78, 70, 66)),
        (0.5, (100, 92, 86)),
        (1.0, (124, 114, 104)),
    ])
    w1 = noise(rng, 12) * 7
    w2 = noise(rng, 12) * 7
    f1, edge, idx = voronoi_edge(rng, 6, XX + w1, YY + w2)
    plate = rng.standard_normal(36)[idx]
    base *= (1.0 + 0.05 * plate)[..., None]
    # plates are slightly raised in the middle and dip to the cracks
    lip = smoothstep(0.0, 14.0, edge)
    base *= (0.9 + 0.1 * lip)[..., None]
    width = 1.1 + 0.7 * smoothstep(-1, 1.5, noise(rng, 20))
    crack = 1.0 - smoothstep(width * 0.5, width + 0.7, edge)
    _, edge2, _ = voronoi_edge(rng, 14, XX + w1 * 0.7 + noise(rng, 4) * 3, YY + w2 * 0.7 + noise(rng, 4) * 3)
    crack2 = (1.0 - smoothstep(0.2, 0.9, edge2)) * smoothstep(-0.5, 0.8, noise(rng, 18))
    base = mix(base, rgb(40, 35, 33), crack * 0.95)
    base = mix(base, rgb(62, 56, 52), crack2 * 0.6)
    # a faint light rim along the crack lips
    rim = smoothstep(width + 0.4, width + 1.6, edge) * (1 - smoothstep(width + 1.6, width + 3.2, edge))
    base *= (1.0 + 0.06 * rim)[..., None]
    base *= (1.0 + 0.04 * noise(rng, 0.6) + 0.03 * noise(rng, 1.8))[..., None]
    img = base
    for p in poisson_points(rng, 110, 16):
        r = rng.uniform(1.0, 2.6)
        col = [(52, 47, 46), (60, 54, 52), (44, 40, 40), (72, 64, 58)][rng.integers(4)]
        dab(img, p[0] + r * 0.45, p[1] + r * 0.55, r * 1.15, (40, 36, 34), alpha=0.45, soft=1.5)
        dab(img, p[0], p[1], r, col, sy=rng.uniform(0.65, 1.0), angle=rng.uniform(0, 180), shade=0.45)
    return img


def make_gravel():
    rng = np.random.default_rng(6006)
    base = ramp(0.5 + 0.15 * fbm(rng, [30, 10]), [
        (0.0, (30, 30, 32)), (1.0, (46, 44, 44))])  # gaps between stones
    img = base

    def layer(grid, size_scale, jitter, palette):
        nonlocal img
        wx = noise(rng, 6) * 2.0
        wy = noise(rng, 6) * 2.0
        f1, f2, idx, vx, vy = voronoi(rng, grid, XX + wx, YY + wy, jitter=jitter)
        cell = N / grid
        cnt = grid * grid
        # stone radius: inside its cell, a bit irregular
        rad = cell * size_scale * (0.75 + 0.35 * rng.random(cnt))[idx]
        border = (f2 - f1)  # distance-ish to the neighbour cell
        inside = smoothstep(0.4, 1.6, border) * (1 - smoothstep(rad - 1.0, rad + 0.3, f1))
        h = np.sqrt(np.clip(1.0 - (f1 / rad) ** 2, 0.0, 1.0))
        choice = rng.integers(len(palette), size=cnt)[idx]
        col = np.array(palette, np.float64)[choice]
        col *= (1.0 + 0.12 * rng.standard_normal(cnt)[idx])[..., None]
        # soft rounded shading and a small wet highlight towards the upper left
        nx = vx / rad
        ny = vy / rad
        light = 0.72 + 0.28 * h - 0.18 * (nx + ny)
        col *= light[..., None]
        spec = np.exp(-(((nx + 0.32) ** 2 + (ny + 0.36) ** 2) / 0.035))
        col += (spec * 70 * (0.5 + 0.5 * rng.random(cnt)[idx]))[..., None]
        col *= (1.0 + 0.05 * noise(rng, 0.7))[..., None]
        img = mix(img, col, inside)

    pal = [(82, 82, 86), (70, 72, 78), (96, 92, 88), (88, 78, 70), (60, 62, 66), (108, 106, 104), (76, 70, 64)]
    layer(64, 0.62, 0.95, pal)
    layer(36, 0.6, 0.85, pal)
    return img


def make_farmland():
    rng = np.random.default_rng(7007)
    period = 32.0  # 8 rows per 256 texels; rows run along x
    wob = noise1d(rng, 40) * 2.2 + noise1d(rng, 9) * 0.6
    yw = YY + wob[None, :].repeat(N, 0) + noise(rng, 14) * 1.4
    ph = 2 * np.pi * yw / period
    ridge = 0.5 + 0.5 * np.cos(ph)  # 1 at row centres
    # soil
    soil_t = 0.5 + 0.15 * fbm(rng, [30, 8])
    soil = ramp(soil_t, [(0.0, (98, 68, 44)), (0.5, (116, 82, 54)), (1.0, (134, 98, 64))])
    soil *= (0.8 + 0.22 * ridge)[..., None]  # furrow bottoms darker
    soil *= (1.0 + 0.05 * noise(rng, 0.7) + 0.04 * noise(rng, 2.0))[..., None]
    # clods
    clod = smoothstep(1.4, 2.4, noise(rng, 1.3))
    soil = mix(soil, soil * 1.15, clod * 0.6)

    # crops: little plants along each ridge, golden to green by patch
    hue = 0.5 + 0.2 * fbm(rng, [55, 22], [1.0, 0.3])
    crop_col = ramp(hue, [
        (0.0, (122, 152, 54)),
        (0.35, (150, 164, 62)),
        (0.65, (184, 170, 72)),
        (1.0, (206, 174, 86)),
    ])
    # plants: stretched along x so the rows read as lines of leaves
    plants = noise(rng, 1.1, sx=1.0, sy=1.6) * 0.6 + noise(rng, 2.6, sx=1.0, sy=1.5) * 0.4
    density = 0.15 * noise(rng, 22)  # slight unevenness: thinner and fuller stretches
    cover = smoothstep(0.38, 0.75, ridge + 0.18 * plants + density)
    leaf_light = 1.0 + 0.10 * plants + 0.12 * (ridge - 0.6)
    crops = crop_col * leaf_light[..., None]
    # soft shadow of the crop row on the furrow
    shadow_ph = 2 * np.pi * (yw - 2.5) / period
    shadow = smoothstep(0.3, 0.8, 0.5 + 0.5 * np.cos(shadow_ph)) * (1 - cover)
    soil *= (1.0 - 0.15 * shadow)[..., None]
    return mix(soil, crops, cover)


def make_scorched():
    rng = np.random.default_rng(8008)
    big = warp(fbm(rng, [30, 12], [1.0, 0.5]), noise(rng, 20) * 10, noise(rng, 20) * 10)
    t = 0.5 + 0.18 * big
    base = ramp(t, [
        (0.0, (30, 25, 23)),
        (0.5, (46, 37, 32)),
        (1.0, (66, 54, 46)),
    ])
    # grey ash drifts
    ash = smoothstep(0.0, 1.8, warp(fbm(rng, [16, 5], [1.0, 0.4]), noise(rng, 10) * 6, noise(rng, 10) * 6))
    ash_col = ramp(0.5 + 0.2 * noise(rng, 3), [(0.0, (84, 80, 76)), (1.0, (114, 108, 102))])
    dust = 0.5 + 0.5 * np.clip(noise(rng, 1.0), -1, 1)
    base = mix(base, ash_col, ash * (0.55 + 0.25 * dust))
    # char streaks and burnt stubble
    streak = smoothstep(1.3, 2.3, noise(rng, 1.6))
    base = mix(base, rgb(20, 17, 16), streak * 0.5)
    stub = smoothstep(1.8, 2.6, noise(rng, 0.7))
    base = mix(base, rgb(84, 66, 48), stub * 0.5 * (1 - ash))
    base *= (1.0 + 0.06 * noise(rng, 0.6) + 0.04 * noise(rng, 2.0))[..., None]
    img = base
    for p in poisson_points(rng, 70, 22, mask=0.2 + 0.8 * (1 - ash)):
        r = rng.uniform(0.7, 1.4)
        dab(img, p[0], p[1], r * 2.6, (120, 52, 24), alpha=0.25, soft=2.5)
        col = [(176, 78, 30), (196, 102, 40), (150, 62, 28)][rng.integers(3)]
        dab(img, p[0], p[1], r, col, alpha=0.9, soft=0.7)
    return img


def make_earth():
    rng = np.random.default_rng(9009)
    # y runs down. Strata as a 1D colour profile over depth (periodic over N),
    # sampled at a wobbled depth so the bands wave along the edge.
    bands = [  # (thickness, colour)
        (40, (66, 46, 32)),     # topsoil
        (14, (84, 58, 38)),
        (34, (112, 76, 48)),    # brown loam
        (10, (128, 86, 52)),
        (46, (160, 98, 56)),    # orange clay
        (8, (140, 86, 52)),
        (30, (176, 124, 76)),   # pale sandy clay
        (24, (150, 92, 56)),
        (44, (126, 80, 52)),    # brown
        (12, (168, 104, 58)),
        (40, (186, 118, 64)),   # orange
        (22, (146, 98, 66)),
        (36, (118, 82, 58)),
        (26, (154, 104, 64)),
        (48, (134, 84, 54)),
        (20, (110, 74, 50)),
        (52, (96, 66, 46)),     # darker deep band, meets the topsoil when tiled
    ]
    total = sum(b[0] for b in bands)
    scale = N / total
    edges = np.cumsum([0] + [b[0] * scale for b in bands])
    cols = np.array([b[1] for b in bands], np.float64)
    depth = np.arange(N * 4) * 0.25
    prof = np.zeros((len(depth), 3))
    # soft transitions between bands
    centres = (edges[:-1] + edges[1:]) / 2
    for i, d in enumerate(depth):
        k = np.searchsorted(edges, d, side="right") - 1
        k = min(max(k, 0), len(bands) - 1)
        lo, hi = edges[k], edges[k + 1]
        c = cols[k]
        blend = 2.5
        if d - lo < blend:
            prev = cols[(k - 1) % len(bands)]
            t = 0.5 + 0.5 * (d - lo) / blend
            c = prev * (1 - t) + c * t
        elif hi - d < blend:
            nxt = cols[(k + 1) % len(bands)]
            t = 0.5 + 0.5 * (hi - d) / blend
            c = nxt * (1 - t) + c * t
        prof[i] = c
    wob = noise1d(rng, 50) * 6 + noise1d(rng, 14) * 2.0
    d = YY + wob[None, :].repeat(N, 0) + noise(rng, 10) * 2.2
    di = (np.floor(d * 4).astype(np.int64)) % (N * 4)
    img = prof[di]
    # horizontal streaks and grain inside the bands
    streak = noise(rng, 1.2, sx=6.0, sy=1.0) * 0.5 + noise(rng, 3, sx=5.0, sy=1.0) * 0.5
    img *= (1.0 + 0.05 * streak + 0.035 * noise(rng, 0.6))[..., None]
    # clay mottling
    mott = smoothstep(1.0, 2.0, noise(rng, 4))
    img = mix(img, img * rgb(1.08, 1.0, 0.9), mott * 0.5)

    # roots in the topsoil
    top_end = edges[2] + 10
    for _ in range(26):
        x = rng.uniform(0, N)
        y = rng.uniform(0, edges[1])
        ang = np.pi / 2 + rng.normal(0, 0.5)
        length = rng.uniform(20, 70)
        thick = rng.uniform(0.55, 1.0)
        col = (150, 116, 80) if rng.random() < 0.6 else (52, 36, 26)
        step = 0.7
        travelled = 0.0
        while travelled < length:
            dab(img, x, y, thick * (1 - 0.5 * travelled / length), col, alpha=0.85, soft=0.6)
            ang += rng.normal(0, 0.18)
            ang = np.clip(ang, 0.3, np.pi - 0.3)
            x += np.cos(ang) * step
            y += np.sin(ang) * step
            travelled += step
            if rng.random() < 0.012:  # little side root
                bx, by, ba = x, y, ang + rng.choice([-1, 1]) * 0.8
                for _ in range(int(rng.uniform(6, 18))):
                    dab(img, bx, by, thick * 0.45, col, alpha=0.7, soft=0.6)
                    bx += np.cos(ba) * step
                    by += np.sin(ba) * step
                    ba += rng.normal(0, 0.2)
    # embedded stones
    for p in poisson_points(rng, 60, 30):
        r = rng.uniform(2.2, 7.5)
        if p[1] < top_end:
            r *= 0.6
        col = [(128, 122, 114), (110, 104, 98), (146, 136, 120), (96, 90, 86), (156, 120, 90)][rng.integers(5)]
        dab(img, p[0] + r * 0.25, p[1] + r * 0.35, r * 1.12, (60, 42, 30), alpha=0.5, soft=1.5)
        dab(img, p[0], p[1], r, col, sy=rng.uniform(0.55, 0.85), angle=rng.uniform(-25, 25), shade=0.4)
    # pebble specks
    for p in poisson_points(rng, 120, 10):
        dab(img, p[0], p[1], rng.uniform(0.7, 1.3), (150, 138, 120), shade=0.3, alpha=0.8)
    return img


def make_water():
    """Deep saturated sea with fine wind ripples. Kept mid-tone and low in
    contrast: the shader adds depth colour, foam and sparkles on top."""
    rng = np.random.default_rng(10010)
    big = fbm(rng, [44, 18], [1.0, 0.5])
    t = 0.5 + 0.16 * big
    base = ramp(t, [
        (0.0, (14, 63, 140)),      # #0E3F8C
        (0.5, (26, 85, 168)),      # #1A55A8
        (1.0, (30, 98, 186)),
    ])
    # ripples: trains of short wavelets. Each train is a warped sine (integer
    # wave vector, so it tiles) cut into dashes along the crest by an elongated
    # noise; light on the crest, a soft darker trough just behind it
    calm = 0.45 + 0.55 * smoothstep(-1.2, 0.8, noise(rng, 26))
    crest = np.zeros((N, N))
    trough = np.zeros((N, N))
    for kx, ky, amp, sig in [(3, 40, 1.0, 2.2), (-6, 66, 0.7, 1.5), (9, 28, 0.6, 3.2)]:
        ph = 2 * np.pi * (kx * XX + ky * YY) / N + noise(rng, 14) * 2.2 + noise(rng, 5) * 0.7
        dash = smoothstep(0.1, 1.3, noise(rng, sig, sx=3.5, sy=1.0, angle=np.degrees(np.arctan2(kx, -ky))))
        sw = np.sin(ph)
        crest += amp * smoothstep(0.55, 0.95, sw) * dash
        trough += amp * smoothstep(0.2, 0.9, -np.sin(ph - 0.9)) * dash
    crest = np.clip(crest * calm, 0, 1)
    trough = np.clip(trough * calm, 0, 1)
    base = mix(base, base * rgb(0.8, 0.86, 0.92), trough * 0.45)
    base = mix(base, rgb(50, 122, 204), crest * 0.5)
    base *= (1.0 + 0.025 * noise(rng, 1.2, sx=3.0, sy=1.0))[..., None]
    return base


def make_lava():
    rng = np.random.default_rng(11011)
    w1 = noise(rng, 20) * 14 + noise(rng, 6) * 3
    w2 = noise(rng, 20) * 14 + noise(rng, 6) * 3
    f1, edge, idx = voronoi_edge(rng, 6, XX + w1, YY + w2)
    _, edge2, _ = voronoi_edge(rng, 13, XX + w1 * 0.8 + noise(rng, 5) * 3, YY + w2 * 0.8 + noise(rng, 5) * 3)
    width = 2.2 + 3.5 * smoothstep(-1.0, 1.4, noise(rng, 25))
    vein = np.exp(-(edge / width) ** 2)
    minor = np.exp(-(edge2 / 1.1) ** 2) * smoothstep(-0.3, 1.0, noise(rng, 16)) * 0.75
    heat = np.clip(np.maximum(vein, minor), 0, 1)
    # crust plates: dark, slightly varied, rough
    plate = rng.standard_normal(36)[idx]
    crust_t = 0.5 + 0.12 * plate + 0.12 * fbm(rng, [8, 2.5]) + 0.06 * noise(rng, 0.7)
    crust = ramp(crust_t, [(0.0, (26, 20, 20)), (0.5, (44, 34, 32)), (1.0, (66, 52, 46))])
    # crust warms near the veins
    glow = np.exp(-(edge / (width * 3.0)) ** 2)
    crust = mix(crust, rgb(120, 44, 20), glow * 0.45)
    molten = ramp(heat, [
        (0.0, (150, 40, 18)),
        (0.35, (214, 76, 22)),
        (0.7, (246, 140, 34)),
        (1.0, (255, 214, 96)),
    ])
    molten *= (1.0 + 0.06 * noise(rng, 2.0))[..., None]
    cover = smoothstep(0.18, 0.45, heat)
    img = mix(crust, molten, cover)
    # a few glowing pin-holes in the crust
    for p in poisson_points(rng, 40, 30):
        dab(img, p[0], p[1], rng.uniform(0.6, 1.2), (236, 110, 30), alpha=0.8, soft=0.8)
    return img


def make_rock():
    """Coastal granite seen from above: clustered rounded boulders and a few low
    shelves, lit from the upper left (-x, -y), dark crevices between them, moss
    and lichen on the tops."""
    rng = np.random.default_rng(13013)
    H = np.zeros((N, N))          # height in texels; 0 is the crevice floor
    sid = np.full((N, N), -1)     # which stone owns the texel
    bump = fbm(rng, [16, 6, 2.5], [1.0, 0.4, 0.08])
    placed = []

    def place(count, rmin, rmax, gap, flat=0.0, tries=60):
        for _ in range(count * tries):
            if count <= 0:
                break
            p = rng.random(2) * N
            r = rng.uniform(rmin, rmax)
            ok = True
            for q, rq in placed:
                dx = abs(p[0] - q[0]); dx = min(dx, N - dx)
                dy = abs(p[1] - q[1]); dy = min(dy, N - dy)
                if dx * dx + dy * dy < ((r + rq) * gap) ** 2:
                    ok = False
                    break
            if not ok:
                continue
            placed.append((p, r))
            count -= 1
            boulder(p, r, flat=(rng.random() < flat))

    def boulder(p, r, flat=False):
        nonlocal H, sid
        i = len(placed) - 1
        sy = rng.uniform(0.7, 1.0)
        ang = rng.uniform(0, np.pi)
        ext = int(r * 1.35) + 2
        xs = np.arange(int(p[0]) - ext, int(p[0]) + ext + 1)
        ys = np.arange(int(p[1]) - ext, int(p[1]) + ext + 1)
        gx, gy = np.meshgrid(xs + 0.5 - p[0], ys + 0.5 - p[1])
        u = gx * np.cos(ang) + gy * np.sin(ang)
        v = (-gx * np.sin(ang) + gy * np.cos(ang)) / sy
        th = np.arctan2(v, u)
        rr = r * (1.0 + sum(rng.normal(0, 0.14 / k) * np.cos(k * th + rng.uniform(0, 6.3))
                            for k in range(2, 6)))
        d = np.sqrt(u * u + v * v) / rr
        if flat:   # a low rock shelf: flat top, steep rim
            h = r * 0.3 * np.clip(1.0 - d ** 6, 0.0, 1.0) ** 0.5
        else:      # a rounded granite boulder, a little flattened on top
            h = r * rng.uniform(0.38, 0.55) * np.clip(1.0 - d ** 2.6, 0.0, 1.0) ** 0.6
        iy = np.ix_(ys % N, xs % N)
        h = np.where(d < 1.0, h + bump[iy] * r * 0.07 * np.clip(1.4 - d, 0, 1) + 1.0, 0.0)
        win = h > H[iy]
        H[iy] = np.where(win, h, H[iy])
        sid[iy] = np.where(win, i, sid[iy])

    place(14, 42, 64, 0.5, flat=0.3)
    place(22, 24, 40, 0.55, flat=0.15)
    # pack the gaps: repeatedly drop a stone into (one of) the roomiest spots,
    # tracked on a coarse grid as the distance to the nearest stone's rim
    step = 4
    cg = (np.arange(N // step) + 0.5) * step
    cx, cy = np.meshgrid(cg, cg)

    def rim_dist(p, r):
        dx = np.abs(cx - p[0]); dx = np.minimum(dx, N - dx)
        dy = np.abs(cy - p[1]); dy = np.minimum(dy, N - dy)
        return np.sqrt(dx * dx + dy * dy) - r

    free = np.full(cx.shape, 1e9)
    for q, rq in placed:
        free = np.minimum(free, rim_dist(q, rq))
    for _ in range(600):
        top = free.max()
        if top < 3.5:
            break
        cand = np.argwhere(free > top * 0.75)
        j, i = cand[rng.integers(len(cand))]
        p = np.array([cx[j, i], cy[j, i]]) + rng.uniform(-1, 1, 2)
        if top > 10:   # boulders into the big gaps
            r = float(np.clip(free[j, i] * rng.uniform(1.25, 1.5), 12.0, 36.0))
        else:          # rubble into the cracks between them
            r = float(np.clip(free[j, i] * rng.uniform(0.8, 1.1), 2.5, 7.0))
        placed.append((p, r))
        boulder(p, r, flat=(r > 18 and rng.random() < 0.15))
        free = np.minimum(free, rim_dist(p, r))
    nstones = len(placed)

    # surface lighting: normal from the height field, sun at the upper left
    gx, gy = grad(H)
    nz = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
    nx, ny = -gx * nz, -gy * nz
    L = np.array([-0.55, -0.55, 0.63])
    L /= np.linalg.norm(L)
    diff = nx * L[0] + ny * L[1] + nz * L[2]
    lit = np.clip(diff, 0.0, 1.0)
    # cast shadows towards the lower right
    sh = np.zeros((N, N))
    for k in range(1, 40):
        sh = np.maximum(sh, np.roll(H, (k, k), (0, 1)) - H - k * 1.414 * 0.85)
    shadow = smoothstep(0.0, 3.0, sh)
    # ambient occlusion: deep where the surroundings stand higher
    cav = np.clip((blur(H, 6) - H) / 6.0, 0.0, 1.0)
    occl = 1.0 - 0.55 * cav

    stone = sid >= 0
    # stone colour drifts slowly across the tile (cool grey to warm grey), with
    # a little variation per stone
    drift = noise(rng, 40)
    cpos = np.array([q for q, _ in placed]).astype(np.int64) % N
    tint = ramp(np.clip(0.5 + 0.35 * drift[cpos[:, 1], cpos[:, 0]] + 0.15 * rng.standard_normal(nstones), 0, 1),
                [(0.0, (134, 132, 128)), (0.5, (142, 137, 127)), (1.0, (148, 139, 122))])
    tint *= (1.0 + 0.04 * rng.standard_normal(nstones))[:, None]
    base = np.where(stone[..., None], tint[np.maximum(sid, 0)], 0.0) / rgb(146, 140, 128)
    light = (0.25 + 0.95 * lit * (1.0 - 0.75 * shadow)) * occl
    t = np.clip(light + 0.04 * noise(rng, 2.0), 0.0, 1.2)
    rock = ramp(t, [
        (0.00, (58, 54, 50)),
        (0.30, (96, 90, 82)),
        (0.55, (138, 132, 120)),   # #8A8478
        (0.80, (163, 156, 142)),   # #A39C8E
        (1.00, (178, 171, 156)),
        (1.20, (190, 183, 168)),
    ]) * base
    # granite grain: soft dark and light flecks
    rock = mix(rock, rock * 0.78, smoothstep(1.8, 2.7, noise(rng, 0.9)) * 0.5)
    rock = mix(rock, rock * 1.08 + 6, smoothstep(2.0, 2.9, noise(rng, 0.9)) * 0.4)
    # cracks across the bigger stones
    w1 = noise(rng, 10) * 7
    _, edge, _ = voronoi_edge(rng, 7, XX + w1, YY + noise(rng, 10) * 7)
    # only well inside the bigger stones, so they read as cracks in the rock
    # rather than lines drawn over it
    top = np.zeros(nstones)
    np.maximum.at(top, sid[stone], H[stone])
    inner = smoothstep(0.35, 0.6, H / np.maximum(top[np.maximum(sid, 0)], 1e-6)) * (stone & (H > 8))
    crack = (1.0 - smoothstep(0.25, 1.0, edge)) * inner * smoothstep(0.0, 0.8, noise(rng, 14))
    lip = np.clip(np.roll(crack, (1, 1), (0, 1)) - crack, 0, 1)   # the wall facing the light
    rock = mix(rock, rgb(78, 74, 68), crack * 0.8)
    rock = mix(rock, rock * 1.12 + 6, lip * 0.6)
    # crevice floor: dark earth and grit with a little green
    floor_c = ramp(0.5 + 0.2 * noise(rng, 3), [(0.0, (46, 42, 36)), (1.0, (70, 66, 52))])
    floor_c = mix(floor_c, rgb(66, 84, 36), smoothstep(0.6, 1.6, noise(rng, 5)) * 0.6)
    floor_c *= (0.7 + 0.3 * (1 - shadow))[..., None]
    img = mix(floor_c, rock, smoothstep(0.0, 1.0, H))
    # moss on upward-facing tops, in patches; lichen as ochre and pale specks
    up = smoothstep(0.86, 0.98, nz) * stone
    moss_m = smoothstep(0.45, 1.7, warp(fbm(rng, [12, 4, 1.2], [1.0, 0.5, 0.25]), noise(rng, 6) * 4, noise(rng, 6) * 4)) * up
    moss_c = ramp(0.5 + 0.25 * noise(rng, 1.5), [(0.0, (84, 104, 40)), (1.0, (126, 142, 56))])
    moss_c *= (0.55 + 0.6 * lit * (1 - 0.6 * shadow))[..., None]
    img = mix(img, moss_c, moss_m * 0.75)
    lich = smoothstep(1.7, 2.5, noise(rng, 2.6) + 0.4 * noise(rng, 0.8)) * smoothstep(0.75, 0.95, nz) * stone
    lich_c = np.where((noise(rng, 6) > 0)[..., None], rgb(196, 162, 80), rgb(206, 198, 166))
    img = mix(img, lich_c * (0.7 + 0.4 * lit)[..., None], lich * 0.75)
    return img


def make_noise():
    """Smooth grayscale fbm for blend masks: continuous (no flat plateaus or
    bands), spanning the full 0..255 range, tileable. Mostly rank-equalised so
    thresholds anywhere in 0..1 cover a predictable share of the tile."""
    rng = np.random.default_rng(12012)
    v = fbm(rng, [34, 17, 9, 5.5], [1.0, 0.5, 0.22, 0.07])
    v = 0.6 * equalize(v) + 0.4 * to01(v)
    return np.repeat(to01(v)[..., None] * 255.0, 3, axis=2)


TEXTURES = [
    ("grass", make_grass),
    ("sand", make_sand),
    ("desert", make_desert),
    ("snow", make_snow),
    ("ash", make_ash),
    ("gravel", make_gravel),
    ("farmland", make_farmland),
    ("scorched", make_scorched),
    ("earth", make_earth),
    ("water", make_water),
    ("lava", make_lava),
    ("noise", make_noise),
    ("rock", make_rock),
    ("meadow", make_meadow),
]


# ---------------------------------------------------------------------------
# Checks and previews

def seam_report(name, a):
    """Compare the jump across each wrap edge with the typical jump between
    neighbouring rows/columns inside the tile. A ratio near 1 means no seam."""
    a = a.astype(np.float64)
    inner_x = np.abs(np.diff(a, axis=1)).mean()
    inner_y = np.abs(np.diff(a, axis=0)).mean()
    edge_x = np.abs(a[:, 0] - a[:, -1]).mean()
    edge_y = np.abs(a[0, :] - a[-1, :]).mean()
    rx = edge_x / max(inner_x, 1e-9)
    ry = edge_y / max(inner_y, 1e-9)
    flag = "OK" if max(rx, ry) < 1.5 else "SEAM?"
    print(f"  {name:9s} left/right {edge_x:6.2f} vs {inner_x:6.2f} (x{rx:4.2f})   "
          f"top/bottom {edge_y:6.2f} vs {inner_y:6.2f} (x{ry:4.2f})   {flag}")
    return max(rx, ry)


def font(size):
    for path in ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
                 "/Library/Fonts/Arial.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def contact_sheet(images):
    cell = 512  # each texture tiled 2x2 at half size
    pad = 16
    label = 34
    cols = 4
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, rows * (cell + pad + label) + pad), (36, 36, 40))
    draw = ImageDraw.Draw(sheet)
    f = font(24)
    for i, (name, img) in enumerate(images):
        tiled = Image.fromarray(np.tile(img, (2, 2, 1))).resize((cell, cell), Image.LANCZOS)
        x = pad + (i % cols) * (cell + pad)
        y = pad + (i // cols) * (cell + pad + label)
        draw.text((x, y), name + ".png", fill=(230, 230, 230), font=f)
        sheet.paste(tiled, (x, y + label))
    return sheet


SCREEN_SCALE = 190.0 / (256.0 * np.sqrt(2.0))  # screen px per texel along the diagonal


def iso(arr, tiles=3, light=0.95):
    """A tiles x tiles tiling as the game shows it: turned 45 degrees, squashed 2:1,
    at full-HD scale, lit by a gentle constant light."""
    big = np.tile(arr, (tiles, tiles, 1)) if arr.shape[0] == N else arr
    im = Image.fromarray(np.clip(big * light, 0, 255).astype(np.uint8))
    # downsample before rotating (the GPU would use mipmaps)
    s = SCREEN_SCALE * 1.0
    im = im.resize((int(im.width * s * 2), int(im.height * s * 2)), Image.LANCZOS)
    im = im.rotate(45, resample=Image.BICUBIC, expand=True)
    im = im.resize((im.width // 2, im.height // 4), Image.LANCZOS)
    return im


def crop_center(im, w, h):
    cx, cy = im.width // 2, im.height // 2
    return im.crop((cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h))


def blend_scene(tex):
    """An island edge, 6x6 cells, as the shader might blend it with soft noisy
    borders: grass with meadow drifts (mixed by the noise layer), a farmed patch
    and a rocky outcrop inland, and a coast that is rocky with a sandy cove."""
    rng = np.random.default_rng(99)
    size = 3 * N
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    t3 = lambda name: np.tile(tex[name], (3, 3, 1)).astype(np.float64)
    nz = np.tile(tex["noise"][..., 0], (3, 3)) / 255.0
    jag = np.tile(noise(rng, 20), (3, 3)) * 40 + np.tile(noise(rng, 5), (3, 3)) * 8
    # grass and meadow drifts
    img = mix(t3("grass"), t3("meadow"), smoothstep(0.5, 0.65, nz))
    # beach along one side (diagonal on screen), water beyond it; the coast is
    # rock except for a sandy cove in the middle
    coast = xx + jag
    shore_w = smoothstep(size * 0.70, size * 0.70 + 40, coast)
    water_w = smoothstep(size * 0.86, size * 0.86 + 8, coast)
    cove = smoothstep(size * 0.3, size * 0.38, yy + jag) * (1 - smoothstep(size * 0.62, size * 0.7, yy + jag))
    farm_w = (smoothstep(size * 0.24 - 30, size * 0.24 + 30, xx + jag * 0.5) *
              (1 - smoothstep(size * 0.52 - 30, size * 0.52 + 30, xx + jag * 0.5)) *
              smoothstep(size * 0.30 - 30, size * 0.30 + 30, yy + jag * 0.5) *
              (1 - smoothstep(size * 0.62 - 30, size * 0.62 + 30, yy + jag * 0.5)))
    outcrop = smoothstep(150, 120, np.hypot(xx - size * 0.15, yy - size * 0.8) + jag)
    img = mix(img, t3("farmland"), farm_w)
    img = mix(img, t3("rock"), np.maximum(outcrop, shore_w * (1 - cove)))
    img = mix(img, t3("sand"), shore_w * cove)
    img = mix(img, t3("water"), water_w)
    return img


def iso_sheet(tex):
    pw, ph = 520, 260
    pad = 14
    label = 30
    cols = 4
    names = [n for n, _ in TEXTURES]
    rows = (len(names) + cols - 1) // cols
    scene = iso(blend_scene(tex))
    scene = crop_center(scene, cols * (pw + pad) - pad, 520)
    width = cols * (pw + pad) + pad
    height = pad + label + scene.height + pad + rows * (ph + label + pad)
    sheet = Image.new("RGB", (width, height), (36, 36, 40))
    draw = ImageDraw.Draw(sheet)
    f = font(20)
    draw.text((pad, pad), "grass + meadow / farmland / rock / sand / water, blended (6x6 cells at full-HD scale)",
              fill=(230, 230, 230), font=f)
    sheet.paste(scene, (pad, pad + label))
    y0 = pad + label + scene.height + pad
    for i, name in enumerate(names):
        im = crop_center(iso(tex[name]), pw, ph)
        x = pad + (i % cols) * (pw + pad)
        y = y0 + (i // cols) * (ph + label + pad)
        draw.text((x, y), name, fill=(230, 230, 230), font=f)
        sheet.paste(im, (x, y + label))
    return sheet


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    only = set(sys.argv[1:])
    tex = {}
    print("Seam check (edge jump vs. typical neighbour jump):")
    worst = 0.0
    for name, fn in TEXTURES:
        arr = np.clip(np.round(fn()), 0, 255).astype(np.uint8)
        tex[name] = arr
        if not only or name in only:
            Image.fromarray(arr).save(os.path.join(OUT_DIR, name + ".png"), optimize=True)
        worst = max(worst, seam_report(name, arr))
        m = arr.reshape(-1, 3).mean(0)
        print(f"            mean colour ({m[0]:.0f}, {m[1]:.0f}, {m[2]:.0f})")
    contact_sheet([(n, tex[n]) for n, _ in TEXTURES]).save(os.path.join(PREVIEW_DIR, "textures.png"))
    iso_sheet(tex).save(os.path.join(PREVIEW_DIR, "textures_iso.png"))
    print(f"Worst seam ratio {worst:.2f}")
    print("Wrote", OUT_DIR)
    print("Wrote", os.path.join(PREVIEW_DIR, "textures.png"), "and textures_iso.png")


if __name__ == "__main__":
    main()
