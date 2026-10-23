import Foundation
import PopulousRender

/// Where each picture sits in a texture made by `IndexedImage.sheet`.
public struct AtlasLayout: Sendable, Equatable {
    public var columns: Int
    public var cellWidth: Int
    public var cellHeight: Int
    public var width: Int
    public var height: Int

    public init(count: Int, columns: Int, cellWidth: Int, cellHeight: Int) {
        self.columns = columns
        self.cellWidth = cellWidth
        self.cellHeight = cellHeight
        let rows = (count + columns - 1) / columns
        width = columns * (cellWidth + 1) - 1
        height = rows * (cellHeight + 1) - 1
    }

    /// Texture coordinates of picture `index`: left, top, right, bottom.
    public func uv(_ index: Int) -> (u0: Float, v0: Float, u1: Float, v1: Float) {
        let x = (index % columns) * (cellWidth + 1)
        let y = (index / columns) * (cellHeight + 1)
        return (
            Float(x) / Float(width), Float(y) / Float(height),
            Float(x + cellWidth) / Float(width), Float(y + cellHeight) / Float(height)
        )
    }
}

/// The HD close-up as triangles and picture quads, in the order they are drawn.
///
/// Land corners are placed in a 3D space where x and y count cells and z counts
/// height steps. The shaders project that space with exactly the original's
/// screen arithmetic (`CloseUp.screenPoint`), which is a true orthographic camera
/// looking down at 30 degrees, so every corner lands where the original draws it
/// and the original's mouse picking still applies.
public struct CloseUpMesh: Sendable {
    /// A step of height measured in cell widths, for lighting: the original's
    /// 8-pixel step seen from a 30-degree orthographic camera is 1/sqrt(6) cells.
    public static let stepHeight = Float(1 / 6.0.squareRoot())

    /// What kind of surface the land is, for the shaders: the ground (land or sea,
    /// which the shaders tell apart by `w0`) or the earth under the view's edges.
    public enum Surface: Float, Sendable {
        case ground = 0, wall = 2
    }

    /// Each cell of land is drawn as this many strips each way, so it can curve.
    public static let subdivisions = 6

    /// A corner of the land, 44 bytes, matching `LandVertex` in the shaders.
    public struct LandVertex: Sendable, Equatable, BitwiseCopyable {
        /// View corners and height steps.
        public var x, y, z: Float
        /// Normal for lighting, in cells (heights scaled by `stepHeight`).
        public var nx, ny, nz: Float
        /// Ground: how wet (sea where over a half), how much farmland, how much
        /// scorched earth (each a half on their cells' edges), and how far out to
        /// sea in cells. Walls: w0 is how far below the top of the land, in steps.
        public var w0, w1, w2, w3: Float
        public var surface: Float
    }

    /// A corner of a model, 40 bytes, matching `ModelVertex` in the shaders.
    public struct ModelVertex: Sendable, Equatable, BitwiseCopyable {
        public var x, y, z: Float
        public var nx, ny, nz: Float
        /// sRGB colour.
        public var r, g, b: Float
        /// 3 plain, 4 the side's colour, 5 glowing.
        public var surface: Float
    }

    /// A corner of a picture: 320x200 screen position and texture position.
    public struct QuadVertex: Sendable, Equatable, BitwiseCopyable {
        public var x, y, u, v: Float
    }

    /// One of the original's pictures still shown in HD, for what has no model yet.
    public struct Picture: Sendable, Equatable {
        public enum Sheet: Sendable { case tiles, sprites }
        /// Its six corners in `quads`.
        public var range: Range<Int>
        public var sheet: Sheet
        /// How far away it counts as, so land and models in front hide it; nil for
        /// marks drawn over everything.
        public var depth: Float?
    }

    /// A model standing in the close-up.
    public struct Placement: Sendable, Equatable {
        public var model: String
        /// Where its base sits: view corners and height steps.
        public var x, y, z: Float
        /// Turn about the vertical, in radians.
        public var angle: Float = 0
        /// The side whose colour its team-coloured parts take, if any.
        public var side: Int?
        /// Drawn this many times its true size.
        public var scale: Float = 1
    }

