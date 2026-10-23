import PopulousRender

/// The HD land as a smooth surface over the close-up's corners.
///
/// Heights pass exactly through every corner the game knows (so the original's
/// mouse picking still matches what is drawn) and curve between them: each cell
/// is a bicubic patch whose slopes at the corners are limited so that level
/// ground stays level and nothing overshoots, which turns the original's folded
/// tiles into rolling hills while flat building plots stay flat.
///
/// Alongside the height it works out fields that vary smoothly over the land:
/// how wet a point is (the sea's cells blurred, so coasts curve round cell
/// corners), how far out to sea it is, and how much of it is farmland or
/// scorched earth.
struct TerrainSurface {
    let closeUp: CloseUp
    let size = Renderer.viewSize
    private let m = CloseUp.margin
    /// Corner slopes along x and y, for corners -1...9.
    private var slopeX: [Float] = []
    private var slopeY: [Float] = []

    init(_ closeUp: CloseUp) {
        self.closeUp = closeUp
        let side = size + 3
        slopeX.reserveCapacity(side * side)
        slopeY.reserveCapacity(side * side)
        for y in -1...size + 1 {
            for x in -1...size + 1 {
                func h(_ x: Int, _ y: Int) -> Float { Float(closeUp.corner(around: x, y)) }
                slopeX.append(Self.limitedSlope(h(x - 1, y), h(x, y), h(x + 1, y)))
                slopeY.append(Self.limitedSlope(h(x, y - 1), h(x, y), h(x, y + 1)))
            }
        }
    }

    /// The slope at a corner from its neighbours on either side: none at a peak,
    /// a hollow or the edge of level ground, else the harmonic mean of the two
    /// sides, which never overshoots (Fritsch and Butland).
    static func limitedSlope(_ before: Float, _ at: Float, _ after: Float) -> Float {
        let a = at - before, b = after - at
        return a * b <= 0 ? 0 : 2 * a * b / (a + b)
    }

    private func slopes(_ x: Int, _ y: Int) -> (Float, Float) {
        let i = (x + 1) + (y + 1) * (size + 3)
        return (slopeX[i], slopeY[i])
    }

    /// Height in steps at (x, y) in view corners, 0...8, and its gradient.
    func height(_ x: Float, _ y: Float) -> (z: Float, dx: Float, dy: Float) {
        let cx = min(max(Int(x.rounded(.down)), 0), size - 1), cy = min(max(Int(y.rounded(.down)), 0), size - 1)
        let u = x - Float(cx), v = y - Float(cy)
        // Cubic Hermite bases for value and slope at each end, and their derivatives.
        func value(_ t: Float) -> (Float, Float) { (2 * t * t * t - 3 * t * t + 1, -2 * t * t * t + 3 * t * t) }
        func slope(_ t: Float) -> (Float, Float) { (t * t * t - 2 * t * t + t, t * t * t - t * t) }
        func dValue(_ t: Float) -> (Float, Float) { (6 * t * t - 6 * t, -6 * t * t + 6 * t) }
        func dSlope(_ t: Float) -> (Float, Float) { (3 * t * t - 4 * t + 1, 3 * t * t - 2 * t) }
        let (fu0, fu1) = value(u), (fv0, fv1) = value(v)
        let (gu0, gu1) = slope(u), (gv0, gv1) = slope(v)
        let (dfu0, dfu1) = dValue(u), (dfv0, dfv1) = dValue(v)
        let (dgu0, dgu1) = dSlope(u), (dgv0, dgv1) = dSlope(v)
        var z: Float = 0, dx: Float = 0, dy: Float = 0
        for (a, fu, gu, dfu, dgu) in [(0, fu0, gu0, dfu0, dgu0), (1, fu1, gu1, dfu1, dgu1)] {
            for (b, fv, gv, dfv, dgv) in [(0, fv0, gv0, dfv0, dgv0), (1, fv1, gv1, dfv1, dgv1)] {
                let f = Float(closeUp.corner(cx + a, cy + b))
                let (sx, sy) = slopes(cx + a, cy + b)
                z += f * fu * fv + sx * gu * fv + sy * fu * gv
                dx += f * dfu * fv + sx * dgu * fv + sy * dfu * gv
                dy += f * fu * dfv + sx * gu * dfv + sy * fu * dgv
            }
        }
        return (z, dx, dy)
    }

    /// The upward normal at (x, y), with heights in cell widths for lighting.
    func normal(_ x: Float, _ y: Float) -> SIMD3<Float> {
        let h = height(x, y)
        return simdNormalize(SIMD3(-h.dx * CloseUpMesh.stepHeight, -h.dy * CloseUpMesh.stepHeight, 1))
    }

    /// A field over cells blurred by a one-cell box: bilinear between cell
    /// centres. It is 1 in the middle of a block of matching cells, a half on an
    /// edge between matching and other cells, and rounds their corners.
    func blurred(_ x: Float, _ y: Float, _ matches: (Int) -> Bool) -> Float {
        let gx = x - 0.5, gy = y - 0.5
        let x0 = Int(gx.rounded(.down)), y0 = Int(gy.rounded(.down))
        let u = gx - Float(x0), v = gy - Float(y0)
        func at(_ cx: Int, _ cy: Int) -> Float { matches(closeUp.tile(around: cx, cy)) ? 1 : 0 }
        return (at(x0, y0) * (1 - u) + at(x0 + 1, y0) * u) * (1 - v)
            + (at(x0, y0 + 1) * (1 - u) + at(x0 + 1, y0 + 1) * u) * v
    }

    static func isSea(_ tile: Int) -> Bool { tile == 0 || tile == 16 }

    /// How far (x, y) is from the nearest cell of land, in cells, up to `reach`;
    /// 0 on land.
    func distanceFromLand(_ x: Float, _ y: Float, reach: Int = 3) -> Float {
        let cx = Int(x.rounded(.down)), cy = Int(y.rounded(.down))
        var best = Float(reach)
        for ny in cy - reach...cy + reach {
            for nx in cx - reach...cx + reach where !Self.isSea(closeUp.tile(around: nx, ny)) {
                let ox = max(Float(nx) - x, 0, x - Float(nx + 1)), oy = max(Float(ny) - y, 0, y - Float(ny + 1))
                best = min(best, (ox * ox + oy * oy).squareRoot())
            }
        }
        return best
    }
}
