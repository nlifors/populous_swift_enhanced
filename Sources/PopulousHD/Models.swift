import Foundation
import PopulousRender

/// The HD models made by `Assets/blender/build_assets.py`, read from its `.pmesh`
/// files. Each is a list of triangles, already turned into the close-up's space: x and y in cells, z in height steps, normals
/// in cells for lighting, base at z = 0 and centred on the origin.
public struct ModelLibrary: Sendable {
    public private(set) var models: [String: [CloseUpMesh.ModelVertex]] = [:]

    public init() {}

    /// Reads every `.pmesh` in `folder`; files that are not valid are skipped.
    public init(folder: URL) {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        for file in files where file.pathExtension == "pmesh" {
            guard let data = try? Data(contentsOf: file), let vertices = Self.parse([UInt8](data)) else { continue }
            models[file.deletingPathExtension().lastPathComponent] = vertices
        }
    }

    /// The models shipped with the game, if they can be found.
    public static func bundled() -> ModelLibrary {
        ResourceFolder.named("Models").map { ModelLibrary(folder: $0) } ?? ModelLibrary()
    }

    /// A `.pmesh` file: "PMSH", version 1, a vertex count, then per vertex ten
    /// little-endian floats (position, normal, sRGB colour, surface), in Blender's
    /// axes and units. Blender's x and y are swapped to match the game's view, and
    /// heights become height steps.
    static func parse(_ bytes: [UInt8]) -> [CloseUpMesh.ModelVertex]? {
        guard bytes.count >= 12, Array(bytes[0..<4]) == Array("PMSH".utf8) else { return nil }
        func word(_ offset: Int) -> UInt32 {
            UInt32(bytes[offset]) | UInt32(bytes[offset + 1]) << 8 | UInt32(bytes[offset + 2]) << 16
                | UInt32(bytes[offset + 3]) << 24
        }
        let count = Int(word(8))
        guard word(4) == 1, count % 3 == 0, bytes.count == 12 + count * 40 else { return nil }
        var vertices: [CloseUpMesh.ModelVertex] = []
        vertices.reserveCapacity(count)
        for v in 0..<count {
            let f = (0..<10).map { Float(bitPattern: word(12 + v * 40 + $0 * 4)) }
            vertices.append(CloseUpMesh.ModelVertex(
                x: f[1], y: f[0], z: f[2] / CloseUpMesh.stepHeight, nx: f[4], ny: f[3], nz: f[5],
                r: f[6], g: f[7], b: f[8], surface: f[9]))
        }
        return vertices
    }
}

/// Which model stands in for which of the original's pictures.
public enum ModelCatalog {
    /// Settlements from tent (tile 33) to keep (41), then the castle's centre and
    /// walls (42-44), the two magnets (45, 46) and the ruins of all of these (54-65).
    static let objects: [Int: String] = [
        33: "house_1_tent", 34: "house_2_mud_hut", 35: "house_3_thatch_hut", 36: "house_4_thatch_stone",
        37: "house_5_log_cabin", 38: "house_6_timber_house", 39: "house_7_stone_house", 40: "house_8_round_fort",
        41: "keep_tower", 42: "castle_center", 43: "castle_wall_x", 44: "castle_wall_y",
        45: "ankh", 46: "skull",
    ]

    /// The three trees (tiles 50-52) of each landscape, as the original draws them.
    static let trees = [
        ["pine", "dead_tree", "fruit_tree"],
        ["palm", "cactus", "dead_bush"],
        ["pine", "dead_tree", "bare_tree"],
        ["bare_tree", "pebbles", "dead_tree"],
    ]

    /// The model for a tile standing on a cell, if there is one.
    public static func object(_ tile: Int, terrain: Int) -> String? {
        if let name = objects[tile] { return name }
        if (50...52).contains(tile) { return trees[min(max(terrain, 0), 3)][tile - 50] }
        // A house the knight burned: the tile of the house, 0x15 on.
        if (54...65).contains(tile) { return tile - 0x15 >= 40 ? "ruin_large" : "ruin_small" }
        return nil
    }

    /// The model for a cell's ground tile that is more than land: rocks and swamp.
    public static func ground(_ tile: Int) -> String? {
        switch tile {
        case 47: "rock_a"
        case 48: "rock_b"
        case 49: "rock_c"
        case 53: "swamp"
        default: nil
        }
    }

    /// How much bigger than its true size a model is drawn, so that people and
    /// buildings read as clearly as the original's pictures did. Castle pieces
    /// stay true so they still join up.
    public static func scale(_ name: String) -> Float {
        if name.hasPrefix("walker") || name.hasPrefix("knight") || name.hasPrefix("armed") { return 1.5 }
        if name.hasPrefix("flag") { return 1.3 }
        // The round fort already fills its cell.
        if name == "house_8_round_fort" { return 1.06 }
        if name.hasPrefix("house") { return 1.12 }
        if name.hasPrefix("castle") || name == "keep_tower" { return 1 }
        if ["pine", "fruit_tree", "dead_tree", "bare_tree", "palm", "cactus"].contains(name) { return 1.1 }
        return 1
    }

    /// The model for a person, by what they are doing and the animation phase.
    public static func figure(_ figure: CloseUp.Figure, phase: Int) -> String? {
        let frame = phase == 0 ? "a" : "b"
        switch figure.kind {
        case .flag: return "flag_" + frame
        case .person:
            guard let walk = figure.walk else { return nil }
            return (walk.knight ? "knight_" : "walker_") + frame
        case .followedMark, .leaderMark: return nil
        }
    }
}