    /// The land of all 64 cells, then the earth under the front edges.
    public var terrain: [LandVertex] = []
    /// Each cell's triangles, in the order of the close-up's cells.
    public var cellTerrain: [Range<Int>] = []
    /// The earth under the view's two front edges.
    public var walls: Range<Int> = 0..<0
    public var models: [Placement] = []
    public var quads: [QuadVertex] = []
    /// Pictures in drawing order.
    public var pictures: [Picture] = []

    /// The depth the shaders give a point: the original's view direction is
    /// (1, 1, 2) in view corners and height steps, so nearer means a larger
    /// x + y + 2z. Kept within 0...1 for anything that fits on the screen.
    public static func depth(x: Float, y: Float, z: Float) -> Float { 0.95 - (x + y + 2 * z) / 56 }

    /// Builds the HD close-up. `terrain` picks the landscape's trees; models not
    /// in `available` fall back to the original's pictures.
    public init(
        _ closeUp: CloseUp, terrain landscape: Int = 0, tiles: AtlasLayout, sprites: AtlasLayout,
        available: Set<String> = []
    ) {
        let surface = TerrainSurface(closeUp)
        func ground(_ x: Float, _ y: Float) -> SIMD3<Float> { SIMD3(x, y, surface.height(x, y).z) }
        func centre(_ cx: Int, _ cy: Int) -> SIMD3<Float> { ground(Float(cx) + 0.5, Float(cy) + 0.5) }
        /// A picture's depth: just behind the front corner of its cell, a step up,
        /// so it shows over its own cell but not through land in front of it.
        func pictureDepth(_ point: SIMD3<Float>) -> Float { Self.depth(x: point.x + 0.4, y: point.y + 0.4, z: point.z + 1) }
        func place(_ name: String?, at point: SIMD3<Float>, angle: Float = 0, side: Int? = nil) -> Bool {
            guard let name, available.contains(name) else { return false }
            models.append(Placement(
                model: name, x: point.x, y: point.y, z: point.z, angle: angle, side: side, scale: ModelCatalog.scale(name)))
            return true
        }

        addGround(surface)
        for cell in closeUp.cells {

            let base = centre(cell.cx, cell.cy)
            let x = Renderer.viewOriginX + 16 * cell.cx - 16 * cell.cy
            let y = Renderer.viewOriginY + 8 * cell.cx + 8 * cell.cy - cell.height * 8
            // Ground that is more than land: rocks and swamp have models; other
            // special ground the land cannot show keeps the original's picture.
            if cell.tile > 30, !Self.paintedGround.contains(cell.tile),
                !place(ModelCatalog.ground(cell.tile), at: base)
            {
                addPicture(x: x, y: y, width: 32, height: 24, uv: tiles.uv(cell.tile), sheet: .tiles, depth: pictureDepth(base))
            }
            for tile in cell.standing where !place(ModelCatalog.object(tile, terrain: landscape), at: base) {
                addPicture(x: x, y: y - 8, width: 32, height: 24, uv: tiles.uv(tile), sheet: .tiles, depth: pictureDepth(base))
            }
        }

        addShoreRocks(surface, available: available)
        addScenery(surface, landscape: landscape, available: available)

        let start = terrain.count
        addWalls(surface)
        walls = start..<terrain.count

        var marks: [CloseUp.Figure] = []
        for figure in closeUp.figures {
            if figure.kind == .followedMark || figure.kind == .leaderMark {
                marks.append(figure)
                continue
            }
            var point = centre(figure.cx, figure.cy)
            var angle: Float = 0
            if let walk = figure.walk, let step = Self.steps[walk.direction] {
                let along = Float(walk.frame) / 8
                point = ground(point.x + Float(step.dx) * along, point.y + Float(step.dy) * along)
                // Models face (1, 1), towards the viewer.
                angle = atan2(Float(step.dy), Float(step.dx)) - .pi / 4
            }
            if figure.kind == .flag {
                // Planted by the right-hand corner, beside the house.
                point = ground(Float(figure.cx) + 0.8, Float(figure.cy) + 0.2)
            }
            if !place(ModelCatalog.figure(figure, phase: closeUp.phase), at: point, angle: angle, side: figure.side) {
                addPicture(
                    x: figure.x, y: figure.y, width: 16, height: 16, uv: sprites.uv(figure.sprite), sheet: .sprites,
                    depth: pictureDepth(point))
            }
        }
        // The shield over the followed person and the leader's symbol stay on top.
        for mark in marks {
            addPicture(x: mark.x, y: mark.y, width: 16, height: 16, uv: sprites.uv(mark.sprite), sheet: .sprites, depth: nil)
        }
    }

