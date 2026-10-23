import PopulousAssets
import PopulousCore

/// What the isometric close-up shows in one frame, worked out once so that more
/// than one renderer can draw it: the classic renderer paints the original's tiles
/// and sprites from it, the HD renderer builds a 3D scene. Reading the world to
/// make it changes nothing in the game.
public struct CloseUp: Sendable {
    /// Corners per side of the view: one more than its cells.
    public static let cornersPerSide = Renderer.viewSize + 1

    /// The map cell at the view's top corner.
    public let viewX: Int
    public let viewY: Int
    /// Flips every tick and drives the two-frame animations.
    public let phase: Int
    /// Land height at each of the view's 9x9 corners, index `x + y * 9`.
    public var corners: [Int]
    /// The 64 cells in the order they are drawn: back to front, row by row.
    public var cells: [Cell]
    /// People, flags and the marks they carry, in the order they are drawn.
    public var figures: [Figure]
    /// Corner heights and ground tiles of the land around the view as well, `margin`
    /// corners or cells beyond each edge, so smooth land and coasts drawn from
    /// them join up as the view scrolls. Empty when not known.
    public var cornersAround: [Int] = []
    public var tilesAround: [Int] = []
    /// What stands on each of those cells (buildings, trees), 0 for nothing.
    public var objectsAround: [Int] = []

    /// How far `cornersAround` and `tilesAround` reach beyond the view.
    public static let margin = 3

    /// One cell of the view.
    public struct Cell: Sendable, Equatable {
        /// Position in the view, 0...7.
        public var cx: Int
        public var cy: Int
        /// Base height: the cell's tile is drawn this many steps up.
        public var height: Int
        /// Land-set tile for the ground, with the water's flicker already applied.
        public var tile: Int
        /// Land-set tiles standing on the cell, one step up, in drawing order: a
        /// building or tree, then the skull, then the ankh.
        public var standing: [Int]
    }

    /// A sprite drawn over the land.
    public struct Figure: Sendable, Equatable {
        public enum Kind: Sendable, Equatable {
            /// A walker, fighter, drowning person, ruin and so on.
            case person
            /// The flag over a settlement.
            case flag
            /// The small shield over the person being followed.
            case followedMark
            /// The side's symbol over its leader.
            case leaderMark
        }

        /// A walker part-way between two cells.
        public struct Walk: Sendable, Equatable {
            /// Map cell offset of the step: -65, -64, -63, -1, 1, 63, 64 or 65.
            public var direction: Int
            /// 0...7: eighths of the way to the next cell.
            public var frame: Int
            /// Height steps from this cell's base to the next one's.
            public var climb: Int
            /// A knight rather than an ordinary walker.
            public var knight: Bool
        }

        /// Top-left corner on the 320x200 screen.
        public var x: Int
        public var y: Int
        /// Index into the game's 16x16 sprites.
        public var sprite: Int
        public var kind: Kind
        /// The person this belongs to (index into `World.people`).
        public var person: Int
        /// The view cell the person stands on.
        public var cx: Int
        public var cy: Int
        /// 0 for the player's side (blue), 1 for the other (red).
        public var side: Int
        /// Set for people walking (not drowning).
        public var walk: Walk?
    }

    /// The height of corner (x, y) of the view, both 0...8.
    public func corner(_ x: Int, _ y: Int) -> Int { corners[x + y * Self.cornersPerSide] }

    /// The cell at view position (cx, cy).
    public func cell(_ cx: Int, _ cy: Int) -> Cell { cells[cx + cy * Renderer.viewSize] }

    /// The height of corner (x, y), which may lie up to `margin` corners outside
    /// the view. Without the surroundings, the nearest corner of the view.
    public func corner(around x: Int, _ y: Int) -> Int {
        let m = Self.margin, side = Self.cornersPerSide + 2 * m
        let ax = min(max(x + m, 0), side - 1), ay = min(max(y + m, 0), side - 1)
        if cornersAround.count == side * side { return cornersAround[ax + ay * side] }
        return corner(min(max(x, 0), Self.cornersPerSide - 1), min(max(y, 0), Self.cornersPerSide - 1))
    }

    /// The ground tile of cell (cx, cy), which may lie up to `margin` cells outside
    /// the view, without the water's flicker. Without the surroundings, the nearest
    /// cell of the view.
    public func tile(around cx: Int, _ cy: Int) -> Int {
        let m = Self.margin, side = Renderer.viewSize + 2 * m
        let ax = min(max(cx + m, 0), side - 1), ay = min(max(cy + m, 0), side - 1)
        if tilesAround.count == side * side { return tilesAround[ax + ay * side] }
        let tile = cell(min(max(cx, 0), Renderer.viewSize - 1), min(max(cy, 0), Renderer.viewSize - 1)).tile
        return tile == 16 ? 0 : tile
    }

    /// What stands on cell (cx, cy), which may lie up to `margin` cells outside
    /// the view: a building or tree tile, or 0. Without the surroundings, the
    /// first thing standing on the nearest cell of the view.
    public func object(around cx: Int, _ cy: Int) -> Int {
        let m = Self.margin, side = Renderer.viewSize + 2 * m
        let ax = min(max(cx + m, 0), side - 1), ay = min(max(cy + m, 0), side - 1)
        if objectsAround.count == side * side { return objectsAround[ax + ay * side] }
        return cell(min(max(cx, 0), Renderer.viewSize - 1), min(max(cy, 0), Renderer.viewSize - 1)).standing.first ?? 0
    }

