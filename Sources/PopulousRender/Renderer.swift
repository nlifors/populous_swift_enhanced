import PopulousAssets
import PopulousCore

/// Draws the game screen the way the original does: board picture, minimap, then
/// the 8x8-cell isometric close-up, all into one 320x200 indexed image. The
/// close-up is worked out as a `CloseUp` first, which other renderers can draw too.
public struct Renderer: Sendable {
    /// Cells per side of the close-up view.
    public static let viewSize = 8
    /// Screen position of the tile for the view's top cell at height 0.
    public static let viewOriginX = 176
    public static let viewOriginY = 64
    /// 16x16 sprites used for the cliff faces along the view's two lower edges.
    static let leftCliffSprite = 76
    static let rightCliffSprite = 77
    static let ankhTile = 45
    static let skullTile = 46
    /// The small diamond that marks the close-up's position on the minimap.
    static let viewMarkerSprite = 0x54

    public let assets: GameAssets

    public init(assets: GameAssets) {
        self.assets = assets
    }

    /// Largest valid view origin on either axis.
    public static var maxViewOrigin: Int { World.size - viewSize }

    /// Minimap position of a cell: the map is drawn as a 128x64 diamond in the
    /// board's top-left corner.
    public static func minimapPoint(x: Int, y: Int) -> (x: Int, y: Int) {
        (x + 64 - y, (x + y) >> 1)
    }

    /// The map cell under a screen point on the minimap, if any ($5F884). The
    /// original centres the close-up three cells up and left of it.
    public static func minimapCell(atX px: Int, y py: Int) -> (x: Int, y: Int)? {
        let x = py + px >> 1 - 0x20
        let y = py - px >> 1 + 0x20
        guard (0..<World.size).contains(x), (0..<World.size).contains(y) else { return nil }
        return (x, y)
    }

    /// The icon under a screen point on the board's lower-left panel, as the grid
    /// column and row the original computes ($5F884). Columns 3-5 x rows 0-2 are the
    /// eight view arrows around a centre button; the rest are commands.
    public static func panelIcon(atX px: Int, y py: Int) -> (column: Int, row: Int)? {
        let u = py + px >> 1
        let v = py - px >> 1
        guard minimapCell(atX: px, y: py) == nil, u - 0x20 < 0x113, v + 0x20 > 0x91 else { return nil }
        return ((u - 0x80) / 16, (v - 0x70) / 16)
    }

    /// The icon under a screen point on the board's lower-right panel ($5F884):
    /// (0,0) game balance, (0,3) music, (0,4) sound effects, (1,1) world, (1,3) pause,
    /// (2,2) message.
    public static func sidePanelIcon(atX px: Int, y py: Int) -> (column: Int, row: Int)? {
        let u = py + px >> 1
        let v = py - px >> 1
        guard minimapCell(atX: px, y: py) == nil, u - 0x20 >= 0x113, v >= 0 else { return nil }
        return ((u - 0x130) / 16, v / 16)
    }

    /// The height corner the mouse points at in the close-up, as map corner
    /// coordinates ($63B2E). The mouse picks the screen column of corners it is in,
    /// then the lowest corner on that column drawn at or above it.
    public static func viewCorner(atX px: Int, y py: Int, world: World, viewX: Int, viewY: Int) -> (x: Int, y: Int)? {
        let u = py + px >> 1 - 0x20
        let v = py - px >> 1 + 0x20
        guard u > 0x3D, u < 0x10B, v > -0x41, v < 0x89, px > 0x3F else { return nil }
        let column = (px - 0x38) >> 4
        var count = 9
        var corner = viewX + viewY * World.corners
        var screenY = 0x48
        if column > 8 {
            count = 17 - column
            corner += column - 8
            screenY += (column - 8) * 8
        }
        if column < 8 {
            count = column + 1
            corner += (8 - column) * World.corners
            screenY += (8 - column) * 8
        }
        var picked: Int?
        for k in 0..<count {
            if screenY - Int(world.altitude[corner + k * 66]) * 8 <= py + 4 { picked = k }
            screenY += 16
        }
        guard let picked else { return nil }
        corner += picked * 66
        return (corner % World.corners, corner / World.corners)
    }