    /// Map cell offsets of the eight walking directions, as steps in x and y.
    static let steps: [Int: (dx: Int, dy: Int)] = [
        -65: (-1, -1), -64: (0, -1), -63: (1, -1), 1: (1, 0), 65: (1, 1), 64: (0, 1), 63: (-1, 1), -1: (-1, 0),
    ]

    /// Ground tiles the land paints itself: each side's farmland (31, 32) and
    /// scorched earth (66).
    static let paintedGround: Set<Int> = [31, 32, 66]


    static let shoreRocks = ["shore_rock_a", "shore_rock_b", "shore_rock_c"]

    /// Boulders in the surf just off the coast, sunk part-way into the sea: a few
    /// on a third-of-a-cell lattice, picked by a hash of the map position so they
    /// stay put as the view scrolls. Smaller than the game's own rocks, which
    /// stand on land and matter to the game; these are only scenery.
    mutating func addShoreRocks(_ surface: TerrainSurface, available: Set<String>) {
        guard Self.shoreRocks.allSatisfy(available.contains) else { return }
        let per = 3
        for j in 0..<Renderer.viewSize * per {
            for i in 0..<Renderer.viewSize * per {
                let h = Self.hash(surface.closeUp.viewX * per + i, surface.closeUp.viewY * per + j)
                guard h % 100 < 42 else { continue }
                let x = (Float(i) + 0.2 + 0.6 * Float(h >> 8 & 0xFF) / 255) / Float(per)
                let y = (Float(j) + 0.2 + 0.6 * Float(h >> 16 & 0xFF) / 255) / Float(per)
                let wet = surface.blurred(x, y, TerrainSurface.isSea)
                guard wet > 0.4, wet < 0.72 else { continue }
                // Never on land in use: only bare shore.
                let cx = min(i / per, Renderer.viewSize - 1), cy = min(j / per, Renderer.viewSize - 1)
                let cell = surface.closeUp.cell(cx, cy)
                guard TerrainSurface.isSea(cell.tile) || ((17...30).contains(cell.tile) && cell.standing.isEmpty)
                else { continue }
                // In the sea they sit on its bed; on the water's edge, in the ground.
                let z = wet > 0.5 ? -0.2 : surface.height(x, y).z - 0.15
                guard z < 0.5 else { continue }
                models.append(Placement(
                    model: Self.shoreRocks[Int(h >> 24) % 3], x: x, y: y, z: z,
                    angle: Float(h >> 4 & 0xFF) / 255 * 2 * .pi, scale: 0.6 + 0.6 * Float(h >> 12 & 0xFF) / 255))
            }
        }
    }

    /// Shrubs and small groves for each landscape; none on the volcanic one.
    static let scenery: [(shrubs: [String], groves: [String])] = [
        (["shrub_a", "shrub_b", "shrub_c"], ["grove_a", "grove_b", "grove_c"]),
        (["dead_bush"], []),
        (["shrub_a", "shrub_c"], ["grove_a", "grove_c"]),
        ([], []),
    ]

