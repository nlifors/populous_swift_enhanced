import Foundation
import Testing

@testable import PopulousAssets
@testable import PopulousCore
@testable import PopulousHD
@testable import PopulousRender

/// The HD close-up places land with `CloseUp.screenPoint`. These tests check that
/// it puts every corner exactly where the original's mouse picking expects it, so
/// clicks reshape the same land whichever graphics are shown.
@Suite struct CloseUpGeometryTests {
    static let worlds = [0, 25, 125, 630].map { number in
        World.conquest(seed: World.conquestSeed(worldNumber: number, levelWord: number * 7), startingPeople: (3, 3))
    }
    static let views = [(0, 0), (20, 31), (40, 12), (56, 56)]

    /// A close-up made straight from the world, without game assets.
    static func closeUp(_ world: World, viewX: Int, viewY: Int) -> CloseUp {
        let side = CloseUp.cornersPerSide
        var corners: [Int] = []
        for y in 0..<side {
            for x in 0..<side { corners.append(Int(world.altitude[viewX + x + (viewY + y) * World.corners])) }
        }
        var cells: [CloseUp.Cell] = []
        for cy in 0..<Renderer.viewSize {
            for cx in 0..<Renderer.viewSize {
                let index = viewX + cx + (viewY + cy) * World.size
                cells.append(CloseUp.Cell(
                    cx: cx, cy: cy, height: Int(world.height[index]), tile: Int(world.tile[index]), standing: []))
            }
        }
        let m = CloseUp.margin
        var cornersAround: [Int] = [], tilesAround: [Int] = [], objectsAround: [Int] = []
        for y in -m..<side + m {
            for x in -m..<side + m {
                let mx = min(max(viewX + x, 0), World.corners - 1), my = min(max(viewY + y, 0), World.corners - 1)
                cornersAround.append(Int(world.altitude[mx + my * World.corners]))
            }
        }
        for y in -m..<Renderer.viewSize + m {
            for x in -m..<Renderer.viewSize + m {
                let mx = min(max(viewX + x, 0), World.size - 1), my = min(max(viewY + y, 0), World.size - 1)
                tilesAround.append(Int(world.tile[mx + my * World.size]))
                objectsAround.append(Int(world.object[mx + my * World.size]))
            }
        }
        return CloseUp(
            viewX: viewX, viewY: viewY, phase: 1, corners: corners, cells: cells, figures: [],
            cornersAround: cornersAround, tilesAround: tilesAround, objectsAround: objectsAround)
    }

    /// The corner a click picks, worked out from the projected corners alone: on
    /// the pointer's column of corners, the front-most one drawn at or above it.
    static func projectedPick(_ closeUp: CloseUp, x px: Int, y py: Int) -> (x: Int, y: Int)? {
        let diagonal = ((px - 0x38) >> 4) - 8
        var picked: (x: Int, y: Int)?
        for sum in 0...16 where (sum + diagonal) % 2 == 0 {
            let x = (sum + diagonal) / 2, y = (sum - diagonal) / 2
            guard (0...8).contains(x), (0...8).contains(y) else { continue }
            let point = CloseUp.screenPoint(x: Double(x), y: Double(y), altitude: Double(closeUp.corner(x, y)))
            if point.y <= Double(py + 4) { picked = (x, y) }
        }
        return picked
    }

    @Test(arguments: worlds.indices, views.indices)
    func clicksPickTheProjectedCorner(_ worldIndex: Int, _ viewIndex: Int) {
        let world = Self.worlds[worldIndex]
        let (viewX, viewY) = Self.views[viewIndex]
        let closeUp = Self.closeUp(world, viewX: viewX, viewY: viewY)
        var compared = 0
        for py in 0..<200 {
            for px in 0..<320 {
                guard let original = Renderer.viewCorner(atX: px, y: py, world: world, viewX: viewX, viewY: viewY)
                else { continue }
                let projected = Self.projectedPick(closeUp, x: px, y: py)
                #expect(projected.map { ($0.x + viewX, $0.y + viewY) } ?? (-1, -1) == (original.x, original.y), "at \(px),\(py)")
                compared += 1
            }
        }
        #expect(compared > 10000)
    }