    /// The states of everybody visible in the close-up, OR-ed together as the
    /// original gathers them while drawing. A side's own people contribute their
    /// whole state (and bit 0 if they recently left a house); others only the high
    /// bits. The player may only reshape land while this is non-zero.
    public static func visibleStates(world: World, viewX: Int, viewY: Int, side: Int) -> Int {
        var states = 0
        for cy in 0..<viewSize {
            for cx in 0..<viewSize {
                let who = Int(world.who[viewX + cx + (viewY + cy) * World.size])
                guard who != 0, who <= World.maxPeople + 1 else { continue }
                let person = world.people[who - 1]
                if Int(person.owner) == side {
                    states |= Int(person.state) | (person.word18 != 0 ? 1 : 0)
                } else {
                    states |= Int(person.state) & 0xF8
                }
            }
        }
        return states
    }

    /// One frame. `viewX`/`viewY` is the map cell at the top corner of the close-up;
    /// `phase` (0 or 1) flips every frame and drives the two-frame animations.
    /// `litIcons` and `litSideIcons` are icons (column, row) on the two panels to
    /// show as switched on. With `closeUp` false the close-up is left out, for a
    /// renderer that draws it separately.
    public func frame(
        world: World, terrain: Int, viewX: Int, viewY: Int, phase: Int = 0,
        litIcons: [(Int, Int)] = [], litSideIcons: [(Int, Int)] = [], closeUp includeCloseUp: Bool = true
    ) -> IndexedImage {
        let land = assets.landSets[terrain]
        var screen = assets.board.image
        drawMinimap(world, land: land, into: &screen)
        drawViewMarker(viewX: viewX, viewY: viewY, into: &screen)
        drawMinimapPeople(world, phase: phase, into: &screen)
        drawManaPointer(world, into: &screen)
        if includeCloseUp {
            draw(closeUp(world: world, viewX: viewX, viewY: viewY, phase: phase), land: land, into: &screen)
        }
        drawShield(world, land: land, phase: phase, into: &screen)
        drawPopulationBars(world, into: &screen)
        lightIcons(litIcons, side: litSideIcons, into: &screen)
        return screen
    }

    /// Brightens the stone of the given icons so the player can see what is switched
    /// on. (The original marks its icons too, by a routine that is not ported; this
    /// is a stand-in.)
    func lightIcons(_ icons: [(Int, Int)], side: [(Int, Int)], into screen: inout IndexedImage) {
        guard !icons.isEmpty || !side.isEmpty else { return }
        for y in 96..<screen.height {
            for x in 0..<screen.width {
                let lit: Bool
                if let icon = Self.panelIcon(atX: x, y: y) {
                    lit = icons.contains { $0 == icon }
                } else if let icon = Self.sidePanelIcon(atX: x, y: y) {
                    lit = side.contains { $0 == icon }
                } else {
                    continue
                }
                // Greys run from 1 (dark) to 5 (light) in the game palette.
                if lit, (1...5).contains(screen[x, y]) { screen[x, y] = min(screen[x, y] + 2, 5) }
            }
        }
    }

    /// The marker on the minimap for where the close-up is ($5EEF2): sprite $54 three
    /// pixels up and left of the view's centre cell, drawn before the blinking dots.
    func drawViewMarker(viewX: Int, viewY: Int, into screen: inout IndexedImage) {
        let centre = Self.minimapPoint(x: viewX + 3, y: viewY + 3)
        screen.draw(assets.sprites[Self.viewMarkerSprite], x: centre.x - 3, y: centre.y - 3)
    }

