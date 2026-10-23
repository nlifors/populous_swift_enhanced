import CoreGraphics
import Foundation
import ImageIO
import Metal

/// The HD ground textures made by `Assets/textures/make_textures.py`: 512x512
/// tileable pictures, each spread over 2x2 map cells, loaded as the layers of one
/// texture array.
public enum GroundTextures {
    /// Layer order; the shaders refer to layers by these positions.
    public static let names = [
        "grass", "sand", "desert", "snow", "ash", "gravel", "farmland", "scorched", "earth", "water", "lava", "noise",
        "rock", "meadow",
    ]
    static let size = 512

    /// Each landscape's own ground and beach layers: grass and sand, desert and
    /// sand, snow and gravel, ash and gravel. The fourth landscape's sea is lava.
    static let landscapes: [(ground: Int, beach: Int)] = [(0, 1), (2, 1), (3, 5), (4, 5)]
    static let lavaLandscape = 3

    /// A flat stand-in for a texture whose file is missing, so the game still runs.
    static let standIns: [String: (UInt8, UInt8, UInt8)] = [
        "grass": (96, 150, 56), "sand": (214, 190, 130), "desert": (200, 140, 80), "snow": (232, 236, 244),
        "ash": (96, 90, 84), "gravel": (90, 88, 86), "farmland": (170, 150, 70), "scorched": (50, 40, 32),
        "earth": (140, 90, 50), "water": (40, 90, 170), "lava": (230, 90, 20), "noise": (128, 128, 128),
        "rock": (138, 132, 120), "meadow": (110, 160, 60),
    ]

    /// The texture array, from `folder` (or the game's own resources); missing or
    /// unreadable files become flat stand-ins.
    static func load(device: MTLDevice, queue: MTLCommandQueue, folder: URL? = ResourceFolder.named("Textures"))
        -> MTLTexture?
    {
        let descriptor = MTLTextureDescriptor()
        descriptor.textureType = .type2DArray
        descriptor.pixelFormat = .rgba8Unorm
        descriptor.width = size
        descriptor.height = size
        descriptor.arrayLength = names.count
        descriptor.mipmapLevelCount = Int(log2(Double(size))) + 1
        descriptor.usage = .shaderRead
        guard let texture = device.makeTexture(descriptor: descriptor) else { return nil }
        for (layer, name) in names.enumerated() {
            let pixels = folder.flatMap { rgba(of: $0.appendingPathComponent(name + ".png")) } ?? standIn(name)
            pixels.withUnsafeBytes {
                texture.replace(
                    region: MTLRegionMake2D(0, 0, size, size), mipmapLevel: 0, slice: layer,
                    withBytes: $0.baseAddress!, bytesPerRow: size * 4, bytesPerImage: size * size * 4)
            }
        }
        if let commands = queue.makeCommandBuffer(), let blit = commands.makeBlitCommandEncoder() {
            blit.generateMipmaps(for: texture)
            blit.endEncoding()
            commands.commit()
            commands.waitUntilCompleted()
        }
        return texture
    }

    static func standIn(_ name: String) -> [UInt8] {
        let (r, g, b) = standIns[name] ?? (128, 128, 128)
        return [UInt8]((0..<size * size).flatMap { _ in [r, g, b, 255] })
    }

    /// A picture's pixels as 8-bit sRGB RGBA, scaled to 512x512.
    static func rgba(of url: URL) -> [UInt8]? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { return nil }
        var pixels = [UInt8](repeating: 0, count: size * size * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { return false }
            context.interpolationQuality = .high
            context.draw(image, in: CGRect(x: 0, y: 0, width: size, height: size))
            return true
        }
        guard drawn else { return nil }
        for i in stride(from: 3, to: pixels.count, by: 4) { pixels[i] = 255 }
        return pixels
    }
}

/// Folders of HD resources shipped with the game: inside the app, or beside the
/// executable when it runs from the build folder.
enum ResourceFolder {
    static func named(_ name: String) -> URL? {
        let bundleName = "Populous_PopulousHD.bundle"
        let places = [
            Bundle.main.resourceURL?.appendingPathComponent(bundleName),
            Bundle.main.bundleURL.appendingPathComponent(bundleName),
            Bundle.main.executableURL?.deletingLastPathComponent().appendingPathComponent(bundleName),
        ]
        for case let place? in places {
            if let bundle = Bundle(url: place), let folder = bundle.url(forResource: name, withExtension: nil) {
                return folder
            }
        }
        return nil
    }
}