    @Test(arguments: worlds.indices, views.indices)
    func meshFollowsTheCorners(_ worldIndex: Int, _ viewIndex: Int) {
        let (viewX, viewY) = Self.views[viewIndex]
        let closeUp = Self.closeUp(Self.worlds[worldIndex], viewX: viewX, viewY: viewY)
        let layout = AtlasLayout(count: 70, columns: 16, cellWidth: 32, cellHeight: 24)
        let mesh = CloseUpMesh(closeUp, tiles: layout, sprites: layout)

        let steps = CloseUpMesh.subdivisions
        #expect(mesh.cellTerrain.count == 64)
        for (cell, range) in zip(closeUp.cells, mesh.cellTerrain) {
            #expect(range.count == steps * steps * 6)
            let heights = [(0, 0), (1, 0), (0, 1), (1, 1)].map { closeUp.corner(cell.cx + $0.0, cell.cy + $0.1) }
            for vertex in mesh.terrain[range] {
                #expect((Float(cell.cx)...Float(cell.cx + 1)).contains(vertex.x))
                #expect((Float(cell.cy)...Float(cell.cy + 1)).contains(vertex.y))
                #expect(vertex.nz > 0, "land faces up")
                // The game's corners are exact, so clicks pick what is drawn.
                if vertex.x == vertex.x.rounded(), vertex.y == vertex.y.rounded() {
                    #expect(vertex.z == Float(closeUp.corner(Int(vertex.x), Int(vertex.y))))
                }
                // Between them the land curves but never rises or dips past them,
                // and level plots stay level.
                #expect(vertex.z >= Float(heights.min()!) - 0.001 && vertex.z <= Float(heights.max()!) + 0.001)
                // In the middle of a cell, the fields say what the cell is.
                if abs(vertex.x - Float(cell.cx) - 0.5) < 0.01, abs(vertex.y - Float(cell.cy) - 0.5) < 0.01 {
                    #expect(vertex.w0 == ([0, 16].contains(cell.tile) ? 1 : 0), "sea")
                    #expect(vertex.w1 == ([31, 32].contains(cell.tile) ? 1 : 0), "farmland")
                    #expect(vertex.w3 == 0 || [0, 16].contains(cell.tile), "only the sea is out from land")
                }
            }
        }
        for vertex in mesh.terrain[mesh.walls] {
            #expect(vertex.x == 8 || vertex.y == 8, "walls stand on the front edges")
            #expect(vertex.nz == 0 && vertex.nx + vertex.ny > 0, "walls face the viewer")
            if vertex.x == vertex.x.rounded(), vertex.y == vertex.y.rounded() {
                #expect(vertex.w0 + vertex.z == Float(closeUp.corner(Int(vertex.x), Int(vertex.y))), "depth below the top")
            }
        }
    }
}

/// The original's disk image is not part of the repository; see AssetTests.
let diskURL: URL? = {
    if let path = ProcessInfo.processInfo.environment["POPULOUS_ADF"] { return URL(fileURLWithPath: path) }
    let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let found = try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
    return found?.first { $0.pathExtension.lowercased() == "adf" }
}()

@Suite(.enabled(if: diskURL != nil, "needs the Populous ADF"))
struct CloseUpDiskTests {
    let assets: GameAssets