    /// Shrubs and small groves dotted over open land: shrubs gather round
    /// buildings and along field edges, groves on hills away from houses. They
    /// stand on cell corners and edges, never in the middle of a cell where people
    /// stand, and never on sea, shore, fields, rocks, swamp, ruins or the game's
    /// own trees. Picked by a hash of the map position, so they stay put as the
    /// view scrolls. Only scenery: the game knows nothing of them.
    mutating func addScenery(_ surface: TerrainSurface, landscape: Int, available: Set<String>) {
        let kinds = Self.scenery[min(max(landscape, 0), Self.scenery.count - 1)]
        let shrubs = kinds.shrubs.filter(available.contains), groves = kinds.groves.filter(available.contains)
        guard !shrubs.isEmpty || !groves.isEmpty else { return }
        let closeUp = surface.closeUp, size = Renderer.viewSize
        // Cells nothing may stand on, and the middles of buildings and trees.
        let open = Set(1...30).subtracting([16])
        // Buildings and trees in and around the view, so scenery near its edges
        // does not change as it scrolls.
        var buildings: [SIMD2<Float>] = [], trees: [SIMD2<Float>] = []
        for cy in -2..<size + 2 {
            for cx in -2..<size + 2 {
                let object = closeUp.object(around: cx, cy)
                let middle = SIMD2(Float(cx) + 0.5, Float(cy) + 0.5)
                if (33...46).contains(object) || (54...65).contains(object) {
                    buildings.append(middle)
                } else if object != 0 {
                    trees.append(middle)
                }
            }
        }
        func nearest(_ points: [SIMD2<Float>], _ p: SIMD2<Float>) -> Float {
            points.map { (($0 - p) * ($0 - p)).sum().squareRoot() }.min() ?? .infinity
        }
        for j in 1..<size * 2 {
            for i in 1..<size * 2 where i % 2 == 0 || j % 2 == 0 {
                let h = Self.hash(closeUp.viewX * 2 + i + 7919, closeUp.viewY * 2 + j + 104_729)
                let roll = Float(h & 0xFFFF) / 65535
                let x = Float(i) / 2 + (Float(h >> 16 & 0xFF) / 255 - 0.5) * 0.24
                let y = Float(j) / 2 + (Float(h >> 24) / 255 - 0.5) * 0.24
                // Every cell it touches must be open land with nothing on it but a
                // building, which shrubs may sit beside.
                let touching = [(-0.3, -0.3), (0.3, -0.3), (-0.3, 0.3), (0.3, 0.3)].map { d in
                    closeUp.tile(around: Int((x + Float(d.0)).rounded(.down)), Int((y + Float(d.1)).rounded(.down)))
                }
                guard touching.allSatisfy({ open.contains($0) || [31, 32].contains($0) }) else { continue }
                guard surface.blurred(x, y, TerrainSurface.isSea) < 0.05 else { continue }
                let p = SIMD2(x, y)
                guard nearest(trees, p) > 0.45 else { continue }
                let toBuilding = nearest(buildings, p)
                guard toBuilding > 0.55 else { continue }
                let field = surface.blurred(x, y) { $0 == 31 || $0 == 32 }
                let ground = surface.height(x, y)
                // A hill is land higher than what lies round it, not just high land.
                let cx = Int(x.rounded()), cy = Int(y.rounded())
                var around: Float = 0
                for dy in -2...2 { for dx in -2...2 { around += Float(closeUp.corner(around: cx + dx, cy + dy)) } }
                let hill = min(max(ground.z - around / 25 - 0.2, 0), 1)
                let slope = min((ground.dx * ground.dx + ground.dy * ground.dy).squareRoot(), 1)

                var name: String?
                if !groves.isEmpty, toBuilding > 1.4, field < 0.2, roll < 0.03 + 0.3 * max(hill, slope * 0.3) {
                    name = groves[Int(h >> 8) % groves.count]
                } else if !shrubs.isEmpty, field < 0.6,
                    roll > 0.55, roll < 0.55 + (toBuilding < 1.6 ? 0.4 : field > 0.2 ? 0.25 : 0.1)
                {
                    name = shrubs[Int(h >> 8) % shrubs.count]
                }
                guard let name else { continue }
                models.append(Placement(
                    model: name, x: x, y: y, z: ground.z,
                    angle: Float(h >> 4 & 0xFF) / 255 * 2 * .pi,
                    scale: (name == "dead_bush" ? 0.6 : 0.8) + 0.35 * Float(h >> 12 & 0xFF) / 255))
            }
        }
    }

    /// A well-mixed 32-bit number from a map position.
    static func hash(_ x: Int, _ y: Int) -> UInt32 {
        var v = UInt32(truncatingIfNeeded: x &* 73_856_093 ^ y &* 19_349_663)
        v ^= v >> 16; v &*= 0x7FEB_352D; v ^= v >> 15; v &*= 0x846C_A68B; v ^= v >> 16
        return v
    }