    /// Where a land corner at height `altitude` appears on the 320x200 screen: the
    /// top corner of the original's tile picture for a cell whose corner it is.
    /// `x` and `y` are corner positions in the view and may be fractions.
    public static func screenPoint(x: Double, y: Double, altitude: Double) -> (x: Double, y: Double) {
        (
            Double(Renderer.viewOriginX + 16) + 16 * (x - y),
            Double(Renderer.viewOriginY + 8) + 8 * (x + y) - 8 * altitude
        )
    }
}

extension Renderer {
    /// Works out what the close-up shows ($69AF6, $69D20).
    public func closeUp(world: World, viewX: Int, viewY: Int, phase: Int) -> CloseUp {
        let side = CloseUp.cornersPerSide
        var corners = [Int](repeating: 0, count: side * side)
        for y in 0..<side {
            for x in 0..<side {
                corners[x + y * side] = Int(world.altitude[viewX + x + (viewY + y) * World.corners])
            }
        }

        var cells: [CloseUp.Cell] = []
        cells.reserveCapacity(Self.viewSize * Self.viewSize)
        var figures: [CloseUp.Figure] = []
        for cy in 0..<Self.viewSize {
            for cx in 0..<Self.viewSize {
                let index = viewX + cx + (viewY + cy) * World.size
                var tile = Int(world.tile[index])
                if tile == 0, phase == 0 { tile = 16 }
                let height = Int(world.height[index])
                // Anything standing on the cell is drawn one step higher.
                var standing: [Int] = []
                let object = Int(world.object[index])
                if object != 0 { standing.append(object) }
                if index == world.skullCell { standing.append(Self.skullTile) }
                if index == world.ankhCell { standing.append(Self.ankhTile) }
                cells.append(CloseUp.Cell(cx: cx, cy: cy, height: height, tile: tile, standing: standing))
                let who = Int(world.who[index])
                if who != 0 {
                    figures += self.figures(world, index: who - 1, cx: cx, cy: cy, lift: height * 8 + 8, phase: phase)
                }
            }
        }
        // The original's sprite routine drops anything that starts above the screen.
        figures.removeAll { $0.y < 0 }
        // The surroundings, held at the map's edges.
        let m = CloseUp.margin
        var cornersAround: [Int] = []
        for y in -m..<side + m {
            for x in -m..<side + m {
                let mx = min(max(viewX + x, 0), World.corners - 1), my = min(max(viewY + y, 0), World.corners - 1)
                cornersAround.append(Int(world.altitude[mx + my * World.corners]))
            }
        }
        var tilesAround: [Int] = [], objectsAround: [Int] = []
        for y in -m..<Self.viewSize + m {
            for x in -m..<Self.viewSize + m {
                let mx = min(max(viewX + x, 0), World.size - 1), my = min(max(viewY + y, 0), World.size - 1)
                tilesAround.append(Int(world.tile[mx + my * World.size]))
                objectsAround.append(Int(world.object[mx + my * World.size]))
            }
        }
        return CloseUp(
            viewX: viewX, viewY: viewY, phase: phase, corners: corners, cells: cells, figures: figures,
            cornersAround: cornersAround, tilesAround: tilesAround, objectsAround: objectsAround)
    }

    /// Paints the close-up the way the original does ($69AF6). Cells are drawn back
    /// to front with no clipping against the board; raised land simply overlaps the
    /// frame.
    public func draw(_ closeUp: CloseUp, land: LandSet, into screen: inout IndexedImage) {
        func drawTile(_ tile: Int, cx: Int, cy: Int, lift: Int) {
            screen.draw(
                land.tiles[tile],
                x: Self.viewOriginX + 16 * cx - 16 * cy,
                y: Self.viewOriginY + 8 * cx + 8 * cy - lift)
        }
        for cell in closeUp.cells {
            let lift = cell.height * 8
            drawTile(cell.tile, cx: cell.cx, cy: cell.cy, lift: lift)
            for tile in cell.standing { drawTile(tile, cx: cell.cx, cy: cell.cy, lift: lift + 8) }
        }

        // Cliff faces: one 8-pixel slab per height step under the view's two lower
        // edges, first down the right edge, then back up the left.
        let last = Self.viewSize - 1
        var x = Self.viewOriginX + 128
        var y = Self.viewOriginY + 64
        for row in 0..<Self.viewSize {
            for step in 0..<closeUp.cell(last, row).height {
                screen.draw(assets.sprites[Self.rightCliffSprite], x: x, y: y - 8 * step)
            }
            x -= 16
            y += 8
        }
        y -= 8
        for column in (0..<Self.viewSize).reversed() {
            for step in 0..<closeUp.cell(column, last).height {
                screen.draw(assets.sprites[Self.leftCliffSprite], x: x, y: y - 8 * step)
            }
            x -= 16
            y -= 8
        }

        // People go on last, in the order their cells were visited.
        for figure in closeUp.figures {
            screen.draw(assets.sprites[figure.sprite], x: figure.x, y: figure.y)
        }
    }
}