    /// The blinking dots on the minimap ($6A71A): houses on one phase, people on the
    /// move and fights on the other. The house colours are the ones the original's
    /// memory image holds for each side.
    func drawMinimapPeople(_ world: World, phase: Int, into screen: inout IndexedImage) {
        for index in 0..<world.personCount {
            let person = world.people[index]
            guard person.life > 0 else { continue }
            let color: UInt8
            if person.state == Person.State.settled {
                guard phase == 0 else { continue }
                color = person.owner == 0 ? 5 : 1
            } else if person.state == Person.State.fighting {
                color = UInt8(phase * 7 + 8)
            } else if person.state == Person.State.walking || person.state & 0x60 != 0 {
                guard phase != 0 else { continue }
                color = person.owner == 0 ? 15 : 8
            } else {
                continue
            }
            let cell = Int(person.cell)
            let point = Self.minimapPoint(x: cell & 63, y: cell >> 6)
            screen[point.x, point.y] = color
        }
    }

    /// Mana levels marked along the board's rail ($72FE4): the floor, then what each
    /// action from reshaping land to Armageddon needs, then the ceiling.
    static let manaMarks = [-250, 10, 200, 2500, 5000, 7500, 10000, 40000, 80000, 160000, 1_999_999]

    /// The pointer that slides down the rail of power icons as the human side's mana
    /// grows ($60A5A): one icon per mark, eight steps between marks.
    func drawManaPointer(_ world: World, into screen: inout IndexedImage) {
        let mana = Int(world.players[0].mana)
        let pointer = assets.sprites[0x45]
        guard let mark = Self.manaMarks.firstIndex(where: { mana <= $0 }), mark <= 9 else {
            screen.draw(pointer, x: 0x137, y: 0x57)
            return
        }
        // (At the floor the original reads below its table; the pointer is simply
        // not drawn here.)
        guard mark >= 1 else { return }
        let below = Self.manaMarks[mark - 1]
        let step = (mana - below) * 8 / (Self.manaMarks[mark] - below)
        screen.draw(pointer, x: (mark - 1) * 16 + step * 2 + 0xA0, y: (mark - 1) * 8 + step + 8)
    }

    /// A bar four pixels wide in the byte column `column` ($6C342): `value` rows of
    /// `color` growing upwards from row `bottom`, the rest of `height` in grey.
    func drawBar(column: Int, bottom: Int, height: Int, value: Int, color: UInt8, into screen: inout IndexedImage) {
        let filled = min(max(value, 0), height)
        for row in 0..<height {
            for x in 2...5 { screen[column * 8 + x, bottom - row] = row < filled ? color : 2 }
        }
    }

    /// The two bars beside the shield showing each side's population ($605C8): up to
    /// 32 pixels tall, growing upwards from row 31.
    func drawPopulationBars(_ world: World, into screen: inout IndexedImage) {
        for (side, column, color) in [(0, 0x20, UInt8(0xF)), (1, 0x27, UInt8(8))] {
            let population = Int(world.players[side].population)
            drawBar(
                column: column, bottom: 0x1F, height: 0x20, value: population == 0 ? 0 : population * 31 / 50000 + 1,
                color: color, into: &screen)
        }
    }

    /// Cell offsets of the eight walking directions in sprite order ($72FC0).
    static let shieldDirections = [-65, -64, -63, 1, 65, 64, 63, -1]