    /// The land and sea of all 64 cells, each as `subdivisions` squared pairs of
    /// triangles over a shared grid of points on the smooth surface.
    mutating func addGround(_ surface: TerrainSurface) {
        let steps = Self.subdivisions, side = Renderer.viewSize * steps + 1
        var grid: [LandVertex] = []
        grid.reserveCapacity(side * side)
        for j in 0..<side {
            for i in 0..<side {
                let x = Float(i) / Float(steps), y = Float(j) / Float(steps)
                let n = surface.normal(x, y)
                let wet = surface.blurred(x, y, TerrainSurface.isSea)
                grid.append(LandVertex(
                    x: x, y: y, z: surface.height(x, y).z, nx: n.x, ny: n.y, nz: n.z,
                    w0: wet,
                    w1: surface.blurred(x, y) { $0 == 31 || $0 == 32 },
                    w2: surface.blurred(x, y) { $0 == 66 },
                    w3: wet > 0 ? surface.distanceFromLand(x, y) : 0,
                    surface: Surface.ground.rawValue))
            }
        }
        for cell in 0..<Renderer.viewSize * Renderer.viewSize {
            let cx = cell % Renderer.viewSize, cy = cell / Renderer.viewSize
            let start = terrain.count
            for j in cy * steps..<(cy + 1) * steps {
                for i in cx * steps..<(cx + 1) * steps {
                    let a = grid[i + j * side], b = grid[i + 1 + j * side]
                    let c = grid[i + 1 + (j + 1) * side], d = grid[i + (j + 1) * side]
                    // Split each square along its flatter diagonal.
                    if abs(a.z - c.z) <= abs(b.z - d.z) {
                        terrain += [a, b, c, a, c, d]
                    } else {
                        terrain += [a, b, d, b, c, d]
                    }
                }
            }
            cellTerrain.append(start..<terrain.count)
        }
    }

    /// The earth under the right edge (x = 8) and the left front edge (y = 8),
    /// from the land down to sea level.
    mutating func addWalls(_ surface: TerrainSurface) {
        let edge = Float(Renderer.viewSize), steps = Self.subdivisions * Renderer.viewSize
        func wall(_ a: (Float, Float), _ b: (Float, Float), normal: SIMD3<Float>) {
            let ha = max(surface.height(a.0, a.1).z, 0), hb = max(surface.height(b.0, b.1).z, 0)
            guard ha > 0.001 || hb > 0.001 else { return }
            func vertex(_ corner: (Float, Float), _ z: Float, top: Float) -> LandVertex {
                LandVertex(
                    x: corner.0, y: corner.1, z: z, nx: normal.x, ny: normal.y, nz: normal.z,
                    w0: top - z, w1: 0, w2: 0, w3: 0, surface: Surface.wall.rawValue)
            }
            let a0 = vertex(a, 0, top: ha), b0 = vertex(b, 0, top: hb)
            let a1 = vertex(a, ha, top: ha), b1 = vertex(b, hb, top: hb)
            terrain += [a0, b0, a1, b0, b1, a1]
        }
        for k in 0..<steps {
            let t0 = Float(k) / Float(Self.subdivisions), t1 = Float(k + 1) / Float(Self.subdivisions)
            wall((edge, t0), (edge, t1), normal: SIMD3(1, 0, 0))
            wall((t0, edge), (t1, edge), normal: SIMD3(0, 1, 0))
        }
    }

    mutating func addPicture(
        x: Int, y: Int, width: Int, height: Int, uv: (u0: Float, v0: Float, u1: Float, v1: Float),
        sheet: Picture.Sheet, depth: Float?
    ) {
        let first = quads.count
        let x0 = Float(x), y0 = Float(y), x1 = Float(x + width), y1 = Float(y + height)
        quads += [
            QuadVertex(x: x0, y: y0, u: uv.u0, v: uv.v0), QuadVertex(x: x1, y: y0, u: uv.u1, v: uv.v0),
            QuadVertex(x: x0, y: y1, u: uv.u0, v: uv.v1), QuadVertex(x: x1, y: y0, u: uv.u1, v: uv.v0),
            QuadVertex(x: x1, y: y1, u: uv.u1, v: uv.v1), QuadVertex(x: x0, y: y1, u: uv.u0, v: uv.v1),
        ]
        pictures.append(Picture(range: first..<quads.count, sheet: sheet, depth: depth))
    }
}

func simdCross(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> {
    SIMD3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

func simdNormalize(_ v: SIMD3<Float>) -> SIMD3<Float> {
    let length = (v * v).sum().squareRoot()
    return length > 0 ? v / length : v
}
