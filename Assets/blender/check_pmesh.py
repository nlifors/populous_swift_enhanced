#!/usr/bin/env python3
"""Validate every .pmesh written by build_assets.py.

Checks the header, that the vertex count is a multiple of 3, no NaN/inf,
unit-length normals, colours in 0..1, surface codes in {3,4,5}, and
reports triangle counts and bounding boxes.

    python3 Assets/blender/check_pmesh.py [model_dir]
"""
import glob
import math
import os
import struct
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "Sources", "PopulousHD", "Resources", "Models")


def check(path):
    errs = []
    data = open(path, "rb").read()
    if data[:4] != b"PMSH":
        return ["bad magic"], None
    ver, n = struct.unpack_from("<II", data, 4)
    if ver != 1:
        errs.append("version %d" % ver)
    if n % 3:
        errs.append("N=%d not a multiple of 3" % n)
    if len(data) != 12 + n * 40:
        errs.append("size %d != %d" % (len(data), 12 + n * 40))
        return errs, None
    f = struct.unpack_from("<%df" % (n * 10), data, 12)
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    surf = {}
    worst_n = 0.0
    for i in range(n):
        v = f[i * 10:(i + 1) * 10]
        if any(math.isnan(x) or math.isinf(x) for x in v):
            errs.append("NaN/inf at vertex %d" % i)
            break
        for k in range(3):
            lo[k] = min(lo[k], v[k])
            hi[k] = max(hi[k], v[k])
        worst_n = max(worst_n, abs(math.sqrt(v[3] ** 2 + v[4] ** 2 + v[5] ** 2) - 1))
        if not all(0.0 <= c <= 1.0 for c in v[6:9]):
            errs.append("colour out of range at vertex %d" % i)
            break
        surf[v[9]] = surf.get(v[9], 0) + 1
    if worst_n > 1e-3:
        errs.append("normal length off by %.4f" % worst_n)
    if not set(surf) <= {3.0, 4.0, 5.0}:
        errs.append("unexpected surface codes %s" % sorted(surf))
    return errs, (n // 3, lo, hi, {k: v // 3 for k, v in surf.items()})


def main():
    files = sorted(glob.glob(os.path.join(DIR, "*.pmesh")))
    if not files:
        print("no .pmesh files in", DIR)
        return 1
    bad = 0
    for p in files:
        errs, info = check(p)
        name = os.path.splitext(os.path.basename(p))[0]
        if info:
            tris, lo, hi, surf = info
            s = " ".join("%s:%d" % ({3.0: "plain", 4.0: "team", 5.0: "glow"}[k], v) for k, v in sorted(surf.items()))
            print("%-22s %5d tris  size %.2f x %.2f x %.2f  x[%+.2f,%+.2f] y[%+.2f,%+.2f] z[%+.2f,%+.2f]  %s %s" % (
                name, tris, hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], lo[0], hi[0], lo[1], hi[1], lo[2], hi[2],
                s, "OK" if not errs else "FAIL: " + "; ".join(errs)))
        else:
            print("%-22s FAIL: %s" % (name, "; ".join(errs)))
        bad += bool(errs)
    print("%d files, %d failed" % (len(files), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