    /// The shield's four quarters for the person being followed ($605C8): the
    /// side's symbol, the weapon carried, a picture of the person and two bars.
    func drawShield(_ world: World, land: LandSet, phase: Int, into screen: inout IndexedImage) {
        guard world.selectedPerson != 0 else { return }
        let person = world.people[world.selectedPerson - 1]
        guard person.life > 0 else { return }
        let owner = Int(person.owner)
        let life = Int(person.life)
        let armed = person.long14 != 0
        func bars(_ left: (value: Int, color: UInt8), _ right: (value: Int, color: UInt8)) {
            drawBar(column: 0x24, bottom: 0x25, height: 0x10, value: left.value, color: left.color, into: &screen)
            drawBar(column: 0x25, bottom: 0x25, height: 0x10, value: right.value, color: right.color, into: &screen)
        }
        func picture(_ sprite: Int, x: Int = 0x110) {
            if assets.sprites.indices.contains(sprite) { screen.draw(assets.sprites[sprite], x: x, y: 0x16) }
        }

        screen.draw(assets.shieldBlocks[owner & 1], x: 0x110, y: 4)
        // The weapon is the rank of the person's strength among the land's eleven.
        let strengths = LandRules(landWords: land.headerWords).strength.sorted()
        if let rank = (1..<11).first(where: { strengths[$0] == Int(person.byte3) }) {
            screen.draw(assets.shieldBlocks[rank + 1], x: 0x120, y: 4)
        }

        if person.state & Person.State.fighting != 0 {
            // Each fighter's share of the life left between them, side 0 on the left.
            picture(Int(person.frame))
            let other = Int(person.word6)
            guard world.people.indices.contains(other) else { return }
            let mine = Int(person.life), theirs = Int(world.people[other].life)
            let total = Int(Int16(truncatingIfNeeded: mine + theirs))
            guard total != 0 else { return }
            let first = owner == 0 ? mine : theirs
            let second = owner == 0 ? theirs : mine
            bars((first * 16 / total, 0xF), (second * 16 / total, 8))
        } else if person.state == Person.State.settled {
            // How good the land is for a house, and how full the house is.
            picture(owner * 2 + phase + 0x40, x: 0x10C)
            var score = world.houseSiteScore(owner: owner, cell: Int(person.cell))
            if score == 0 { score = 1 }
            let land = score == World.castleScore ? 16 : ((score << 4) & 0xFFFF) / 0x131
            bars((land, 0xA), (((life << 4) & 0xFFFF) / score, 0xC))
        } else {
            let sprite: Int
            if person.state & 0x60 != 0 {
                sprite = (armed ? 0x7D : 0x65) + owner * 2 + phase
            } else if person.state & Person.State.drowning != 0 {
                sprite = (armed ? 0x6D : 0x5D) + owner * 4 + Int(world.tick & 3)
            } else {
                let direction = Self.shieldDirections.firstIndex(of: Int(person.direction)) ?? 4
                sprite = direction * 2 + phase + (armed ? 0x20 : 0) + owner * 16
            }
            picture(sprite)
            // Strength in two digits: sixteens of sixteen, or thousands when huge.
            if life > 0x1000 {
                bars((life / 0x400, 0xA), (life % 0x400, 9))
            } else {
                bars((life / 0x100, 9), (life % 0x100 / 0x10, 9))
            }
        }
    }

    /// One pixel per cell, coloured by its tile ($5F76E).
    func drawMinimap(_ world: World, land: LandSet, into screen: inout IndexedImage) {
        let colors = land.minimapColors
        for x in 0..<World.size {
            for y in 0..<World.size {
                let tile = Int(world.tile[x + y * World.size])
                let color: UInt8
                switch tile {
                case 0..<32: color = colors[tile & 15]  // slopes at sea level reuse the shape's colour
                case 47...49: color = 2  // rock
                default: color = colors[15]  // everything built shows as flat land
                }
                let point = Self.minimapPoint(x: x, y: y)
                screen[point.x, point.y] = color
            }
        }
    }

    /// Per walking direction (as a cell offset): the pixel step per animation frame,
    /// the first sprite of that direction, and which view edges the walker must not
    /// be on once it is more than half way out of its cell.
    private static let walks: [Int: (dx: Int, dy: Int, sprite: Int, edges: [(Int, Int)])] = [
        -65: (0, -2, 0, [(0, -1), (-1, 0)]),
        -64: (2, -1, 2, [(-1, 0)]),
        -63: (4, 0, 4, [(7, -1), (-1, 0)]),
        1: (2, 1, 6, [(7, -1)]),
        65: (0, 2, 8, [(7, -1), (-1, 7)]),
        64: (-2, 1, 10, [(-1, 7)]),
        63: (-4, 0, 12, [(0, -1), (-1, 7)]),
        -1: (-2, -1, 14, [(0, -1)]),
    ]