    init() throws {
        assets = try GameAssets(volume: ADFVolume(data: [UInt8](try Data(contentsOf: #require(diskURL)))))
    }

    /// A sea-level tile's left and right corners lie on the line between its two
    /// widest pixel rows.
    @Test func cornersSitOnTheOriginalTiles() {
        let water = assets.landSets[0].tiles[0]
        let opaque = (0..<water.height).map { y in (0..<water.width).filter { water[$0, y] != IndexedImage.transparent }.count }
        let widest = opaque.indices.filter { opaque[$0] == water.width }
        // The tile for cell (cx, cy) at height 0 is drawn with its top-left here.
        let cx = 3, cy = 5
        let drawX = Renderer.viewOriginX + 16 * cx - 16 * cy
        let drawY = Renderer.viewOriginY + 8 * cx + 8 * cy
        let left = CloseUp.screenPoint(x: Double(cx), y: Double(cy + 1), altitude: 0)
        let right = CloseUp.screenPoint(x: Double(cx + 1), y: Double(cy), altitude: 0)
        #expect(widest.map { drawY + $0 } == [Int(left.y) - 1, Int(left.y)])
        #expect(Int(left.x) == drawX && Int(right.x) == drawX + 32 && left.y == right.y)
    }

    /// Classic drawing through `CloseUp` is the same as drawing it all in one go.
    @Test func classicFrameIncludesTheCloseUp() {
        let renderer = Renderer(assets: assets)
        var world = World.conquest(seed: World.conquestSeed(worldNumber: 0, levelWord: assets.levels[0].word), startingPeople: (3, 3))
        let rules = LandRules(landWords: assets.landSets[0].headerWords)
        for _ in 0..<300 { world.tick(rules: rules) }
        let leader = Int(world.players[0].leader)
        let cell = leader > 0 ? Int(world.people[leader - 1].cell) : 0
        let viewX = min(max(cell % 64 - 4, 0), 56), viewY = min(max(cell / 64 - 4, 0), 56)
        let whole = renderer.frame(world: world, terrain: 0, viewX: viewX, viewY: viewY, phase: world.phase)
        var parts = renderer.frame(world: world, terrain: 0, viewX: viewX, viewY: viewY, phase: world.phase, closeUp: false)
        let closeUp = renderer.closeUp(world: world, viewX: viewX, viewY: viewY, phase: world.phase)
        renderer.draw(closeUp, land: assets.landSets[0], into: &parts)
        #expect(whole == parts)
        #expect(!closeUp.figures.isEmpty, "the leader is in view")
    }
}

/// The models made by Assets/blender/build_assets.py.
@Suite struct ModelTests {
    static let folder = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("Sources/PopulousHD/Resources/Models")
    static let library = ModelLibrary(folder: folder)

    @Test func everyPictureWithAModelHasOne() {
        let names = Set(Self.library.models.keys)
        var wanted: [String] = []
        for terrain in 0..<4 {
            for tile in 33...65 { if let name = ModelCatalog.object(tile, terrain: terrain) { wanted.append(name) } }
        }
        wanted += (47...53).compactMap(ModelCatalog.ground)
        wanted += ["walker_a", "walker_b", "knight_a", "knight_b", "flag_a", "flag_b"]
        #expect(Set(wanted).subtracting(names).isEmpty, "missing: \(Set(wanted).subtracting(names).sorted())")
    }

    @Test func modelsStandInTheirCell() {
        #expect(Self.library.models.count >= 37)
        for (name, vertices) in Self.library.models {
            #expect(vertices.count % 3 == 0 && !vertices.isEmpty, "\(name)")
            let xs = vertices.map(\.x), ys = vertices.map(\.y), zs = vertices.map(\.z)
            let scale = ModelCatalog.scale(name)
            // Within its cell (castle pieces reach the edges) and standing on the ground.
            #expect(xs.min()! * scale >= -0.52 && xs.max()! * scale <= 0.52, "\(name) x")
            #expect(ys.min()! * scale >= -0.52 && ys.max()! * scale <= 0.52, "\(name) y")
            #expect(abs(zs.min()!) < 0.05, "\(name) base at \(zs.min()!)")
            for v in vertices {
                let length = (v.nx * v.nx + v.ny * v.ny + v.nz * v.nz).squareRoot()
                #expect(abs(length - 1) < 0.01, "\(name) normal")
            }
        }
    }

    /// The x and y of Blender's models are swapped into the view's: the castle
    /// wall for the cells beside the centre along x (tile 43) runs along y.
    @Test func castleWallsLineUp() throws {
        let wall = try #require(Self.library.models["castle_wall_x"])
        let xs = wall.map(\.x), ys = wall.map(\.y)
        #expect(ys.max()! - ys.min()! > 0.95)
        #expect(xs.max()! - xs.min()! < 0.5)
    }

    @Test func meshPlacesModelsInsteadOfPictures() {
        let world = CloseUpGeometryTests.worlds[0]
        var closeUp = CloseUpGeometryTests.closeUp(world, viewX: 20, viewY: 31)
        closeUp.cells[9].standing = [35, 45]
        closeUp.cells[10].tile = 48
        closeUp.figures = [CloseUp.Figure(
            x: 100, y: 100, sprite: 3, kind: .person, person: 0, cx: 2, cy: 3, side: 1,
            walk: .init(direction: 1, frame: 4, climb: 0, knight: false))]
        let layout = AtlasLayout(count: 70, columns: 16, cellWidth: 32, cellHeight: 24)
        let mesh = CloseUpMesh(closeUp, tiles: layout, sprites: layout, available: Set(Self.library.models.keys))
        #expect(mesh.models.map(\.model).contains("house_3_thatch_hut"))
        #expect(mesh.models.map(\.model).contains("ankh"))
        #expect(mesh.models.map(\.model).contains("rock_b"))
        let walker = mesh.models.first { $0.model == "walker_b" }
        // Half way from cell (2, 3) towards (3, 3), facing along x, in red.
        #expect(walker?.x == 3 && walker?.y == 3.5 && walker?.side == 1)
        #expect(abs((walker?.angle ?? 0) + .pi / 4) < 0.001)
        #expect(mesh.pictures.isEmpty)
    }

    /// Shrubs, groves and shore rocks are only scenery: they keep off the middles
    /// of cells where people stand, off buildings and the game's trees, and out of
    /// fields and the sea, and they do not move as the view scrolls.
    @Test(arguments: CloseUpGeometryTests.worlds.indices)
    func sceneryKeepsClear(_ worldIndex: Int) {
        let world = CloseUpGeometryTests.worlds[worldIndex]
        let layout = AtlasLayout(count: 70, columns: 16, cellWidth: 32, cellHeight: 24)
        let scenery = Set(CloseUpMesh.scenery.flatMap { $0.shrubs + $0.groves })
        var placed = 0
        for (viewX, viewY) in [(0, 0), (20, 31), (21, 31), (40, 12), (56, 56)] {
            let closeUp = CloseUpGeometryTests.closeUp(world, viewX: viewX, viewY: viewY)
            let mesh = CloseUpMesh(closeUp, terrain: 0, tiles: layout, sprites: layout, available: Set(Self.library.models.keys))
            for model in mesh.models where scenery.contains(model.model) {
                placed += 1
                let cx = Int(model.x), cy = Int(model.y)
                let fx = model.x - Float(cx), fy = model.y - Float(cy)
                #expect(abs(fx - 0.5) > 0.3 || abs(fy - 0.5) > 0.3, "not in the middle of a cell")
                for (dx, dy) in [(-0.3, -0.3), (0.3, -0.3), (-0.3, 0.3), (0.3, 0.3)] {
                    let x = min(max(Int((model.x + Float(dx)).rounded(.down)), 0), 7)
                    let y = min(max(Int((model.y + Float(dy)).rounded(.down)), 0), 7)
                    let cell = closeUp.cell(x, y)
                    #expect(![0, 16].contains(cell.tile), "not in the sea")
                    if cell.standing.isEmpty == false {
                        let middle = SIMD2(Float(x) + 0.5, Float(y) + 0.5) - SIMD2(model.x, model.y)
                        #expect((middle * middle).sum().squareRoot() > 0.45, "clear of buildings and trees")
                    }
                }
            }
            // One cell along, the same land keeps the same scenery (away from the
            // view's edges, where none is placed so nothing overhangs them).
            if viewX == 21 {
                let before = CloseUpMesh(
                    CloseUpGeometryTests.closeUp(world, viewX: 20, viewY: 31), terrain: 0,
                    tiles: layout, sprites: layout, available: Set(Self.library.models.keys))
                let here = Set(mesh.models.filter { scenery.contains($0.model) && $0.x + 21 > 22 && $0.x + 21 < 27.5 }
                    .map { "\($0.model) \($0.x + 21) \($0.y)" })
                let there = Set(before.models.filter { scenery.contains($0.model) && $0.x + 20 > 22 && $0.x + 20 < 27.5 }
                    .map { "\($0.model) \($0.x + 20) \($0.y)" })
                #expect(here == there, "\(here.symmetricDifference(there).sorted())")
            }
        }
        #expect(placed > 0)
    }
}