    /// Works out the sprite and position for the person on a view cell, and the
    /// marks they carry ($69D20).
    func figures(_ world: World, index: Int, cx: Int, cy: Int, lift: Int, phase: Int) -> [CloseUp.Figure] {
        let person = world.people[index]
        var x = 16 * cx - 16 * cy
        var y = 8 * cx + 8 * cy - lift
        let sprite: Int
        let kind: CloseUp.Figure.Kind
        var walk: CloseUp.Figure.Walk?

        let state = person.state
        let armed = person.long14 != 0
        let second = person.owner != 0
        if state == Person.State.settled {
            // A flag in the side's colour.
            x += 0xC0
            y += 0x40
            sprite = 0x40 + phase + (second ? 2 : 0)
            kind = .flag
        } else if state == Person.State.walking || state & Person.State.drowning != 0 {
            let frame = Int(person.frame)
            guard frame < 8 else { return [] }
            let cell = Int(person.cell)
            let direction = Int(person.direction)
            // Climb or descend gradually towards the cell being walked to.
            let target = cell + direction
            var climb = 0
            if world.height.indices.contains(target) {
                climb = Int(world.height[target]) - Int(world.height[cell])
                y -= climb * frame
            }
            if world.tile[cell] != World.Tile.flat { y += 4 }
            var step = (dx: 0, dy: 0)
            var base = 8
            if let walk = Self.walks[direction] {
                if frame > 4, walk.edges.contains(where: { ($0.0 == cx) || ($0.1 == cy) }) { return [] }
                step = (walk.dx, walk.dy)
                base = walk.sprite
            }
            if state & Person.State.drowning != 0 {
                sprite = 0x5D + Int(world.tick & 3) + (armed ? 0x10 : 0) + (second ? 4 : 0)
            } else {
                let knight = person.long14 >> 16 != 0
                sprite = base + (knight ? 0x20 : 0) + (second ? 0x10 : 0) + phase
                walk = CloseUp.Figure.Walk(direction: direction, frame: frame, climb: climb, knight: knight)
            }
            x += step.dx * frame + 0xB8
            y += step.dy * frame + 0x40
            kind = .person
        } else if state & 0x60 != 0 {
            // Stopped to greet a passer-by.
            x += 0xC0
            y += 0x40
            sprite = Int(person.frame) + (armed ? 0x18 : 0) + (second ? 2 : 0)
            kind = .person
        } else if state & Person.State.fighting != 0 {
            x += 0xC0
            y += 0x40
            sprite = Int(person.frame)
            kind = .person
        } else if state & 0x80 != 0 {
            // A burning ruin.
            x += 0xB8
            y += 0x40
            sprite = 0x69 + Int(world.tick & 3)
            kind = .person
        } else {
            // Celebrating a win.
            x += 0xC0
            y += 0x40
            sprite = Int(person.frame) + (armed ? 0x20 : 0) + (second ? 4 : 0)
            kind = .person
        }
        guard assets.sprites.indices.contains(sprite) else { return [] }

        let owner = Int(person.owner)
        func figure(x: Int, sprite: Int, kind: CloseUp.Figure.Kind) -> CloseUp.Figure {
            CloseUp.Figure(
                x: x, y: y, sprite: sprite, kind: kind, person: index, cx: cx, cy: cy, side: owner,
                walk: kind == .person ? walk : nil)
        }
        var figures = [figure(x: x, sprite: sprite, kind: kind)]
        // The person being followed carries a small shield.
        if world.selectedPerson == index + 1 { figures.append(figure(x: x + 8, sprite: 0x44, kind: .followedMark)) }
        // The leader carries its side's symbol.
        if world.players[owner].leader == Int16(index + 1) {
            figures.append(figure(x: x + 8, sprite: 0x4A + owner, kind: .leaderMark))
        }
        return figures
    }
}
